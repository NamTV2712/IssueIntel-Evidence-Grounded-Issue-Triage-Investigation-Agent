"""Synthetic manual observations, including concurrency and saved-source attacks."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3

import pytest

from issueintel.investigation.check_journal import (
    CheckJournalStore, FLAGS, initial_journal, validate_journal, render_journal_markdown,
)
from issueintel.investigation.workbench import Workbench
from issueintel.runtime.io import digest, write_json
from test_report_workbench import report_workbench, example, deny_reacquisition, repair_own_export
from test_hypothesis_review_workbench import CompetingProvider
from test_workbench import completed, http, local_server


def observation(check='check_1', **overrides):
    return dict({'check_id': check, 'outcome': 'reported_difference',
        'conditions': 'Synthetic parser, Python, input=None', 'expected_result': 'An empty list',
        'observed_result': 'TypeError observed by the user', 'interpretation': 'Caller or parser contract still needs investigation',
        'tested_commit_sha': None, 'supersedes': None}, **overrides)


def append(store, report, state, **overrides):
    return store.append(report, {'expected_sha256': state['sha256'], 'observation': observation(**overrides)})


@pytest.fixture
def source(report_workbench):
    job = example(report_workbench)
    return report_workbench, job, report_workbench.investigation_report(job['id'])


def test_manual_observations_persist_and_do_not_infer_tested_commit_or_change_report(source):
    wb, job, report = source
    pack, legacy = wb.result_bytes(job['id']), wb.result_bytes(job['id'], 'report.md')
    initial = wb.check_journal(job['id'])
    assert initial == initial_journal(report) and initial['revision'] == 0
    assert initial['checks'] == report['next_checks']
    state = wb.check_journal(job['id'], {'expected_sha256': initial['sha256'], 'observation': observation()})
    assert state['revision'] == 1 and state['parent_sha256'] == initial['sha256']
    event = state['events'][0]
    assert event['observation']['tested_commit_sha'] is None
    assert event['check_binding']['text_sha256'] == sha256(report['next_checks'][0]['text'].encode()).hexdigest()
    assert event['check_binding']['hypothesis_ids'] == []
    for name, value in FLAGS.items():
        assert state[name] == event[name] == value
    assert CheckJournalStore(wb.plans.path).load(report) == state
    assert wb.result_bytes(job['id']) == pack and wb.result_bytes(job['id'], 'report.md') == legacy
    assert wb.investigation_report(job['id']) == report


def test_correction_is_append_only_same_check_and_cannot_correct_an_already_superseded_record(source):
    wb, job, report = source
    store = wb.check_journals
    first = append(store, report, store.load(report), tested_commit_sha='9'*40)
    second = append(store, report, first, supersedes='observation_1', observed_result='Corrected: result was an empty list', outcome='reported_match')
    assert second['events'][0] == first['events'][0]
    assert second['events'][1]['observation']['supersedes'] == 'observation_1'
    for invalid in ['observation_1', 'observation_900']:
        with pytest.raises(ValueError): append(store, report, second, supersedes=invalid)
    third = append(store, report, second, supersedes='observation_2', outcome='inconclusive')
    assert len(third['events']) == 3 and third['events'][:2] == second['events']
    assert validate_journal(third, report) == third
    exported = render_journal_markdown(third, report)
    assert 'observation_1 (superseded)' in exported and 'observation_2 (superseded)' in exported
    assert '9'*40 in exported and 'Report commit: '+report['source']['commit_sha'] in exported
    assert 'Causal correctness' in exported or 'unverified' in exported


def test_invalid_claims_fields_and_unrun_observations_are_rejected_without_writes(source):
    wb, _, report = source
    store = wb.check_journals; initial = store.load(report)
    variants = [{'outcome': 'verified'}, {'check_id': 'step_1'}, {'check_id': 'check_99'},
        {'conditions': ''}, {'expected_result': ' '}, {'observed_result': ''},
        {'tested_commit_sha': 'HEAD'}, {'tested_commit_sha': True}, {'tested_commit_sha': 'A'*40},
        {'interpretation': '\0'}, {'observed_result': 'x'*4001}, {'conditions': []},
        {'supersedes': 'observation_1'}, {'supersedes': False}, {'fix_verified': True},
        {'outcome': 'not_run'}, {'outcome': 'not_run', 'observed_result': '', 'interpretation': ''},
        {'outcome': 'not_run', 'observed_result': '', 'tested_commit_sha': 'a'*40}]
    for change in variants:
        with pytest.raises((ValueError, TypeError)):
            append(store, report, initial, **change)
        assert store.load(report) == initial
    for bad in [None, {}, {'expected_sha256': initial['sha256'], 'observation': observation(), 'source': report['source']}]:
        with pytest.raises(ValueError): store.append(report, bad)
    unrun = append(store, report, initial, outcome='not_run', observed_result='', interpretation='Need a local environment first')
    assert unrun['events'][0]['observation']['tested_commit_sha'] is None


def test_same_revision_writers_and_exact_request_retry_never_append_twice(source):
    wb, _, report = source
    stores = [CheckJournalStore(wb.plans.path), CheckJournalStore(wb.plans.path)]
    initial = stores[0].load(report)
    request = {'expected_sha256': initial['sha256'], 'observation': observation()}
    def save(index):
        try: return stores[index].append(report, request)
        except ValueError: return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, range(2)))
    assert sum(result is not None for result in results) == 1
    with pytest.raises(ValueError, match='changed'): stores[0].append(report, request)
    assert stores[0].load(report)['revision'] == 1


def test_corrupt_or_resealed_event_cannot_change_bound_check_source_types_or_provenance(source, tmp_path):
    _, _, report = source
    def reseal(event):
        event['sha256'] = digest({key:value for key,value in event.items() if key!='sha256'})
    mutations = [lambda e: e['observation'].update(observed_result='rewritten without repairing digest'),
        lambda e: e['check_binding'].update(text_sha256='0'*64),
        lambda e: e['source'].update(report_sha256='0'*64),
        lambda e: e.update(fix_verified=True), lambda e: e.update(revision=True),
        lambda e: e.update(recorded_at='2026-10-18T12:00:00'), lambda e: e.update(extra='unapproved'),
        lambda e: e.update(parent_sha256='0'*64)]
    for index, mutate in enumerate(mutations):
        store = CheckJournalStore(tmp_path/f'events-{index}.sqlite')
        state = append(store, report, store.load(report))
        event = deepcopy(state['events'][0]); mutate(event)
        if index: reseal(event)
        with sqlite3.connect(store.path) as db:
            db.execute('UPDATE check_observations SET sha256=?,payload=?', (event['sha256'],json.dumps(event)))
        with pytest.raises(ValueError): store.load(report)
    state = initial_journal(report)
    for change in [lambda s:s.update(revision=False), lambda s:s.update(fix_verified=0), lambda s:s['source'].update(strict_issue_time_verified=0)]:
        modified = deepcopy(state);change(modified)
        with pytest.raises(ValueError): validate_journal(modified, report)


def test_limit_and_literal_handoff_export_preserve_all_reports_without_execution(source, monkeypatch):
    wb, _, report = source; store = wb.check_journals
    monkeypatch.setattr('issueintel.investigation.check_journal.LIMIT', 2)
    first = append(store, report, store.load(report), observed_result='<script>x</script>\n@everyone https://invalid.test')
    second = append(store, report, first, outcome='inconclusive')
    with pytest.raises(ValueError, match='limit'): append(store, report, second)
    markdown = render_journal_markdown(second, report)
    assert '<script>' not in markdown and '&lt;script&gt;' in markdown
    assert '@everyone' not in markdown and 'https://invalid.test' not in markdown
    assert report['sha256'] in markdown and second['sha256'] in markdown
    assert 'not independent human benchmark labels' in markdown
    assert len(store.load(report)['events']) == 2


def test_saved_read_write_export_restart_never_acquires_git_network_or_model(source, monkeypatch):
    wb, job, report = source
    repository = Path(json.loads(wb.result_bytes(job['id']))['snapshot']['repository'])
    assert repository.resolve().is_relative_to(wb.root.resolve())
    repository.rename(repository.with_name(repository.name+'-unavailable'))
    deny_reacquisition(monkeypatch)
    monkeypatch.setattr('subprocess.Popen', lambda *a,**k: pytest.fail('Git executed'))
    monkeypatch.setattr('urllib.request.urlopen', lambda *a,**k: pytest.fail('Network executed'))
    state = wb.check_journal(job['id'])
    state = wb.check_journal(job['id'], {'expected_sha256': state['sha256'], 'observation': observation()})
    with WorkbenchForTest(wb.root) as reopened:
        assert reopened.check_journal(job['id']) == state
        for format in ('json','md'):
            raw, report_sha, journal_sha = reopened.check_journal_export(job['id'],format)
            assert raw and report_sha == report['sha256'] and journal_sha == state['sha256']
    with pytest.raises(ValueError): wb.check_journal_export(job['id'],'html')


class WorkbenchForTest:
    def __init__(self, root): self.wb=Workbench(root,provider_factory=lambda *a,**k:pytest.fail('Model called'))
    def __enter__(self): return self.wb
    def __exit__(self,*args): self.wb.close()


def test_checks_bind_to_deduplicated_report_ids_and_not_legacy_checklist_positions(tmp_path, monkeypatch):
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda root:SimpleNamespace(reasoning_effort='high'))
    with WorkbenchForTest(tmp_path) as wb:
        provider = CompetingProvider(); wb.provider_factory=lambda *a,**k:provider
        job=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id'])
        assert job['status']=='completed'
        state = wb.check_journal(job['id']); report=wb.investigation_report(job['id'])
        assert len(wb.plan(job['id'])['steps']) == 3 and len(state['checks']) == 2
        saved=wb.check_journal(job['id'],{'expected_sha256':state['sha256'],'observation':observation()})
        assert saved['events'][0]['check_binding']['hypothesis_ids'] == ['h0','h2']
        assert saved['events'][0]['check_binding']['evidence_ids'] == report['next_checks'][0]['evidence_ids']
        changed=deepcopy(saved['events'][0]['observation']);changed.update(check_id='check_2',supersedes='observation_1')
        with pytest.raises(ValueError): wb.check_journal(job['id'],{'expected_sha256':saved['sha256'],'observation':changed})
        assert len(provider.calls)==2


def test_product_followup_is_allowed_but_forged_benchmark_ancestry_and_changed_pack_are_denied(source):
    wb, job, _ = source
    child=completed(wb,wb.submit({'mode':'followup','parent_job_id':job['id'],'additional_context':'Synthetic environment note'})['id'])
    assert wb.check_journal(child['id'])['source']['job_id']==child['id']
    path=wb._job_path(job['id']); row=json.loads(path.read_bytes()); row['request']['mode']='main_dev';write_json(path,row)
    for identifier in (job['id'],child['id']):
        with pytest.raises(ValueError): wb.check_journal(identifier)
    row['request']['mode']='example';write_json(path,row)
    original=wb.check_journal(job['id']);append(wb.check_journals,wb.investigation_report(job['id']),original)
    repair_own_export(wb,job,lambda pack:pack['case'].update(problem_statement='Resealed different query'))
    with pytest.raises(ValueError): wb.check_journal(job['id'])


def test_strict_benchmark_or_standalone_report_cannot_seed_a_manual_journal(source):
    _, _, report=source
    for mutate in [lambda r:r['source'].update(job_id=None),
                   lambda r:r['source'].update(protocol='issue_time',strict_issue_time_verified=True),
                   lambda r:r['issue'].update(case_id='verified:synthetic'),
                   lambda r:r['issue'].update(case_id='live:synthetic')]:
        changed=deepcopy(report);mutate(changed)
        changed['sha256']=digest({key:value for key,value in changed.items() if key!='sha256'})
        with pytest.raises(ValueError): initial_journal(changed)


def test_http_journal_revision_bound_exports_and_guarded_append(tmp_path):
    with local_server(tmp_path) as server:
        job=example(server.workbench); headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        route='/api/jobs/'+job['id']+'/check-journal'
        status,response,raw=http(server,'GET',route+'.json',headers=headers);state=json.loads(raw)
        assert status==200 and response['X-IssueIntel-Journal-SHA256']==state['sha256']
        assert response['X-IssueIntel-Report-SHA256']==state['source']['report_sha256']
        request=json.dumps({'expected_sha256':state['sha256'],'observation':observation()})
        assert http(server,'POST',route,request,{'Content-Type':'application/json'})[0]==403
        assert http(server,'POST',route,request,{**headers,'Origin':'https://invalid.test'})[0]==403
        status,_,raw=http(server,'POST',route,request,headers);saved=json.loads(raw)
        assert status==200 and saved['revision']==1
        assert http(server,'POST',route,request,headers)[0]==400
        status,response,raw=http(server,'GET',route+'.md',headers=headers)
        assert status==200 and b'Manual check journal' in raw and b'IssueIntel investigation' in raw
        assert response['X-IssueIntel-Journal-SHA256']==saved['sha256'] and 'attachment' in response['Content-Disposition']
        assert http(server,'POST',route+'/../check-journal',request,headers)[0]==400
        assert http(server,'GET','/check-journal.js')[0]==200
        assert http(server,'GET',route+'.html',headers=headers)[0]==404
