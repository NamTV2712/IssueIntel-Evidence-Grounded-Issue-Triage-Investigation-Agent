"""Durable explicitly confirmed memory-assisted product investigations."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
import subprocess
import threading

import pytest

from issueintel.investigation.memory_selection import validate_saved_selection
from issueintel.interfaces.web import make_server
from issueintel.runtime.io import digest
from test_product_memory import memory_workbench,bound,architecture
from test_workbench import completed,http


@pytest.fixture
def execution_source(memory_workbench):
    wb,source,model=memory_workbench;capture=architecture(wb,source);fact=capture['memory']['facts'][0]
    selections=[{'capture_id':capture['receipt']['id'],'receipt_sha256':capture['receipt']['sha256'],
                 'item_id':fact['id'],'item_sha256':fact['sha256']}]
    preview=wb.memory.preview(source['id'],bound(wb,source,selections=selections))
    assert preview['available']
    return wb,source,model,preview


def request_for(source,preview,*,provider='none',depth='quick'):
    return {'parent_job_id':source['id'],'parent_pack_sha256':preview['source']['source_pack_sha256'],
            'selection_sha256':preview['selection']['sha256'],'selections':deepcopy(preview['selection']['selections']),
            'provider':provider,'depth':depth,'confirmation':
                'start_memory_investigation_with_api_cost' if provider=='groq' else 'start_memory_investigation'}


def test_offline_execution_uses_exact_preview_retrieves_current_code_and_keeps_source_immutable(execution_source):
    wb,source,model,preview=execution_source;raw=wb.result_bytes(source['id']);calls=len(model.calls)
    request=request_for(source,preview);child=completed(wb,wb.memory_followups.submit(request)['id'])
    assert child['status']=='completed',child
    pack=json.loads(wb.result_bytes(child['id']));parent=json.loads(raw)
    link=pack['followup'];selection=link['memory_selection']
    assert selection==preview['selection'] and link['parent_pack_sha256']==sha256(raw).hexdigest()
    assert pack['snapshot']==parent['snapshot'] and pack['annotation'] is None
    assert pack['case']['problem_statement']==parent['case']['problem_statement']+'\n\n[User follow-up context; unverified]\n'+selection['additional_context']
    assert pack['intake']==wb._inspect_query(pack['case']['problem_statement'],wb._cue_text(parent))
    assert all(e['revision']==parent['snapshot']['commit_sha'] for e in pack['evidence'] if e['kind']=='code')
    assert len(model.calls)==calls and wb.result_bytes(source['id'])==raw
    assert not pack['leakage_audit']['strict_issue_time_verified']
    assert wb.memory_followups.submit(request)['id']==child['id']
    assert wb.intake(child['id'])['check_journal_available']
    report=json.loads(wb.report_bytes(child['id']))
    assert 'selected_memory' in next(row['details'] for row in report['provenance'] if row['kind']=='followup')
    assert {row['job_id'] for row in wb.dossier(child['id'])['runs']}=={source['id'],child['id']}


def test_paid_execution_generates_fresh_hypotheses_with_new_evidence_ids_and_owned_receipts(execution_source):
    wb,source,model,preview=execution_source;before=len(model.calls)
    child=completed(wb,wb.memory_followups.submit(request_for(source,preview,provider='groq'))['id'])
    assert child['status']=='completed',child
    pack=json.loads(wb.result_bytes(child['id']))
    assert len(model.calls)==before+2 and pack['annotation'] is not None
    assert pack['verification']['kind']=='llm_separate_pass'
    assert child['accounting']['reservations']==2 and child['accounting']['tracking_available']
    assert all(r['status']=='settled' for r in child['accounting']['receipts'])
    generated=model.calls[before]
    assert preview['selection']['additional_context'] in generated['case']['problem_statement']
    assert all(h['correctness']=='unverified' for h in pack['annotation']['hypotheses'])
    assert all(eid in {e['id'] for e in pack['evidence']} for h in pack['annotation']['hypotheses'] for eid in h['support_ids'])


def test_duplicate_execution_after_restart_or_policy_change_needs_no_git_store_or_model(execution_source,monkeypatch):
    wb,source,model,preview=execution_source;request=request_for(source,preview);child=completed(wb,wb.memory_followups.submit(request)['id'])
    raw=wb.result_bytes(child['id']);before=len(model.calls)
    monkeypatch.setattr('issueintel.investigation.memory_followups.policy_sha256',lambda:'f'*64)
    monkeypatch.setattr('issueintel.investigation.question_jobs.replay_snapshot',lambda *a:pytest.fail('Saved replay read Git'))
    monkeypatch.setattr(wb.memory.store,'read',lambda *a:pytest.fail('Saved replay read capture store'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Saved replay constructed provider')
    from issueintel.investigation.memory_followups import MemoryFollowups
    wb.memory_followups=MemoryFollowups(wb)
    assert wb.memory_followups.submit(request)['id']==child['id']
    assert wb.result_bytes(child['id'])==raw and wb.report_bytes(child['id'])
    assert len(wb.dossier(child['id'])['runs'])==2 and len(model.calls)==before


def test_same_preview_cannot_change_provider_or_depth_to_mint_a_second_execution(execution_source):
    wb,source,_,preview=execution_source;request=request_for(source,preview);child=completed(wb,wb.memory_followups.submit(request)['id'])
    for altered in [request_for(source,preview,provider='groq'),request_for(source,preview,depth='deep')]:
        with pytest.raises(ValueError,match='different execution intent'):wb.memory_followups.submit(altered)
    assert wb.memory_followups.submit(request)['id']==child['id']


def test_concurrent_submits_create_one_child_and_keep_one_selection_receipt(execution_source):
    wb,source,_,preview=execution_source;request=request_for(source,preview)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids=list(pool.map(lambda _:wb.memory_followups.submit(deepcopy(request))['id'],range(4)))
    assert len(set(ids))==1 and completed(wb,ids[0])['status']=='completed'
    with wb.memory_followups._db() as db:assert db.execute('SELECT count(*) FROM intents').fetchone()[0]==1


@pytest.mark.parametrize('change',[
    lambda r:r.pop('confirmation'),lambda r:r.update(confirmation='yes'),
    lambda r:r.update(provider='typesafe'),lambda r:r.update(depth='planned'),
    lambda r:r.update(parent_job_id='../.env'),lambda r:r.update(policy_sha256='a'*64),
    lambda r:r.update(provider=['groq']),lambda r:r.update(depth=['quick']),
])
def test_public_request_requires_explicit_canonical_confirmation_and_modes(execution_source,change):
    wb,source,model,preview=execution_source;request=request_for(source,preview,provider='groq');before=wb.history();calls=len(model.calls);change(request)
    with pytest.raises(ValueError):wb.memory_followups.submit(request)
    assert wb.history()==before and len(model.calls)==calls


@pytest.mark.parametrize('change',[
    lambda r:r.update(parent_pack_sha256='f'*64),
    lambda r:r.update(selection_sha256='f'*64),
    lambda r:r['selections'][0].update(item_sha256='f'*64),
    lambda r:r['selections'][0].update(receipt_sha256='f'*64),
])
def test_stale_source_or_selection_is_rejected_before_queue_or_provider(execution_source,change):
    wb,source,model,preview=execution_source;request=request_for(source,preview);before=wb.history();calls=len(model.calls);change(request)
    with pytest.raises(ValueError):wb.memory_followups.submit(request)
    assert wb.history()==before and len(model.calls)==calls
    with wb.memory_followups._db() as db:assert db.execute('SELECT count(*) FROM intents').fetchone()[0]==0


def test_worker_failure_consumes_intent_without_automatic_retry(execution_source,monkeypatch):
    wb,source,model,preview=execution_source;request=request_for(source,preview,provider='groq');before=len(model.calls)
    wb.provider_factory=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('Synthetic provider unavailable'))
    child=completed(wb,wb.memory_followups.submit(request)['id']);assert child['status']=='failed'
    monkeypatch.setattr('issueintel.investigation.question_jobs.replay_snapshot',lambda *a:pytest.fail('Retry admission read Git'))
    assert wb.memory_followups.submit(request)['id']==child['id'] and len(model.calls)==before


@pytest.mark.parametrize('tamper',['policy','parent','capture'])
def test_inflight_source_mutation_stops_next_call_and_retains_first_call_receipt(execution_source,monkeypatch,tamper):
    wb,source,model,preview=execution_source;before=len(model.calls);original_factory=wb.provider_factory
    original_policy=__import__('issueintel.investigation.memory_followups',fromlist=['policy_sha256']).policy_sha256
    def factory(*args,**kwargs):
        provider=original_factory(*args,**kwargs);transport=provider.transport
        def changed(*a,**k):
            result=transport(*a,**k)
            if tamper=='policy':monkeypatch.setattr('issueintel.investigation.memory_followups.policy_sha256',lambda:'f'*64)
            elif tamper=='parent':
                row=json.loads(wb._job_path(source['id']).read_bytes());path=wb.root/row['result']['pack.json']['path'];path.write_bytes(path.read_bytes()+b' ')
            else:
                with wb.memory.store._db(write=True) as db:db.execute('UPDATE objects SET payload=?',(b'{}',))
            return result
        provider.transport=changed;return provider
    wb.provider_factory=factory
    child=completed(wb,wb.memory_followups.submit(request_for(source,preview,provider='groq'))['id'])
    assert child['status']=='failed' and 'result' not in child
    assert len(model.calls)==before+1 and child['accounting']['reservations']==1
    assert child['accounting']['receipts'][0]['status']=='settled'
    if tamper=='policy':monkeypatch.setattr('issueintel.investigation.memory_followups.policy_sha256',original_policy)


def test_missing_receipt_or_forged_saved_intent_cannot_be_read_as_a_child(execution_source):
    wb,source,_,preview=execution_source;child=completed(wb,wb.memory_followups.submit(request_for(source,preview))['id'])
    with wb.memory_followups._db() as db:db.execute('DELETE FROM intents');db.commit()
    with pytest.raises(ValueError,match='intent'):wb.result_bytes(child['id'])


def test_followup_after_memory_retains_cues_and_excludes_own_lineage_from_related(execution_source):
    wb,source,_,preview=execution_source;child=completed(wb,wb.memory_followups.submit(request_for(source,preview))['id'])
    assert child['status']=='completed'
    next_run=completed(wb,wb.submit({'mode':'followup','parent_job_id':child['id'],'additional_context':'Environment: Python 3.12'})['id'])
    assert next_run['status']=='completed'
    assert not wb.related({'job_id':next_run['id']})['results']
    assert len(wb.dossier(next_run['id'])['runs'])==3
    assert wb.intake(next_run['id'])['fields']['environment']['status']=='mentioned'


def test_http_execution_is_session_and_origin_guarded_and_idempotent(execution_source):
    wb,source,_,preview=execution_source;server=make_server(wb.root,port=0,workbench=wb)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        body=json.dumps(request_for(source,preview)).encode();route='/api/memory-investigations'
        assert http(server,'POST',route,body,headers={'Content-Type':'application/json'})[0]==403
        headers={'Content-Type':'application/json','X-IssueIntel-Token':server.session_token}
        assert http(server,'POST',route,body,headers=headers|{'Origin':'https://invalid.example'})[0]==403
        status,_,raw=http(server,'POST',route,body,headers=headers);assert status==202;first=json.loads(raw)
        assert completed(wb,first['id'])['status']=='completed'
        status,_,raw=http(server,'POST',route,body,headers=headers);assert status==202 and json.loads(raw)['id']==first['id']
    finally:server.shutdown();server.server_close();thread.join()


def test_saved_selection_replay_requires_no_store_graph_git_or_current_policy(execution_source,monkeypatch):
    wb,source,_,preview=execution_source;raw=wb.result_bytes(source['id'])
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('Saved selection ran Git'))
    monkeypatch.setattr('issueintel.repository.memory.build_graph',lambda *a:pytest.fail('Saved selection ran AST'))
    assert validate_saved_selection(preview['selection'],raw)==preview['selection']
    changed=deepcopy(preview['selection']);changed['items'][0]['hint']['runtime_verified']=True
    changed['sha256']=digest({k:v for k,v in changed.items() if k!='sha256'})
    with pytest.raises(ValueError):validate_saved_selection(changed,raw)


def test_selected_prior_hypothesis_remains_a_hint_and_not_a_new_code_citation(execution_source):
    wb,source,model,preview=execution_source
    lesson=wb.memory.capture(source['id'],bound(wb,source,kind='lesson',hypothesis_ids=['h0'],note='Unverified earlier idea'))
    h=lesson['memory']['hypotheses'][0]
    choices=preview['selection']['selections']+[{'capture_id':lesson['receipt']['id'],'receipt_sha256':lesson['receipt']['sha256'],
        'item_id':h['id'],'item_sha256':digest(h)}]
    preview=wb.memory.preview(source['id'],bound(wb,source,selections=choices));calls=len(model.calls)
    child=completed(wb,wb.memory_followups.submit(request_for(source,preview,provider='groq'))['id'])
    assert child['status']=='completed',child
    pack=json.loads(wb.result_bytes(child['id']));hint=pack['followup']['memory_selection']['items'][1]['hint']
    assert hint['kind']=='prior_hypothesis' and hint['historical_eligible'] is False
    assert not {'support_ids','counter_ids','correctness','source_quality'}&hint.keys()
    assert h['statement'] in model.calls[calls]['case']['problem_statement']
    assert not any(e['kind']=='memory' for e in pack['evidence'])
    assert not pack['leakage_audit']['strict_issue_time_verified']


def test_cancelled_paid_attempt_keeps_charged_receipt_and_cannot_retry(execution_source):
    wb,source,model,preview=execution_source;original_factory=wb.provider_factory;entered=threading.Event();release=threading.Event();calls=len(model.calls)
    def factory(*args,**kwargs):
        provider=original_factory(*args,**kwargs);transport=provider.transport
        def blocked(*a,**k):
            entered.set();assert release.wait(10);return transport(*a,**k)
        provider.transport=blocked;return provider
    wb.provider_factory=factory;request=request_for(source,preview,provider='groq')
    child=wb.memory_followups.submit(request)
    try:
        assert entered.wait(10);wb.cancel(child['id'])
    finally:release.set()
    child=completed(wb,child['id']);assert child['status']=='cancelled' and 'result' not in child
    assert child['accounting']['reservations']==1 and len(model.calls)==calls+1
    assert wb.memory_followups.submit(request)['id']==child['id']
    assert len(model.calls)==calls+1


def test_memory_handoff_preserves_exact_hints_and_verifies_on_an_independent_python(execution_source,tmp_path,monkeypatch):
    from test_handoff import exported,contents
    from issueintel.investigation.portable_verify import verify
    import sys
    wb,source,model,preview=execution_source;child=completed(wb,wb.memory_followups.submit(request_for(source,preview))['id'])
    calls=len(model.calls)
    monkeypatch.setattr(wb.memory.store,'read',lambda *a:pytest.fail('Export read the live memory store'))
    monkeypatch.setattr('issueintel.investigation.question_jobs.replay_snapshot',lambda *a:pytest.fail('Export read Git'))
    manifest,raw=exported(wb,child['id']);files=contents(raw);path=tmp_path/'memory.zip';path.write_bytes(raw)
    assert manifest['automatic_ancestor_job_ids']==[source['id']]
    assert json.loads(files['runs/'+child['id']+'/memory-selection.json'])==preview['selection']
    assert verify(path)['runs']==2 and len(model.calls)==calls
    program=tmp_path/'verify.py';program.write_bytes(files['verify.py'])
    result=subprocess.run([sys.executable,'-I',str(program),str(path),'--expected-sha256',sha256(raw).hexdigest()],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['semantic_verified'] is False


@pytest.mark.parametrize('mutation',['missing','context','promoted'])
def test_portable_memory_attachment_is_required_even_if_outer_manifest_is_resealed(execution_source,tmp_path,mutation):
    from test_handoff import exported,contents,repack
    from issueintel.investigation.portable_verify import verify,encoded,sha
    wb,source,_,preview=execution_source;child=completed(wb,wb.memory_followups.submit(request_for(source,preview))['id'])
    _,raw=exported(wb,child['id']);files=contents(raw);name='runs/'+child['id']+'/memory-selection.json'
    if mutation=='missing':files.pop(name)
    else:
        receipt=json.loads(files[name])
        if mutation=='context':receipt['additional_context']='Different selected memory'
        else:receipt['causal_verified']=True
        receipt['sha256']=digest({k:v for k,v in receipt.items() if k!='sha256'});files[name]=encoded(receipt)
    manifest=json.loads(files['manifest.json']);manifest['entries']=[r for r in manifest['entries'] if r['path'] in files]
    for row in manifest['entries']:row.update(bytes=len(files[row['path']]),sha256=sha(files[row['path']]))
    manifest['sha256']=digest({k:v for k,v in manifest.items() if k!='sha256'});files['manifest.json']=encoded(manifest)
    path=tmp_path/'changed.zip';path.write_bytes(repack(files))
    with pytest.raises(ValueError,match='selected memory attachment'):verify(path)


@pytest.mark.parametrize('mutation',['query','commit','truth','receipt','hint'])
def test_portable_memory_replays_query_commit_receipt_and_truth_scope(execution_source,mutation):
    from issueintel.investigation.portable_verify import _verify_memory
    wb,source,_,preview=execution_source;child=completed(wb,wb.memory_followups.submit(request_for(source,preview))['id'])
    pack=json.loads(wb.result_bytes(child['id']));parent_raw=wb.result_bytes(source['id']);parent=json.loads(parent_raw)
    _verify_memory(pack,parent,parent_raw,child['id'])
    value=pack['followup']['memory_selection']
    if mutation=='query':pack['case']['problem_statement']+=' changed'
    elif mutation=='commit':pack['snapshot']['commit_sha']='f'*40
    elif mutation=='truth':value['items'][0]['hint']['runtime_verified']=True
    elif mutation=='receipt':value['selections'][0]['receipt_sha256']='f'*64
    else:value['items'][0]['hint']['source']['qualname']='Unselected source'
    value['sha256']=digest({k:v for k,v in value.items() if k!='sha256'})
    with pytest.raises(ValueError):_verify_memory(pack,parent,parent_raw,child['id'])
