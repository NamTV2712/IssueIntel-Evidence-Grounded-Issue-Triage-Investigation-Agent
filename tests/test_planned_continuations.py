"""Private synthetic Planned continuations: durable consent and shared bounds."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import hashlib
import json
import threading
from types import SimpleNamespace
import uuid
from urllib.error import HTTPError

import pytest

from issueintel.investigation.planned_continuations import PlannedContinuations, validate_request
from issueintel.investigation.planned_waiting import totals, validate_plan
from issueintel.investigation.providers import GroqProvider
from issueintel.investigation.workbench import Workbench, validate_request as validate_product_request
from issueintel.runtime.io import digest, write_json
from test_planned_pipeline import PlannedModel, planned_repository
from test_runtime import configured
from test_workbench import completed, http


@pytest.fixture
def waiting_workbench(tmp_path, monkeypatch, planned_repository, request):
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-secret')
    config = replace(configured(tmp_path), reasoning_effort='high')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda _: config)
    model = PlannedModel(focuses=['parser parse_list items'])
    calls, fail = [], [False]
    strict_rejection = getattr(request, 'param', {}).get('strict_rejection', False)
    def transport(request, **kwargs):
        body = json.loads(request.data)
        calls.append(body)
        if strict_rejection and len(calls) == 1:
            raise HTTPError(request.full_url, 400, 'Synthetic strict output rejection', {},
                            BytesIO(json.dumps({'error': {'code': 'json_validate_failed'}}).encode()))
        if fail[0] and not callable(fail[0]):
            raise RuntimeError('Synthetic private transport failure')
        schema = body.get('response_format', {}).get('json_schema', {}).get('schema', {})
        if set(schema.get('properties', {})) == {'checks'}:
            from test_check_plan_jobs import check_response
            answer = check_response(json.loads(body['messages'][1]['content']))
        else:
            answer = model(body['messages'], body['max_completion_tokens'])
        if 'hypotheses' in answer:
            for index, hypothesis in enumerate(answer['hypotheses'], 1):
                hypothesis['id'] = 'H' + str(index)
        if callable(fail[0]):
            fail[0](body, answer)
        return BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode())
    wb = Workbench(tmp_path, provider_factory=lambda cfg, **kw: GroqProvider(cfg, transport=transport, **kw))
    try:
        service = PlannedContinuations(wb)
        case, path = planned_repository
        source_request = {'mode': 'local', 'provider': 'groq', 'depth': 'planned', 'repository': str(path),
                          'revision': case['revision'], 'issue_text': case['problem_statement']}
        if getattr(request, 'param', {}).get('github', False):
            url = 'https://github.com/synthetic/repository/issues/1'
            monkeypatch.setattr('issueintel.investigation.workbench.fetch_github_issue', lambda _: {
                'url': url, 'repository': 'synthetic/repository',
                'problem_statement': case['problem_statement'], 'created_at': '2026-01-01T00:00:00Z'})
            monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository',
                                lambda *a, **k: SimpleNamespace(path=path))
            source_request = {'mode': 'github', 'provider': 'groq', 'depth': 'planned',
                              'issue_url': url, 'revision': case['revision']}
        job = completed(wb, wb.submit(source_request)['id'])
        assert job['status'] == 'completed', job
        pack = json.loads(wb.result_bytes(job['id']))
        assert pack['workflow_plan']['schema'] == 'planned_investigation_v3'
        assert pack['stop_reason'] == 'waiting_for_user', pack['workflow_plan']
        yield wb, service, job, calls, fail
    finally:
        wb.close()


def request_for(preview, *, all_answers=True):
    questions = preview['waiting']['questions']['questions']
    if not all_answers:
        questions = questions[:1]
    return {key: preview[key] for key in ('source_pack_sha256', 'workflow_plan_sha256',
            'waiting_sha256', 'policy_sha256')} | {'language': 'vi',
            'answers': [{'question_id': row['id'], 'answer': 'Chưa biết; chưa kiểm tra.'} for row in questions],
            'confirmation': 'continue_planned_with_api_cost'}


def test_preview_replays_saved_pause_without_git_config_or_provider(waiting_workbench, monkeypatch):
    wb, service, source, calls, _ = waiting_workbench
    before = len(calls)
    monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot',
                        lambda *a, **k: pytest.fail('Preview read Git'))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',
                        lambda *a, **k: pytest.fail('Preview read provider config'))
    wb.provider_factory = lambda *a, **k: pytest.fail('Preview constructed provider')
    preview = service.preview(source['id'])
    assert preview['available'] is True and preview['continuation_job'] is None
    assert preview['remaining']['retrieval_rounds'] == 2
    assert preview['remaining']['generation_passes'] == 3
    assert preview['remaining']['provider_calls'] == 8
    assert wb.result_bytes(source['id'])
    assert len(calls) == before == 1


def test_answers_create_source_bound_child_without_resetting_budget(waiting_workbench):
    wb, service, source, calls, _ = waiting_workbench
    original_raw = wb.result_bytes(source['id'])
    original_usage = wb.usage(source['id'])
    preview = service.preview(source['id'])
    child = completed(wb, service.submit(source['id'], request_for(preview))['id'])
    assert child['status'] == 'completed', child
    pack = json.loads(wb.result_bytes(child['id']))
    parent = json.loads(original_raw)
    plan = validate_plan(pack, parent=parent)
    assert pack['continued_from']['parent_pack_sha256'] == hashlib.sha256(original_raw).hexdigest()
    assert pack['continued_from']['waiting_sha256'] == preview['waiting_sha256']
    assert pack['continued_from']['answers_receipt']['language'] == 'vi'
    assert plan['inherited']['counts'] == totals(parent['workflow_plan'])
    assert plan['limits'] == parent['workflow_plan']['limits']
    assert totals(plan)['provider_calls'] == len(calls) <= 9
    assert totals(plan)['verifier_passes'] == 1
    assert pack['verification']['kind'] == 'llm_separate_pass'
    assert wb.result_bytes(source['id']) == original_raw and wb.usage(source['id']) == original_usage
    assert pack['leakage_audit']['strict_issue_time_verified'] is False


def test_same_intent_replays_after_restart_without_git_and_different_answers_conflict(waiting_workbench, monkeypatch):
    wb, service, source, calls, _ = waiting_workbench
    request = request_for(service.preview(source['id']))
    child = completed(wb, service.submit(source['id'], request)['id'])
    before = len(calls)
    again = PlannedContinuations(wb)
    monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot',
                        lambda *a, **k: pytest.fail('Duplicate submit read Git'))
    assert again.submit(source['id'], request)['id'] == child['id']
    changed = deepcopy(request)
    changed['answers'][0]['answer'] = 'A different answer must not fork the same pause.'
    with pytest.raises(ValueError, match='different Planned continuation'):
        again.submit(source['id'], changed)
    assert again.preview(source['id'])['continuation_job']['id'] == child['id']
    assert len(calls) == before


def test_fresh_workbench_reopens_child_without_git_config_or_model(waiting_workbench, monkeypatch):
    wb, service, source, calls, _ = waiting_workbench
    request = request_for(service.preview(source['id']))
    child = completed(wb, service.submit(source['id'], request)['id'])
    raw = wb.result_bytes(child['id'])
    before = len(calls)
    monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot',
                        lambda *a, **k: pytest.fail('Historical child read Git'))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',
                        lambda *a, **k: pytest.fail('Historical child read provider config'))
    other = Workbench(wb.root, provider_factory=lambda *a, **k: pytest.fail('Historical child called model'))
    try:
        assert other.result_bytes(child['id']) == raw
        assert PlannedContinuations(other).submit(source['id'], request)['id'] == child['id']
        assert PlannedContinuations(other).preview(source['id'])['continuation_job']['id'] == child['id']
    finally:
        other.close()
    assert len(calls) == before


def test_existing_intent_remains_readable_after_policy_change(waiting_workbench, monkeypatch):
    wb, service, source, calls, _ = waiting_workbench
    request = request_for(service.preview(source['id']))
    child = completed(wb, service.submit(source['id'], request)['id'])
    before = len(calls)
    monkeypatch.setattr('issueintel.investigation.planned_waiting.policy_sha256', lambda: '0' * 64)
    preview = service.preview(source['id'])
    assert preview['available'] is False and preview['reason'] == 'planned_policy_changed'
    assert preview['continuation_job']['id'] == child['id']
    assert service.submit(source['id'], request)['id'] == child['id']
    assert wb.result_bytes(child['id']) and len(calls) == before


def test_concurrent_same_request_has_one_durable_child(waiting_workbench):
    wb, service, source, _, _ = waiting_workbench
    request = request_for(service.preview(source['id']))
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda _: service.submit(source['id'], request), range(3)))
    assert len({row['id'] for row in rows}) == 1
    assert completed(wb, rows[0]['id'])['status'] == 'completed'
    db = service._db()
    try:
        assert db.execute('SELECT COUNT(*) FROM intents WHERE source_job=?', (source['id'],)).fetchone() == (1,)
    finally:
        db.close()


def test_transport_failure_is_terminal_for_this_pause(waiting_workbench):
    wb, service, source, calls, fail = waiting_workbench
    request = request_for(service.preview(source['id']))
    fail[0] = True
    child = completed(wb, service.submit(source['id'], request)['id'])
    assert child['status'] == 'failed'
    before = len(calls)
    assert service.submit(source['id'], request)['id'] == child['id']
    assert len(calls) == before == 2
    assert wb.usage(child['id'])['retained_bounds'] == 1
    assert wb.result_bytes(source['id'])


def test_changed_policy_blocks_new_calls_but_preserves_old_read(waiting_workbench, monkeypatch):
    wb, service, source, calls, _ = waiting_workbench
    preview = service.preview(source['id'])
    request = request_for(preview)
    old = wb.result_bytes(source['id'])
    monkeypatch.setattr('issueintel.investigation.planned_waiting.policy_sha256', lambda: '0' * 64)
    blocked = service.preview(source['id'])
    assert blocked['available'] is False and blocked['reason'] == 'planned_policy_changed'
    with pytest.raises(ValueError, match='policy changed'):
        service.submit(source['id'], request)
    assert wb.result_bytes(source['id']) == old and len(calls) == 1


def test_git_bytes_are_replayed_before_enqueue(waiting_workbench, monkeypatch):
    wb, service, source, calls, _ = waiting_workbench
    request = request_for(service.preview(source['id']))
    monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot',
                        lambda *a, **k: (_ for _ in ()).throw(ValueError('Synthetic missing Git proof')))
    with pytest.raises(ValueError, match='missing Git proof'):
        service.submit(source['id'], request)
    assert service._intent(source['id']) is None and len(calls) == 1


@pytest.mark.parametrize('field', ['source_pack_sha256', 'workflow_plan_sha256', 'waiting_sha256', 'policy_sha256'])
def test_changed_binding_never_queues_or_calls(waiting_workbench, field):
    wb, service, source, calls, _ = waiting_workbench
    request = request_for(service.preview(source['id']))
    request[field] = '0' * 64
    with pytest.raises(ValueError):
        service.submit(source['id'], request)
    assert service._intent(source['id']) is None and len(calls) == 1


@pytest.mark.parametrize('mutation', [
    lambda row: row.update(usage_tracking=False),
    lambda row: row.update(usage_receipts={}),
    lambda row: next(iter(row['usage_receipts'].values())).update(status='failed_bound'),
    lambda row: next(iter(row['usage_receipts'].values())).update(stage='verification'),
    lambda row: next(iter(row['usage_receipts'].values())).update(model='unauthorized-model'),
    lambda row: row['request'].update(issue_text='A different original issue'),
    lambda row: row.update(mode='main_dev'),
])
def test_unknown_or_mismatched_parent_authority_and_receipts_fail_closed(waiting_workbench, mutation):
    wb, service, source, calls, _ = waiting_workbench
    request = request_for(service.preview(source['id']))
    row = json.loads(wb._job_path(source['id']).read_bytes())
    mutation(row)
    write_json(wb._job_path(source['id']), row)
    assert service.preview(source['id'])['available'] is False
    with pytest.raises(ValueError):
        service.submit(source['id'], request)
    assert len(calls) == 1


@pytest.mark.parametrize('mutation', [
    lambda request: request.update(confirmation='yes'),
    lambda request: request.update(provider='none'),
    lambda request: request.update(language='fr'),
    lambda request: request.update(answers=[]),
    lambda request: request.update(answers=[{'question_id': 'missing:gold', 'answer': 'hidden'}]),
])
def test_public_request_requires_exact_consent_and_offered_bounded_answers(waiting_workbench, mutation):
    wb, service, source, calls, _ = waiting_workbench
    request = request_for(service.preview(source['id']))
    mutation(request)
    with pytest.raises(ValueError):
        service.submit(source['id'], request)
    assert len(calls) == 1


def test_partial_answers_can_pause_again_with_one_shared_budget(waiting_workbench):
    wb, service, source, calls, _ = waiting_workbench
    child = completed(wb, service.submit(source['id'],
        request_for(service.preview(source['id']), all_answers=False))['id'])
    assert child['status'] == 'completed', child
    child_pack = json.loads(wb.result_bytes(child['id']))
    assert child_pack['stop_reason'] == 'waiting_for_user'
    answered = set(child_pack['continued_from']['answered_fields'])
    preview = service.preview(child['id'])
    assert preview['available'] is True
    assert all(row['field'] not in answered for row in preview['waiting']['questions']['questions'])
    grandchild = completed(wb, service.submit(child['id'], request_for(preview))['id'])
    assert grandchild['status'] == 'completed', grandchild
    pack = json.loads(wb.result_bytes(grandchild['id']))
    assert totals(pack['workflow_plan'])['provider_calls'] == len(calls) <= 9
    assert totals(pack['workflow_plan'])['retrieval_rounds'] <= 3
    assert totals(pack['workflow_plan'])['verifier_passes'] == 1


@pytest.mark.parametrize('waiting_workbench', [{'strict_rejection': True}], indirect=True)
def test_completed_schema_fallback_keeps_failed_bound_in_shared_call_count(waiting_workbench):
    wb, service, source, calls, _ = waiting_workbench
    parent = json.loads(wb.result_bytes(source['id']))
    assert parent['workflow_plan']['provider_calls'] == len(calls) == 2
    assert parent['annotation']['provenance']['repair_count'] == 1
    assert wb.usage(source['id'])['retained_bounds'] == 1
    preview = service.preview(source['id'])
    assert preview['available'] is True and preview['remaining']['provider_calls'] == 7
    child = completed(wb, service.submit(source['id'], request_for(preview))['id'])
    assert child['status'] == 'completed', child
    pack = json.loads(wb.result_bytes(child['id']))
    assert totals(pack['workflow_plan'])['provider_calls'] == len(calls) <= 9
    assert wb.usage(source['id'])['retained_bounds'] == 1


def test_child_link_cannot_be_rehashed_to_a_different_source(waiting_workbench):
    wb, service, source, _, _ = waiting_workbench
    child = completed(wb, service.submit(source['id'], request_for(service.preview(source['id'])))['id'])
    assert child['status'] == 'completed', child
    row = json.loads(wb._job_path(child['id']).read_bytes())
    pack = json.loads(wb.result_bytes(child['id']))
    pack['continued_from']['answers_receipt']['answers'][0]['answer'] = 'Forged later answer'
    link = pack['continued_from']
    link['sha256'] = digest({key: value for key, value in link.items() if key != 'sha256'})
    path = wb.root / row['result']['pack.json']['path']
    write_json(path, pack)
    row['result']['pack.json']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(wb._job_path(child['id']), row)
    with pytest.raises(ValueError):
        wb.result_bytes(child['id'])


def test_reassigned_sqlite_intent_does_not_authenticate_foreign_child(waiting_workbench):
    wb, service, source, _, _ = waiting_workbench
    request = request_for(service.preview(source['id']))
    child = completed(wb, service.submit(source['id'], request)['id'])
    row = json.loads(wb._job_path(child['id']).read_bytes())
    row['request']['source_job_id'] = 'f' * 24
    write_json(wb._job_path(child['id']), row)
    db = service._db()
    try:
        db.execute('UPDATE intents SET request_sha=? WHERE source_job=?',
                   (digest(row['request']), source['id']))
        db.commit()
    finally:
        db.close()
    assert service.preview(source['id'])['available'] is False
    with pytest.raises(ValueError):
        service.submit(source['id'], request)


def test_offline_and_nonplanned_products_have_no_paid_continuation(tmp_path):
    wb = Workbench(tmp_path, provider_factory=lambda *a, **k: pytest.fail('Provider constructed'))
    service = PlannedContinuations(wb)
    try:
        for depth in ('quick', 'planned'):
            job = completed(wb, wb.submit({'mode': 'example', 'depth': depth})['id'])
            assert job['status'] == 'completed', job
            assert service.preview(job['id'])['available'] is False
        assert service.preview('a' * 24)['available'] is False
    finally:
        wb.close()


def test_http_pause_read_and_explicit_child_require_local_session_and_origin(waiting_workbench):
    from issueintel.interfaces.web import make_server
    wb, service, source, calls, _ = waiting_workbench
    server = make_server(wb.root, port=0, workbench=wb)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        route = '/api/jobs/' + source['id'] + '/planned-continuation'
        token = {'X-IssueIntel-Token': server.session_token, 'Content-Type': 'application/json'}
        assert http(server, 'GET', route)[0] == 403
        assert http(server, 'POST', route, '{}')[0] == 403
        assert http(server, 'GET', route, headers={**token, 'Origin': 'https://attacker.invalid'})[0] == 403
        assert http(server, 'POST', route, '{}', {**token, 'Origin': 'https://attacker.invalid'})[0] == 403
        status, headers, raw = http(server, 'GET', route, headers=token)
        preview = json.loads(raw)
        assert status == 200 and headers['Cache-Control'] == 'no-store'
        assert preview['available'] is True and preview['continuation_job'] is None
        assert len(calls) == 1 and service._intent(source['id']) is None
        for path, marker in [('/planned-waiting.js', b'IssueIntelPlannedWaiting'),
                             ('/planned-waiting.css', b'.planned-waiting')]:
            status, _, raw = http(server, 'GET', path)
            assert status == 200 and marker in raw
        request = request_for(preview)
        denied = dict(request, confirmation='yes')
        assert http(server, 'POST', route, json.dumps(denied), token)[0] == 400
        assert len(calls) == 1 and service._intent(source['id']) is None
        status, _, raw = http(server, 'POST', route, json.dumps(request), token)
        assert status == 200
        child = completed(wb, json.loads(raw)['id'])
        assert child['status'] == 'completed', child
        status, _, raw = http(server, 'GET', '/api/jobs/' + child['id'] + '/pack.json', headers=token)
        assert status == 200
        assert json.loads(raw)['continued_from']['parent_job_id'] == source['id']
        before = len(calls)
        status, _, raw = http(server, 'POST', route, json.dumps(request), token)
        assert status == 200 and json.loads(raw)['id'] == child['id']
        assert len(calls) == before
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize('mutation', ['source_authority', 'policy'])
@pytest.mark.parametrize('require_repair', [False, True])
def test_source_or_policy_change_during_call_blocks_next_call_and_publish(
        waiting_workbench, monkeypatch, mutation, require_repair):
    wb, service, source, calls, control = waiting_workbench
    request = request_for(service.preview(source['id']))
    original_source = wb._job_path(source['id']).read_bytes()
    def change_during_child(body, answer):
        if len(calls) != 2:
            return
        if mutation == 'policy':
            monkeypatch.setattr('issueintel.investigation.planned_waiting.policy_sha256', lambda: '0' * 64)
        else:
            row = json.loads(original_source)
            row['request']['issue_text'] = 'A different query, changed while the child HTTP call was in flight.'
            write_json(wb._job_path(source['id']), row)
        if require_repair:
            answer['hypotheses'][0]['support_ids'] = ['fabricated-source-never-in-the-request']
    control[0] = change_during_child
    child = completed(wb, service.submit(source['id'], request)['id'])
    assert child['status'] == 'failed', child
    assert len(calls) == 2  # Seed ancestor + exactly one charged child response.
    usage = wb.usage(child['id'])
    assert len(usage['receipts']) == 1 and usage['receipts'][0]['status'] == 'settled'
    assert usage['receipts'][0]['stage'] == 'seed_generation'
    with pytest.raises(ValueError, match='not complete'):
        wb.result_bytes(child['id'])
    # Restoring a local source cannot revive the consumed intent or trigger a
    # repair/verification call. A separately requested investigation is needed.
    wb._job_path(source['id']).write_bytes(original_source)
    assert service.submit(source['id'], request)['id'] == child['id']
    assert len(calls) == 2
    assert wb.result_bytes(source['id'])


@pytest.mark.parametrize('mutation', ['source_authority', 'policy'])
def test_change_during_final_verifier_response_blocks_publication(waiting_workbench, monkeypatch, mutation):
    wb, service, source, calls, control = waiting_workbench
    request = request_for(service.preview(source['id']))
    original_source = wb._job_path(source['id']).read_bytes()
    changed = []
    def change_after_verification(body, answer):
        if 'reviews' not in answer:
            return
        changed.append(len(calls))
        if mutation == 'policy':
            monkeypatch.setattr('issueintel.investigation.planned_waiting.policy_sha256', lambda: '0' * 64)
        else:
            row = json.loads(original_source)
            row['request']['revision'] = 'f' * 40
            write_json(wb._job_path(source['id']), row)
    control[0] = change_after_verification
    child = completed(wb, service.submit(source['id'], request)['id'])
    assert changed and child['status'] == 'failed', child
    assert len(calls) == changed[-1]
    usage = wb.usage(child['id'])
    assert usage['receipts'][-1]['stage'] == 'verification'
    assert usage['receipts'][-1]['status'] == 'settled'
    assert 'planned_complete' in child['stage_checkpoints']
    with pytest.raises(ValueError, match='not complete'):
        wb.result_bytes(child['id'])
    wb._job_path(source['id']).write_bytes(original_source)
    assert service.submit(source['id'], request)['id'] == child['id']
    assert len(calls) == changed[-1]


def test_grandchild_admission_replays_ancestor_receipt_ownership(waiting_workbench):
    wb, service, source, calls, _ = waiting_workbench
    child = completed(wb, service.submit(source['id'],
        request_for(service.preview(source['id']), all_answers=False))['id'])
    assert child['status'] == 'completed', child
    preview = service.preview(child['id'])
    assert preview['available'] is True
    request = request_for(preview)
    before = len(calls)
    ancestor = json.loads(wb._job_path(source['id']).read_bytes())
    next(iter(ancestor['usage_receipts'].values()))['reservation_id'] = 'foreign-reservation'
    write_json(wb._job_path(source['id']), ancestor)
    assert service.preview(child['id'])['available'] is False
    with pytest.raises(ValueError):
        service.submit(child['id'], request)
    assert len(calls) == before and service._intent(child['id']) is None


def generic_child_request(wb, source, mode):
    """Valid public requests cannot turn a waiting workflow into a new budget."""
    raw = wb.result_bytes(source['id'])
    pack = json.loads(raw)
    if mode == 'followup':
        return {'mode': mode, 'parent_job_id': source['id'], 'provider': 'groq',
                'additional_context': 'Unknown; no independent reproduction yet.'}
    if mode == 'clarification':
        questions = wb._questions_for_pack(pack)
        return {'mode': mode, 'parent_job_id': source['id'], 'provider': 'groq',
                'parent_pack_sha256': hashlib.sha256(raw).hexdigest(),
                'questions_sha256': questions['sha256'], 'language': 'en',
                'answers': [{'question_id': questions['questions'][0]['id'], 'answer': 'Unknown'}]}
    if mode == 'context':
        return {'mode': mode, 'parent_job_id': source['id'], 'provider': 'groq',
                'parent_pack_sha256': hashlib.sha256(raw).hexdigest(), 'selected_sources': [{
                    'key': 'pull:2', 'record_sha256': 'a' * 64, 'receipt_sha256': 'b' * 64}]}
    from test_check_plan_jobs import generate
    from test_check_observation_workbench import append, followup_request
    plan_job, plan = generate(wb, source)
    journal = append(wb, plan_job)
    request = followup_request(plan_job, plan, journal, provider='groq')
    # Creating/reading a separately consented plan and manual observation stays
    # available; it does not authorize a fresh investigation budget.
    assert wb.check_observation_export(plan_job['id'])[3] == journal['sha256']
    return request


@pytest.mark.parametrize('waiting_workbench', [{'github': True}], indirect=True)
@pytest.mark.parametrize('mode', ['followup', 'clarification', 'context', 'check_followup'])
def test_waiting_source_rejects_generic_child_before_queue_or_new_intent(
        waiting_workbench, monkeypatch, mode):
    wb, _, source, calls, _ = waiting_workbench
    request = generic_child_request(wb, source, mode)
    before = len(calls), len(wb.history())
    old = wb.result_bytes(source['id'])
    monkeypatch.setattr(wb, '_enqueue', lambda *a, **k: pytest.fail('Waiting fork queued'))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',
                        lambda *a, **k: pytest.fail('Waiting fork loaded API configuration'))
    wb.provider_factory = lambda *a, **k: pytest.fail('Waiting fork constructed a provider')
    submit = wb.submit_check_followup if mode == 'check_followup' else wb.submit
    with pytest.raises(ValueError, match='Planned continuation'):
        submit(request)
    assert (len(calls), len(wb.history())) == before
    assert wb.result_bytes(source['id']) == old
    intake = wb.intake(source['id'])
    assert intake['clarifications_available'] is False and intake['check_journal_available'] is True
    assert wb.check_journal(source['id'])['revision'] == 0
    if mode == 'check_followup':
        db = wb.check_followups._db()
        try:
            assert db.execute('SELECT COUNT(*) FROM intents').fetchone() == (0,)
        finally:
            db.close()


@pytest.mark.parametrize('waiting_workbench', [{'github': True}], indirect=True)
@pytest.mark.parametrize('mode', ['followup', 'clarification', 'context', 'check_followup'])
def test_worker_rejects_crafted_generic_waiting_fork_before_provider(
        waiting_workbench, monkeypatch, mode):
    wb, _, source, calls, _ = waiting_workbench
    request = generic_child_request(wb, source, mode)
    target = uuid.uuid4().hex[:24]
    if mode == 'check_followup':
        plan = wb.check_plan_bundle(request['plan_job_id'])
        journal = wb.check_observations(request['plan_job_id'])
        request = dict(request, mode=mode, parent_job_id=source['id'],
                       parent_pack_sha256=plan['source']['source_pack_sha256'],
                       journal_revision=journal['revision'])
        db = wb.check_followups._db()
        try:
            db.execute('INSERT INTO intents VALUES (?, ?)', (wb.check_followups.request_sha(request), target))
            db.commit()
        finally:
            db.close()
    else:
        request = validate_product_request(request)
    before = len(calls)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',
                        lambda *a, **k: pytest.fail('Crafted waiting fork loaded API configuration'))
    wb.provider_factory = lambda *a, **k: pytest.fail('Crafted waiting fork constructed a provider')
    child = completed(wb, wb._enqueue(request, wb._run, identifier=target)['id'])
    assert child['status'] == 'failed', child
    assert child['error'].startswith('Investigation failed (ValueError)')
    assert not child.get('stage_checkpoints') and len(calls) == before
    assert wb.result_bytes(source['id'])


def test_terminal_continuation_allows_generic_followup_and_related_excludes_mixed_lineage(waiting_workbench):
    wb, service, source, calls, _ = waiting_workbench
    child = completed(wb, service.submit(source['id'], request_for(service.preview(source['id'])))['id'])
    assert child['status'] == 'completed', child
    child_pack = json.loads(wb.result_bytes(child['id']))
    assert child_pack['workflow_plan']['waiting'] is None
    before = len(calls)
    mixed = completed(wb, wb.submit({'mode': 'followup', 'parent_job_id': child['id'],
                                    'additional_context': 'Unknown runtime environment.'})['id'])
    assert mixed['status'] == 'completed', mixed
    other = completed(wb, wb.submit({'mode': 'local', 'repository': child_pack['snapshot']['repository'],
        'revision': child_pack['snapshot']['commit_sha'], 'issue_text': 'parser parse_list fails on empty input'})['id'])
    assert other['status'] == 'completed', other
    assert len(calls) == before
    for selected in (source, child, mixed):
        related = wb.related({'job_id': selected['id']})
        assert related['distinct_other_issues'] == 1 and related['skipped_integrity'] == 0
        assert [row['job_id'] for row in related['results']] == [other['id']]
    # Selecting the other issue returns the canonical root, never its answer or
    # generic-followup derivative, even when the newest saved job is a child.
    related = wb.related({'job_id': other['id']})
    assert related['distinct_other_issues'] == 1
    assert [row['job_id'] for row in related['results']] == [source['id']]
