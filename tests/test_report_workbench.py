"""Saved-source report integration; all cases and transports are synthetic."""
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from issueintel.investigation.workbench import Workbench
from issueintel.runtime.io import write_json
from test_workbench import completed, http, local_server, SyntheticProvider
from test_github_integration import github_workbench


@pytest.fixture
def report_workbench(tmp_path):
    wb = Workbench(tmp_path, provider_factory=lambda *a, **k: pytest.fail('Report read called a provider'))
    try:
        yield wb
    finally:
        wb.close()


def example(wb):
    job = completed(wb, wb.submit({'mode': 'example'})['id'])
    assert job['status'] == 'completed', job
    return job


def repair_own_export(wb, job, mutate):
    """A self hash is insufficient proof for malformed public pack contents."""
    path = wb.root / job['result']['pack.json']['path']
    pack = json.loads(path.read_bytes())
    mutate(pack)
    write_json(path, pack)
    row = json.loads(wb._job_path(job['id']).read_bytes())
    row['result']['pack.json']['sha256'] = sha256(path.read_bytes()).hexdigest()
    write_json(wb._job_path(job['id']), row)


def deny_reacquisition(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('Rendering a saved report must not acquire sources or run inference')
    for target in ('issueintel.investigation.workbench.RuntimeConfig.load',
                   'issueintel.investigation.workbench.investigate',
                   'issueintel.investigation.workbench.acquire_repository',
                   'issueintel.investigation.workbench.fetch_github_issue',
                   'issueintel.investigation.pipeline.build_snapshot',
                   'issueintel.repository.snapshot.build_snapshot',
                   'issueintel.repository.graph.build_graph'):
        monkeypatch.setattr(target, denied)


def test_derived_report_binds_exact_saved_bytes_without_git_or_inference(report_workbench, monkeypatch):
    wb = report_workbench
    job = example(wb)
    raw = wb.result_bytes(job['id'])
    pack = json.loads(raw)
    legacy = wb.result_bytes(job['id'], 'report.md')
    repository = Path(pack['snapshot']['repository'])
    assert repository.resolve().is_relative_to(wb.root.resolve())
    repository.rename(repository.with_name(repository.name + '-unavailable'))
    deny_reacquisition(monkeypatch)

    result = wb.investigation_report(job['id'])
    assert result['source']['job_id'] == job['id']
    assert result['source']['pack_sha256'] == sha256(raw).hexdigest()
    assert result['source']['pack_hash_kind'] == 'serialized_pack_bytes'
    assert result['source']['query_sha256'] == sha256(pack['case']['problem_statement'].encode()).hexdigest()
    assert result['source']['commit_sha'] == pack['snapshot']['commit_sha']
    assert result['source']['snapshot_sha256'] == pack['snapshot']['snapshot_sha256']
    assert json.loads(wb.report_bytes(job['id'], 'json')) == result
    markdown = wb.report_bytes(job['id'], 'md')
    assert sha256(raw).hexdigest().encode() in markdown
    assert pack['snapshot']['commit_sha'].encode() in markdown
    assert wb.result_bytes(job['id']) == raw
    assert wb.result_bytes(job['id'], 'report.md') == legacy
    assert wb.investigation_report(job['id']) == result


def test_old_markdown_is_kept_while_new_report_is_derived_after_restart(report_workbench, monkeypatch):
    wb = report_workbench
    job = example(wb)
    raw = wb.result_bytes(job['id'])
    legacy = b'# Legacy report\nSaved before the shared report contract.\n'
    path = wb.root / job['result']['report.md']['path']
    path.write_bytes(legacy)
    row = json.loads(wb._job_path(job['id']).read_bytes())
    row['result']['report.md']['sha256'] = sha256(legacy).hexdigest()
    write_json(wb._job_path(job['id']), row)
    # Old jobs do not need a newly generated report.json file to be readable.
    derived_path = path.with_name('report.json')
    if derived_path.exists():
        derived_path.unlink()
    deny_reacquisition(monkeypatch)
    other = Workbench(wb.root)
    try:
        report = other.investigation_report(job['id'])
        assert report['source']['pack_sha256'] == sha256(raw).hexdigest()
        assert other.report_bytes(job['id'], 'md') != legacy
        assert other.result_bytes(job['id'], 'report.md') == legacy
        assert other.result_bytes(job['id']) == raw
        assert path.read_bytes() == legacy
        assert not derived_path.exists()
    finally:
        other.close()


def test_new_export_writes_derived_json_without_embedding_or_rehashing_the_pack(report_workbench):
    wb = report_workbench
    job = example(wb)
    raw = wb.result_bytes(job['id'])
    pack = json.loads(raw)
    output = wb.root / job['result']['pack.json']['path']
    saved = json.loads(output.with_name('report.json').read_bytes())
    assert saved['source']['job_id'] is None  # Standalone export has no queue identity.
    assert saved['source']['pack_sha256'] == sha256(raw).hexdigest()
    assert saved['source']['pack_hash_kind'] == 'serialized_pack_bytes'
    assert 'report' not in pack and 'report_sha256' not in pack
    source_bound = wb.investigation_report(job['id'])
    # Both are the same projection; the web view additionally binds its saved job.
    source_bound['source']['job_id'] = None
    source_bound.pop('sha256')
    saved.pop('sha256')
    assert source_bound == saved
    assert wb.result_bytes(job['id']) == raw


def test_report_read_replays_followup_parent_and_preserves_changed_query(report_workbench):
    wb = report_workbench
    parent = example(wb)
    original = wb.result_bytes(parent['id'])
    child = completed(wb, wb.submit({'mode': 'followup', 'parent_job_id': parent['id'],
                                    'additional_context': 'Expected: return an empty result, not a crash.'})['id'])
    assert child['status'] == 'completed'
    report = wb.investigation_report(child['id'])
    source = report['source']
    assert source['query_sha256'] != wb.investigation_report(parent['id'])['source']['query_sha256']
    assert source['commit_sha'] == json.loads(original)['snapshot']['commit_sha']
    serialized = json.dumps(report)
    assert parent['id'] in serialized and sha256(original).hexdigest() in serialized
    assert wb.compare({'before_id': parent['id'], 'after_id': child['id']})['same_context'] is False
    path = wb.root / parent['result']['pack.json']['path']
    path.write_bytes(original + b' ')
    with pytest.raises(ValueError, match='integrity'):
        wb.investigation_report(child['id'])
    with pytest.raises(ValueError, match='integrity'):
        wb.report_bytes(child['id'], 'md')


def test_valid_long_clarification_receipt_does_not_turn_a_completed_investigation_into_export_failure(report_workbench):
    wb = report_workbench
    parent = example(wb)
    intake = wb.intake(parent['id'])
    questions = intake['clarifications']
    answers = [{'question_id': row['id'], 'answer': '\\' * 4000} for row in questions['questions']]
    assert len(answers) == 3
    child = completed(wb, wb.submit({'mode': 'clarification', 'parent_job_id': parent['id'],
        'parent_pack_sha256': intake['source_pack_sha256'], 'questions_sha256': questions['sha256'],
        'language': 'vi', 'answers': answers})['id'])
    assert child['status'] == 'completed', child
    pack = json.loads(wb.result_bytes(child['id']))
    assert [row['answer'] for row in pack['followup']['clarification']['answers']] == [row['answer'] for row in answers]
    report = wb.investigation_report(child['id'])
    assert report['source']['pack_sha256'] == sha256(wb.result_bytes(child['id'])).hexdigest()
    assert any(row['kind'] == 'clarification' for row in report['provenance'])
    assert wb.report_bytes(child['id'], 'md')


def test_model_supported_review_is_kept_separate_from_causal_or_human_validation(tmp_path, monkeypatch):
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',
                        lambda root: SimpleNamespace(reasoning_effort='high'))
    class SupportedProvider(SyntheticProvider):
        def complete_structured(self, *args, **kwargs):
            result = super().complete_structured(*args, **kwargs)
            for review in result.get('reviews', []):
                review['assessment'] = 'supported'
                review['reason'] = 'The cited guard is consistent with this hypothesis; runtime remains untested.'
            return result
    provider = SupportedProvider()
    wb = Workbench(tmp_path, provider_factory=lambda *a, **k: provider)
    try:
        job = completed(wb, wb.submit({'mode': 'example', 'provider': 'groq'})['id'])
        assert job['status'] == 'completed', job
        calls = len(provider.calls)
        deny_reacquisition(monkeypatch)
        result = wb.investigation_report(job['id'])
        assert result['execution']['status'] == 'completed'
        assert result['findings'] == {
            'status': 'hypotheses_unverified', 'causal_status': 'unverified',
            'severity': 'unassessed', 'confidence_probability': None, 'confidence_method': None}
        assert len(result['hypotheses']) == 3
        for row in result['hypotheses']:
            verification = row['verification']
            assert verification['citation_integrity'] == 'valid'
            assert verification['model_assessment'] == 'supported'
            assert verification['model_evidence_ids']
            assert verification['semantic_support'] == 'unverified'
            assert verification['independent_human'] is False
        assert all(row['execution_status'] == 'not_executed_by_issueintel' for row in result['next_checks'])
        assert {'causal_unverified', 'no_independent_human', 'runtime_not_executed'} <= set(result['unknowns'])
        markdown = wb.report_bytes(job['id'], 'md').decode()
        assert 'Model assessment: supported (unverified).' in markdown
        assert 'Semantic support: unverified. Independent human: no.' in markdown
        assert len(provider.calls) == calls == 2
    finally:
        wb.close()


@pytest.mark.parametrize('mutation', ['hash', 'revision', 'locator'])
def test_report_rejects_corrupt_citation_even_if_own_file_hash_is_updated(report_workbench, mutation):
    wb = report_workbench
    job = example(wb)
    def mutate(pack):
        code = next(row for row in pack['evidence'] if row['kind'] == 'code')
        if mutation == 'hash':
            code['sha256'] = '0' * 64
        elif mutation == 'revision':
            code['revision'] = '0' * 40
        else:
            code['locator'] = 'parser.py:0-99999'
    repair_own_export(wb, job, mutate)
    with pytest.raises(ValueError):
        wb.investigation_report(job['id'])
    with pytest.raises(ValueError):
        wb.report_bytes(job['id'], 'md')


@pytest.mark.parametrize('fmt', ['', 'html', 'pack.json', '../.env'])
def test_report_format_cannot_select_arbitrary_files(report_workbench, fmt):
    job = example(report_workbench)
    with pytest.raises(ValueError):
        report_workbench.report_bytes(job['id'], fmt)


def test_report_api_requires_session_and_exports_matching_saved_projection(tmp_path):
    with local_server(tmp_path) as server:
        wb = server.workbench
        job = example(wb)
        prefix = '/api/jobs/' + job['id'] + '/investigation-report.'
        routes = [prefix + 'json', prefix + 'md', '/api/investigation-report-schema']
        token = {'X-IssueIntel-Token': server.session_token}
        for route in routes:
            assert http(server, 'GET', route)[0] == 403
            assert http(server, 'GET', route, headers={**token, 'Origin': 'http://attacker.invalid'})[0] == 403
        status, headers, raw = http(server, 'GET', prefix + 'json', headers=token)
        assert status == 200 and headers['Content-Type'].startswith('application/json')
        assert headers['Cache-Control'] == 'no-store'
        assert 'investigation-report.json' in headers['Content-Disposition']
        assert json.loads(raw) == wb.investigation_report(job['id'])
        status, headers, raw = http(server, 'GET', prefix + 'md', headers=token)
        assert status == 200 and headers['Content-Type'].startswith('text/markdown')
        assert 'investigation-report.md' in headers['Content-Disposition']
        assert raw == wb.report_bytes(job['id'], 'md')
        status, _, raw = http(server, 'GET', '/api/investigation-report-schema', headers=token)
        assert status == 200
        schema = json.loads(raw)
        assert schema['type'] == 'object' and schema.get('properties')
        assert schema.get('additionalProperties') is False
        assert http(server, 'GET', prefix + 'html', headers=token)[0] == 404


def test_github_preview_uses_shared_report_and_keeps_exact_body_approval(tmp_path, monkeypatch):
    wb, revision = github_workbench(tmp_path, monkeypatch)
    monkeypatch.setenv('GITHUB_TOKEN', 'synthetic-key')
    posted = []
    monkeypatch.setattr('issueintel.investigation.workbench.post_reviewed_comment',
                        lambda draft, **kw: posted.append(draft) or {'comment_id': 7})
    try:
        job = completed(wb, wb.submit({'mode': 'github',
            'issue_url': 'https://github.com/synthetic/example/issues/5', 'revision': revision})['id'])
        assert job['status'] == 'completed'
        raw = wb.result_bytes(job['id'])
        from issueintel.investigation.report import render_markdown
        report = wb.investigation_report(job['id'])
        draft = wb.preview_comment(job['id'])
        assert draft['body'] == render_markdown(report, compact=True)
        assert draft['pack_sha256'] == sha256(raw).hexdigest()
        assert f'/blob/{revision}/parser.py#L' in draft['body']
        assert sha256(raw).hexdigest() in draft['body']
        assert draft['body_sha256'] == sha256(draft['body'].encode()).hexdigest()
        from issueintel.integrations.github import comment_preview
        with pytest.raises(ValueError):
            comment_preview(json.loads(raw), source_pack_sha256='')
        assert not posted
        approval = {'approved_sha256': draft['body_sha256'], 'confirmation': 'publish_reviewed_comment'}
        with pytest.raises(ValueError):
            wb.publish_comment(job['id'], {**approval, 'approved_sha256': '0' * 64})
        assert not posted
        wb.publish_comment(job['id'], approval)
        assert len(posted) == 1 and posted[0]['body'] == draft['body']
        with pytest.raises(ValueError, match='already attempted'):
            wb.publish_comment(job['id'], approval)
        assert len(posted) == 1
    finally:
        wb.close()


def test_long_untrusted_issue_is_bounded_and_literal_in_exports_and_comment(tmp_path, monkeypatch):
    wb, revision = github_workbench(tmp_path, monkeypatch)
    statement = ('Parser error on empty input.\n<script>alert(1)</script>\n'
                 '@everyone @owner [click](javascript:alert(1))\n```\n# Fake conclusion\n```\n'
                 + 'Untrusted source text only. ' * 2000)
    monkeypatch.setattr('issueintel.investigation.workbench.fetch_github_issue', lambda url: {
        'url': 'https://github.com/synthetic/example/issues/5', 'repository': 'synthetic/example',
        'problem_statement': statement, 'created_at': '2020-01-01T00:00:00Z'})
    try:
        job = completed(wb, wb.submit({'mode': 'github',
            'issue_url': 'https://github.com/synthetic/example/issues/5', 'revision': revision})['id'])
        assert job['status'] == 'completed', job
        raw = wb.result_bytes(job['id'])
        report = wb.investigation_report(job['id'])
        assert report['summary']['truncated'] is True
        assert report['summary']['text'] == statement[:len(report['summary']['text'])]
        assert report['source']['query_sha256'] == sha256(statement.encode()).hexdigest()
        issue = next(row for row in report['evidence'] if row['kind'] == 'issue')
        assert issue['text_truncated'] is True and len(issue['text']) <= 1200
        markdown = wb.report_bytes(job['id'], 'md').decode()
        comment = wb.preview_comment(job['id'])['body']
        for rendered in (markdown, comment):
            assert '<script>' not in rendered and '&lt;script&gt;' in rendered
            assert '@everyone' not in rendered and '@owner' not in rendered
            assert '[click](javascript:' not in rendered
            assert '\n# Fake conclusion\n' not in rendered
            assert sha256(raw).hexdigest() in rendered
        assert len(comment.encode()) <= 60000
        assert len(comment) <= 16000
        assert wb.result_bytes(job['id']) == raw
    finally:
        wb.close()
