"""Public export recovery integrates with saved product flows, never a new call."""
import hashlib
import json
import sqlite3
import threading

import pytest

from issueintel.interfaces.web import make_server
from issueintel.investigation.workbench import Workbench
from issueintel.runtime.io import write_json
from test_product_finalization import approval, finalization_source
from test_workbench import completed, http


def finalized(wb, source):
    preview = wb.finalization_preview(source['id'])
    assert preview['available'], preview
    child = completed(wb, wb.submit_finalization(source['id'], approval(preview))['id'])
    assert child['status'] == 'completed', child
    return child


def deny_new_resources(wb, monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('Saved finalization attempted config, provider or Git acquisition')

    wb.provider_factory = denied
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load', denied)
    for module in ('issueintel.repository.snapshot', 'issueintel.investigation.pipeline',
                   'issueintel.investigation.resume', 'issueintel.investigation.question_jobs'):
        monkeypatch.setattr(module + '.build_snapshot', denied)
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository', denied)


def rewrite_pack(wb, child, mutate):
    row = json.loads(wb._job_path(child['id']).read_bytes())
    path = wb.root / row['result']['pack.json']['path']
    pack = json.loads(path.read_bytes())
    mutate(pack)
    write_json(path, pack)
    row['result']['pack.json']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(wb._job_path(child['id']), row)


def test_recovery_and_public_exports_work_without_key_config_provider_or_git(
        finalization_source, monkeypatch):
    wb, _, source, provider = finalization_source
    git_dir = wb.root / 'artifacts/demo/example_repo/.git'
    unavailable = git_dir.with_name('.git-finalization-unavailable')
    assert git_dir.resolve().is_relative_to(wb.root)
    assert unavailable.resolve().is_relative_to(wb.root)
    git_dir.rename(unavailable)
    deny_new_resources(wb, monkeypatch)
    child = finalized(wb, source)
    raw = wb.result_bytes(child['id'])
    pack = json.loads(raw)
    report = json.loads(wb.report_bytes(child['id']))
    provenance = [json.loads(row['details']) for row in report['provenance'] if row['kind'] == 'resume']
    assert report['source']['job_id'] == child['id']
    assert report['source']['pack_sha256'] == hashlib.sha256(raw).hexdigest()
    assert any(row.get('schema') == 'product_export_finalization_v1'
               and row['source_job_id'] == source['id'] and row['model_calls'] == 0 for row in provenance)
    assert pack['finalized_from']['full_git_replay_performed'] is False
    assert pack['verification']['independent_human'] is False
    assert report['findings']['causal_status'] == 'unverified'
    assert b'IssueIntel investigation' in wb.result_bytes(child['id'], 'report.md')
    assert b'IssueIntel investigation' in wb.report_bytes(child['id'], 'md')
    assert len(provider.calls) == 2
    assert wb.job(source['id'])['status'] == 'failed'


def test_finalized_child_retains_product_read_capabilities_without_new_calls(
        finalization_source, monkeypatch):
    wb, _, source, provider = finalization_source
    child = finalized(wb, source)
    deny_new_resources(wb, monkeypatch)
    intake = wb.intake(child['id'])
    assert intake['clarifications_available'] is True
    assert intake['check_journal_available'] is True
    assert wb.question_history(child['id'])['available'] is True
    assert wb.check_plan_history(child['id'])['available'] is True
    journal = wb.check_journal(child['id'])
    assert journal['revision'] == 0
    assert journal['source']['job_id'] == child['id']
    assert wb.hypothesis_review(child['id'])['source']['job_id'] == child['id']
    assert wb.finalization_preview(child['id'])['available'] is False
    assert wb.resume_preview(child['id'])['available'] is False
    with pytest.raises(ValueError):
        wb.preview_comment(child['id'])
    assert len(provider.calls) == 2


def test_failed_original_has_no_pack_requirement_for_downstream_followup(finalization_source):
    wb, _, source, provider = finalization_source
    row = json.loads(wb._job_path(source['id']).read_bytes())
    row.pop('result', None)
    write_json(wb._job_path(source['id']), row)
    child = finalized(wb, source)
    followup = completed(wb, wb.submit({'mode': 'followup', 'parent_job_id': child['id'],
        'additional_context': 'A later synthetic input also fails.', 'provider': 'none'})['id'])
    assert followup['status'] == 'completed', followup
    assert wb.intake(followup['id'])['check_journal_available'] is True
    assert json.loads(wb.result_bytes(followup['id']))['followup']['depth'] == 1
    assert len(provider.calls) == 2


@pytest.mark.parametrize('authority', ['main_dev', 'followup', 'planned', 'offline', 'completed'])
def test_valid_saved_stages_cannot_grant_unsupported_source_authority(
        finalization_source, monkeypatch, authority):
    wb, _, source, provider = finalization_source
    preview = wb.finalization_preview(source['id'])
    row = json.loads(wb._job_path(source['id']).read_bytes())
    assert {'verification_input', 'verification'} <= set(row['stage_checkpoints'])
    if authority == 'main_dev':
        row['mode'] = 'main_dev'
        row['request'] = {'mode': 'main_dev', 'provider': 'groq', 'depth': 'deep',
                          'case_id': 'verified:synthetic'}
    elif authority == 'followup':
        row['mode'] = 'followup'
        row['request'] = {'mode': 'followup', 'provider': 'groq', 'depth': 'deep',
                          'parent_job_id': 'a' * 24, 'additional_context': 'Synthetic context'}
    elif authority == 'planned':
        row['depth'] = row['request']['depth'] = 'planned'
    elif authority == 'offline':
        row['provider'] = row['request']['provider'] = 'none'
    else:
        row['status'] = 'completed'
    write_json(wb._job_path(source['id']), row)
    monkeypatch.setattr(wb, '_enqueue', lambda *a, **k: pytest.fail('Unsupported source queued'))
    assert wb.finalization_preview(source['id'])['available'] is False
    with pytest.raises(ValueError):
        wb.submit_finalization(source['id'], approval(preview))
    assert len(provider.calls) == 2


def test_queued_recovery_admission_rechecks_current_policy(finalization_source, monkeypatch):
    wb, _, source, provider = finalization_source
    started, release = threading.Event(), threading.Event()
    def blocker():
        started.set()
        assert release.wait(10)
    wb.executor.submit(blocker)
    assert started.wait(5)
    try:
        child = wb.submit_finalization(source['id'], approval(wb.finalization_preview(source['id'])))
        monkeypatch.setattr('issueintel.investigation.finalization_jobs.resume_policy', lambda: 'f' * 64)
        release.set()
        result = completed(wb, child['id'])
        assert result['status'] == 'failed', result
        assert not result.get('result')
        assert not (wb.directory / 'runs' / child['id']).exists()
        assert wb.finalization_preview(source['id'])['available'] is False
        assert len(provider.calls) == 2
    finally:
        release.set()


def test_completed_child_replays_after_policy_change_and_restart(finalization_source, monkeypatch):
    wb, _, source, provider = finalization_source
    child = finalized(wb, source)
    raw, report = wb.result_bytes(child['id']), wb.report_bytes(child['id'])
    monkeypatch.setattr('issueintel.investigation.finalization_jobs.resume_policy', lambda: 'f' * 64)
    assert wb.finalization_preview(source['id'])['available'] is False
    deny_new_resources(wb, monkeypatch)
    restarted = Workbench(wb.root, provider_factory=lambda *a, **k: pytest.fail('Read spent API'))
    try:
        assert restarted.result_bytes(child['id']) == raw
        assert restarted.report_bytes(child['id']) == report
        assert restarted.intake(child['id'])['check_journal_available'] is True
    finally:
        restarted.close()
    assert len(provider.calls) == 2


@pytest.mark.parametrize('tamper', ['remove_link', 'source_id', 'zero_call_bool',
                                  'extra_lineage', 'wrong_request_mode', 'request_hash'])
def test_rehashed_child_cannot_change_lineage_truth_or_job_authority(finalization_source, tamper):
    wb, _, source, provider = finalization_source
    child = finalized(wb, source)
    if tamper in {'wrong_request_mode', 'request_hash'}:
        row = json.loads(wb._job_path(child['id']).read_bytes())
        if tamper == 'wrong_request_mode':
            row['request']['mode'] = 'example'
        else:
            row['request']['source_request_sha256'] = 'f' * 64
        write_json(wb._job_path(child['id']), row)
    else:
        def alter(pack):
            if tamper == 'remove_link':
                pack.pop('finalized_from')
            elif tamper == 'source_id':
                pack['finalized_from']['source_job_id'] = 'a' * 24
            elif tamper == 'zero_call_bool':
                pack['finalized_from']['model_calls'] = False
            else:
                pack['followup'] = {'schema': 'investigation_followup_v1'}
        rewrite_pack(wb, child, alter)
    for read in (wb.result_bytes, wb.report_bytes, wb.check_journal):
        with pytest.raises(ValueError):
            read(child['id'])
    assert len(provider.calls) == 2


@pytest.mark.parametrize('change', ['delete', 'redirect'])
def test_saved_child_exports_require_original_durable_intent(finalization_source, change):
    wb, _, source, provider = finalization_source
    child = finalized(wb, source)
    with sqlite3.connect(wb.directory / 'finalizations.sqlite') as db:
        if change == 'delete':
            db.execute('DELETE FROM intents')
        else:
            db.execute('UPDATE intents SET target_job=?', ('a' * 24,))
    with pytest.raises(ValueError):
        wb.result_bytes(child['id'])
    with pytest.raises(ValueError):
        wb.report_bytes(child['id'])
    assert len(provider.calls) == 2


def test_http_finalization_guarded_preview_submit_and_normal_exports(finalization_source, monkeypatch):
    wb, _, source, provider = finalization_source
    deny_new_resources(wb, monkeypatch)
    server = make_server(wb.root, port=0, workbench=wb)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        route = '/api/jobs/' + source['id'] + '/finalization'
        headers = {'X-IssueIntel-Token': server.session_token, 'Content-Type': 'application/json',
                   'Origin': 'http://127.0.0.1:' + str(server.server_port)}
        assert http(server, 'GET', route)[0] == 403
        status, _, raw = http(server, 'GET', route, headers=headers)
        preview = json.loads(raw)
        assert status == 200 and preview['available'] is True
        body = json.dumps(approval(preview))
        assert http(server, 'POST', route, body, {'Content-Type': 'application/json'})[0] == 403
        assert http(server, 'POST', route, body, {**headers, 'Origin': 'https://invalid.test'})[0] == 403
        assert http(server, 'POST', route, '{}', headers)[0] == 400
        status, _, raw = http(server, 'POST', route, body, headers)
        assert status == 202, raw
        child = completed(wb, json.loads(raw)['id'])
        assert child['status'] == 'completed', child
        for suffix, mime in [('pack.json', 'application/json'),
                             ('investigation-report.json', 'application/json'),
                             ('investigation-report.md', 'text/markdown')]:
            status, response, raw = http(server, 'GET', '/api/jobs/' + child['id'] + '/' + suffix,
                                         headers=headers)
            assert status == 200, raw
            assert response['Content-Type'].startswith(mime)
            assert raw
        for asset, mime in [('/finalization.js', 'text/javascript'), ('/finalization.css', 'text/css')]:
            status, response, raw = http(server, 'GET', asset)
            assert status == 200 and raw
            assert response['Content-Type'].startswith(mime)
        assert len(provider.calls) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
