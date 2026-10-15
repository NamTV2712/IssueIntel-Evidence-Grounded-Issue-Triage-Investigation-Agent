from copy import deepcopy
import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from issueintel.investigation.pipeline import investigate, validate_prepared_evidence
from issueintel.investigation.controller import Action
from issueintel.investigation.stages import StageCheckpoints
from issueintel.investigation.workbench import Workbench
from issueintel.repository.snapshot import build_snapshot
from issueintel.repository.graph import build_graph
from issueintel.retrieval import retrieve_evidence
from issueintel.retrieval.focused import expand_for_hypotheses
from issueintel.runtime.budget import BudgetDenied
from issueintel.interfaces.cli import main, _investigate
from test_workbench import completed, local_server, http


@pytest.fixture
def fixture_repo(tmp_path):
    path=tmp_path/'repo';path.mkdir()
    def git(*args):
        return subprocess.check_output(['git','-c','core.hooksPath=','-C',str(path),*args]).decode().strip()
    git('init','-q')
    (path/'parser.py').write_text('def parse_list(items):\n    return [item.strip() for item in items]\n',encoding='utf-8')
    (path/'fallback.py').write_text('def recover_none(sentinel):\n    return [] if sentinel is None else sentinel\n',encoding='utf-8')
    (path/'formatting.py').write_text('def render(value):\n    return str(value)\n',encoding='utf-8')
    git('add','.')
    git('-c','user.name=Synthetic','-c','user.email=test@example.invalid','-c','commit.gpgsign=false','commit','-qm','Synthetic fixture')
    case={'case_id':'synthetic:parser','repository':'synthetic/repo','revision':git('rev-parse','HEAD'),
          'problem_statement':'parser.parse_list crashes'}
    return case,path


def hypotheses(eid,focus='fallback recover_none sentinel'):
    return [{'id':f'h{i}','statement':f'{focus} may explain condition {i}',
             'support_ids':[eid],'counter_ids':[],'counter_status':'not_found',
             'falsification_test':f'Inspect {focus} guard {i}'} for i in range(3)]


class FakeModel:
    def __init__(self,focus='fallback recover_none sentinel',fail_generation=None):
        self.calls=[];self.focus=focus;self.generations=0;self.fail_generation=fail_generation

    def __call__(self,messages,max_output_tokens):
        payload=json.loads(messages[1]['content']);self.calls.append(payload)
        if 'case' in payload:
            self.generations+=1
            if self.generations==self.fail_generation:
                raise BudgetDenied('Synthetic budget denial; never retry')
            eid=next(e['id'] for e in payload['evidence'] if e['kind']=='code')
            return {'hypotheses':hypotheses(eid,self.focus),'abstention':{'label':'uncertain','reason':'Not reproduced'}}
        return {'reviews':[{'hypothesis_id':h['id'],'assessment':'uncertain','reason':'Not executed',
                           'evidence_ids':h['support_ids']} for h in payload['hypotheses']]}


def test_deep_search_adds_same_snapshot_evidence_regenerates_then_critiques(fixture_repo):
    case,path=fixture_repo;model=FakeModel()
    snapshot=build_snapshot(path,case['revision'])
    pack=investigate(case,path,snapshot=snapshot,top_k=1,complete_json=model,refinement_rounds=1)
    assert len(model.calls)==3 and model.generations==2
    before={e['id'] for e in model.calls[0]['evidence']}
    after={e['id'] for e in model.calls[1]['evidence']}
    assert before<after
    assert any(e['locator'].startswith('fallback.py:') for e in model.calls[1]['evidence'])
    assert {e['id'] for e in model.calls[2]['evidence']}==after
    assert pack['refinement']['generation_passes']==2 and pack['refinement']['retrieval_rounds']==1
    assert pack['refinement']['search']['counter_evidence_confirmed'] is False
    assert len(pack['refinement']['search']['queries'])<=3 and len(after-before)<=6
    validate_prepared_evidence(case,pack['evidence'],snapshot)
    assert all(h['correctness']=='unverified' for h in pack['annotation']['hypotheses'])
    assert pack['leakage_audit']['gold_in_inference'] is False
    for record in pack['evidence']:
        if record['kind']=='code':assert record['revision']==snapshot.commit_sha


def test_no_new_evidence_avoids_an_extra_generation(fixture_repo):
    case,path=fixture_repo;model=FakeModel(focus='parser parse_list items')
    pack=investigate(case,path,top_k=1,complete_json=model,refinement_rounds=1)
    assert len(model.calls)==2 and model.generations==1
    assert pack['refinement']['search']['added_ids']==[]
    assert pack['refinement']['stop_reason']=='no_new_evidence'


def test_offline_and_baseline_keep_existing_bounded_behavior(fixture_repo):
    case,path=fixture_repo
    offline=investigate(case,path,refinement_rounds=1)
    assert offline['annotation'] is None and offline['refinement']['generation_passes']==0
    baseline=investigate(case,path)
    assert 'refinement' not in baseline and baseline['run_id']!=offline['run_id']
    model=FakeModel();investigate(case,path,complete_json=model)
    assert len(model.calls)==2


@pytest.mark.parametrize('value',[True,-1,2,'1',None])
def test_invalid_round_limit_rejected_before_any_provider(fixture_repo,value):
    case,path=fixture_repo
    with pytest.raises(ValueError,match='round'):
        investigate(case,path,refinement_rounds=value,complete_json=lambda *a,**k:pytest.fail('Model called'))


def test_frozen_prepared_inputs_cannot_silently_expand(fixture_repo):
    case,path=fixture_repo
    with pytest.raises(ValueError,match='frozen'):
        investigate(case,path,refinement_rounds=1,prepared_evidence=[],complete_json=lambda *a,**k:pytest.fail('Model called'))


def test_seed_and_retrieval_checkpoints_survive_second_call_denial(fixture_repo,tmp_path):
    case,path=fixture_repo;model=FakeModel(fail_generation=2)
    checkpoints=StageCheckpoints(tmp_path,tmp_path/'artifacts/stages')
    with pytest.raises(BudgetDenied):
        investigate(case,path,top_k=1,complete_json=model,refinement_rounds=1,on_stage=checkpoints)
    assert set(checkpoints.receipts)=={'prepared_input','seed_generation','focused_retrieval'}
    ref=checkpoints.receipts['seed_generation'];raw=(tmp_path/ref['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==ref['sha256']
    record=json.loads(raw)
    assert record['payload']['annotation']['hypotheses'] and record['automatic_retry'] is False
    assert record['evaluation_performed'] is False
    with pytest.raises(ValueError,match='repeated'):checkpoints('seed_generation',{})
    with pytest.raises(ValueError):StageCheckpoints(tmp_path,tmp_path.parent/'outside')


def test_controller_stop_has_no_retrieval_or_critic_call(fixture_repo):
    case,path=fixture_repo;model=FakeModel()
    controller=SimpleNamespace(name='synthetic-stop',decide=lambda *a:Action.STOP)
    pack=investigate(case,path,top_k=1,complete_json=model,refinement_rounds=1,controller=controller)
    assert len(model.calls)==1
    assert pack['stop_reason']=='controller_stopped' and pack['verification']['kind']=='structural'
    assert pack['refinement']['retrieval_rounds']==0


def test_focused_search_rejects_wrong_revision_before_accepting_candidates(fixture_repo,monkeypatch):
    case,path=fixture_repo;snapshot=build_snapshot(path,case['revision']);graph=build_graph(snapshot)
    seed=retrieve_evidence(case['problem_statement'],snapshot,graph,top_k=1)
    corrupted=deepcopy(seed[0]);corrupted['revision']='0'*40
    monkeypatch.setattr('issueintel.retrieval.focused.retrieve_evidence',lambda *a,**k:[corrupted])
    with pytest.raises(ValueError,match='snapshot'):
        expand_for_hypotheses(hypotheses(seed[0]['id']),seed,snapshot,graph)


def test_added_evidence_cap_stops_search_even_with_more_seed_hypotheses(fixture_repo,monkeypatch):
    case,path=fixture_repo
    for i in range(10):(path/f'guard_{i}.py').write_text(f'def sentinel_guard_{i}(token):\n    return token\n',encoding='utf-8')
    subprocess.check_call(['git','-C',str(path),'add','.'])
    subprocess.check_call(['git','-c','core.hooksPath=','-C',str(path),'-c','user.name=Synthetic',
                           '-c','user.email=test@example.invalid','-c','commit.gpgsign=false','commit','-qm','Synthetic guards'])
    snapshot=build_snapshot(path,'HEAD');graph=build_graph(snapshot)
    seed=retrieve_evidence('parser parse_list',snapshot,graph,top_k=1,expand=0)
    candidates=retrieve_evidence('sentinel guard',snapshot,graph,top_k=10,expand=0)
    calls=[]
    def retrieve(*args,**kwargs):
        start=len(calls)*3;calls.append(args[0]);return candidates[start:start+3]
    monkeypatch.setattr('issueintel.retrieval.focused.retrieve_evidence',retrieve)
    added,trace=expand_for_hypotheses(hypotheses(seed[0]['id']),seed,snapshot,graph)
    assert len(added)==6 and len(calls)==2 and len(trace['queries'])==2


def test_strict_snapshot_and_query_proofs_survive_refinement(fixture_repo):
    case,path=fixture_repo;case=dict(case,issue_created_at='2020-01-02T00:00:00Z')
    proof='a'*64
    snapshot=build_snapshot(path,case['revision'],protocol='issue_time',cutoff=case['issue_created_at'],
        availability={'commit_sha':case['revision'],'verified':True,'source_id':'https://example.invalid/push',
                      'available_at':'2020-01-01T00:00:00Z','strict_dev_proof_sha256':proof})
    query={'schema':'main_dev_original_query_receipt_v1','verified':True,'case_id':case['case_id'],
           'created_at':case['issue_created_at'],'text_sha256':hashlib.sha256(case['problem_statement'].encode()).hexdigest(),
           'strict_dev_proof_sha256':proof,'source_urls':['https://example.invalid/issues/1']}
    pack=investigate(case,path,snapshot=snapshot,protocol='issue_time',query_receipt=query,
                     top_k=1,refinement_rounds=1,complete_json=FakeModel())
    assert pack['leakage_audit']['strict_issue_time_verified'] is True
    assert pack['case']['problem_statement']==case['problem_statement']
    assert pack['evidence'][0]['provenance']['receipt']==query
    assert all(e['available_at']<'2020-01-02T00:00:00Z' for e in pack['evidence'] if e['kind']=='code')


def test_failed_product_job_exposes_only_hash_checked_completed_stages(tmp_path,monkeypatch):
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda root:SimpleNamespace(reasoning_effort='low'))
    model=FakeModel(focus='formatting render value',fail_generation=2)
    class Provider:
        config=SimpleNamespace(max_output_tokens=512)
        model='openai/gpt-oss-120b'
        def __call__(self,*a,**k):return model(*a,**k)
    wb=Workbench(tmp_path,provider_factory=lambda *a,**k:Provider())
    try:
        job=completed(wb,wb.submit({'mode':'example','depth':'deep','provider':'groq'})['id'])
        assert job['status']=='failed' and 'result' not in job
        record=json.loads(wb.stage_bytes(job['id'],'seed_generation'))
        assert record['payload']['annotation']['provenance']['kind']=='llm'
        ref=job['stage_checkpoints']['seed_generation'];path=tmp_path/ref['path']
        path.write_bytes(path.read_bytes()+b' ')
        with pytest.raises(ValueError,match='integrity'):wb.stage_bytes(job['id'],'seed_generation')
        with pytest.raises(ValueError):wb.stage_bytes(job['id'],'../../.env')
        assert model.generations==2
    finally:wb.close()


def test_stage_download_requires_session_and_preserves_checkpoint_bytes(tmp_path,monkeypatch):
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda root:SimpleNamespace(reasoning_effort='low'))
    model=FakeModel()
    with local_server(tmp_path) as server:
        server.workbench.provider_factory=lambda *a,**k:SimpleNamespace(
            model='openai/gpt-oss-120b',config=SimpleNamespace(max_output_tokens=512),
            complete_structured=lambda messages,max_output_tokens,schema:model(messages,max_output_tokens))
        job=completed(server.workbench,server.workbench.submit({'mode':'example','provider':'groq'})['id'])
        assert job['status']=='completed'
        route='/api/jobs/'+job['id']+'/stages/seed_generation'
        assert http(server,'GET',route)[0]==403
        status,headers,raw=http(server,'GET',route,headers={'X-IssueIntel-Token':server.session_token})
        assert status==200 and 'attachment' in headers['Content-Disposition']
        assert raw==server.workbench.stage_bytes(job['id'],'seed_generation')


def test_cli_explicit_round_flag_works_without_provider(fixture_repo,tmp_path,capsys):
    case,path=fixture_repo;issue=tmp_path/'issue.txt';issue.write_text(case['problem_statement'],encoding='utf-8')
    assert main(['--root',str(tmp_path),'investigate','--repo',str(path),'--revision',case['revision'],
                 '--issue-file',str(issue),'--refinement-rounds','1'])==0
    report=capsys.readouterr().out.strip()
    from pathlib import Path
    pack=json.loads((Path(report).parent/'pack.json').read_bytes())
    assert pack['refinement']['generation_passes']==0 and pack['annotation'] is None


def test_cli_failure_prints_completed_stage_refs_without_retry(fixture_repo,tmp_path,monkeypatch,capsys):
    from pathlib import Path
    case,path=fixture_repo;issue=tmp_path/'issue.txt';issue.write_text(case['problem_statement'],encoding='utf-8')
    model=FakeModel(fail_generation=2);model.model='synthetic/mock';model.config=SimpleNamespace(max_output_tokens=512)
    monkeypatch.setattr('issueintel.investigation.providers.GroqProvider',lambda config:model)
    monkeypatch.setattr('issueintel.interfaces.cli.RuntimeConfig.load',lambda root:SimpleNamespace())
    args=SimpleNamespace(root=tmp_path,output=Path('artifacts/cli'),case_id=None,issue_file=issue,
                         revision=case['revision'],repo=str(path),provider='groq',no_critic=False,refinement_rounds=1)
    with pytest.raises(BudgetDenied):_investigate(args)
    refs=json.loads(capsys.readouterr().err)['completed_stage_checkpoints']
    assert set(refs)=={'prepared_input','seed_generation','focused_retrieval'}
    assert model.generations==2
    for ref in refs.values():assert hashlib.sha256((tmp_path/ref['path']).read_bytes()).hexdigest()==ref['sha256']
