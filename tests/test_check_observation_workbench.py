"""Synthetic coverage for explicit observation-bound product investigations."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
import threading

import pytest

from issueintel.investigation.workbench import Workbench, validate_request
from issueintel.runtime.io import digest, write_json
from test_check_plan_jobs import CheckProvider, checking, generate as generate_plan
from test_clarification_workbench import repair_export
from test_workbench import completed, http, local_server


def observation(**changes):
    return dict({'check_id': 'check_1', 'outcome': 'A',
        'conditions': 'Local disposable fixture; identical interpreter and input.',
        'observed_result': 'The first input raised; the second returned a list.',
        'interpretation': 'A user report for investigation, not verified causation.',
        'tested_commit_sha': None, 'supersedes': None}, **changes)


def append(wb, plan_job, **changes):
    journal = wb.check_observations(plan_job['id'])
    return wb.check_observations(plan_job['id'], {
        'expected_sha256': journal['sha256'], 'observation': observation(**changes)})


def followup_request(plan_job, plan, journal, **changes):
    return dict({'plan_job_id': plan_job['id'], 'plan_sha256': plan['sha256'],
        'journal_sha256': journal['sha256'],
        'observation_ids': [journal['events'][-1]['observation_id']],
        'provider': 'none', 'depth': 'quick'}, **changes)


def child_for(wb, source, **observation_changes):
    plan_job, plan = generate_plan(wb, source)
    journal = append(wb, plan_job, **observation_changes)
    request = followup_request(plan_job, plan, journal)
    child = completed(wb, wb.submit_check_followup(request)['id'])
    assert child['status'] == 'completed', child
    return plan_job, plan, journal, request, child


def test_supplemental_observations_preserve_original_report_plan_and_journal(checking):
    wb, provider, source = checking
    before_pack = wb.result_bytes(source['id'])
    before_report = wb.report_bytes(source['id'])
    before_journal = wb.check_journal_export(source['id'])
    plan_job, plan = generate_plan(wb, source)
    before_plan = wb.check_plan_export(plan_job['id'])
    calls = len(provider.calls), len(provider.check_calls)
    journal = append(wb, plan_job)
    assert journal['schema'] == 'supplemental_check_observation_journal_v1'
    assert journal['source']['plan_job_id'] == plan_job['id']
    assert journal['source']['plan_sha256'] == plan['sha256']
    assert journal['source']['source_pack_sha256'] == hashlib.sha256(before_pack).hexdigest()
    assert journal['checks'] == plan['checks'] and journal['revision'] == 1
    assert journal['provenance'] == 'user_report_unverified'
    for flag in ('executed_by_issueintel', 'causal_verified', 'fix_verified',
                 'independent_human', 'evaluation_performed'):
        assert journal[flag] is False
    assert wb.result_bytes(source['id']) == before_pack
    assert wb.report_bytes(source['id']) == before_report
    assert wb.check_journal_export(source['id']) == before_journal
    assert wb.check_plan_export(plan_job['id']) == before_plan
    assert (len(provider.calls), len(provider.check_calls)) == calls


def test_observation_exports_bind_exact_report_plan_and_journal(checking):
    wb, provider, source = checking
    plan_job, plan = generate_plan(wb, source)
    journal = append(wb, plan_job)
    before = len(provider.calls), len(provider.check_calls)
    raw, report_sha, plan_sha, journal_sha = wb.check_observation_export(plan_job['id'])
    assert json.loads(raw) == journal
    assert report_sha == plan['source']['report_sha256']
    assert plan_sha == plan['sha256'] and journal_sha == journal['sha256']
    markdown = wb.check_observation_export(plan_job['id'], 'md')
    assert markdown[1:] == (report_sha, plan_sha, journal_sha)
    assert journal_sha.encode() in markdown[0] and b'observation_1' in markdown[0]
    assert (len(provider.calls), len(provider.check_calls)) == before


def test_followup_none_is_explicit_new_source_bound_job_without_provider(checking, monkeypatch):
    wb, provider, source = checking
    source_raw = wb.result_bytes(source['id'])
    plan_job, plan = generate_plan(wb, source)
    journal = append(wb, plan_job, tested_commit_sha='f' * 40)
    request = followup_request(plan_job, plan, journal)
    before = len(provider.calls), len(provider.check_calls)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',
                        lambda *a: pytest.fail('Offline follow-up loaded paid config'))
    wb.provider_factory = lambda *a, **k: pytest.fail('Offline follow-up constructed provider')
    child = completed(wb, wb.submit_check_followup(request)['id'])
    assert child['status'] == 'completed', child
    pack = json.loads(wb.result_bytes(child['id']))
    parent = json.loads(source_raw)
    assert child['mode'] == 'check_followup' and child['provider'] == 'none'
    assert pack['annotation'] is None
    assert pack['snapshot'] == parent['snapshot']
    assert pack['followup']['parent_job_id'] == source['id']
    assert pack['followup']['check_observations']['schema'] == 'check_observation_followup_v1'
    assert 'f' * 40 in pack['case']['problem_statement']
    assert pack['snapshot']['commit_sha'] != 'f' * 40
    assert pack['leakage_audit']['strict_issue_time_verified'] is False
    assert journal['events'][0]['observation']['observed_result'] in pack['case']['problem_statement']
    assert wb.result_bytes(source['id']) == source_raw
    assert (len(provider.calls), len(provider.check_calls)) == before
    with pytest.raises(ValueError):
        wb.preview_comment(child['id'])
    assert wb.resume_preview(child['id'])['available'] is False


def test_groq_followup_is_an_explicit_new_generation_not_inherited_calls(checking):
    wb, provider, source = checking
    plan_job, plan = generate_plan(wb, source)
    journal = append(wb, plan_job)
    request = followup_request(plan_job, plan, journal, provider='groq')
    before = len(provider.calls)
    child = completed(wb, wb.submit_check_followup(request)['id'])
    assert child['status'] == 'completed', child
    assert len(provider.calls) == before + 2
    assert len(provider.check_calls) == 1
    pack = json.loads(wb.result_bytes(child['id']))
    assert pack['annotation'] is not None and pack['verification']['kind'] == 'llm_separate_pass'
    assert pack['followup']['check_observations']['schema'] == 'check_observation_followup_v1'


def test_concurrent_duplicate_submissions_share_one_durable_child(checking):
    wb, provider, source = checking
    plan_job, plan = generate_plan(wb, source)
    journal = append(wb, plan_job)
    request = followup_request(plan_job, plan, journal, provider='groq')
    before = len(provider.calls)
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = list(pool.map(lambda _: wb.submit_check_followup(request), range(3)))
    assert len({job['id'] for job in jobs}) == 1
    assert completed(wb, jobs[0]['id'])['status'] == 'completed'
    assert len(provider.calls) == before + 2
    assert wb.submit_check_followup(request)['id'] == jobs[0]['id']


def test_saved_child_journal_and_idempotence_survive_restart_without_git_or_provider(checking, monkeypatch):
    wb, provider, source = checking
    plan_job, plan, journal, request, child = child_for(wb, source)
    child_raw = wb.result_bytes(child['id'])
    export = wb.check_observation_export(plan_job['id'])
    other = Workbench(wb.root, provider_factory=lambda *a, **k: pytest.fail('Restart spent API'))
    try:
        monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',
                            lambda *a: pytest.fail('Saved read loaded config'))
        monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot',
                            lambda *a, **k: pytest.fail('Saved read replayed Git'))
        assert other.result_bytes(child['id']) == child_raw
        assert other.check_observations(plan_job['id']) == journal
        assert other.check_observation_export(plan_job['id']) == export
        assert other.submit_check_followup(request)['id'] == child['id']
    finally:
        other.close()


def test_later_append_and_correction_do_not_rewrite_or_invalidate_saved_child(checking):
    wb, _, source = checking
    plan_job, plan, journal, request, child = child_for(wb, source)
    saved = wb.result_bytes(child['id'])
    report = wb.report_bytes(child['id'])
    updated = append(wb, plan_job, outcome='B', observed_result='A later trial returned a list.',
                     supersedes='observation_1')
    assert updated['revision'] == 2 and len(updated['events']) == 2
    assert wb.result_bytes(child['id']) == saved
    assert wb.report_bytes(child['id']) == report
    assert wb.submit_check_followup(request)['id'] == child['id']


@pytest.mark.parametrize('change', [
    {'provider': None}, {'provider': 'another-model'}, {'depth': 'planned'},
    {'revision': 'HEAD'}, {'issue_text': 'override'}, {'patch': 'restricted'},
    {'plan_job_id': '../.env'}, {'plan_sha256': '0' * 64}, {'journal_sha256': '0' * 64},
    {'observation_ids': []}, {'observation_ids': ['observation_1', 'observation_1']},
    {'observation_ids': ['observation_999']},
    {'observation_ids': ['observation_1', 'observation_2', 'observation_3', 'observation_4']},
])
def test_invalid_or_stale_followup_rejected_before_queue_or_provider(checking, monkeypatch, change):
    wb, _, source = checking
    plan_job, plan = generate_plan(wb, source)
    journal = append(wb, plan_job)
    request = followup_request(plan_job, plan, journal, **change)
    monkeypatch.setattr(wb, '_enqueue', lambda *a, **k: pytest.fail('Invalid request queued'))
    wb.provider_factory = lambda *a, **k: pytest.fail('Invalid request constructed provider')
    with pytest.raises(ValueError):
        wb.submit_check_followup(request)


def test_provider_and_depth_must_be_explicit(checking):
    wb, _, source = checking
    plan_job, plan = generate_plan(wb, source)
    journal = append(wb, plan_job)
    for field in ('provider', 'depth'):
        request = followup_request(plan_job, plan, journal)
        request.pop(field)
        with pytest.raises(ValueError):
            wb.submit_check_followup(request)


def test_unrun_or_superseded_observations_cannot_be_selected(checking):
    wb, _, source = checking
    plan_job, plan = generate_plan(wb, source)
    unrun = append(wb, plan_job, outcome='not_run', conditions='', observed_result='',
                   interpretation='Waiting for disposable environment.')
    with pytest.raises(ValueError):
        wb.submit_check_followup(followup_request(plan_job, plan, unrun))
    active = append(wb, plan_job, supersedes='observation_1')
    with pytest.raises(ValueError):
        wb.submit_check_followup(followup_request(plan_job, plan, active,
                                                observation_ids=['observation_1']))
    child = completed(wb, wb.submit_check_followup(followup_request(plan_job, plan, active))['id'])
    assert child['status'] == 'completed', child


def test_journal_optimistic_concurrency_preserves_draft_and_saved_events(checking):
    wb, _, source = checking
    plan_job, _ = generate_plan(wb, source)
    initial = wb.check_observations(plan_job['id'])
    saved = append(wb, plan_job)
    with pytest.raises(ValueError):
        wb.check_observations(plan_job['id'], {'expected_sha256': initial['sha256'],
                                              'observation': observation(outcome='B')})
    assert wb.check_observations(plan_job['id']) == saved


def deferred(wb, monkeypatch):
    captured, ready = [], threading.Event()
    run = wb._run
    def capture(row):
        captured.append(row)
        ready.set()
    monkeypatch.setattr(wb, '_run', capture)
    return captured, ready, run


@pytest.mark.parametrize('mutation', ['source', 'journal'])
def test_queued_followup_rechecks_source_and_current_journal_before_provider(checking, monkeypatch, mutation):
    wb, _, source = checking
    plan_job, plan = generate_plan(wb, source)
    journal = append(wb, plan_job)
    captured, ready, run = deferred(wb, monkeypatch)
    child = wb.submit_check_followup(followup_request(plan_job, plan, journal, provider='groq'))
    assert ready.wait(5)
    if mutation == 'source':
        repair_export(wb, source, lambda pack: pack.update(confidence={'level': 'changed', 'probability': None}))
    else:
        append(wb, plan_job, observed_result='A newer observation arrived while queued.')
    wb.provider_factory = lambda *a, **k: pytest.fail('Stale queued source constructed provider')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',
                        lambda *a: pytest.fail('Stale queued source loaded config'))
    run(captured[0])
    assert wb.job(child['id'])['status'] == 'failed'


def test_queued_cancel_never_constructs_provider_and_does_not_replace_intent(checking, monkeypatch):
    wb, _, source = checking
    plan_job, plan = generate_plan(wb, source)
    journal = append(wb, plan_job)
    request = followup_request(plan_job, plan, journal, provider='groq')
    captured, ready, run = deferred(wb, monkeypatch)
    child = wb.submit_check_followup(request)
    assert ready.wait(5)
    wb.cancel(child['id'])
    wb.provider_factory = lambda *a, **k: pytest.fail('Cancelled attempt constructed provider')
    run(captured[0])
    assert wb.job(child['id'])['status'] == 'cancelled'
    assert wb.submit_check_followup(request)['id'] == child['id']


def test_benchmark_authority_cannot_escape_through_saved_plan_or_child(checking):
    wb, _, source = checking
    plan_job, plan, journal, request, child = child_for(wb, source)
    row = json.loads(wb._job_path(source['id']).read_bytes())
    row['request'] = {'mode': 'main_dev', 'case_id': 'verified:synthetic'}
    write_json(wb._job_path(source['id']), row)
    with pytest.raises(ValueError):
        wb.check_observations(plan_job['id'])
    with pytest.raises(ValueError):
        wb.result_bytes(child['id'])
    with pytest.raises(ValueError):
        wb.submit_check_followup(request)


@pytest.mark.parametrize('mutation', ['plan_binding', 'boolean_truth_flag'])
def test_tampered_child_observation_receipt_fails_even_after_rehashing_export(checking, mutation):
    wb, _, source = checking
    _, _, _, _, child = child_for(wb, source)
    def alter(pack):
        receipt = pack['followup']['check_observations']
        receipt.pop('sha256')
        if mutation == 'plan_binding':
            receipt['source']['plan_sha256'] = '0' * 64
        else:
            # False == 0 in Python, but these are different JSON truth contracts.
            receipt['user_reports_verified'] = 0
        receipt['sha256'] = digest(receipt)
    repair_export(wb, child, alter)
    with pytest.raises(ValueError):
        wb.result_bytes(child['id'])


def test_changed_saved_child_request_cannot_restate_observation_authority(checking):
    wb, _, source = checking
    _, _, _, _, child = child_for(wb, source)
    row = json.loads(wb._job_path(child['id']).read_bytes())
    row['request']['observation_ids'] = ['observation_999']
    write_json(wb._job_path(child['id']), row)
    with pytest.raises(ValueError):
        wb.result_bytes(child['id'])


@pytest.mark.parametrize('mutation', ['delete', 'swap'])
def test_missing_or_changed_followup_intent_denies_queued_provider(checking, monkeypatch, mutation):
    wb, _, source = checking
    plan_job, plan = generate_plan(wb, source)
    journal = append(wb, plan_job)
    captured, ready, run = deferred(wb, monkeypatch)
    request = followup_request(plan_job, plan, journal, provider='groq')
    child = wb.submit_check_followup(request)
    assert ready.wait(5)
    with sqlite3.connect(wb.directory / 'check_followups.sqlite') as db:
        if mutation == 'delete':
            db.execute('DELETE FROM intents')
        else:
            db.execute('UPDATE intents SET target_job=?', ('0' * 24,))
    wb.provider_factory = lambda *a, **k: pytest.fail('Missing durable authority constructed provider')
    run(captured[0])
    assert wb.job(child['id'])['status'] == 'failed'


def test_saved_child_export_requires_its_durable_followup_intent(checking):
    wb, _, source = checking
    _, _, _, _, child = child_for(wb, source)
    with sqlite3.connect(wb.directory / 'check_followups.sqlite') as db:
        db.execute('DELETE FROM intents')
    with pytest.raises(ValueError):
        wb.result_bytes(child['id'])


@pytest.mark.parametrize('correction', [False, True])
def test_journal_change_during_invalid_output_blocks_repair_and_completed_artifact(checking, correction):
    wb, provider, source = checking
    plan_job, plan = generate_plan(wb, source)
    journal = append(wb, plan_job)
    request = followup_request(plan_job, plan, journal, provider='groq')
    attempts = []
    def invalid_and_change_journal(messages, *, max_output_tokens, schema):
        attempts.append(schema)
        assert len(attempts) == 1, 'Stale source was allowed to reach a repair provider call'
        append(wb, plan_job, outcome='B', observed_result='A corrected or newer result.',
               supersedes='observation_1' if correction else None)
        return {'invalid': True}
    provider.complete_structured = invalid_and_change_journal
    child = completed(wb, wb.submit_check_followup(request)['id'])
    assert child['status'] == 'failed', child
    assert len(attempts) == 1 and not child.get('result')
    assert wb.submit_check_followup(request)['id'] == child['id']
    with pytest.raises(ValueError):
        wb.result_bytes(child['id'])


def test_observation_text_enters_intake_but_generated_controls_do_not(checking):
    wb, _, source = checking
    plan_job, plan = generate_plan(wb, source)
    journal = append(wb, plan_job, conditions='Environment: Python 3.12 in a local fixture.',
                     observed_result='Observed: the supplied input raised.', interpretation='Still uncertain.')
    child = completed(wb, wb.submit_check_followup(followup_request(plan_job, plan, journal))['id'])
    assert child['status'] == 'completed', child
    pack = json.loads(wb.result_bytes(child['id']))
    cue = wb._cue_text(pack)
    assert 'Environment: Python 3.12 in a local fixture.' in cue
    assert 'Observed: the supplied input raised.' in cue
    assert plan['checks'][0]['assumptions'] not in cue
    assert plan['checks'][0]['outcome_a'] not in cue
    assert pack['intake']['fields']['environment']['status'] == 'mentioned'


def test_mixed_followup_lineage_can_compare_to_original_without_losing_authority(checking):
    wb, _, source = checking
    _, _, _, _, child = child_for(wb, source)
    later = completed(wb, wb.submit({'mode': 'followup', 'parent_job_id': child['id'],
                                    'additional_context': 'One more local observation.'})['id'])
    assert later['status'] == 'completed', later
    pack = json.loads(wb.result_bytes(later['id']))
    assert pack['followup']['depth'] == 2
    comparison = wb.compare({'before_id': source['id'], 'after_id': later['id']})
    assert comparison['before']['case_id'] == comparison['after']['case_id']
    assert comparison['same_context'] is False
    assert wb.intake(later['id'])['check_journal_available'] is True


def test_direct_generic_submit_cannot_forge_check_followup_authority():
    with pytest.raises(ValueError):
        validate_request({'mode': 'check_followup', 'provider': 'groq', 'depth': 'quick',
                          'parent_job_id': 'a' * 24, 'additional_context': 'Forged observations'})


def test_check_followup_shares_eight_generation_limit_with_generic_followups(checking, monkeypatch):
    wb, _, source = checking
    parent = source
    for depth in range(1, 9):
        parent = completed(wb, wb.submit({'mode': 'followup', 'parent_job_id': parent['id'],
            'additional_context': 'One more bounded report.',
            'provider': 'groq' if depth == 8 else 'none'})['id'])
        assert parent['status'] == 'completed', parent
    plan_job, plan = generate_plan(wb, parent)
    journal = append(wb, plan_job)
    monkeypatch.setattr(wb, '_enqueue', lambda *a, **k: pytest.fail('Ninth generation queued'))
    with pytest.raises(ValueError):
        wb.submit_check_followup(followup_request(plan_job, plan, journal))


def test_repeated_check_followups_replay_with_bounded_reads_and_keep_generation_limit(checking, monkeypatch):
    wb, provider, source = checking
    parent = source
    export_bytes = wb._export_bytes
    load_verified = wb._load_pack_verified
    reads = []
    verified = []

    def counted(identifier, name='pack.json'):
        reads.append((identifier, name))
        return export_bytes(identifier, name)

    def counted_validation(identifier, seen=None):
        verified.append(identifier)
        return load_verified(identifier, seen)

    for depth in range(1, 9):
        plan_job, plan = generate_plan(wb, parent)
        journal = append(wb, plan_job, observed_result=f'Local trial {depth} matched A.')
        parent = completed(wb, wb.submit_check_followup(
            followup_request(plan_job, plan, journal, provider='groq'))['id'])
        assert parent['status'] == 'completed', parent
        # Every generation has its own hypotheses, proposed check, observation
        # and child receipt; generic follow-ups would miss the branching replay.
        assert len(provider.check_calls) == depth
        if depth in {5, 8}:
            before_calls = len(provider.calls), len(provider.check_calls)
            reads.clear()
            verified.clear()
            with monkeypatch.context() as counted_read:
                counted_read.setattr(wb, '_export_bytes', counted)
                counted_read.setattr(wb, '_load_pack_verified', counted_validation)
                raw = wb.result_bytes(parent['id'])
            assert json.loads(raw)['followup']['depth'] == depth
            # Full replay remains once per distinct source. Cheap hash-bound
            # authority/cue reads still walk bounded ancestry quadratically;
            # neither kind of work may branch exponentially through sidecars.
            assert len(verified) == len(set(verified)) == depth + 1
            assert len(reads) <= 2 * (depth + 1) ** 2
            assert (len(provider.calls), len(provider.check_calls)) == before_calls
    plan_job, plan = generate_plan(wb, parent)
    journal = append(wb, plan_job)
    monkeypatch.setattr(wb, '_enqueue', lambda *a, **k: pytest.fail('Ninth CHECK generation queued'))
    with pytest.raises(ValueError):
        wb.submit_check_followup(followup_request(plan_job, plan, journal, provider='groq'))


@pytest.mark.parametrize('mutation', ['export_bytes', 'rehashed_source', 'job_authority'])
def test_replay_cache_does_not_hide_between_operation_source_changes(checking, mutation):
    wb, _, source = checking
    _, _, _, _, child = child_for(wb, source)
    saved_child = wb.result_bytes(child['id'])
    source_path = wb.root / source['result']['pack.json']['path']
    job_path = wb._job_path(source['id'])
    original_source, original_job = source_path.read_bytes(), job_path.read_bytes()
    if mutation == 'export_bytes':
        source_path.write_bytes(original_source + b' ')
    elif mutation == 'rehashed_source':
        repair_export(wb, source, lambda pack: pack['trace'].append({'stage': 'changed_after_read'}))
    else:
        row = json.loads(original_job)
        row['request'] = {'mode': 'main_dev', 'case_id': 'verified:synthetic'}
        write_json(job_path, row)
    with pytest.raises(ValueError):
        wb.result_bytes(child['id'])
    # Failed reads must also release their operation memo. Restoring bytes can
    # be independently replayed, without creating a new Workbench or API call.
    source_path.write_bytes(original_source)
    job_path.write_bytes(original_job)
    assert wb.result_bytes(child['id']) == saved_child


def test_check_plan_sidecar_cannot_reenter_active_child_with_fresh_lineage_set(checking):
    wb, _, source = checking
    plan_job, _, _, _, child = child_for(wb, source)
    saved_child = wb.result_bytes(child['id'])
    plan_job_path = wb._job_path(plan_job['id'])
    original_job = plan_job_path.read_bytes()
    row = json.loads(original_job)
    child_sha = hashlib.sha256(saved_child).hexdigest()
    row['request']['source_job_id'] = child['id']
    row['request']['source_pack_sha256'] = child_sha
    write_json(plan_job_path, row)
    with sqlite3.connect(wb.directory / 'check_plans.sqlite') as db:
        db.execute('UPDATE intents SET source_job=?, source_sha=? WHERE target_job=?',
                   (child['id'], child_sha, plan_job['id']))
    with pytest.raises(ValueError, match='cycle'):
        wb.result_bytes(child['id'])
    plan_job_path.write_bytes(original_job)
    request = json.loads(original_job)['request']
    with sqlite3.connect(wb.directory / 'check_plans.sqlite') as db:
        db.execute('UPDATE intents SET source_job=?, source_sha=? WHERE target_job=?',
                   (request['source_job_id'], request['source_pack_sha256'], plan_job['id']))
    assert wb.result_bytes(child['id']) == saved_child


def test_http_observations_and_followup_are_session_bound_and_hash_bound(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda _: SimpleNamespace(
        reasoning_effort='high', model='openai/gpt-oss-120b', max_output_tokens=512))
    with local_server(tmp_path) as server:
        wb = server.workbench
        provider = CheckProvider()
        wb.provider_factory = lambda *a, **k: provider
        source = completed(wb, wb.submit({'mode': 'example', 'provider': 'groq'})['id'])
        plan_job, plan = generate_plan(wb, source)
        headers = {'X-IssueIntel-Token': server.session_token, 'Content-Type': 'application/json',
                   'Origin': 'http://127.0.0.1:' + str(server.server_port)}
        route = '/api/jobs/' + plan_job['id'] + '/check-observations'
        assert http(server, 'GET', route + '.json')[0] == 403
        status, _, raw = http(server, 'GET', route + '.json', headers=headers)
        assert status == 200, raw
        journal = json.loads(raw)
        status, _, raw = http(server, 'POST', route, json.dumps({
            'expected_sha256': journal['sha256'], 'observation': observation()}), headers)
        assert status == 200, raw
        journal = json.loads(raw)
        for extension in ('json', 'md'):
            status, response_headers, raw = http(server, 'GET', route + '.' + extension, headers=headers)
            expected, report_sha, plan_sha, journal_sha = wb.check_observation_export(plan_job['id'], extension)
            assert status == 200 and raw == expected
            assert response_headers['X-IssueIntel-Report-SHA256'] == report_sha
            assert response_headers['X-IssueIntel-Check-Plan-SHA256'] == plan_sha
            assert response_headers['X-IssueIntel-Check-Observations-SHA256'] == journal_sha
        before = len(provider.calls), len(provider.check_calls)
        status, _, raw = http(server, 'POST', '/api/check-followups',
                             json.dumps(followup_request(plan_job, plan, journal)), headers)
        assert status == 202, raw
        child = completed(wb, json.loads(raw)['id'])
        assert child['status'] == 'completed', child
        assert (len(provider.calls), len(provider.check_calls)) == before
