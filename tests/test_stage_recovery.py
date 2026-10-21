"""Product stage-prefix reuse, faults, source policy and inherited call bounds."""
from copy import deepcopy
from hashlib import sha256
import json
import subprocess

import pytest

from issueintel.investigation.pipeline import investigate
from issueintel.investigation.stages import StageCheckpoints
from issueintel.investigation.planner import validate_planned
from issueintel.investigation.comparison import compare_packs
from issueintel.runtime.io import digest
from test_focused_retrieval import fixture_repo,FakeModel
from test_planned_pipeline import planned_repository,PlannedModel


class BoundaryFailure(RuntimeError):pass


class Journal:
    def __init__(self,root,name,fail_after=None):
        self.store=StageCheckpoints(root,root/'artifacts'/name);self.records=[];self.fail_after=fail_after
    def __call__(self,stage,record):
        ref=self.store(stage,record);self.records.append(deepcopy(record))
        if stage==self.fail_after:raise BoundaryFailure(stage)
        return ref


def run(fixture,model,journal,*,records=None,prior=None,**options):
    case,path=fixture
    if records is not None:records=json.loads(json.dumps(records))
    return investigate(case,path,complete_json=model,provider='groq',model='openai/gpt-oss-120b',top_k=1,
        on_stage=journal,recoverable=True,recovery_records=records,prior_provider_calls=prior,**options)


def raw_sources(root,journal):
    return {stage:(root/ref['path']).read_bytes() for stage,ref in journal.store.receipts.items()}


@pytest.mark.parametrize('boundary',['execution_input','initial_retrieval','prepared_input','seed_generation','verification_input','verification'])
def test_quick_fault_at_each_boundary_skips_every_completed_operation(fixture_repo,tmp_path,monkeypatch,boundary):
    model=FakeModel();original=Journal(tmp_path,'source',boundary)
    with pytest.raises(BoundaryFailure):run(fixture_repo,model,original,resumable=True)
    before=raw_sources(tmp_path,original);prior=len(model.calls);resumed=Journal(tmp_path,'child')
    if any(r['stage']=='initial_retrieval' for r in original.records):
        monkeypatch.setattr('issueintel.investigation.pipeline.retrieve_evidence',lambda *a,**k:pytest.fail('Completed retrieval repeated'))
    pack=run(fixture_repo,model,resumed,records=original.records,prior=prior,resumable=True)
    assert len(model.calls)==2 and model.generations==1
    recovery=pack['stage_recovery']
    assert recovery['inherited_provider_calls']==prior and recovery['new_provider_calls']==2-prior
    assert recovery['max_provider_calls']==4 and recovery['automatic_retry'] is False
    assert [r['stage'] for r in recovery['reused_stages']]==[r['stage'] for r in original.records]
    assert not set(original.store.receipts)&set(resumed.store.receipts)
    assert before==raw_sources(tmp_path,original)
    assert pack['verification']['kind']=='llm_separate_pass'
    assert compare_packs(pack,pack)
    assert all(h['correctness']=='unverified' for h in pack['annotation']['hypotheses'])
    if 'seed_generation' in before:
        assert pack['annotation']==json.loads(before['seed_generation'])['payload']['annotation']


@pytest.mark.parametrize('boundary',['seed_generation','focused_retrieval','refined_generation','verification_input','verification'])
def test_deep_reuses_seed_retrieval_and_regeneration_without_repeated_paid_calls(fixture_repo,tmp_path,monkeypatch,boundary):
    model=FakeModel();original=Journal(tmp_path,'source',boundary)
    with pytest.raises(BoundaryFailure):run(fixture_repo,model,original,refinement_rounds=1,resumable=True)
    prior=len(model.calls);resumed=Journal(tmp_path,'child')
    monkeypatch.setattr('issueintel.investigation.pipeline.retrieve_evidence',lambda *a,**k:pytest.fail('Initial retrieval repeated'))
    if 'focused_retrieval' in original.store.receipts:
        monkeypatch.setattr('issueintel.investigation.pipeline.expand_for_hypotheses',lambda *a,**k:pytest.fail('Completed focused retrieval repeated'))
    pack=run(fixture_repo,model,resumed,records=original.records,prior=prior,refinement_rounds=1,resumable=True)
    assert model.generations==2 and len(model.calls)==3
    assert pack['refinement']['generation_passes']==2 and pack['refinement']['retrieval_rounds']==1
    assert pack['stage_recovery']['new_provider_calls']==3-prior and pack['stage_recovery']['max_provider_calls']==6
    assert not set(original.store.receipts)&set(resumed.store.receipts)
    assert compare_packs(pack,pack)


@pytest.mark.parametrize('boundary',['planned_input','seed_generation','planned_retrieval_1','planned_generation_1',
    'planned_retrieval_2','planned_generation_2','planned_retrieval_3','planned_generation_3','verification','planned_complete'])
def test_all_three_planned_rounds_resume_at_saved_boundaries(planned_repository,tmp_path,monkeypatch,boundary):
    model=PlannedModel();original=Journal(tmp_path,'source',boundary)
    with pytest.raises(BoundaryFailure):run(planned_repository,model,original,planned_workflow=True)
    before=raw_sources(tmp_path,original);prior=len(model.calls);resumed=Journal(tmp_path,'child')
    monkeypatch.setattr('issueintel.investigation.pipeline.retrieve_evidence',lambda *a,**k:pytest.fail('Initial retrieval repeated'))
    import issueintel.investigation.pipeline as pipeline
    search=pipeline.expand_for_hypotheses;calls=[]
    def counted(*a,**k):calls.append(1);return search(*a,**k)
    monkeypatch.setattr(pipeline,'expand_for_hypotheses',counted)
    pack=run(planned_repository,model,resumed,records=original.records,prior=prior,planned_workflow=True)
    plan=validate_planned(pack)
    assert len(plan['rounds'])==3 and model.generations==4 and len(model.calls)==5
    completed_searches=sum(r['stage'].startswith('planned_retrieval_') for r in original.records)
    assert len(calls)==3-completed_searches
    assert pack['stage_recovery']['new_provider_calls']==5-prior
    assert pack['stage_recovery']['max_provider_calls']==9
    assert before==raw_sources(tmp_path,original)


@pytest.mark.parametrize('policy',['bounded_planner_v2','bounded_planner_v3'])
def test_gap_and_waiting_policies_reuse_a_completed_search_without_changing_their_semantics(planned_repository,tmp_path,policy):
    model=PlannedModel(focuses=['parser parse_list items']);original=Journal(tmp_path,'source','planned_retrieval_1')
    with pytest.raises(BoundaryFailure):run(planned_repository,model,original,planned_workflow=True,planner_policy=policy)
    prior=len(model.calls)
    pack=run(planned_repository,model,Journal(tmp_path,'child'),records=original.records,prior=prior,
        planned_workflow=True,planner_policy=policy)
    plan=validate_planned(pack)
    assert plan['policy']==policy and model.generations==1
    assert pack['stage_recovery']['inherited_provider_calls']==prior
    if policy.endswith('v3'):
        assert plan['waiting'] is not None and plan['stop_reason']=='waiting_for_user'
        assert len(model.calls)==1 and pack['stage_recovery']['new_provider_calls']==0


@pytest.mark.parametrize('change',['query','model','top_k','context','policy','gap','reordered','unknown_calls','underreported_calls','over_limit_calls'])
def test_source_recipe_sequence_and_unknown_accounting_deny_before_provider(fixture_repo,tmp_path,monkeypatch,change):
    model=FakeModel();original=Journal(tmp_path,'source','seed_generation')
    with pytest.raises(BoundaryFailure):run(fixture_repo,model,original)
    records=deepcopy(original.records);case,path=deepcopy(fixture_repo[0]),fixture_repo[1];calls=len(model.calls);kwargs={}
    if change=='query':case['problem_statement']+=' edited'
    elif change=='model':records[0]['model']='another-model'
    elif change=='top_k':records[0]['payload']['context']['options']['top_k']=12
    elif change=='context':records[0]['payload']['context']['snapshot']['snapshot_sha256']='f'*64
    elif change=='policy':monkeypatch.setattr('issueintel.investigation.stage_recovery.policy_sha256',lambda:'f'*64)
    elif change=='gap':records.pop(1)
    elif change=='reordered':records[1:]=list(reversed(records[1:]))
    prior=None if change=='unknown_calls' else calls
    if change=='underreported_calls':prior=0
    if change=='over_limit_calls':prior=5
    with pytest.raises(ValueError):run((case,path),model,Journal(tmp_path,'child'),records=records,prior=prior,**kwargs)
    assert len(model.calls)==calls


def test_changed_head_still_resumes_the_original_pinned_commit(fixture_repo,tmp_path):
    case,path=fixture_repo;case=dict(case,revision='HEAD');model=FakeModel();original=Journal(tmp_path,'source','seed_generation')
    with pytest.raises(BoundaryFailure):run((case,path),model,original)
    commit=original.records[0]['commit_sha']
    (path/'parser.py').write_text('def parse_list(items):\n    return []\n')
    subprocess.run(['git','-C',str(path),'add','.'],check=True,capture_output=True)
    subprocess.run(['git','-c','user.name=Synthetic','-c','user.email=test@example.invalid','-c','commit.gpgsign=false','-C',str(path),'commit','-qm','New synthetic revision'],check=True,capture_output=True)
    pack=run((case,path),model,Journal(tmp_path,'child'),records=original.records,prior=len(model.calls))
    assert pack['snapshot']['commit_sha']==commit and pack['case']['revision']=='HEAD'
    assert all(e['revision']==commit for e in pack['evidence'] if e['kind']=='code')


def test_failed_transport_counts_toward_inherited_budget_and_stages_are_not_regenerated(fixture_repo,tmp_path):
    model=FakeModel();original=Journal(tmp_path,'source','seed_generation')
    with pytest.raises(BoundaryFailure):run(fixture_repo,model,original)
    # The owning interface must include attempted/failed calls, not infer usage
    # from the successful prefix. With 3/4 consumed, only the critic fits.
    pack=run(fixture_repo,model,Journal(tmp_path,'child'),records=original.records,prior=3)
    assert pack['stage_recovery']['inherited_provider_calls']==3 and pack['stage_recovery']['new_provider_calls']==1
    with pytest.raises(ValueError,match='budget exhausted'):
        run(fixture_repo,model,Journal(tmp_path,'exhausted'),records=original.records,prior=4)
    assert model.generations==1 and len(model.calls)==2


def test_policy_change_during_call_stops_before_output_repair_or_next_call(fixture_repo,tmp_path,monkeypatch):
    model=FakeModel();original=model
    def changed(*a,**k):
        result=original(*a,**k)
        monkeypatch.setattr('issueintel.investigation.stage_recovery.policy_sha256',lambda:'f'*64)
        return result
    journal=Journal(tmp_path,'source')
    with pytest.raises(ValueError,match='policy changed'):run(fixture_repo,changed,journal)
    assert len(model.calls)==1 and 'seed_generation' not in journal.store.receipts
    assert set(journal.store.receipts)=={'execution_input','initial_retrieval','prepared_input'}


def test_cached_code_must_match_actual_snapshot_bytes_even_with_resealed_record_chain(fixture_repo,tmp_path):
    model=FakeModel();original=Journal(tmp_path,'source','initial_retrieval')
    with pytest.raises(BoundaryFailure):run(fixture_repo,model,original)
    records=deepcopy(original.records);evidence=records[1]['payload']['evidence'][0]
    evidence['text']='Forged source';evidence['sha256']=sha256(evidence['text'].encode()).hexdigest()
    with pytest.raises(ValueError,match='snapshot bytes'):
        run(fixture_repo,model,Journal(tmp_path,'child'),records=records,prior=0)
    assert not model.calls


@pytest.mark.parametrize('change',[
    {'split':'main_dev'},{'split':'main_test'},{'split':None},{'instance_id':'synthetic__restricted'},
    {'case_id':'verified:restricted'},{'case_id':'live:restricted'},{'case_id':'followup:restricted'},
])
def test_recovery_scope_is_rejected_before_any_git_or_model(fixture_repo,tmp_path,monkeypatch,change):
    case,path=fixture_repo;case=dict(case,**change);model=FakeModel()
    monkeypatch.setattr('issueintel.investigation.pipeline.build_snapshot',lambda *a,**k:pytest.fail('Restricted source read Git'))
    with pytest.raises(ValueError):run((case,path),model,Journal(tmp_path,'source'))
    assert not model.calls


def test_durable_attempt_sink_runs_before_provider_and_records_failed_attempts(fixture_repo,tmp_path):
    model=FakeModel();attempts=[];journal=Journal(tmp_path,'source')
    def fail(*a,**k):
        assert attempts[-1]['provider_calls']==1 and attempts[-1]['stage']=='seed_generation'
        raise RuntimeError('Synthetic failed provider attempt')
    with pytest.raises(RuntimeError,match='failed provider'):
        run(fixture_repo,fail,journal,on_recovery_attempt=lambda value:attempts.append(deepcopy(value)))
    assert len(attempts)==1 and attempts[0]['max_provider_calls']==4
    resumed_attempts=[]
    pack=run(fixture_repo,model,Journal(tmp_path,'child'),records=journal.records,prior=attempts[-1]['provider_calls'],
        on_recovery_attempt=lambda value:resumed_attempts.append(deepcopy(value)))
    assert [a['provider_calls'] for a in resumed_attempts]==[2,3]
    assert [a['stage'] for a in resumed_attempts]==['seed_generation','verification']
    assert pack['stage_recovery']['inherited_provider_calls']==1 and pack['stage_recovery']['new_provider_calls']==2


def test_unwritable_attempt_sink_never_reaches_the_provider(fixture_repo,tmp_path):
    model=FakeModel();journal=Journal(tmp_path,'source')
    def unavailable(_):raise OSError('Synthetic durable journal unavailable')
    with pytest.raises(OSError):run(fixture_repo,model,journal,on_recovery_attempt=unavailable)
    assert not model.calls and 'seed_generation' not in journal.store.receipts


def test_structured_groq_mock_and_schema_repair_use_separate_owned_receipts(fixture_repo,tmp_path,monkeypatch):
    from io import BytesIO
    from issueintel.investigation.providers import GroqProvider
    from test_runtime import configured
    monkeypatch.setenv('GROQ_API_KEY','synthetic-stage-recovery-key')
    config=configured(tmp_path);model=FakeModel();http_calls=[];receipts={};attempts=[]
    def transport(request,**kwargs):
        body=json.loads(request.data);http_calls.append(body)
        # Invalid schema on the first HTTP call; the second succeeds.
        answer={'unexpected':'malformed'} if len(http_calls)==1 else model(body['messages'],body['max_completion_tokens'])
        return BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}).encode())
    provider=GroqProvider(config,transport=transport,on_receipt=lambda value:receipts.update({value['reservation_id']:deepcopy(value)}))
    journal=Journal(tmp_path,'source','seed_generation')
    with pytest.raises(BoundaryFailure):run(fixture_repo,provider,journal,on_recovery_attempt=lambda a:attempts.append(deepcopy(a)))
    assert len(http_calls)==len(receipts)==2 and attempts[-1]['provider_calls']==2
    assert journal.records[-1]['payload']['annotation']['provenance']['repair_count']==1
    old=deepcopy(receipts);new_receipts={}
    provider=GroqProvider(config,transport=transport,on_receipt=lambda value:new_receipts.update({value['reservation_id']:deepcopy(value)}))
    pack=run(fixture_repo,provider,Journal(tmp_path,'child'),records=journal.records,prior=2)
    assert len(http_calls)==3 and len(new_receipts)==1 and receipts==old
    assert set(old).isdisjoint(new_receipts) and all(r['status']=='settled' for r in [*old.values(),*new_receipts.values()])
    assert pack['annotation']['provenance']['repair_count']==1
    assert pack['stage_recovery']['inherited_provider_calls']==2 and pack['stage_recovery']['new_provider_calls']==1


@pytest.mark.parametrize('options',[{}, {'refinement_rounds':1}, {'planned_workflow':True,'planner_policy':'bounded_planner_v3'}])
def test_offline_recovery_reuses_initial_retrieval_with_zero_model_or_cost_calls(fixture_repo,tmp_path,monkeypatch,options):
    case,path=fixture_repo;journal=Journal(tmp_path,'source','initial_retrieval')
    with pytest.raises(BoundaryFailure):
        investigate(case,path,top_k=1,on_stage=journal,recoverable=True,**options)
    monkeypatch.setattr('issueintel.investigation.pipeline.retrieve_evidence',lambda *a,**k:pytest.fail('Completed retrieval repeated'))
    attempts=[];pack=investigate(case,path,top_k=1,on_stage=Journal(tmp_path,'child'),recoverable=True,
        recovery_records=journal.records,prior_provider_calls=0,on_recovery_attempt=attempts.append,**options)
    assert pack['annotation'] is None and pack['verification'] is None and not attempts
    assert pack['stage_recovery']['max_provider_calls']==0 and pack['stage_recovery']['new_provider_calls']==0
    assert pack['stop_reason']=='provider_not_configured'
    if options.get('planned_workflow'):assert validate_planned(pack)['provider_calls']==0


def test_policy_changed_in_attempt_sink_does_not_reach_provider(fixture_repo,tmp_path,monkeypatch):
    model=FakeModel();attempts=[]
    def sink(attempt):
        attempts.append(deepcopy(attempt));monkeypatch.setattr('issueintel.investigation.stage_recovery.policy_sha256',lambda:'f'*64)
    with pytest.raises(ValueError,match='policy changed'):
        run(fixture_repo,model,Journal(tmp_path,'source'),on_recovery_attempt=sink)
    assert len(attempts)==1 and not model.calls


def test_declared_transport_model_cannot_differ_from_the_recovery_recipe(fixture_repo,tmp_path):
    model=FakeModel();model.model='another-model'
    with pytest.raises(ValueError,match='transport model'):
        run(fixture_repo,model,Journal(tmp_path,'source'))
    assert not model.calls


@pytest.mark.parametrize('mutation',['unknown_stage','extra_proof_field','payload_shape','false_boolean','wrong_round','changed_verifier_kind'])
def test_resealed_stage_shape_or_semantics_cannot_change_the_replayed_workflow(planned_repository,tmp_path,mutation):
    model=PlannedModel();boundary='verification' if mutation=='changed_verifier_kind' else 'planned_generation_1'
    original=Journal(tmp_path,'source',boundary)
    with pytest.raises(BoundaryFailure):run(planned_repository,model,original,planned_workflow=True)
    records=deepcopy(original.records);last=records[-1];prior=len(model.calls)
    if mutation=='unknown_stage':last['stage']='invented_stage'
    elif mutation=='extra_proof_field':last['recovery']['override_policy']=True
    elif mutation=='payload_shape':last['payload']=[]
    elif mutation=='false_boolean':last['automatic_retry']=0
    elif mutation=='wrong_round':last['payload']['round']=3
    else:
        value=last['payload']['verification'];value['kind']='structural';value.pop('reviews')
    with pytest.raises(ValueError):run(planned_repository,model,Journal(tmp_path,'child'),records=records,prior=prior,planned_workflow=True)
    assert len(model.calls)==prior
