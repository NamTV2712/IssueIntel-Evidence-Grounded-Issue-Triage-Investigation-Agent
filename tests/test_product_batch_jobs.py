"""Synthetic lifecycle and crash-boundary checks for product batch intent."""
from copy import deepcopy
import json
import threading
import time
from types import SimpleNamespace

import pytest

from issueintel.investigation import product_batches as contract
from issueintel.investigation.batch_jobs import BatchConflict
from issueintel.investigation.workbench import Workbench
from issueintel.runtime.io import digest, write_json
from test_focused_retrieval import fixture_repo


def manifest(repository, count=2, *, key='synthetic-batch', provider='none'):
    return {'schema': contract.MANIFEST_SCHEMA, 'run_key': key, 'items': [
        {'key': 'item-'+str(index), 'request': {'mode': 'local', 'repository': str(repository),
            'revision': 'HEAD', 'issue_text': 'Synthetic issue '+str(index)+' parser None failure.',
            'provider': provider}} for index in range(count)]}


def approval(preview, *, api=False):
    return {'manifest': preview['manifest'], 'preview': preview, 'confirmed': True, 'allow_api_calls': api}


def binding(batch):
    return {key: batch[key] for key in ('manifest_sha256','preview_sha256')}


def wait_for(wb, identifier, predicate=None):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        value=wb.batch(identifier)
        if (predicate(value) if predicate else not any(value['counts'][key] for key in ('pending','queued','running'))):
            return value
        time.sleep(.02)
    pytest.fail('Synthetic batch did not reach its expected state')


def deny(*args,**kwargs):
    pytest.fail('Synthetic batch attempted a provider or external acquisition')


@pytest.fixture
def host(tmp_path,monkeypatch):
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',deny)
    monkeypatch.setattr('issueintel.investigation.workbench.fetch_github_issue',deny)
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository',deny)
    wb=Workbench(tmp_path,provider_factory=deny,automatic_sync=False)
    yield wb
    wb.close()


def hold_scheduler(wb,monkeypatch):
    monkeypatch.setattr(wb.batch_jobs,'_start',lambda identifier=None: wb.batch_jobs.enabled.add(identifier) if identifier else None)


def prepare(wb,identifier,*,phase='prepared',ordinal=0):
    with wb.batch_jobs._db(write=True) as db:
        db.execute('BEGIN IMMEDIATE')
        batch,preview,items=wb.batch_jobs._records(identifier,db)
        item=items[ordinal]
        row=wb._prepare_batch_job(preview['items'][ordinal]['resolved_request'],item['child_id'],
                                  wb.batch_jobs._binding(batch,preview,item))
        db.execute('UPDATE items SET phase=? WHERE child_id=?',(phase,item['child_id']))
    return row


def test_preview_is_zero_call_and_private_capture_rejects_rehashed_client_source(host,fixture_repo,monkeypatch):
    _,repo=fixture_repo
    preview=host.batch_preview(manifest(repo))
    assert preview['can_start'] and preview['model_calls']==0 and not host.history()
    forged=deepcopy(preview)
    request=forged['items'][0]['resolved_request']
    request['issue_text']='Changed on the client after preview.'
    forged['manifest']['items'][0]['request']['issue_text']=request['issue_text']
    source=forged['items'][0]['source']
    source['case']['problem_statement']=request['issue_text']
    source['case']['case_id']='local:'+digest(request)[:16]
    source=contract.seal_source(source,request=request)
    rows=[{key:item[key] for key in ('key','resolved_request','source','error')} for item in forged['items']]
    rows[0]['source']=source
    forged=contract.build_preview(forged['manifest'],rows,policy_sha256=forged['policy_sha256'])
    with pytest.raises(ValueError,match='captured'):
        host.batch_submit(approval(forged))
    assert not host.history()


def test_duplicate_ack_and_changed_key_conflict_allocate_no_new_children(host,fixture_repo,monkeypatch):
    _,repo=fixture_repo
    hold_scheduler(host,monkeypatch)
    preview=host.batch_preview(manifest(repo))
    first=host.batch_submit(approval(preview))
    second=host.batch_submit(approval(preview))
    assert first['id']==second['id'] and [row['job_id'] for row in first['items']]==[row['job_id'] for row in second['items']]
    changed=manifest(repo)
    changed['items'][0]['request']['issue_text']+=' changed'
    with pytest.raises(BatchConflict):
        host.batch_submit(approval(host.batch_preview(changed)))
    assert not host.history() and first['counts']['pending']==2


def test_duplicate_existing_intent_does_not_check_current_policy_or_start_scheduler(host,fixture_repo,monkeypatch):
    _,repo=fixture_repo
    hold_scheduler(host,monkeypatch)
    preview=host.batch_preview(manifest(repo))
    first=host.batch_submit(approval(preview))
    monkeypatch.setattr('issueintel.investigation.batch_jobs.policy_sha256',deny)
    monkeypatch.setattr(host.batch_jobs,'_start',deny)
    assert host.batch_submit(approval(preview))['id']==first['id']


def test_paid_batch_requires_explicit_approval_and_saved_approval_replays(host,fixture_repo,monkeypatch):
    _,repo=fixture_repo
    hold_scheduler(host,monkeypatch)
    preview=host.batch_preview(manifest(repo,provider='groq'))
    assert preview['cost_bound_micro_usd'] is None and preview['requires_api']
    with pytest.raises(ValueError,match='approval'):
        host.batch_submit(approval(preview))
    accepted=host.batch_submit(approval(preview,api=True))
    with pytest.raises(ValueError,match='approval'):
        host.batch_action(accepted['id'],'continue',binding(accepted))
    with host.batch_jobs._db(write=True) as db:
        db.execute('UPDATE batches SET approved_api=0 WHERE id=?',(accepted['id'],))
    with pytest.raises(ValueError,match='approval'):
        host.batch(accepted['id'])


def test_more_items_than_shared_queue_capacity_complete_without_replacement(host,fixture_repo):
    _,repo=fixture_repo
    accepted=host.batch_submit(approval(host.batch_preview(manifest(repo,6))))
    value=wait_for(host,accepted['id'])
    assert value['status']=='completed' and value['counts']['completed']==6,value
    assert len(host.history())==6 and len({item['job_id'] for item in value['items']})==6
    for item in value['items']:
        assert json.loads(host.result_bytes(item['job_id']))['case']['revision']==item['revision']


def test_pause_cancel_pending_then_continue_reuses_remaining_fixed_ids(host,fixture_repo,monkeypatch):
    _,repo=fixture_repo
    hold_scheduler(host,monkeypatch)
    accepted=host.batch_submit(approval(host.batch_preview(manifest(repo,3))))
    host.batch_action(accepted['id'],'pause',binding(accepted))
    assert not host.batch_jobs.dispatch_once()
    cancelled=host.batch_item_cancel(accepted['id'],'item-1',binding(accepted))
    assert cancelled['items'][1]['status']=='cancelled' and not cancelled['items'][1]['job_available']
    host.batch_action(accepted['id'],'continue',binding(accepted))
    for _ in range(100):
        host.batch_jobs.dispatch_once()
        value=host.batch(accepted['id'])
        if value['counts']['completed']==2:break
        time.sleep(.02)
    assert value['status']=='partial_success' and value['counts']['cancelled']==1,value
    assert [item['job_id'] for item in value['items']]==[item['job_id'] for item in accepted['items']]
    assert len(host.history())==2


@pytest.mark.parametrize('phase',['pending','prepared','started'])
def test_restart_boundaries_adopt_only_never_started_children(tmp_path,fixture_repo,monkeypatch,phase):
    _,repo=fixture_repo
    wb=Workbench(tmp_path,automatic_sync=False,provider_factory=deny)
    hold_scheduler(wb,monkeypatch)
    preview=wb.batch_preview(manifest(repo,1,key='restart-'+phase))
    accepted=wb.batch_submit(approval(preview))
    child=accepted['items'][0]['job_id']
    if phase!='pending':
        row=prepare(wb,accepted['id'],phase=phase)
        if phase=='started':
            wb._save(row,status='running',stage='seed_generation',usage_receipts={'preserved':'synthetic-paid-receipt'})
    wb.close()
    observer=Workbench(tmp_path,automatic_sync=False,provider_factory=deny)
    try:
        assert observer.batch_jobs.thread is None
        before=observer.batch(accepted['id'])
        assert observer.batch_jobs.thread is None
        assert before['items'][0]['status']==('interrupted' if phase=='started' else 'pending' if phase=='pending' else 'queued')
        observer.batch_action(accepted['id'],'continue',binding(before))
        value=wait_for(observer,accepted['id'])
        assert value['items'][0]['job_id']==child
        assert value['items'][0]['status']==('interrupted' if phase=='started' else 'completed'),value
        if phase=='started':
            assert json.loads(observer._job_path(child).read_bytes())['usage_receipts']=={'preserved':'synthetic-paid-receipt'}
    finally:observer.close()


def test_missing_terminal_or_started_child_stays_unknown_without_replacement(host,fixture_repo,monkeypatch):
    _,repo=fixture_repo
    hold_scheduler(host,monkeypatch)
    accepted=host.batch_submit(approval(host.batch_preview(manifest(repo,1))))
    row=prepare(host,accepted['id'],phase='started')
    host._save(row,status='completed',stage='completed')
    host.batch_jobs.dispatch_once()
    host._job_path(row['id']).unlink()
    value=host.batch(accepted['id'])
    assert value['items'][0]['status']=='interrupted' and not value['items'][0]['job_available']
    host.batch_action(value['id'],'continue',binding(value))
    assert not host.batch_jobs.dispatch_once() and not host._job_path(row['id']).exists()


def test_read_and_continue_one_batch_do_not_start_unapproved_restart_batch(tmp_path,fixture_repo,monkeypatch):
    _,repo=fixture_repo
    wb=Workbench(tmp_path,automatic_sync=False,provider_factory=deny)
    hold_scheduler(wb,monkeypatch)
    first=wb.batch_submit(approval(wb.batch_preview(manifest(repo,1,key='first'))))
    second=wb.batch_submit(approval(wb.batch_preview(manifest(repo,1,key='second'))))
    wb.close()
    observer=Workbench(tmp_path,automatic_sync=False,provider_factory=deny)
    try:
        assert observer.batch_jobs.thread is None and observer.batch_history()['batches']
        observer.batch_action(first['id'],'continue',binding(first))
        assert wait_for(observer,first['id'])['counts']['completed']==1
        assert observer.batch(second['id'])['counts']['pending']==1
        assert not observer._job_path(second['items'][0]['job_id']).exists()
    finally:observer.close()


def test_running_cancel_is_durable_and_does_not_run_later_items(host,fixture_repo,monkeypatch):
    _,repo=fixture_repo
    entered=threading.Event();release=threading.Event()
    original=host._run
    def blocked(row):
        entered.set();release.wait(10);original(row)
    monkeypatch.setattr(host,'_run',blocked)
    accepted=host.batch_submit(approval(host.batch_preview(manifest(repo,2))))
    assert entered.wait(5)
    host.batch_action(accepted['id'],'cancel',binding(accepted))
    assert host.batch_jobs.is_cancelled(accepted['items'][0]['job_id'])
    release.set()
    value=wait_for(host,accepted['id'])
    assert value['status']=='cancelled' and value['counts']['cancelled']==2,value
    assert len(host.history())==1


def test_unrelated_single_job_does_not_consult_batch_database(host,monkeypatch):
    monkeypatch.setattr(host.batch_jobs,'is_cancelled',deny)
    row=host.submit({'mode':'example'})
    deadline=time.monotonic()+10
    while host.job(row['id'])['status'] in {'queued','running'} and time.monotonic()<deadline:
        time.sleep(.02)
    assert host.job(row['id'])['status']=='completed'


def test_runtime_failure_preserves_partial_success_and_does_not_retry(host,fixture_repo,monkeypatch):
    _,repo=fixture_repo
    import issueintel.investigation.workbench as module
    original=module.investigate
    calls=[]
    def investigate(case,*args,**kwargs):
        calls.append(case['case_id'])
        if case['problem_statement'].startswith('Synthetic issue 0 '):
            raise RuntimeError('Synthetic pipeline failure')
        return original(case,*args,**kwargs)
    monkeypatch.setattr(module,'investigate',investigate)
    accepted=host.batch_submit(approval(host.batch_preview(manifest(repo,3))))
    value=wait_for(host,accepted['id'])
    assert value['status']=='partial_success' and value['counts']['failed']==1 and value['counts']['completed']==2,value
    assert len(calls)==len(set(calls))==3
    assert host.batch_submit(approval(host.batch_preview(manifest(repo,3))))['id']==value['id']
    assert len(calls)==3


def test_two_workbenches_share_one_outstanding_child_and_claim_each_id_once(tmp_path,fixture_repo,monkeypatch):
    _,repo=fixture_repo
    entered=threading.Event();release=threading.Event();seen=[]
    first=Workbench(tmp_path,automatic_sync=False,provider_factory=deny)
    second=Workbench(tmp_path,automatic_sync=False,provider_factory=deny)
    original1=first._run;original2=second._run
    def worker(original,row):
        seen.append(row['id'])
        if len(seen)==1:
            entered.set();release.wait(10)
        original(row)
    monkeypatch.setattr(first,'_run',lambda row:worker(original1,row))
    monkeypatch.setattr(second,'_run',lambda row:worker(original2,row))
    try:
        accepted=first.batch_submit(approval(first.batch_preview(manifest(repo,3))))
        assert entered.wait(5)
        second.batch_action(accepted['id'],'continue',binding(accepted))
        time.sleep(.2)
        assert len(seen)==1
        release.set()
        result=wait_for(first,accepted['id'])
        assert result['counts']['completed']==3 and len(seen)==len(set(seen))==3,result
    finally:
        release.set();first.close();second.close()


def test_terminal_row_status_disagreement_is_unknown(host,fixture_repo,monkeypatch):
    _,repo=fixture_repo
    hold_scheduler(host,monkeypatch)
    accepted=host.batch_submit(approval(host.batch_preview(manifest(repo,1))))
    row=prepare(host,accepted['id'],phase='started')
    host._save(row,status='completed',stage='completed')
    host.batch_jobs.dispatch_once()
    host._save(row,status='failed',stage='failed')
    assert host.batch(accepted['id'])['items'][0]['status']=='interrupted'


def test_existing_remote_cache_still_acquires_the_pinned_missing_commit(host,fixture_repo,monkeypatch):
    case,_=fixture_repo
    hold_scheduler(host,monkeypatch)
    cache=host.root/'data/repos/owner__repository.git'
    cache.mkdir(parents=True)
    host.batch_jobs.revision_resolver=lambda repository,revision:case['revision']
    host.batch_jobs.issue_fetcher=lambda url:{'repository':'owner/repository','number':7,
        'problem_statement':'Synthetic captured body','created_at':'2026-01-01T00:00:00Z',
        'provenance':{'source_url':'https://api.github.com/repos/owner/repository/issues/7',
            'response_sha256':'a'*64,'fetched_at':'2026-02-01T00:00:00Z',
            'body_version_at_creation_verified':False,'limitation':'Current synthetic body; not opening-version proof.'}}
    value={'schema':contract.MANIFEST_SCHEMA,'run_key':'remote-cached','items':[{'key':'issue',
        'request':{'mode':'github','issue_url':'https://github.com/owner/repository/issues/7','revision':'branch'}}]}
    preview=host.batch_preview(value)
    assert preview['can_start'],preview
    accepted=host.batch_submit(approval(preview))
    row=prepare(host,accepted['id'],phase='started')
    fetched=[]
    def acquire(repository,revision,directory):
        fetched.append((repository,revision))
        return SimpleNamespace(path=cache)
    def git(repository,*arguments):
        if not fetched:raise ValueError('Synthetic missing commit in existing cache')
        return case['revision'].encode()
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository',acquire)
    monkeypatch.setattr('issueintel.repository.snapshot._git',git)
    result,local=host.batch_jobs.input(row)
    assert result['problem_statement']=='Synthetic captured body' and local==cache
    assert fetched==[('owner/repository',case['revision'])]


def test_invalid_historical_batch_does_not_starve_later_valid_work(host,fixture_repo):
    _,repo=fixture_repo
    first=host.batch_submit(approval(host.batch_preview(manifest(repo,1,key='old-invalid'))))
    assert wait_for(host,first['id'])['counts']['completed']==1
    with host.batch_jobs._db(write=True) as db:
        db.execute('UPDATE batches SET manifest_sha=? WHERE id=?',('f'*64,first['id']))
    second=host.batch_submit(approval(host.batch_preview(manifest(repo,1,key='new-valid'))))
    assert wait_for(host,second['id'])['counts']['completed']==1
    history=host.batch_history()
    assert [item['id'] for item in history['batches']]==[second['id']]
    assert history['errors']==[{'id':first['id'],'code':'source_unavailable'}]


def test_invalid_but_owned_batch_blocks_other_batch_until_lease_is_released(host,fixture_repo,monkeypatch):
    _,repo=fixture_repo
    hold_scheduler(host,monkeypatch)
    first=host.batch_submit(approval(host.batch_preview(manifest(repo,1,key='invalid-owned'))))
    row=prepare(host,first['id'],phase='started')
    lease=host.stage_recoveries.lease(row['id'],create=True)
    try:
        with host.batch_jobs._db(write=True) as db:
            db.execute('UPDATE batches SET manifest_sha=? WHERE id=?',('f'*64,first['id']))
        second=host.batch_submit(approval(host.batch_preview(manifest(repo,1,key='after-invalid-owned'))))
        assert not host.batch_jobs.dispatch_once()
        assert not host._job_path(second['items'][0]['job_id']).exists()
    finally:lease.close()
    assert host.batch_jobs.dispatch_once()


def test_completion_between_file_read_and_ownership_probe_is_not_interrupted(host,fixture_repo,monkeypatch):
    _,repo=fixture_repo
    hold_scheduler(host,monkeypatch)
    accepted=host.batch_submit(approval(host.batch_preview(manifest(repo,1))))
    row=prepare(host,accepted['id'],phase='started')
    host._save(row,status='running',stage='investigate')
    def finish_before_probe(identifier):
        host._save(row,status='completed',stage='completed')
        return False
    monkeypatch.setattr(host.batch_jobs,'_owned',finish_before_probe)
    assert host.batch(accepted['id'])['items'][0]['status']=='completed'


@pytest.mark.parametrize('phase',['pending','prepared'])
def test_read_projection_allows_json_to_advance_after_sqlite_snapshot(host,fixture_repo,monkeypatch,phase):
    _,repo=fixture_repo
    hold_scheduler(host,monkeypatch)
    accepted=host.batch_submit(approval(host.batch_preview(manifest(repo,1))))
    row=prepare(host,accepted['id'],phase=phase)
    host._save(row,status='running',stage='investigate')
    monkeypatch.setattr(host.batch_jobs,'_owned',lambda identifier:True)
    assert host.batch(accepted['id'])['items'][0]['status']=='running'


def test_corrupt_child_with_live_owner_never_frees_global_outstanding_slot(host,fixture_repo,monkeypatch):
    _,repo=fixture_repo
    hold_scheduler(host,monkeypatch)
    first=host.batch_submit(approval(host.batch_preview(manifest(repo,1,key='live-corrupt-child'))))
    row=prepare(host,first['id'],phase='started')
    row['request']['issue_text']='Synthetic child authority corruption'
    write_json(host._job_path(row['id']),row)
    second=host.batch_submit(approval(host.batch_preview(manifest(repo,1,key='must-wait-for-owner'))))
    monkeypatch.setattr(host.batch_jobs,'_owned',lambda identifier: identifier==row['id'])
    assert not host.batch_jobs.dispatch_once()
    assert not host._job_path(second['items'][0]['job_id']).exists()
    with host.batch_jobs._db() as db:
        assert db.execute('SELECT phase FROM items WHERE child_id=?',(row['id'],)).fetchone()['phase']=='started'


def test_unrelated_unowned_queued_job_remains_readable_with_corrupt_batch_database(host):
    host.batch_jobs.path.write_bytes(b'Synthetic corrupt optional batch database')
    identifier='b'*24
    write_json(host._job_path(identifier),{'id':identifier,'status':'queued','stage':'queued',
        'created_at':'2026-01-01T00:00:00Z','mode':'example','provider':'none','depth':'quick',
        'request':{'mode':'example','provider':'none','depth':'quick'}})
    assert host.job(identifier)['status']=='interrupted'
