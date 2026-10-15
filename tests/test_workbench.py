from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from http.client import HTTPConnection
import json
import re
import threading
import time

import pytest

from issueintel.benchmarks import dev_context
from issueintel.investigation.workbench import Workbench, validate_request
from issueintel.interfaces.web import make_server
from issueintel.runtime.io import digest, write_json


def completed(workbench, identifier):
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        job = workbench.job(identifier)
        if job['status'] not in {'queued', 'running'}:
            return job
        time.sleep(.02)
    pytest.fail('Synthetic job did not finish')


def test_example_runs_shared_offline_pipeline_and_hashes_exports(tmp_path):
    wb = Workbench(tmp_path, provider_factory=lambda *a, **k: pytest.fail('Provider called'))
    try:
        job = completed(wb, wb.submit({'mode': 'example'})['id'])
        assert job['status'] == 'completed'
        pack = json.loads(wb.result_bytes(job['id']))
        assert any(e['kind'] == 'code' and 'parser.py' in e['locator'] for e in pack['evidence'])
        assert pack['annotation'] is None
        assert pack['snapshot']['protocol'] == 'benchmark_base'
        assert pack['leakage_audit']['strict_issue_time_verified'] is False
        assert b'IssueIntel investigation' in wb.result_bytes(job['id'], 'report.md')
        assert 'request' not in wb.history()[0]
        path = tmp_path/job['result']['pack.json']['path']
        path.write_bytes(path.read_bytes()+b' ')
        with pytest.raises(ValueError, match='integrity'):
            wb.result_bytes(job['id'])
    finally:
        wb.close()


@pytest.mark.parametrize('input_request', [
    {'mode': 'example', 'patch': 'hidden'}, {'mode': 'main_dev', 'case_id': 'verified:test-1', 'issue_text': 'override'},
    {'mode': 'main_dev', 'case_id': 'verified:test-1', 'revision': 'different'},
    {'mode': 'example', 'provider': 'another_provider'}, {'mode': 'example', 'depth': 'unbounded'},
    {'mode': 'local', 'repository': 'x', 'revision': 'HEAD'},
    {'mode': 'example', 'case_id': 'verified:fake'}, {'mode': 'local', 'repository': 'x', 'revision': 'HEAD\n', 'issue_text': 'query'},
])
def test_invalid_requests_rejected_before_job_or_model(input_request):
    with pytest.raises(ValueError):
        validate_request(input_request)


def test_main_dev_denial_precedes_config_or_provider(tmp_path, monkeypatch):
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda root: pytest.fail('Config loaded before eligibility'))
    wb = Workbench(tmp_path, provider_factory=lambda *a, **k: pytest.fail('Provider called'))
    try:
        job = completed(wb, wb.submit({'mode': 'main_dev', 'case_id': 'verified:holdout-1', 'provider': 'groq'})['id'])
        assert job['status'] == 'failed' and 'ValueError' in job['error']
    finally:
        wb.close()


class SyntheticProvider:
    model = 'openai/gpt-oss-120b'
    config = SimpleNamespace(max_output_tokens=512)

    def __init__(self):
        self.calls = []

    def complete_structured(self, messages, *, max_output_tokens, schema):
        self.calls.append(schema)
        payload = json.loads(messages[1]['content'])
        if 'case' in payload:
            eid = next(e['id'] for e in payload['evidence'] if e['kind'] == 'code')
            return {'hypotheses': [{'id': f'h{i}', 'statement': f'Candidate mechanism {i}',
                'support_ids': [eid], 'counter_ids': [], 'counter_status': 'not_found',
                'falsification_test': f'Check missing input condition {i}'} for i in range(3)],
                'abstention': {'label': 'uncertain', 'reason': 'Not reproduced'}}
        return {'reviews': [{'hypothesis_id': h['id'], 'assessment': 'uncertain',
                            'reason': 'Not executed', 'evidence_ids': h['support_ids']}
                           for h in payload['hypotheses']]}

    def __call__(self, *args, **kwargs):
        pytest.fail('Structured output support was dropped')


def test_shared_structured_generation_and_critic_are_separate(tmp_path, monkeypatch):
    config = SimpleNamespace(reasoning_effort='high')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda root: config)
    provider = SyntheticProvider()
    def factory(cfg, *, call_context):
        assert cfg is config and call_context['category'] == 'ARTIFACT_GENERATION_COST'
        assert call_context['reasoning_effort'] == 'high'
        return provider
    wb = Workbench(tmp_path, provider_factory=factory)
    try:
        job = completed(wb, wb.submit({'mode': 'example', 'provider': 'groq', 'depth': 'deep'})['id'])
        assert job['status'] == 'completed'
        pack = json.loads(wb.result_bytes(job['id']))
        assert len(provider.calls) == 2
        assert len(pack['annotation']['hypotheses']) == 3
        assert pack['verification']['kind'] == 'llm_separate_pass'
        assert all(h['correctness'] == 'unverified' for h in pack['annotation']['hypotheses'])
    finally:
        wb.close()


def test_product_low_effort_keeps_compatible_durable_budget_accounting(tmp_path, monkeypatch):
    from decimal import Decimal
    from issueintel.runtime.budget import BudgetLedger, BudgetPolicy
    ledger=BudgetLedger(tmp_path/'budget.sqlite',BudgetPolicy(10000,Decimal('1')))
    price=SimpleNamespace(model='openai/gpt-oss-120b',context_tokens=10000,max_output_tokens=512,
                          checked_at='2026-10-14',micro_usd=lambda a,b:1)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda root:SimpleNamespace(reasoning_effort='low'))
    reservations=[]
    class AccountedProvider(SyntheticProvider):
        def __init__(self,context):super().__init__();self.context=context
        def complete_structured(self,*args,**kwargs):
            reservation=ledger.reserve(price,10,3,context=self.context);reservations.append(reservation)
            result=super().complete_structured(*args,**kwargs)
            ledger.reconcile(reservation,price,{'prompt_tokens':2,'completion_tokens':1,'total_tokens':3})
            return result
    wb=Workbench(tmp_path,provider_factory=lambda config,call_context:AccountedProvider(call_context))
    try:
        job=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id'])
        assert job['status']=='completed' and len(reservations)==2
        assert ledger.status()['calls']==2
        assert ledger.status()['daily_tokens_charged_or_reserved']==6
    finally:wb.close()


def test_cancel_after_first_call_prevents_next_call(tmp_path, monkeypatch):
    started = threading.Event(); release = threading.Event()
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', lambda root: SimpleNamespace(reasoning_effort='high'))
    class BlockingProvider(SyntheticProvider):
        def complete_structured(self, *args, **kwargs):
            started.set()
            assert release.wait(10)
            return super().complete_structured(*args, **kwargs)
    provider = BlockingProvider()
    wb = Workbench(tmp_path, provider_factory=lambda *a, **k: provider)
    try:
        identifier = wb.submit({'mode': 'example', 'provider': 'groq'})['id']
        assert started.wait(10)
        wb.cancel(identifier); release.set()
        job = completed(wb, identifier)
        assert job['status'] == 'cancelled'
        assert len(provider.calls) == 1
        assert 'result' not in job
    finally:
        release.set(); wb.close()


def test_restart_does_not_retry_interrupted_job(tmp_path):
    wb = Workbench(tmp_path)
    identifier = 'a'*24
    write_json(wb.jobs/(identifier+'.json'), {'id': identifier, 'status': 'running', 'stage': 'investigate', 'request': {'provider': 'groq'}})
    try:
        assert wb.job(identifier)['status'] == 'interrupted'
        assert json.loads((wb.jobs/(identifier+'.json')).read_bytes())['status'] == 'running'
        with pytest.raises(ValueError):
            wb.result_bytes(identifier)
        with pytest.raises(ValueError):
            wb.job('../.env')
    finally:
        wb.close()


@contextmanager
def local_server(tmp_path):
    server = make_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield server
    finally:
        server.shutdown(); server.server_close(); server.workbench.close(); thread.join(5)


def http(server, method, path, body=None, headers=None):
    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=10)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse(); raw = response.read()
    result = response.status, dict(response.getheaders()), raw
    connection.close(); return result


def test_http_origin_session_filesystem_and_csp(tmp_path):
    with local_server(tmp_path) as server:
        status, headers, raw = http(server, 'GET', '/')
        assert status == 200 and b'__SESSION_TOKEN__' not in raw
        assert server.session_token.encode() in raw
        assert "frame-ancestors 'none'" in headers['Content-Security-Policy']
        assert http(server, 'GET', '/api/jobs')[0] == 403
        token = {'X-IssueIntel-Token': server.session_token}
        assert http(server, 'GET', '/api/jobs', headers=token)[0] == 200
        assert http(server, 'GET', '/', headers={'Host': 'attacker.invalid'})[0] == 403
        assert http(server, 'GET', '/api/jobs', headers={**token, 'Origin': 'http://attacker.invalid'})[0] == 403
        assert http(server, 'GET', '/.env')[0] == 404
        assert http(server, 'GET', '/../../.env')[0] == 404
        assert http(server, 'GET', '/app.js')[0] == 200
        assert b'textContent' in http(server, 'GET', '/app.js')[2]


def test_http_example_api_export_and_invalid_inputs(tmp_path):
    with local_server(tmp_path) as server:
        headers = {'X-IssueIntel-Token': server.session_token, 'Content-Type': 'application/json'}
        assert http(server, 'POST', '/api/investigations', '{', headers)[0] == 400
        assert http(server, 'POST', '/api/investigations', json.dumps({'mode':'example','patch':'hidden'}), headers)[0] == 400
        assert http(server, 'POST', '/api/investigations', None, {**headers,'Content-Length':'512001'})[0] == 400
        status, _, raw = http(server, 'POST', '/api/investigations', json.dumps({'mode':'example'}), headers)
        assert status == 202
        identifier = json.loads(raw)['id']; job = completed(server.workbench, identifier)
        assert job['status'] == 'completed'
        status, response_headers, raw = http(server, 'GET', '/api/jobs/'+identifier+'/pack.json', headers=headers)
        assert status == 200 and 'attachment' in response_headers['Content-Disposition']
        assert json.loads(raw)['leakage_audit']['gold_in_inference'] is False


def test_remote_binding_rejected(tmp_path):
    with pytest.raises(ValueError, match='localhost'):
        make_server(tmp_path, host='0.0.0.0')


@pytest.fixture
def synthetic_catalog(tmp_path, monkeypatch):
    cases = [{'case': {'case_id': f'verified:synthetic__repo-{i}', 'split':'main_dev',
                      'repository':'synthetic/repo','problem_statement':f'Synthetic query {i}'}} for i in range(400)]
    parent = {'name':'main-dev-strict-v1','evaluation_authorized':False,'pool_sha256':'a'*64,
              'members':[{'case_id':cases[0]['case']['case_id'],'receipt':{}}],
              'ordered_case_ids':[cases[0]['case']['case_id']], 'accepted_count':1,
              'exclusions':[{'case_id':x['case']['case_id']} for x in cases[1:]]}
    identifier = digest(parent)
    monkeypatch.setattr(dev_context, 'PARENT', identifier)
    monkeypatch.setattr(dev_context, 'load_pool', lambda *a: {'candidates':cases})
    write_json(tmp_path/'data/development/cohorts'/(identifier+'.json'), parent)
    return tmp_path, cases


def test_catalog_only_original_dev_and_acceptance_partition(synthetic_catalog):
    root, cases = synthetic_catalog
    catalog = dev_context.public_catalog(root)
    assert catalog['accepted'] == 1 and catalog['pending'] == 399
    assert not any(key.startswith('_') for key in catalog)
    assert 'problem_statement' not in catalog['cases'][0]
    with pytest.raises(ValueError, match='Only accepted'):
        dev_context.accepted_context(root, cases[1]['case']['case_id'])
    cases[2]['case']['split'] = 'main_test'
    with pytest.raises(ValueError, match='partition'):
        dev_context.public_catalog(root)
