"""Synthetic coverage for explicit, source-bound product check-plan jobs."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
import hashlib
import json
import sqlite3
import threading

import pytest

from issueintel.investigation.providers import GroqProvider
from issueintel.investigation.workbench import Workbench
from issueintel.runtime.io import write_json
from test_clarification_workbench import repair_export
from test_runtime import configured
from test_workbench import SyntheticProvider, completed, http, local_server


def check_response(payload):
    code = next(row['id'] for row in payload['evidence'] if row['kind'] == 'code')
    return {'checks': [{
        'id': 'check_1',
        'procedure': 'Call parse_list with None and then an empty list in a disposable local reproduction.',
        'assumptions': 'Keep the same Python version, caller and repository commit for both inputs.',
        'outcome_a': 'None raises TypeError while an empty list returns an empty list.',
        'outcome_b': 'Both inputs return an empty list.',
        'effort': 'low', 'evidence_ids': [code],
        'predictions': [
            {'hypothesis_id': row['id'], 'outcome': ('A', 'B', 'unknown')[index % 3],
             'reason': 'Synthetic expected behavior; not an observation or causal confirmation.'}
            for index, row in enumerate(payload['hypotheses'])
        ],
    }]}


class CheckProvider(SyntheticProvider):
    def __init__(self):
        super().__init__()
        self.check_calls = []
        self.check_transform = lambda response: response

    def complete_structured(self, messages, *, max_output_tokens, schema):
        if set(schema.get('properties', {})) != {'checks'}:
            return super().complete_structured(messages, max_output_tokens=max_output_tokens, schema=schema)
        payload = json.loads(messages[1]['content'])
        self.check_calls.append(payload)
        return self.check_transform(check_response(payload))


@pytest.fixture
def checking(tmp_path, monkeypatch):
    config = SimpleNamespace(reasoning_effort='high', model='openai/gpt-oss-120b', max_output_tokens=512)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda _: config)
    provider = CheckProvider()
    wb = Workbench(tmp_path, provider_factory=lambda *a, **k: provider)
    try:
        source = completed(wb, wb.submit({'mode': 'example', 'provider': 'groq'})['id'])
        assert source['status'] == 'completed', source
        yield wb, provider, source
    finally:
        wb.close()


def plan_request(wb, source, **overrides):
    return dict({
        'source_job_id': source['id'],
        'source_pack_sha256': hashlib.sha256(wb.result_bytes(source['id'])).hexdigest(),
        'confirmation': 'generate_check_plan_with_api_cost',
    }, **overrides)


def generate(wb, source):
    job = completed(wb, wb.submit_check_plan(plan_request(wb, source))['id'])
    assert job['status'] == 'completed', job
    return job, wb.check_plan_bundle(job['id'])


def test_explicit_check_plan_preserves_saved_report_pack_and_existing_journal(checking):
    wb, provider, source = checking
    identifier = source['id']
    pack, report = wb.result_bytes(identifier), wb.report_bytes(identifier)
    legacy = wb.result_bytes(identifier, 'report.md')
    journal = wb.check_journal(identifier)
    journal = wb.check_journal(identifier, {
        'expected_sha256': journal['sha256'],
        'observation': {'check_id': journal['checks'][0]['id'], 'outcome': 'not_run',
            'conditions': '', 'expected_result': '', 'observed_result': '',
            'interpretation': 'Waiting for a local reproduction environment.',
            'tested_commit_sha': None, 'supersedes': None},
    })
    journal_export = wb.check_journal_export(identifier)
    before = len(provider.calls)
    preview = wb.check_plan_history(identifier)
    assert preview['available'] and preview['plan_jobs'] == []
    assert provider.check_calls == []
    job, bundle = generate(wb, source)
    assert job['mode'] == 'check_plan' and job['provider'] == 'groq' and job['depth'] == 'checks'
    assert len(provider.check_calls) == 1 and len(provider.calls) == before
    assert bundle['source']['source_job_id'] == identifier
    assert bundle['source']['source_pack_sha256'] == hashlib.sha256(pack).hexdigest()
    assert {row['hypothesis_id'] for row in bundle['checks'][0]['predictions']} == {
        row['id'] for row in json.loads(pack)['annotation']['hypotheses']}
    assert wb.result_bytes(identifier) == pack
    assert wb.result_bytes(identifier, 'report.md') == legacy
    assert wb.report_bytes(identifier) == report
    assert wb.check_journal(identifier) == journal
    assert wb.check_journal_export(identifier) == journal_export
    assert [row['id'] for row in wb.check_plan_history(identifier)['plan_jobs']] == [job['id']]
    assert job['id'] not in {row['id'] for row in wb.history()}
    with pytest.raises(ValueError):
        wb.result_bytes(job['id'])
    with pytest.raises(ValueError):
        wb.preview_comment(job['id'])
    assert wb.resume_preview(job['id'])['available'] is False


def test_empty_check_plan_is_saved_without_manufacturing_a_discriminating_check(checking):
    wb, provider, source = checking
    provider.check_transform = lambda response: {'checks': []}
    job, bundle = generate(wb, source)
    assert bundle['checks'] == [] and len(provider.check_calls) == 1
    raw, report_sha, plan_sha = wb.check_plan_export(job['id'])
    assert json.loads(raw) == bundle
    assert report_sha == wb.investigation_report(source['id'])['sha256']
    assert plan_sha == bundle['sha256']


def test_saved_exports_and_intent_survive_restart_without_git_config_or_provider(checking, monkeypatch):
    wb, provider, source = checking
    job, bundle = generate(wb, source)
    json_export = wb.check_plan_export(job['id'])
    markdown_export = wb.check_plan_export(job['id'], 'md')
    assert bundle['sha256'] in markdown_export[0].decode()
    request = plan_request(wb, source)
    other = Workbench(wb.root, provider_factory=lambda *a, **k: pytest.fail('Saved read called provider'))
    try:
        monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda *a: pytest.fail('Saved read loaded config'))
        monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot', lambda *a: pytest.fail('Saved read reacquired Git'))
        assert other.check_plan_bundle(job['id']) == bundle
        assert other.check_plan_export(job['id']) == json_export
        assert other.check_plan_export(job['id'], 'md') == markdown_export
        assert other.submit_check_plan(request)['id'] == job['id']
        assert len(provider.check_calls) == 1
    finally:
        other.close()


def test_concurrent_duplicate_requests_share_a_single_durable_attempt(checking):
    wb, provider, source = checking
    request = plan_request(wb, source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: wb.submit_check_plan(request), range(2)))
    assert jobs[0]['id'] == jobs[1]['id']
    assert completed(wb, jobs[0]['id'])['status'] == 'completed'
    assert len(provider.check_calls) == 1


def test_interrupted_attempt_is_not_replaced_after_restart(checking):
    wb, provider, source = checking
    job, _ = generate(wb, source)
    request = plan_request(wb, source)
    row = json.loads(wb._job_path(job['id']).read_bytes())
    # Simulate a process stopping after persisted progress but before completion.
    row.update(status='running', stage='generate_check_plan')
    row.pop('check_plan_result')
    write_json(wb._job_path(job['id']), row)
    other = Workbench(wb.root, provider_factory=lambda *a, **k: pytest.fail('Restart retried a paid call'))
    try:
        resumed = other.submit_check_plan(request)
        assert resumed['id'] == job['id'] and resumed['status'] == 'interrupted'
        assert other.check_plan_history(source['id'])['plan_jobs'][0]['status'] == 'interrupted'
        with pytest.raises(ValueError):
            other.check_plan_export(job['id'])
        assert len(provider.check_calls) == 1
    finally:
        other.close()


@pytest.mark.parametrize('change', [
    {'confirmation': 'yes'}, {'confirmation': None}, {'source_job_id': '../.env'},
    {'source_pack_sha256': '0' * 64}, {'source_pack_sha256': 'invalid'},
    {'provider': 'groq'}, {'revision': 'HEAD'}, {'patch': 'restricted'},
])
def test_invalid_requests_do_not_queue_or_construct_a_provider(checking, monkeypatch, change):
    wb, provider, source = checking
    monkeypatch.setattr(wb, '_enqueue', lambda *a, **k: pytest.fail('Invalid request queued'))
    with pytest.raises(ValueError):
        wb.submit_check_plan(plan_request(wb, source, **change))
    assert provider.check_calls == []


def test_offline_source_does_not_generate_hypotheses_or_load_config(checking, monkeypatch):
    wb, provider, _ = checking
    source = completed(wb, wb.submit({'mode': 'example'})['id'])
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda *a: pytest.fail('No hypotheses'))
    assert wb.check_plan_history(source['id'])['available'] is False
    with pytest.raises(ValueError):
        wb.submit_check_plan(plan_request(wb, source))
    assert provider.check_calls == []


@pytest.mark.parametrize('descendant', [False, True])
def test_benchmark_authority_cannot_escape_through_a_product_looking_followup(checking, descendant):
    wb, provider, source = checking
    chosen = source
    if descendant:
        chosen = completed(wb, wb.submit({'mode': 'followup', 'provider': 'groq',
            'parent_job_id': source['id'], 'additional_context': 'unknown'})['id'])
        assert chosen['status'] == 'completed'
    request = plan_request(wb, chosen)
    row = json.loads(wb._job_path(source['id']).read_bytes())
    row['request'] = {'mode': 'main_dev', 'case_id': 'verified:synthetic'}
    write_json(wb._job_path(source['id']), row)
    assert wb.check_plan_history(chosen['id'])['available'] is False
    with pytest.raises(ValueError):
        wb.submit_check_plan(request)
    assert provider.check_calls == []


@pytest.mark.parametrize('mutation', ['excerpt', 'snapshot', 'missing_git'])
def test_git_snapshot_and_evidence_replay_before_provider_construction(checking, monkeypatch, mutation):
    wb, provider, source = checking
    if mutation == 'excerpt':
        def change(pack):
            record = next(row for row in pack['evidence'] if row['kind'] == 'code')
            record['text'] = 'forged source'
            record['sha256'] = hashlib.sha256(record['text'].encode()).hexdigest()
        repair_export(wb, source, change)
    elif mutation == 'snapshot':
        repair_export(wb, source, lambda pack: pack['snapshot']['files'][0].update(raw_sha256='0' * 64))
    else:
        git_dir = wb.root / 'artifacts/demo/example_repo/.git'
        git_dir.rename(git_dir.with_name('.git-unavailable'))
    request = plan_request(wb, source)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda *a: pytest.fail('Paid before proof replay'))
    try:
        job = completed(wb, wb.submit_check_plan(request)['id'])
    except ValueError:
        pass
    else:
        assert job['status'] == 'failed'
    assert provider.check_calls == []


def deferred(wb, monkeypatch):
    captured, ready = [], threading.Event()
    original = wb.check_plans._run
    def defer(row):
        captured.append(row)
        ready.set()
    monkeypatch.setattr(wb.check_plans, '_run', defer)
    return captured, ready, original


def test_source_changed_while_queued_stops_before_provider(checking, monkeypatch):
    wb, provider, source = checking
    request = plan_request(wb, source)
    captured, ready, run = deferred(wb, monkeypatch)
    job = wb.submit_check_plan(request)
    assert ready.wait(5)
    repair_export(wb, source, lambda pack: pack.update(confidence={'level': 'changed', 'probability': None}))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda *a: pytest.fail('Stale paid call'))
    run(captured[0])
    assert wb.job(job['id'])['status'] == 'failed'
    assert provider.check_calls == []


@pytest.mark.parametrize('change', [
    {'confirmation': 'yes'}, {'mode': 'adaptive_questions'}, {'provider': 'another-provider'}, {'depth': 'unbounded'},
])
def test_changed_saved_request_authority_is_denied_before_provider(checking, monkeypatch, change):
    wb, provider, source = checking
    captured, ready, run = deferred(wb, monkeypatch)
    job = wb.submit_check_plan(plan_request(wb, source))
    assert ready.wait(5)
    row = json.loads(wb._job_path(job['id']).read_bytes())
    row['request'].update(change)
    write_json(wb._job_path(job['id']), row)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda *a: pytest.fail('Changed authority spent'))
    run(captured[0])
    assert wb.job(job['id'])['status'] == 'failed'
    assert provider.check_calls == []


@pytest.mark.parametrize('mutation', ['delete', 'swap'])
def test_missing_or_swapped_generation_intent_denies_a_queued_call(checking, monkeypatch, mutation):
    wb, provider, source = checking
    captured, ready, run = deferred(wb, monkeypatch)
    job = wb.submit_check_plan(plan_request(wb, source))
    assert ready.wait(5)
    with sqlite3.connect(wb.directory / 'check_plans.sqlite') as db:
        if mutation == 'delete':
            db.execute('DELETE FROM intents')
        else:
            db.execute('UPDATE intents SET target_job=?', ('0' * 24,))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda *a: pytest.fail('Missing intent spent'))
    run(captured[0])
    assert wb.job(job['id'])['status'] == 'failed'
    assert provider.check_calls == []


def test_source_changed_during_call_does_not_publish_a_completed_plan(checking):
    wb, provider, source = checking
    started, release = threading.Event(), threading.Event()
    def block(response):
        started.set()
        assert release.wait(10)
        return response
    provider.check_transform = block
    try:
        job = wb.submit_check_plan(plan_request(wb, source))
        assert started.wait(10)
        repair_export(wb, source, lambda pack: pack.update(confidence={'level': 'changed', 'probability': None}))
        release.set()
        finished = completed(wb, job['id'])
        assert finished['status'] == 'failed'
        assert 'check_plan_result' not in finished
        with pytest.raises(ValueError):
            wb.check_plan_bundle(job['id'])
        assert len(provider.check_calls) == 1
    finally:
        release.set()


def test_cancelled_attempt_is_not_published_or_retried(checking):
    wb, provider, source = checking
    started, release = threading.Event(), threading.Event()
    def block(response):
        started.set()
        assert release.wait(10)
        return response
    provider.check_transform = block
    request = plan_request(wb, source)
    try:
        job = wb.submit_check_plan(request)
        assert started.wait(10)
        wb.cancel(job['id'])
        release.set()
        finished = completed(wb, job['id'])
        assert finished['status'] == 'cancelled' and 'check_plan_result' not in finished
        assert wb.submit_check_plan(request)['id'] == job['id']
        assert len(provider.check_calls) == 1
    finally:
        release.set()


def test_invalid_output_uses_one_bounded_repair_and_failed_intent_is_reused(checking):
    wb, provider, source = checking
    provider.check_transform = lambda response: {'checks': [{'id': 'check_1'}]}
    request = plan_request(wb, source)
    job = completed(wb, wb.submit_check_plan(request)['id'])
    assert job['status'] == 'failed' and 'no automatic retry' in job['error']
    assert wb.submit_check_plan(request)['id'] == job['id']
    assert len(provider.check_calls) == 2
    with pytest.raises(ValueError):
        wb.check_plan_bundle(job['id'])


@pytest.mark.parametrize('mutation', ['source_content', 'source_authority'])
def test_changed_source_after_invalid_first_output_blocks_the_repair_call(checking, mutation):
    wb, provider, source = checking
    request = plan_request(wb, source)
    def invalidate_and_change_source(response):
        if mutation == 'source_content':
            repair_export(wb, source, lambda pack: pack.update(confidence={'level': 'changed', 'probability': None}))
        else:
            row = json.loads(wb._job_path(source['id']).read_bytes())
            row['request'] = {'mode': 'main_dev', 'case_id': 'verified:synthetic'}
            write_json(wb._job_path(source['id']), row)
        return {'checks': [{'id': 'check_1'}]}
    provider.check_transform = invalidate_and_change_source
    job = completed(wb, wb.submit_check_plan(request)['id'])
    assert job['status'] == 'failed' and 'check_plan_result' not in job
    assert len(provider.check_calls) == 1
    assert not (wb.directory / 'check_plan_runs' / job['id'] / 'check-plan.json').exists()
    with pytest.raises(ValueError):
        wb.check_plan_bundle(job['id'])


@pytest.mark.parametrize('mutation', ['source', 'intent', 'path', 'artifact', 'request'])
def test_saved_read_rejects_changed_source_or_plan_authority(checking, mutation):
    wb, _, source = checking
    job, _ = generate(wb, source)
    if mutation == 'source':
        repair_export(wb, source, lambda pack: pack['annotation']['hypotheses'][0].update(statement='Changed cause'))
    elif mutation == 'intent':
        with sqlite3.connect(wb.directory / 'check_plans.sqlite') as db:
            db.execute('DELETE FROM intents')
    else:
        row = json.loads(wb._job_path(job['id']).read_bytes())
        if mutation == 'path':
            row['check_plan_result']['path'] = '../outside.json'
        elif mutation == 'request':
            row['request']['confirmation'] = 'yes'
        else:
            path = wb.root / row['check_plan_result']['path']
            bundle = json.loads(path.read_bytes())
            bundle['checks'][0]['predictions'][0]['hypothesis_id'] = 'invented'
            write_json(path, bundle)
            row['check_plan_result']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(wb._job_path(job['id']), row)
    with pytest.raises(ValueError):
        wb.check_plan_bundle(job['id'])


def test_unauthorized_model_is_rejected_before_provider_factory(checking, monkeypatch):
    wb, provider, source = checking
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda _: SimpleNamespace(
        model='another-provider/model', reasoning_effort='high', max_output_tokens=512))
    wb.provider_factory = lambda *a, **k: pytest.fail('Constructed unapproved model')
    job = completed(wb, wb.submit_check_plan(plan_request(wb, source))['id'])
    assert job['status'] == 'failed' and provider.check_calls == []


def test_provider_model_mismatch_is_rejected_before_a_call(checking):
    wb, provider, source = checking
    provider.model = 'another-provider/model'
    job = completed(wb, wb.submit_check_plan(plan_request(wb, source))['id'])
    assert job['status'] == 'failed' and provider.check_calls == []


def test_transport_failure_retains_owned_receipt_bound_before_http_and_does_not_retry(tmp_path, monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-secret')
    config = replace(configured(tmp_path), reasoning_effort='high')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda _: config)
    fixture = SyntheticProvider()
    paid_calls = []
    workbench = None
    def transport(request, **kwargs):
        body = json.loads(request.data)
        schema = body['response_format']['json_schema']['schema']
        if set(schema['properties']) == {'checks'}:
            paid_calls.append(body)
            jobs = [row for row in workbench.check_plan_history(source['id'])['plan_jobs'] if row['status'] == 'running']
            assert len(jobs) == 1
            usage = workbench.usage(jobs[0]['id'])
            assert usage['receipts'][0]['status'] == 'reserved'
            assert usage['receipts'][0]['stage'] == 'check_plan'
            raise RuntimeError('synthetic-secret private response')
        answer = fixture.complete_structured(body['messages'], max_output_tokens=body['max_completion_tokens'], schema=schema)
        return BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode())
    workbench = Workbench(tmp_path, provider_factory=lambda cfg, **kwargs: GroqProvider(cfg, transport=transport, **kwargs))
    try:
        source = completed(workbench, workbench.submit({'mode': 'example', 'provider': 'groq'})['id'])
        assert source['status'] == 'completed'
        request = plan_request(workbench, source)
        job = completed(workbench, workbench.submit_check_plan(request)['id'])
        assert job['status'] == 'failed'
        report = workbench.usage(job['id'])
        assert report['tracking_available'] and report['reservations'] == 1
        assert report['retained_bounds'] == 1 and report['tokens_charged_or_reserved'] > 0
        assert report['receipts'][0]['status'] == 'failed_bound'
        assert report['receipts'][0]['stage'] == 'check_plan'
        assert workbench.submit_check_plan(request)['id'] == job['id'] and len(paid_calls) == 1
        assert 'synthetic-secret' not in json.dumps(report) + job['error']
        assert 'private response' not in job['error']
    finally:
        workbench.close()


def test_http_plan_generation_and_exports_are_session_bound(tmp_path, monkeypatch):
    config = SimpleNamespace(reasoning_effort='high', model='openai/gpt-oss-120b', max_output_tokens=512)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda _: config)
    provider = CheckProvider()
    with local_server(tmp_path) as server:
        wb = server.workbench
        wb.provider_factory = lambda *a, **k: provider
        source = completed(wb, wb.submit({'mode': 'example', 'provider': 'groq'})['id'])
        headers = {'X-IssueIntel-Token': server.session_token, 'Content-Type': 'application/json',
                   'Origin': 'http://127.0.0.1:' + str(server.server_port)}
        route = '/api/jobs/' + source['id'] + '/check-plans'
        assert http(server, 'GET', route)[0] == 403
        status, _, raw = http(server, 'GET', route, headers=headers)
        assert status == 200 and json.loads(raw)['plan_jobs'] == []
        status, _, raw = http(server, 'POST', '/api/check-plans', json.dumps(plan_request(wb, source)), headers)
        assert status == 202, raw
        job = completed(wb, json.loads(raw)['id'])
        assert job['status'] == 'completed', job
        for extension in ('json', 'md'):
            export = '/api/jobs/' + job['id'] + '/check-plan.' + extension
            assert http(server, 'GET', export)[0] == 403
            status, response_headers, body = http(server, 'GET', export, headers=headers)
            expected, report_sha, plan_sha = wb.check_plan_export(job['id'], extension)
            assert status == 200 and body == expected
            assert 'attachment' in response_headers['Content-Disposition']
            assert response_headers['X-IssueIntel-Report-SHA256'] == report_sha
            assert response_headers['X-IssueIntel-Check-Plan-SHA256'] == plan_sha
        assert len(provider.check_calls) == 1
