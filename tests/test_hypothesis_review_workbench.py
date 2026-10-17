"""Saved-source hypothesis review through the product interfaces; synthetic only."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from issueintel.investigation.hypothesis_review import (
    build_hypothesis_review, render_markdown, validate_hypothesis_review,
)
from issueintel.investigation.pipeline import export_pack
from issueintel.investigation.report import build_report
from issueintel.investigation.workbench import Workbench
from issueintel.runtime.io import digest, write_json
from test_report_workbench import deny_reacquisition, example, repair_own_export
from test_workbench import SyntheticProvider, completed, http, local_server


@pytest.fixture
def workbench(tmp_path):
    wb = Workbench(tmp_path, provider_factory=lambda *a, **k: pytest.fail('Unexpected provider call'))
    try:
        yield wb
    finally:
        wb.close()


def assert_source(review, report, pack, raw, job_id):
    assert review['source'] == dict(report['source'], report_sha256=report['sha256'])
    assert review['source']['job_id'] == job_id
    assert review['source']['pack_sha256'] == sha256(raw).hexdigest()
    assert review['source']['pack_hash_kind'] == 'serialized_pack_bytes'
    assert review['source']['query_sha256'] == sha256(pack['case']['problem_statement'].encode()).hexdigest()
    assert review['source']['commit_sha'] == pack['snapshot']['commit_sha']
    assert review['source']['snapshot_sha256'] == pack['snapshot']['snapshot_sha256']
    assert validate_hypothesis_review(review, report) is review
    markdown = render_markdown(review)
    for key in ('pack_sha256', 'query_sha256', 'snapshot_sha256', 'commit_sha', 'report_sha256'):
        assert review['source'][key] in markdown
    assert review['sha256'] in markdown
    assert review['model_calls'] == 0 and review['evaluation_performed'] is False
    assert review['correctness'] == 'unverified' and review['independent_human'] is False


def deny_saved_acquisition(monkeypatch):
    deny_reacquisition(monkeypatch)

    def denied(*args, **kwargs):
        pytest.fail('A saved comparison must not run Git, model or network acquisition')

    monkeypatch.setattr('subprocess.Popen', denied)
    monkeypatch.setattr('urllib.request.urlopen', denied)


def test_offline_view_preserves_pack_and_legacy_report_without_inventing_hypotheses(workbench, monkeypatch):
    wb = workbench
    job = example(wb)
    raw = wb.result_bytes(job['id'])
    old_markdown = wb.result_bytes(job['id'], 'report.md')
    pack = json.loads(raw)
    repository = Path(pack['snapshot']['repository'])
    assert repository.resolve().is_relative_to(wb.root.resolve())
    repository.rename(repository.with_name(repository.name + '-unavailable'))
    deny_saved_acquisition(monkeypatch)

    report = wb.investigation_report(job['id'])
    review = wb.hypothesis_review(job['id'])
    assert_source(review, report, pack, raw, job['id'])
    assert review['hypotheses'] == review['pairs'] == review['evidence'] == []
    assert review['checks'] == report['next_checks']
    assert b'No hypotheses were generated' in wb.hypothesis_review_bytes(job['id'], 'md')
    assert json.loads(wb.hypothesis_review_bytes(job['id'])) == review
    assert wb.hypothesis_review_bytes(job['id'], 'md') == render_markdown(review).encode()
    assert wb.result_bytes(job['id']) == raw
    assert wb.result_bytes(job['id'], 'report.md') == old_markdown


class CompetingProvider(SyntheticProvider):
    """Three saved claims with reused IDs and opposite asserted citation roles."""

    def complete_structured(self, messages, **kwargs):
        result = super().complete_structured(messages, **kwargs)
        payload = json.loads(messages[1]['content'])
        if 'case' in payload:
            code = next(row['id'] for row in payload['evidence'] if row['kind'] == 'code')
            issue = next(row['id'] for row in payload['evidence'] if row['kind'] == 'issue')
            left, right, third = result['hypotheses']
            left.update(support_ids=[code], counter_ids=[issue], counter_status='found',
                        falsification_test='Inspect the saved branches with the same input.')
            right.update(support_ids=[issue], counter_ids=[code], counter_status='found')
            third.update(falsification_test=left['falsification_test'])
        else:
            for review, assessment in zip(result['reviews'], ('supported', 'contradicted', 'uncertain')):
                review.update(assessment=assessment, reason='Saved automatic critique; not executed.')
        return result


def test_saved_model_roles_reviews_and_shared_checks_survive_read_and_restart(tmp_path, monkeypatch):
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',
                        lambda root: SimpleNamespace(reasoning_effort='high'))
    provider = CompetingProvider()
    wb = Workbench(tmp_path, provider_factory=lambda *a, **k: provider)
    try:
        job = completed(wb, wb.submit({'mode': 'example', 'provider': 'groq'})['id'])
        assert job['status'] == 'completed', job
        assert len(provider.calls) == 2
        raw, legacy = wb.result_bytes(job['id']), wb.result_bytes(job['id'], 'report.md')
        pack = json.loads(raw)
        deny_saved_acquisition(monkeypatch)
        report = wb.investigation_report(job['id'])
        review = wb.hypothesis_review(job['id'])
        assert_source(review, report, pack, raw, job['id'])
        code = next(row['id'] for row in review['evidence'] if row['kind'] == 'code')
        issue = next(row['id'] for row in review['evidence'] if row['kind'] == 'issue')
        left, right, third = review['hypotheses']
        pair = review['pairs'][0]
        assert pair['left_support_right_counter_ids'] == [code]
        assert pair['left_counter_right_support_ids'] == [issue]
        assert pair['shared_support_ids'] == pair['shared_counter_ids'] == []
        assert review['pairs'][1]['shared_support_ids'] == [code]
        assert review['pairs'][1]['shared_check_ids'] == left['check_ids'] == third['check_ids']
        assert left['gap_codes'] == []
        assert right['gap_codes'] == ['no_code_support_citation', 'model_review_contradicted']
        assert third['gap_codes'] == ['missing_counter_in_retrieved_scope', 'model_review_uncertain']
        assert [row['model_review']['assessment'] for row in review['hypotheses']] == [
            'supported', 'contradicted', 'uncertain']
        assert all(row['semantic_support'] == 'unverified' for row in review['hypotheses'])
        assert all(row['execution_status'] == 'not_executed_by_issueintel' for row in review['checks'])
        markdown = wb.hypothesis_review_bytes(job['id'], 'md')
        assert b'not verified agreement or semantic contradiction' in markdown
        assert b'proposed, not executed' in markdown
        assert wb.result_bytes(job['id']) == raw and wb.result_bytes(job['id'], 'report.md') == legacy
    finally:
        wb.close()
    other = Workbench(tmp_path, provider_factory=lambda *a, **k: pytest.fail('Restart called provider'))
    try:
        assert other.hypothesis_review(job['id']) == review
        assert other.hypothesis_review_bytes(job['id'], 'md') == markdown
        assert len(provider.calls) == 2
        assert other.result_bytes(job['id']) == raw
    finally:
        other.close()


def test_old_job_ignores_mutable_derived_files_and_keeps_original_markdown(workbench, monkeypatch):
    wb = workbench
    job = example(wb)
    raw = wb.result_bytes(job['id'])
    directory = (wb.root / job['result']['pack.json']['path']).parent
    legacy = b'# Old saved report\nNo comparison file existed for this job.\n'
    (directory / 'report.md').write_bytes(legacy)
    row = json.loads(wb._job_path(job['id']).read_bytes())
    row['result']['report.md']['sha256'] = sha256(legacy).hexdigest()
    write_json(wb._job_path(job['id']), row)
    # Derived files are not authoritative and need not exist in an older job.
    for name in ('report.json', 'hypothesis-review.json', 'hypothesis-review.md'):
        (directory / name).unlink(missing_ok=True)
    deny_saved_acquisition(monkeypatch)
    baseline = wb.hypothesis_review(job['id'])
    assert not (directory / 'hypothesis-review.json').exists()
    for name in ('report.json', 'hypothesis-review.json', 'hypothesis-review.md'):
        (directory / name).write_text('untrusted stale projection', encoding='utf-8')
    other = Workbench(wb.root)
    try:
        assert other.hypothesis_review(job['id']) == baseline
        assert other.result_bytes(job['id']) == raw
        assert other.result_bytes(job['id'], 'report.md') == legacy
        assert (directory / 'hypothesis-review.json').read_text() == 'untrusted stale projection'
    finally:
        other.close()


def test_standalone_export_hashes_actual_pack_bytes_and_replays_without_job_or_acquisition(workbench, monkeypatch, tmp_path):
    wb = workbench
    parent = example(wb)
    child = completed(wb, wb.submit({'mode': 'followup', 'parent_job_id': parent['id'],
        'additional_context': 'Dữ kiện tổng hợp: đầu vào rỗng, kết quả mong đợi là [].'})['id'])
    assert child['status'] == 'completed', child
    pack = json.loads(wb.result_bytes(child['id']))
    before = deepcopy(pack)
    deny_saved_acquisition(monkeypatch)
    directory = tmp_path / 'standalone-export'
    assert export_pack(pack, directory) == directory / 'report.md'
    raw = (directory / 'pack.json').read_bytes()
    report = json.loads((directory / 'report.json').read_bytes())
    review = json.loads((directory / 'hypothesis-review.json').read_bytes())
    assert pack == before == json.loads(raw)
    assert digest(pack) != sha256(raw).hexdigest()  # Canonical JSON is not the saved serialization.
    assert_source(review, report, pack, raw, None)
    assert report == build_report(pack, source_pack_sha256=sha256(raw).hexdigest())
    assert review == build_hypothesis_review(report)
    assert (directory / 'hypothesis-review.md').read_text(encoding='utf-8') == render_markdown(review)
    assert b'hypothesis_review' not in raw and 'report_sha256' not in pack
    assert wb.result_bytes(child['id']) == raw


@pytest.mark.parametrize('corruption', ['parent', 'child'])
def test_comparison_replays_followup_lineage_after_a_self_hash_is_repaired(workbench, corruption):
    wb = workbench
    parent = example(wb)
    child = completed(wb, wb.submit({'mode': 'followup', 'parent_job_id': parent['id'],
                                   'additional_context': 'Expected empty output instead of an exception.'})['id'])
    assert child['status'] == 'completed', child
    assert wb.hypothesis_review(child['id'])['source']['job_id'] == child['id']
    if corruption == 'parent':
        repair_own_export(wb, parent, lambda pack: pack['trace'].append({'stage': 'synthetic_local_note'}))
    else:
        repair_own_export(wb, child, lambda pack: pack['followup'].update(parent_pack_sha256='0' * 64))
    with pytest.raises(ValueError):
        wb.hypothesis_review(child['id'])
    for fmt in ('json', 'md'):
        with pytest.raises(ValueError):
            wb.hypothesis_review_bytes(child['id'], fmt)


@pytest.mark.parametrize('corruption', ['citation_hash', 'citation_revision', 'citation_locator', 'query'])
def test_comparison_denies_bad_pack_content_even_with_repaired_export_hash(workbench, corruption):
    wb = workbench
    job = example(wb)

    def mutate(pack):
        code = next(row for row in pack['evidence'] if row['kind'] == 'code')
        if corruption == 'citation_hash':
            code['sha256'] = '0' * 64
        elif corruption == 'citation_revision':
            code['revision'] = '0' * 40
        elif corruption == 'citation_locator':
            code['locator'] = 'parser.py:0-99999'
        else:
            pack['case']['problem_statement'] += '\nChanged query without source evidence.'

    repair_own_export(wb, job, mutate)
    with pytest.raises(ValueError):
        wb.hypothesis_review(job['id'])
    with pytest.raises(ValueError):
        wb.hypothesis_review_bytes(job['id'], 'md')


@pytest.mark.parametrize('fmt', ['', 'html', 'JSON', 'pack.json', '../.env'])
def test_comparison_format_is_an_enum_not_a_file_selector(workbench, fmt):
    job = example(workbench)
    with pytest.raises(ValueError):
        workbench.hypothesis_review_bytes(job['id'], fmt)


def test_http_comparison_is_session_guarded_with_exact_source_headers_and_no_acquisition(tmp_path, monkeypatch):
    with local_server(tmp_path) as server:
        wb = server.workbench
        job = example(wb)
        raw_pack, old_report = wb.result_bytes(job['id']), wb.result_bytes(job['id'], 'report.md')
        expected = wb.hypothesis_review(job['id'])
        prefix = '/api/jobs/' + job['id'] + '/hypothesis-review.'
        token = {'X-IssueIntel-Token': server.session_token}
        deny_saved_acquisition(monkeypatch)
        for fmt in ('json', 'md'):
            path = prefix + fmt
            assert http(server, 'GET', path)[0] == 403
            assert http(server, 'GET', path, headers={**token, 'Origin': 'http://attacker.invalid'})[0] == 403
            status, headers, raw = http(server, 'GET', path, headers=token)
            assert status == 200
            assert headers['X-IssueIntel-Report-SHA256'] == expected['source']['report_sha256']
            assert headers['Content-Type'].startswith('application/json' if fmt == 'json' else 'text/markdown')
            assert headers['Content-Length'] == str(len(raw))
            assert headers['Cache-Control'] == 'no-store'
            assert headers['X-Content-Type-Options'] == 'nosniff'
            assert 'hypothesis-review.' + fmt in headers['Content-Disposition']
            assert raw == wb.hypothesis_review_bytes(job['id'], fmt)
            if fmt == 'json':
                assert json.loads(raw) == expected
            else:
                for key in ('pack_sha256', 'query_sha256', 'snapshot_sha256', 'commit_sha', 'report_sha256'):
                    assert expected['source'][key].encode() in raw
        assert wb.result_bytes(job['id']) == raw_pack
        assert wb.result_bytes(job['id'], 'report.md') == old_report
        assert http(server, 'GET', prefix + 'html', headers=token)[0] == 404
        assert http(server, 'GET', '/api/jobs/' + 'a' * 24 + '/hypothesis-review.json', headers=token)[0] == 404
        assert http(server, 'GET', '/api/jobs/../hypothesis-review.json', headers=token)[0] == 409
        assert http(server, 'GET', prefix + 'json/../../.env', headers=token)[0] == 404
        status, headers, raw = http(server, 'GET', '/hypothesis-review.js')
        assert status == 200 and headers['Content-Type'].startswith('text/javascript')
        assert b'textContent' in raw and b'IssueIntelHypothesisReview' in raw
        assert http(server, 'GET', '/static/../.env')[0] == 404


def test_http_comparison_denies_repaired_self_hash_after_source_corruption(tmp_path):
    with local_server(tmp_path) as server:
        wb = server.workbench
        job = example(wb)
        repair_own_export(wb, job, lambda pack: pack['evidence'][0].update(sha256='0' * 64))
        token = {'X-IssueIntel-Token': server.session_token}
        for fmt in ('json', 'md'):
            status, _, raw = http(server, 'GET', '/api/jobs/' + job['id'] + '/hypothesis-review.' + fmt,
                                  headers=token)
            assert status == 409
            assert json.loads(raw) == {'error': 'Local data integrity check failed'}
