"""Automatic connection/webhook tests are synthetic, local and provider-free."""
from copy import deepcopy
from hashlib import sha256
import hmac
import json
import time
from unittest.mock import patch

import pytest

from test_github_index import connected, Reader, REPO, BASE, SHA, STAMP, issue
from issueintel.integrations.github_index import GitHubReader, GitHubReadError, RepositoryIndex
from issueintel.integrations.github_sync import GitHubSync
from issueintel.integrations.repository_updates import event_update, enqueue_update, process_updates
from issueintel.investigation.workbench import Workbench
from issueintel.runtime.io import digest


DELIVERY = '12345678-1234-1234-1234-123456789abc'
IDENTITY = {'id':42,'full_name':REPO,'private':False}


@pytest.fixture
def automatic(connected, monkeypatch):
    index, builder = connected
    snapshot = builder('HEAD')
    reader = Reader()
    original = GitHubReader
    class Transport(original):
        def get(self, url):
            value, cursor, sha = reader.get(url)
            if url == BASE:
                value = {**value,'default_branch':'main'}
            return value, cursor, sha
    monkeypatch.setattr('issueintel.integrations.github_index.GitHubReader', Transport)
    monkeypatch.setattr('issueintel.integrations.repository_updates.GitHubReader', Transport)
    monkeypatch.setattr(RepositoryIndex, '_build_snapshot', lambda self,revision:snapshot)
    return index, reader


def deliver(sync, event, payload, delivery=DELIVERY):
    raw = json.dumps(payload).encode()
    signature = 'sha256='+hmac.new(b'synthetic', raw, sha256).hexdigest()
    return sync.receive(raw, signature=signature, event=event, delivery=delivery, secret='synthetic')


def wait_ready(wb, *, limit=10):
    deadline = time.monotonic()+limit
    while time.monotonic()<deadline:
        connection=wb.github_sync.connection(REPO)
        if connection['phase']=='ready' or connection['paused']:
            return connection
        time.sleep(.02)
    pytest.fail('Automatic sync did not reach a terminal state')


def test_connect_automatically_finishes_multiple_batches_without_model_or_second_click(automatic, monkeypatch):
    index, reader = automatic
    normal = reader.get
    def get(url):
        if '/issues?' in url:
            from urllib.parse import parse_qs,urlsplit
            page=int(parse_qs(urlsplit(url).query)['page'][0])
            next_url=url.replace('page='+str(page)+'&','page='+str(page+1)+'&') if page<23 else None
            return [issue(page)], next_url, digest(url)
        return normal(url)
    reader.get=get
    wb=Workbench(index.root, provider_factory=lambda *_a,**_k:pytest.fail('Provider constructed'))
    try:
        wb.connect_repository({'repository':REPO})
        result=wait_ready(wb)
        assert result['phase']=='ready' and not result['paused']
        assert index.status()['counts']['issue']==23
        assert len(wb.repository_history())>=2
        assert all(job['provider']=='none' for job in wb.repository_history())
        assert wb.history()==[]
    finally:wb.close()


def test_issue_event_updates_canonical_api_text_and_duplicate_never_reverts_pause(automatic):
    index, reader=automatic
    sync=GitHubSync(index.root);sync.connect(REPO);sync.batch(REPO)
    reader.pages['/issues/1']= (issue(1,body='Canonical fresh Unicode text'),None,digest('response'))
    payload={'repository':IDENTITY,'action':'edited','issue':{'number':1,'body':'UNTRUSTED WEBHOOK BODY'}}
    assert deliver(sync,'issues',payload)['automatic']
    sync.batch(REPO)
    assert index.item('issue:1')['record']['body']=='Canonical fresh Unicode text'
    assert index.status()['pending_updates']==0
    calls=len(reader.calls)
    sync.change(REPO,paused=True)
    deliver(sync,'issues',payload)
    assert sync.connection(REPO)['paused']
    assert len(reader.calls)==calls
    with pytest.raises(ValueError,match='Delivery ID'):
        deliver(sync,'issues',{**payload,'action':'closed'})


def test_signature_repository_and_installation_boundaries(automatic):
    index,reader=automatic
    sync=GitHubSync(index.root);sync.connect(REPO);sync.batch(REPO)
    with pytest.raises(ValueError,match='signature'):
        sync.receive(b'not-json',signature='bad',event='issues',delivery=DELIVERY,secret='synthetic')
    payload={'repository':{**IDENTITY,'id':43},'action':'opened','issue':{'number':1}}
    with pytest.raises(ValueError,match='repository ID'):
        deliver(sync,'issues',payload)
    payload['repository']=IDENTITY;payload['installation']={'id':9}
    with pytest.raises(ValueError,match='installation'):
        deliver(sync,'issues',payload)
    assert index.status()['pending_updates']==0


def test_deleted_comment_stays_in_audit_but_disappears_from_search(automatic):
    index,reader=automatic
    comment={'id':7,'issue_url':BASE+'/issues/1','html_url':f'https://github.com/{REPO}/issues/1#issuecomment-7',
             'body':'Unique deletion sentinel','created_at':STAMP,'updated_at':STAMP}
    reader.pages['/issues/comments?']=([comment],None,digest(comment))
    sync=GitHubSync(index.root);sync.connect(REPO);sync.batch(REPO)
    assert index.search('sentinel')['results']
    deliver(sync,'issue_comment',{'repository':IDENTITY,'action':'deleted','comment':{'id':7},'issue':{'number':1}})
    reader.pages['/issues/1']=(issue(1),None,digest('issue'))
    sync.batch(REPO)
    assert not index.search('sentinel')['results']
    assert index.item('comments:7')['availability']['reason']=='not_observed_in_current_source'
    assert index.item('comments:7')['record']['body']==comment['body']


def test_rate_limit_retries_durably_without_tight_loop_and_integrity_failure_pauses(automatic):
    index,reader=automatic
    sync=GitHubSync(index.root);sync.connect(REPO)
    original=reader.get
    reader.get=lambda url: (_ for _ in ()).throw(GitHubReadError(429,120))
    sync.batch(REPO)
    state=sync.connection(REPO)
    assert not state['paused'] and state['next_attempt']>time.time()+100
    assert sync.due()==[]
    assert GitHubSync(index.root).connection(REPO)['error']=='github_http_429'
    reader.get=original
    sync.change(REPO,next_attempt=0);sync.batch(REPO)
    assert sync.connection(REPO)['phase']=='ready'
    deliver(sync,'issues',{'repository':IDENTITY,'action':'edited','issue':{'number':1}})
    reader.pages['/issues/1']=(issue(2),None,digest('wrong'))
    with pytest.raises(ValueError,match='number mismatch'):
        sync.batch(REPO)
    assert sync.connection(REPO)['paused']


def test_installation_event_connects_selected_public_repos_and_removal_pauses(automatic,monkeypatch):
    index,_=automatic;sync=GitHubSync(index.root)
    payload={'installation':{'id':99},'action':'created','repositories':[IDENTITY]}
    with pytest.raises(ValueError,match='not configured'):
        deliver(sync,'installation',payload)
    monkeypatch.setenv('GITHUB_INSTALLATION_ID','99')
    deliver(sync,'installation',payload)
    assert sync.connection(REPO)['installation_id']==99
    sync.batch(REPO)
    removed={'installation':{'id':99},'action':'removed','repositories_removed':[IDENTITY]}
    deliver(sync,'installation_repositories',removed,delivery=DELIVERY[:-1]+'d')
    assert sync.connection(REPO)['paused']
    # Redelivery of the old installation must not reconnect a removed repository.
    deliver(sync,'installation',payload)
    assert sync.connection(REPO)['paused']


def test_restart_resumes_pending_connection_and_no_network_for_idle_server(automatic):
    index,reader=automatic
    wb=Workbench(index.root)
    time.sleep(.05)
    assert reader.calls==[]
    wb.close()
    GitHubSync(index.root).connect(REPO)
    wb=Workbench(index.root)
    try:assert wait_ready(wb)['phase']=='ready'
    finally:wb.close()


def test_cli_style_workbench_does_not_reconcile_or_schedule_connected_repositories(automatic):
    index,reader=automatic;sync=GitHubSync(index.root)
    sync.connect(REPO);sync.batch(REPO)
    before=deepcopy(sync.connection(REPO));reader.calls.clear()
    assert before['phase']=='ready'
    wb=Workbench(index.root,automatic_sync=False)
    try:
        assert wb.sync_thread is None and sync.connection(REPO)==before
        assert not wb.stage_recoveries.preview('a'*24)['available']
        assert reader.calls==[] and wb.repository_history()==[]
    finally:wb.close()


def test_push_resolves_tracked_ref_not_payload_commit_and_ignores_other_branch(automatic,monkeypatch):
    index,reader=automatic;sync=GitHubSync(index.root);sync.connect(REPO);sync.batch(REPO)
    calls=[]
    snapshot=index.status()['current_snapshot']
    monkeypatch.setattr(RepositoryIndex,'_build_snapshot',lambda self,revision:(calls.append(revision) or snapshot))
    deliver(sync,'push',{'repository':IDENTITY,'ref':'refs/heads/unrelated','after':SHA,'deleted':False})
    sync.batch(REPO)
    assert calls==[]
    deliver(sync,'push',{'repository':IDENTITY,'ref':'refs/heads/main','after':SHA,'deleted':False},delivery=DELIVERY[:-1]+'d')
    sync.batch(REPO)
    assert calls==['HEAD']


def test_expected_installation_repository_id_rejected_before_snapshot(automatic,monkeypatch):
    index,reader=automatic
    sync=GitHubSync(index.root);sync.connect(REPO,repository_id=999)
    monkeypatch.setattr(RepositoryIndex,'_build_snapshot',lambda *_:pytest.fail('Wrong repo snapshot fetched'))
    with pytest.raises(ValueError,match='repository ID mismatch'):
        sync.batch(REPO)
    assert sync.connection(REPO)['paused']


def test_app_id_can_authorize_first_installation_before_installation_id_exists(automatic,monkeypatch):
    index,_=automatic;sync=GitHubSync(index.root)
    monkeypatch.delenv('GITHUB_INSTALLATION_ID',raising=False)
    monkeypatch.setenv('GITHUB_APP_ID','77')
    payload={'installation':{'id':99,'app_id':77},'action':'created','repositories':[IDENTITY]}
    deliver(sync,'installation',payload)
    assert sync.connection(REPO)['installation_id']==99
    # Subsequent App events usually include only installation.id/node_id.
    added={'installation':{'id':99},'action':'added','repositories_added':[
        {'id':43,'full_name':'owner/another','private':False}]}
    deliver(sync,'installation_repositories',added,delivery=DELIVERY[:-1]+'e')
    assert sync.connection('owner/another')['app_id']==77
    payload['installation']['app_id']=78
    with pytest.raises(ValueError,match='not configured'):
        deliver(sync,'installation',payload,delivery=DELIVERY[:-1]+'d')


def test_resume_reconciles_events_missed_while_paused(automatic):
    index,reader=automatic;sync=GitHubSync(index.root)
    reader.pages['/issues?']=([issue(1,body='Original text')],None,digest('old'))
    sync.connect(REPO);sync.batch(REPO)
    original=index.status()['run']['id']
    sync.change(REPO,paused=True)
    payload={'repository':IDENTITY,'action':'edited','issue':{'number':1}}
    assert deliver(sync,'issues',payload)['status']=='ignored'
    reader.pages['/issues?']=([issue(1,body='New reconciled text')],None,digest('new'))
    sync.connect(REPO);sync.batch(REPO)
    assert index.status()['run']['id']!=original
    assert index.item('issue:1')['record']['body']=='New reconciled text'


def test_pr_update_replaces_current_file_membership_and_preserves_old_audit(automatic):
    index,reader=automatic;sync=GitHubSync(index.root)
    pull=issue(2,pull=True)
    pull.update(base={'sha':SHA,'repo':{'full_name':REPO}},head={'sha':SHA})
    old={'filename':'old.py','sha':SHA,'status':'modified'}
    new={'filename':'new.py','sha':SHA,'status':'modified'}
    reader.pages.update({'/pulls?':([pull],None,digest(pull)),
                         '/pulls/2/files?':([old],None,digest(old))})
    sync.connect(REPO);sync.batch(REPO)
    assert index.search('old')['results']
    reader.pages['/pulls/2/files?']=([new],None,digest(new))
    reader.pages['/pulls/2/commits?']=([],None,digest('empty commits'))
    reader.pages['/pulls/2']=(pull,None,digest(pull))
    deliver(sync,'pull_request',{'repository':IDENTITY,'action':'synchronize','number':2,'pull_request':{'number':2}})
    sync.batch(REPO)
    assert not index.search('old')['results'] and index.search('new')['results']
    assert index.item('file:2:old.py')['availability']['reason']=='not_observed_in_current_source'


def test_queued_event_identity_tampering_is_rejected(automatic):
    index,reader=automatic;sync=GitHubSync(index.root);sync.connect(REPO);sync.batch(REPO)
    deliver(sync,'issues',{'repository':IDENTITY,'action':'edited','issue':{'number':1}})
    with index.connect() as db:
        row=db.execute('SELECT value FROM updates').fetchone()
        value=json.loads(row[0]);value['operations'][0]['subject']=2
        db.execute('UPDATE updates SET value=?',(json.dumps(value),))
    with pytest.raises(ValueError,match='ID mismatch'):
        sync.batch(REPO)
    assert sync.connection(REPO)['paused']
