from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace
import hashlib
import json

import pytest

from issueintel.investigation.providers import ProviderError
from issueintel.investigation.workbench import Workbench
from issueintel.investigation.resume import replay_verification_input, validate_product_case
from test_workbench import SyntheticProvider, completed, local_server, http


class InterruptingProvider(SyntheticProvider):
    def __init__(self):super().__init__();self.fail=True;self.attempts=[]
    def complete_structured(self,messages,*,max_output_tokens,schema):
        self.attempts.append('verify' if 'reviews' in schema['properties'] else 'generate')
        if self.fail and self.attempts[-1]=='verify':raise ProviderError('Synthetic verifier network failure')
        return super().complete_structured(messages,max_output_tokens=max_output_tokens,schema=schema)


@pytest.fixture
def resume_workbench(tmp_path,monkeypatch):
    config=SimpleNamespace(reasoning_effort='high',model='openai/gpt-oss-120b',max_output_tokens=512)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    provider=InterruptingProvider();wb=Workbench(tmp_path,provider_factory=lambda *a,**k:provider)
    try:yield wb,provider
    finally:wb.close()


def approval(preview):
    return {'checkpoint_sha256':preview['checkpoint_sha256'],'confirmation':'resume_verification_with_api_cost'}


@pytest.mark.parametrize('depth',['quick','deep'])
def test_resume_replays_generation_and_runs_only_verifier(resume_workbench,depth):
    wb,provider=resume_workbench
    original=completed(wb,wb.submit({'mode':'example','provider':'groq','depth':depth})['id'])
    assert original['status']=='failed'
    saved=wb.stage_bytes(original['id'],'verification_input')
    record=json.loads(saved);preview=wb.resume_preview(original['id'])
    assert preview['available'] and preview['generation_reused']
    assert provider.attempts==['generate','verify']
    provider.fail=False
    resumed=completed(wb,wb.submit_resume(original['id'],approval(preview))['id'])
    assert resumed['status']=='completed',resumed
    assert provider.attempts==['generate','verify','verify']
    pack=json.loads(wb.result_bytes(resumed['id']))
    assert pack['annotation']==record['payload']['prepared']['annotation']
    assert pack['evidence']==record['payload']['prepared']['evidence']
    assert pack['snapshot']['commit_sha']==preview['commit_sha']
    assert pack['resumed_from']['checkpoint_sha256']==hashlib.sha256(saved).hexdigest()
    assert pack['trace'][-2]['stage']=='generation_reused'
    assert set(pack['stage_checkpoints'])=={'verification'}
    assert wb.stage_bytes(original['id'],'verification_input')==saved
    assert wb.job(original['id'])['status']=='failed'
    assert wb.submit_resume(original['id'],approval(preview))['id']==resumed['id']
    assert len(provider.attempts)==3
    with pytest.raises(ValueError):wb.preview_comment(resumed['id'])
    assert wb.stage_bytes(resumed['id'],'verification')
    wb2=Workbench(wb.root)
    try:assert wb2.result_bytes(resumed['id'])==wb.result_bytes(resumed['id'])
    finally:wb2.close()


def test_repeated_and_concurrent_resume_dispatch_is_one_attempt(resume_workbench):
    wb,provider=resume_workbench
    source=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id'])
    preview=wb.resume_preview(source['id']);provider.fail=False
    with ThreadPoolExecutor(max_workers=3) as executor:
        results=list(executor.map(lambda _:wb.submit_resume(source['id'],approval(preview)),range(3)))
    assert len({row['id'] for row in results})==1
    assert completed(wb,results[0]['id'])['status']=='completed'
    assert provider.attempts==['generate','verify','verify']


def test_completed_verifier_is_not_called_again_after_export_failure(resume_workbench):
    wb,provider=resume_workbench
    provider.fail=False
    source=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id'])
    assert 'verification' in source['stage_checkpoints']
    row=json.loads(wb._job_path(source['id']).read_text(encoding='utf-8'))
    wb._save(row,status='failed',stage='export')
    assert wb.resume_preview(source['id'])['available'] is False
    assert provider.attempts==['generate','verify']


@pytest.mark.parametrize('mutation',['policy','query','snapshot','evidence','annotation'])
def test_corrupted_checkpoint_rejected_before_provider(resume_workbench,mutation):
    wb,provider=resume_workbench
    source=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id'])
    record=json.loads(wb.stage_bytes(source['id'],'verification_input'))
    if mutation=='policy':record['payload']['policy_sha256']='f'*64
    elif mutation=='query':record['payload']['prepared']['case']['problem_statement']='changed'
    elif mutation=='snapshot':record['payload']['prepared']['snapshot']['snapshot_sha256']='f'*64
    elif mutation=='evidence':record['payload']['prepared']['evidence'][-1]['text']='changed'
    else:record['payload']['prepared']['annotation']['hypotheses'][0]['support_ids']=['missing']
    with pytest.raises(ValueError):replay_verification_input(record)
    assert provider.attempts==['generate','verify']


def test_explicit_binding_and_original_product_scope_required(resume_workbench):
    wb,provider=resume_workbench
    source=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id'])
    preview=wb.resume_preview(source['id'])
    for body in [{},approval(dict(preview,checkpoint_sha256='f'*64)),dict(approval(preview),confirmation='yes')]:
        with pytest.raises(ValueError):wb.submit_resume(source['id'],body)
    for cid,split,protocol in [('verified:case','main_dev','benchmark_base'),('live:case',None,'benchmark_base'),('followup:case',None,'benchmark_base'),('local:case',None,'issue_time')]:
        with pytest.raises(ValueError):validate_product_case({'case_id':cid,'repository':'synthetic/repo','problem_statement':'query','split':split},protocol)
    assert provider.attempts==['generate','verify']


def test_modified_parent_checkpoint_invalidates_completed_child_export(resume_workbench):
    wb,provider=resume_workbench
    source=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id']);preview=wb.resume_preview(source['id']);provider.fail=False
    child=completed(wb,wb.submit_resume(source['id'],approval(preview))['id'])
    path=wb.root/source['stage_checkpoints']['verification_input']['path']
    path.write_bytes(path.read_bytes()+b' ')
    assert wb.resume_preview(source['id'])['available'] is False
    with pytest.raises(ValueError):wb.result_bytes(child['id'])


@pytest.mark.parametrize('cancel',[False,True])
def test_queued_resume_rechecks_source_and_cancellation_before_provider(resume_workbench,cancel):
    import threading
    wb,provider=resume_workbench
    source=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id']);preview=wb.resume_preview(source['id'])
    release=threading.Event();started=threading.Event()
    def block():started.set();assert release.wait(10)
    wb.executor.submit(block);assert started.wait(5)
    try:
        child=wb.submit_resume(source['id'],approval(preview))
        if cancel:wb.cancel(child['id'])
        else:
            path=wb.root/source['stage_checkpoints']['verification_input']['path'];path.write_bytes(path.read_bytes()+b' ')
        release.set();state=completed(wb,child['id'])
        assert state['status']==('cancelled' if cancel else 'failed')
        assert provider.attempts==['generate','verify']
    finally:release.set()


def test_resume_api_session_and_preview_do_not_spend(resume_workbench):
    wb,provider=resume_workbench
    source=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id'])
    from issueintel.interfaces.web import make_server
    import threading
    server=make_server(wb.root,port=0,workbench=wb);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        route='/api/jobs/'+source['id']+'/resume';headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        assert http(server,'GET',route)[0]==403
        status,_,raw=http(server,'GET',route,headers=headers)
        assert status==200 and json.loads(raw)['available']
        assert provider.attempts==['generate','verify']
        assert http(server,'POST',route,'{}',headers)[0]==400
    finally:server.shutdown();server.server_close();thread.join(5)
