"""Synthetic integration coverage for paid, source-bound clarification probes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
import hashlib
import json
import threading

import pytest

from issueintel.investigation.providers import GroqProvider
from issueintel.investigation.workbench import Workbench
from issueintel.runtime.io import write_json
from test_clarification_workbench import answer_request, repair_export
from test_runtime import configured
from test_workbench import SyntheticProvider, completed, http, local_server


def adaptive_response(payload):
    code = next(row['id'] for row in payload['evidence'] if row['kind'] == 'code')
    hypotheses = [row['id'] for row in payload['hypotheses'][:2]]
    return {'questions': [{
        'id': 'probe:q1',
        'prompts': {
            'en': 'Does parser.py fail with None on your Python version, or only with empty text?',
            'vi': 'parser.py lỗi với None trên phiên bản Python của bạn hay chỉ với chuỗi rỗng?',
        },
        'purpose': {
            'en': 'Distinguish the expected input guard from the reproduction path.',
            'vi': 'Phân biệt nhánh kiểm tra đầu vào mong đợi với cách tái hiện.',
        },
        'answer_use': {
            'en': 'An observation may guide another check; an unknown answer establishes neither cause.',
            'vi': 'Quan sát giúp chọn bước kiểm tra tiếp; chưa biết không xác nhận nguyên nhân nào.',
        },
        'hypothesis_ids': hypotheses,
        'evidence_ids': [code],
    }]}


class AdaptiveProvider(SyntheticProvider):
    """No transport: generation and verification retain their real schemas."""
    def __init__(self):
        super().__init__()
        self.question_calls = []
        self.question_transform = lambda response: response

    def complete_structured(self, messages, *, max_output_tokens, schema):
        if 'questions' not in schema.get('properties', {}):
            return super().complete_structured(messages, max_output_tokens=max_output_tokens, schema=schema)
        payload = json.loads(messages[1]['content'])
        self.question_calls.append(payload)
        return self.question_transform(adaptive_response(payload))


@pytest.fixture
def adaptive(tmp_path, monkeypatch):
    config = SimpleNamespace(reasoning_effort='high', model='openai/gpt-oss-120b', max_output_tokens=512)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda _: config)
    provider = AdaptiveProvider()
    wb = Workbench(tmp_path, provider_factory=lambda *args, **kwargs: provider)
    try:
        source = completed(wb, wb.submit({'mode': 'example', 'provider': 'groq'})['id'])
        assert source['status'] == 'completed', source
        yield wb, provider, source
    finally:
        wb.close()


def generation_request(wb, source, **overrides):
    return dict({
        'source_job_id': source['id'],
        'source_pack_sha256': hashlib.sha256(wb.result_bytes(source['id'])).hexdigest(),
        'confirmation': 'generate_questions_with_api_cost',
    }, **overrides)


def generate(wb, source):
    job = completed(wb, wb.submit_questions(generation_request(wb, source))['id'])
    assert job['status'] == 'completed', job
    return job, json.loads(wb.question_bytes(job['id']))


def adaptive_answers(wb, source, questions, bundle, **overrides):
    return dict({
        'mode': 'clarification',
        'parent_job_id': source['id'],
        'parent_pack_sha256': bundle['source']['source_pack_sha256'],
        'questions_sha256': bundle['sha256'],
        'question_job_id': questions['id'],
        'language': 'vi',
        'answers': [{'question_id': 'probe:q1', 'answer': '  chưa biết\n'}],
    }, **overrides)


def test_explicit_question_generation_has_one_call_and_preserves_original(adaptive):
    wb, provider, source = adaptive
    original = wb.result_bytes(source['id'])
    before = len(provider.calls)
    preview = wb.question_history(source['id'])
    assert preview['available'] and preview['question_jobs'] == []
    assert provider.question_calls == []
    job, bundle = generate(wb, source)
    assert job['mode'] == 'adaptive_questions' and job['provider'] == 'groq'
    assert job['depth'] == 'questions'
    assert len(provider.question_calls) == 1 and len(provider.calls) == before
    assert bundle['schema'] == 'adaptive_clarifications_v1'
    assert bundle['source']['source_job_id'] == source['id']
    assert bundle['source']['source_pack_sha256'] == hashlib.sha256(original).hexdigest()
    assert len(bundle['questions'][0]['hypothesis_ids']) == 2
    assert bundle['questions'][0]['evidence_ids']
    assert wb.result_bytes(source['id']) == original
    assert [row['id'] for row in wb.question_history(source['id'])['question_jobs']] == [job['id']]
    assert job['id'] not in {row['id'] for row in wb.history()}
    with pytest.raises(ValueError):
        wb.result_bytes(job['id'])
    assert wb.resume_preview(job['id'])['available'] is False
    with pytest.raises(ValueError):
        wb.preview_comment(job['id'])


def test_saved_questions_and_idempotent_intent_survive_restart_without_provider(adaptive, monkeypatch):
    wb, provider, source = adaptive
    job, _ = generate(wb, source)
    raw = wb.question_bytes(job['id'])
    request = generation_request(wb, source)
    other = Workbench(wb.root, provider_factory=lambda *a, **k: pytest.fail('Read retried a paid call'))
    try:
        monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda *a: pytest.fail('Read loaded config'))
        monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot', lambda *a: pytest.fail('Saved question read reacquired Git'))
        assert other.question_bytes(job['id']) == raw
        assert other.question_history(source['id'])['question_jobs'][0]['id'] == job['id']
        assert other.submit_questions(request)['id'] == job['id']
        assert len(provider.question_calls) == 1
    finally:
        other.close()


def test_concurrent_duplicate_requests_share_one_durable_intent(adaptive):
    wb, provider, source = adaptive
    request = generation_request(wb, source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: wb.submit_questions(request), range(2)))
    assert jobs[0]['id'] == jobs[1]['id']
    assert completed(wb, jobs[0]['id'])['status'] == 'completed'
    assert len(provider.question_calls) == 1


@pytest.mark.parametrize('change', [
    {'confirmation': 'yes'}, {'confirmation': None}, {'source_job_id': '../.env'},
    {'source_pack_sha256': '0' * 64}, {'source_pack_sha256': 'bad'},
    {'provider': 'groq'}, {'revision': 'HEAD'}, {'patch': 'restricted'},
])
def test_invalid_generation_request_never_queues_or_calls(adaptive, monkeypatch, change):
    wb, provider, source = adaptive
    request = generation_request(wb, source, **change)
    monkeypatch.setattr(wb, '_enqueue', lambda *a, **k: pytest.fail('Invalid request queued'))
    with pytest.raises(ValueError):
        wb.submit_questions(request)
    assert provider.question_calls == []


def test_offline_report_does_not_synthesize_questions_or_load_provider(adaptive, monkeypatch):
    wb, provider, _ = adaptive
    offline = completed(wb, wb.submit({'mode': 'example'})['id'])
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda *a: pytest.fail('No hypotheses'))
    preview = wb.question_history(offline['id'])
    assert preview['available'] is False
    with pytest.raises(ValueError):
        wb.submit_questions(generation_request(wb, offline))
    assert provider.question_calls == []


@pytest.mark.parametrize('descendant', [False, True])
def test_benchmark_authority_cannot_escape_through_product_looking_followup(adaptive, descendant):
    wb, provider, source = adaptive
    chosen = source
    if descendant:
        chosen = completed(wb, wb.submit({'mode': 'followup', 'provider': 'groq',
            'parent_job_id': source['id'], 'additional_context': 'unknown'})['id'])
        assert chosen['status'] == 'completed'
    request = generation_request(wb, chosen)
    row = json.loads(wb._job_path(source['id']).read_bytes())
    row['request'] = {'mode': 'main_dev', 'case_id': 'verified:synthetic'}
    write_json(wb._job_path(source['id']), row)
    assert wb.question_history(chosen['id'])['available'] is False
    with pytest.raises(ValueError):
        wb.submit_questions(request)
    assert provider.question_calls == []


@pytest.mark.parametrize('mutation', ['excerpt', 'snapshot', 'missing_git'])
def test_real_snapshot_and_excerpts_replay_before_provider(adaptive, monkeypatch, mutation):
    wb, provider, source = adaptive
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
    request = generation_request(wb, source)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda *a: pytest.fail('Paid before proof replay'))
    try:
        job = completed(wb, wb.submit_questions(request)['id'])
    except ValueError:
        pass
    else:
        assert job['status'] == 'failed'
    assert provider.question_calls == []


def test_source_change_while_queued_stops_before_provider(adaptive, monkeypatch):
    wb, provider, source = adaptive
    request = generation_request(wb, source)
    captured = []
    ready = threading.Event()
    run = wb.questions._run
    def defer(row):
        captured.append(row)
        ready.set()
    monkeypatch.setattr(wb.questions, '_run', defer)
    job = wb.submit_questions(request)
    assert ready.wait(5)
    repair_export(wb, source, lambda pack: pack.update(confidence={'level': 'changed', 'probability': None}))
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda *a: pytest.fail('Stale paid call'))
    run(captured[0])
    assert wb.job(job['id'])['status'] == 'failed'
    assert provider.question_calls == []


def test_partial_unknown_answers_preserve_exact_question_and_do_not_create_cues(adaptive, monkeypatch):
    wb, provider, source = adaptive
    questions, bundle = generate(wb, source)
    original = wb.result_bytes(source['id'])
    request = adaptive_answers(wb, source, questions, bundle)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda *a: pytest.fail('Answer default must be offline'))
    child = completed(wb, wb.submit(request)['id'])
    assert child['status'] == 'completed', child
    raw = wb.result_bytes(child['id'])
    pack = json.loads(raw)
    receipt = pack['followup']['clarification']
    assert receipt['schema'] == 'adaptive_clarification_answers_v1'
    assert receipt['questions_sha256'] == bundle['sha256']
    assert receipt['language'] == 'vi'
    assert receipt['answers'][0]['answer'] == '  chưa biết\n'
    assert receipt['answers'][0]['question'] == bundle['questions'][0]['prompts']['vi']
    assert receipt['answers'][0]['hypothesis_ids'] == bundle['questions'][0]['hypothesis_ids']
    assert pack['snapshot'] == json.loads(original)['snapshot']
    assert pack.get('repository_context') is None
    assert pack['leakage_audit']['strict_issue_time_verified'] is False
    assert pack['case']['issue_created_at'] is None
    intake = wb.intake(child['id'])
    for field in ('expected_behavior', 'environment', 'reproduction'):
        assert intake['fields'][field]['status'] == 'not_found'
    assert 'phiên bản Python' not in intake['cue_text']
    assert wb.result_bytes(source['id']) == original
    assert len(provider.question_calls) == 1
    other = Workbench(wb.root, provider_factory=lambda *a, **k: pytest.fail('Restart spent'))
    try:
        assert other.result_bytes(child['id']) == raw
    finally:
        other.close()


def test_bundle_cannot_be_answered_against_another_source_job(adaptive):
    wb, provider, source = adaptive
    questions, bundle = generate(wb, source)
    other = completed(wb, wb.submit({'mode': 'example', 'provider': 'groq'})['id'])
    request = adaptive_answers(wb, other, questions, bundle,
        parent_pack_sha256=hashlib.sha256(wb.result_bytes(other['id'])).hexdigest())
    with pytest.raises(ValueError):
        wb.submit(request)
    assert len(provider.question_calls) == 1


@pytest.mark.parametrize('change', [
    {'questions_sha256': '0' * 64}, {'question_job_id': '0' * 24},
    {'answers': [{'question_id': 'probe:q2', 'answer': 'unknown'}]},
    {'answers': [{'question_id': 'missing:environment', 'answer': 'unknown'}]},
])
def test_answers_reject_stale_hash_wrong_job_and_unoffered_questions(adaptive, monkeypatch, change):
    wb, _, source = adaptive
    questions, bundle = generate(wb, source)
    monkeypatch.setattr(wb, '_enqueue', lambda *a, **k: pytest.fail('Invalid answers queued'))
    with pytest.raises((ValueError, FileNotFoundError)):
        wb.submit(adaptive_answers(wb, source, questions, bundle, **change))


def test_question_read_rejects_changed_parent_even_if_own_export_hash_is_repaired(adaptive):
    wb, _, source = adaptive
    questions, _ = generate(wb, source)
    repair_export(wb, source, lambda pack: pack['annotation']['hypotheses'][0].update(statement='Changed cause'))
    with pytest.raises(ValueError):
        wb.question_bytes(questions['id'])


def test_invalid_generated_output_is_one_attempt_and_failed_intent_is_reused(adaptive):
    wb, provider, source = adaptive
    provider.question_transform = lambda result: {'questions': []}
    request = generation_request(wb, source)
    job = completed(wb, wb.submit_questions(request)['id'])
    assert job['status'] == 'failed'
    assert 'no automatic retry' in job['error']
    assert wb.submit_questions(request)['id'] == job['id']
    assert len(provider.question_calls) == 1
    with pytest.raises(ValueError):
        wb.question_bytes(job['id'])


def test_old_template_answers_still_load_after_adaptive_questions_exist(adaptive):
    wb, _, source = adaptive
    generate(wb, source)
    child = completed(wb, wb.submit(answer_request(wb, source['id']))['id'])
    assert child['status'] == 'completed'
    receipt = json.loads(wb.result_bytes(child['id']))['followup']['clarification']
    assert receipt['schema'] == 'issue_clarification_answers_v1'


def test_symbolic_request_revision_keeps_separate_resolved_commit_binding(adaptive):
    wb, provider, source = adaptive
    repository = json.loads(wb.result_bytes(source['id']))['snapshot']['repository']
    local = completed(wb, wb.submit({'mode': 'local', 'repository': repository,
        'revision': 'HEAD', 'issue_text': 'Parser crashes on None input', 'provider': 'groq'})['id'])
    assert local['status'] == 'completed'
    _, bundle = generate(wb, local)
    commit = json.loads(wb.result_bytes(local['id']))['snapshot']['commit_sha']
    assert bundle['source']['case']['revision'] == 'HEAD'
    assert bundle['source']['commit_sha'] == commit
    assert all(row['revision'] == commit for row in provider.question_calls[-1]['evidence'] if row['kind'] == 'code')


def test_adaptive_answers_share_eight_generation_limit_with_freeform_followups(adaptive):
    wb, _, parent = adaptive
    for depth in range(1, 8):
        parent = completed(wb, wb.submit({'mode': 'followup', 'parent_job_id': parent['id'],
            'additional_context': 'unknown', 'provider': 'groq'})['id'])
        assert parent['status'] == 'completed', parent
        assert json.loads(wb.result_bytes(parent['id']))['followup']['depth'] == depth
    questions, bundle = generate(wb, parent)
    child = completed(wb, wb.submit(adaptive_answers(wb, parent, questions, bundle))['id'])
    assert child['status'] == 'completed', child
    assert json.loads(wb.result_bytes(child['id']))['followup']['depth'] == 8
    assert wb.question_history(child['id'])['available'] is False
    with pytest.raises(ValueError):
        wb.submit_questions(generation_request(wb, child))


@pytest.mark.parametrize('mutation', ['question', 'question_job', 'request_answer'])
def test_adaptive_answer_receipt_replay_rejects_edited_saved_answers(adaptive, mutation):
    wb, _, source = adaptive
    questions, bundle = generate(wb, source)
    child = completed(wb, wb.submit(adaptive_answers(wb, source, questions, bundle))['id'])
    assert child['status'] == 'completed', child
    if mutation == 'request_answer':
        row = json.loads(wb._job_path(child['id']).read_bytes())
        row['request']['answers'][0]['answer'] = 'different answer'
        write_json(wb._job_path(child['id']), row)
    else:
        def change(pack):
            if mutation == 'question':
                pack['followup']['clarification']['answers'][0]['question'] = 'Changed question'
            else:
                pack['followup']['question_job_id'] = '0' * 24
        repair_export(wb, child, change)
    with pytest.raises(ValueError):
        wb.result_bytes(child['id'])


def test_cancelled_question_attempt_is_not_retried_or_published(adaptive):
    wb, provider, source = adaptive
    started, release = threading.Event(), threading.Event()
    def block(response):
        started.set()
        assert release.wait(10)
        return response
    provider.question_transform = block
    request = generation_request(wb, source)
    try:
        job = wb.submit_questions(request)
        assert started.wait(10)
        wb.cancel(job['id'])
        release.set()
        finished = completed(wb, job['id'])
        assert finished['status'] == 'cancelled'
        assert 'question_result' not in finished
        assert wb.submit_questions(request)['id'] == job['id']
        assert len(provider.question_calls) == 1
    finally:
        release.set()


def test_question_job_never_constructs_unauthorized_model(adaptive, monkeypatch):
    wb, provider, source = adaptive
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda _: SimpleNamespace(
        model='another-provider/model', reasoning_effort='high', max_output_tokens=512))
    wb.provider_factory = lambda *a, **k: pytest.fail('Constructed unapproved model')
    job = completed(wb, wb.submit_questions(generation_request(wb, source))['id'])
    assert job['status'] == 'failed'
    assert provider.question_calls == []


def test_paid_failure_retains_owned_usage_receipt_and_never_retries(tmp_path, monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-secret')
    config = replace(configured(tmp_path), reasoning_effort='high')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda _: config)
    fixture = SyntheticProvider()
    paid_calls = []
    workbench = None
    def transport(request, **kwargs):
        body = json.loads(request.data)
        schema = body['response_format']['json_schema']['schema']
        if 'questions' in schema['properties']:
            paid_calls.append(body)
            rows = [row for row in workbench.question_history(source['id'])['question_jobs'] if row['status'] == 'running']
            assert len(rows) == 1
            usage = workbench.usage(rows[0]['id'])
            assert usage['receipts'][0]['status'] == 'reserved'
            assert usage['receipts'][0]['stage'] == 'adaptive_questions'
            raise RuntimeError('synthetic-secret private response')
        answer = fixture.complete_structured(body['messages'], max_output_tokens=body['max_completion_tokens'], schema=schema)
        return BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode())
    workbench = Workbench(tmp_path, provider_factory=lambda cfg, **kwargs: GroqProvider(cfg, transport=transport, **kwargs))
    try:
        source = completed(workbench, workbench.submit({'mode': 'example', 'provider': 'groq'})['id'])
        assert source['status'] == 'completed'
        request = generation_request(workbench, source)
        job = completed(workbench, workbench.submit_questions(request)['id'])
        assert job['status'] == 'failed'
        report = workbench.usage(job['id'])
        assert report['tracking_available'] and report['reservations'] == 1
        assert report['retained_bounds'] == 1
        assert report['receipts'][0]['status'] == 'failed_bound'
        assert report['receipts'][0]['stage'] == 'adaptive_questions'
        assert report['tokens_charged_or_reserved'] > 0
        assert workbench.submit_questions(request)['id'] == job['id']
        assert len(paid_calls) == 1
        assert 'synthetic-secret' not in json.dumps(report) + job['error']
        assert 'private response' not in job['error']
    finally:
        workbench.close()


def test_http_generation_and_saved_question_routes_are_session_bound(tmp_path, monkeypatch):
    config = SimpleNamespace(reasoning_effort='high', model='openai/gpt-oss-120b', max_output_tokens=512)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda _: config)
    provider = AdaptiveProvider()
    with local_server(tmp_path) as server:
        wb = server.workbench
        wb.provider_factory = lambda *a, **k: provider
        source = completed(wb, wb.submit({'mode': 'example', 'provider': 'groq'})['id'])
        headers = {'X-IssueIntel-Token': server.session_token, 'Content-Type': 'application/json',
                   'Origin': 'http://127.0.0.1:' + str(server.server_port)}
        route = '/api/jobs/' + source['id'] + '/adaptive-questions'
        assert http(server, 'GET', route)[0] == 403
        status, _, body = http(server, 'GET', route, headers=headers)
        assert status == 200 and json.loads(body)['question_jobs'] == []
        request = generation_request(wb, source)
        assert http(server, 'POST', '/api/adaptive-questions', json.dumps(request))[0] == 403
        status, _, raw = http(server, 'POST', '/api/adaptive-questions', json.dumps(request), headers)
        assert status == 202, raw
        job = completed(wb, json.loads(raw)['id'])
        assert job['status'] == 'completed'
        export = '/api/jobs/' + job['id'] + '/questions.json'
        assert http(server, 'GET', export)[0] == 403
        status, response_headers, body = http(server, 'GET', export, headers=headers)
        assert status == 200 and 'attachment' in response_headers['Content-Disposition']
        assert json.loads(body)['schema'] == 'adaptive_clarifications_v1'
        assert len(provider.question_calls) == 1
