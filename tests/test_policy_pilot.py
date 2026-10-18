"""Synthetic tool parity, check partitions and durable paid-stage ownership."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import json
import shutil

import pytest

from issueintel.investigation.discriminating_checks import validate_findings,compare_checks,candidate_files
from issueintel.investigation.policy_agent import run_policy_agent,ARMS,LIMITS,ACTION_SCHEMA
from issueintel.benchmarks import policy_pilot as pilot
from issueintel.repository.snapshot import build_snapshot
from issueintel.runtime.budget import ModelPrice,BudgetPolicy
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import digest,write_json
from test_pipeline import repository


def findings(payload):
    evidence=payload['evidence'];eid=evidence[0]['id']
    return {'hypotheses':[{'id':'H'+str(i),'statement':'Distinct possible mechanism '+str(i),
        'support_ids':[eid],'counter_ids':[],'counter_status':'not_found','falsification_test':'Inspect input '+str(i)} for i in range(1,4)],
        'abstention':{'label':'uncertain','reason':'Not executed'},'ranked_paths':['parser.py'],
        'checks':[{'id':'check_1','procedure':'Compare [] with None using the same pinned build',
            'assumptions':'Confirm the same build and caller input; this is not an executed test',
            'outcome_a':'The explicit empty list succeeds','outcome_b':'The explicit empty list raises an exception',
            'effort':'low','evidence_ids':[eid],
            'predictions':[{'hypothesis_id':'H'+str(i),'outcome':'A' if i==1 else 'B' if i==2 else 'unknown','reason':'Tentative prediction '+str(i)} for i in range(1,4)]}]}


def wire_findings(payload):
    value=findings(payload);del value['ranked_paths']
    value['file_preferences']={row['id']:'rank_1' if row['path']=='parser.py' else 'omit'
                               for row in payload['candidate_files']}
    return value


@pytest.fixture
def context(repository):
    snapshot=build_snapshot(repository[0],repository[1])
    return {'case_id':'synthetic:policy','repository':'synthetic/example','revision':repository[1],
            'problem_statement':'parser.parse_list fails on None input','split':'main_dev'},snapshot


def fake_call(stages,mode='finish'):
    def call(stage,messages,schema,maximum):
        stages.append((stage,messages,schema,maximum));payload=json.loads(messages[1]['content'])
        if stage.endswith('findings'):return wire_findings(payload)
        reads=[row for row in payload.get('catalog',[]) if row['id'].startswith('R')]
        if mode=='read_code' and reads:
            return {'choice_id':reads[0]['id'],'reason':'Inspect the parser implementation'}
        searches=[row for row in payload.get('catalog',[]) if 'query' in row]
        return {'choice_id':searches[0]['id'] if mode!='finish' and searches else 'STOP',
                'reason':'Inspect the parser implementation'}
    return call


def test_both_arms_have_identical_tools_seed_and_bounds_and_never_execute_repository(context,monkeypatch):
    case,snapshot=context;monkeypatch.setattr('subprocess.Popen',lambda *a,**k:pytest.fail('Target code executed'))
    calls=[];results=[]
    for arm in ARMS:
        stages=[];results.append(run_policy_agent(case,snapshot,arm,fake_call(stages,'read_code')));calls.append(stages)
    assert [row[0] for row in calls[0]]==[row[0] for row in calls[1]]==['decision_1','decision_2','decision_3','findings']
    for left,right in zip(*calls):
        assert left[2:]==right[2:]
        assert json.loads(left[1][1]['content'])==json.loads(right[1][1]['content'])
        assert left[1][0]['content']!=right[1][0]['content']
    assert results[0]['evidence']==results[1]['evidence']
    assert all(row['status']=='ok' for row in results[0]['actions'])
    assert results[0]['comparison']['recommended_check_id']=='check_1'
    assert results[0]['comparison']['predictions_verified'] is False


def test_used_query_leaves_the_catalog_and_unknown_selection_never_executes(context):
    stages=[];selected=[]
    def call(stage,messages,schema,maximum):
        stages.append(stage);payload=json.loads(messages[1]['content'])
        if stage.endswith('findings'):return wire_findings(payload)
        searches=[row for row in payload['catalog'] if 'query' in row]
        if stage=='decision_1':
            selected.append(searches[0]);return {'choice_id':searches[0]['id'],'reason':'Check parser references'}
        assert selected[0]['query'] not in {row['query'] for row in searches}
        return {'choice_id':'S999','reason':'An unoffered selection must be rejected'}
    result=run_policy_agent(*context,ARMS[0],call)
    assert stages==['decision_1','decision_2','findings']
    assert result['actions'][0]['status']=='ok'
    assert result['actions'][0]['argument']==selected[0]['query']
    rejected=result['actions'][1]
    assert rejected['action']=='finish' and rejected['status']=='degraded_stop'
    assert rejected['new_evidence_ids']==[] and result['controller_degraded'] is True


def test_backend_repeat_guard_remains_even_if_query_catalog_incorrectly_reoffers_used_query(context,monkeypatch):
    monkeypatch.setattr('issueintel.investigation.policy_agent._search_options',lambda *args:['parse_list'])
    def call(stage,messages,schema,maximum):
        if stage.endswith('findings'):return wire_findings(json.loads(messages[1]['content']))
        return {'choice_id':'STOP' if stage=='decision_3' else 'S1','reason':'Check parser references'}
    result=run_policy_agent(*context,ARMS[0],call)
    assert result['actions'][0]['status']=='ok'
    rejected=result['actions'][1]
    assert rejected['action']=='search_code' and rejected['argument']=='parse_list'
    assert rejected['status']=='invalid_or_repeated_tool_request'
    assert rejected['failure']['code']=='repeated_request' and rejected['new_evidence_ids']==[]
    assert result['actions'][2]['status']=='finished' and result['controller_degraded'] is False


def test_catalog_selection_uses_exact_program_query_and_never_accepts_free_query_override():
    from issueintel.investigation.policy_agent import _decode_decision
    proposal={'choice_id':'S1','reason':'Inspect the listed parser references'}
    decoded=_decode_decision(proposal,['parser.py:1-2'],['parser.parse_list'])
    assert decoded['action']=='search_code' and decoded['argument']=='parser.parse_list'
    assert decoded['proposal']==proposal
    with pytest.raises(ValueError,match='unexpected fields'):
        _decode_decision({**proposal,'search_query':'an arbitrary replacement query'},['parser.py:1-2'],['parser.parse_list'])
    with pytest.raises(ValueError):_decode_decision({'choice_id':'S2','reason':'Not offered'},[],['parser.parse_list'])
    with pytest.raises(ValueError):_decode_decision({'choice_id':'FIND','reason':'Previous free-search protocol'},[],['parser.parse_list'])


def test_unknown_predictions_produce_no_recommendation_and_lower_effort_is_only_a_tiebreak(context):
    case,snapshot=context;result=run_policy_agent(case,snapshot,ARMS[0],fake_call([]));value=result['findings'];evidence=result['evidence']
    for row in value['checks'][0]['predictions']:row['outcome']='unknown'
    assert compare_checks(value,evidence,['parser.py'])['recommended_check_id'] is None
    value['checks'][0]['predictions'][0]['outcome']='A';value['checks'][0]['predictions'][1]['outcome']='B'
    value['checks'][0]['effort']='high';second=deepcopy(value['checks'][0]);second.update(id='check_2',effort='low');value['checks'].append(second)
    comparison=compare_checks(value,evidence,['parser.py'])
    assert comparison['recommended_check_id']=='check_2' and comparison['checks'][0]['asserted_separated_pairs']==[['H1','H2']]
    assert comparison['information_gain_estimated'] is False


def test_invented_sources_predictions_hidden_fields_or_rank_paths_are_rejected(context):
    case,snapshot=context;result=run_policy_agent(case,snapshot,ARMS[0],fake_call([]));original=result['findings']
    mutations=[lambda v:v['hypotheses'][0].update(support_ids=['fake']),lambda v:v.update(ranked_paths=['../secret.py']),
        lambda v:v['checks'][0]['predictions'].pop(),lambda v:v['checks'][0]['predictions'][0].update(hypothesis_id='H5'),
        lambda v:v['checks'][0].update(outcome_b=v['checks'][0]['outcome_a']),lambda v:v.update(gold={'patch':'hidden'})]
    for mutate in mutations:
        value=deepcopy(original);mutate(value)
        with pytest.raises(ValueError):validate_findings(value,result['evidence'],['parser.py'])
    bad={**case,'human_rationale':'restricted'}
    with pytest.raises(ValueError):run_policy_agent(bad,snapshot,ARMS[0],lambda *a:pytest.fail('Provider created'))


def test_repeated_ranked_path_is_rejected_even_when_both_entries_fit_the_array_bound(context):
    from issueintel.annotations.structured import _validate_wire_schema
    from issueintel.investigation.discriminating_checks import findings_schema
    from issueintel.investigation.policy_agent import _read
    case,snapshot=context
    evidence=[_read('parser.py:1-2',snapshot),_read('unrelated.py:1-2',snapshot)]
    schema=findings_schema(evidence);value=findings({'evidence':evidence})
    # This matches the observed repeated-file failure: both entries are valid
    # snapshot paths and their count is allowed; uniqueness is the violation.
    value['ranked_paths']=['parser.py','parser.py']
    assert len(value['ranked_paths'])<=schema['properties']['ranked_paths']['maxItems']
    with pytest.raises(ValueError,match='ranked_paths has duplicate items'):
        _validate_wire_schema(value,schema)
    with pytest.raises(ValueError,match='ranked_paths has duplicate items'):
        validate_findings(value,evidence,[row.path for row in snapshot.files])
    value['ranked_paths']=['parser.py','unrelated.py']
    assert validate_findings(value,evidence,[row.path for row in snapshot.files])==value


def test_unique_object_items_compare_canonical_content_after_validating_each_item():
    from issueintel.annotations.structured import _validate_wire_schema,obj
    schema={'type':'array','uniqueItems':True,'items':obj({'name':{'type':'string'},'nested':obj({'value':{'type':'string'}})})}
    original={'name':'parser','nested':{'value':'first'}}
    reordered={'nested':{'value':'first'},'name':'parser'}
    with pytest.raises(ValueError,match='duplicate items'):
        _validate_wire_schema([original,reordered],schema)
    _validate_wire_schema([original,{'nested':{'value':'second'},'name':'parser'}],schema)
    with pytest.raises(ValueError,match=r'response\[1\].nested.value must be a string'):
        _validate_wire_schema([original,{'name':'parser','nested':{'value':object()}}],schema)


def test_keyed_file_preferences_bind_paths_and_record_stable_tie_breaking(context):
    from issueintel.investigation.discriminating_checks import project_findings
    from issueintel.investigation.policy_agent import _read
    case,snapshot=context
    evidence=[_read('unrelated.py:1-2',snapshot),_read('parser.py:1-2',snapshot)]
    catalog=candidate_files(evidence)
    assert catalog==[{'id':'P1','path':'parser.py'},{'id':'P2','path':'unrelated.py'}]
    payload={'evidence':evidence,'candidate_files':catalog};wire=wire_findings(payload)
    wire['file_preferences']={'P2':'rank_1','P1':'rank_2'}
    original=deepcopy(wire)
    result,projection=project_findings(wire,evidence,[row.path for row in snapshot.files])
    assert result['ranked_paths']==['unrelated.py','parser.py']
    assert projection['candidates']==catalog and projection['model_preferences']==wire['file_preferences']
    assert projection['tied_groups']==[] and projection['quality']=='unreviewed'
    assert 'file_preferences' not in result and wire==original
    # Ties retain the saved candidate order, never object insertion order or
    # evidence arrival order, and the projection declares that choice.
    wire['file_preferences']={'P2':'rank_1','P1':'rank_1'}
    tied,projection=project_findings(wire,evidence,[row.path for row in snapshot.files])
    assert tied['ranked_paths']==['parser.py','unrelated.py']
    assert projection['tied_groups']==[{'preference':'rank_1','candidate_ids':['P1','P2']}]
    assert projection['ranked_paths']==tied['ranked_paths']
    wire['file_preferences']={'P1':'omit','P2':'omit'}
    assert project_findings(wire,evidence,[row.path for row in snapshot.files])[0]['ranked_paths']==[]


@pytest.mark.parametrize('fault',['unknown_id','missing_id','invalid_rank','injected_paths','unbound_citation','wrong_snapshot'])
def test_keyed_file_preferences_never_accept_unknown_candidates_or_bypass_full_findings_validation(context,fault):
    from issueintel.investigation.discriminating_checks import project_findings
    from issueintel.investigation.policy_agent import _read
    case,snapshot=context
    evidence=[_read('parser.py:1-2',snapshot),_read('unrelated.py:1-2',snapshot)]
    value=wire_findings({'evidence':evidence,'candidate_files':candidate_files(evidence)})
    paths=[row.path for row in snapshot.files]
    if fault=='unknown_id':value['file_preferences']['P999']='rank_1'
    elif fault=='missing_id':del value['file_preferences']['P2']
    elif fault=='invalid_rank':value['file_preferences']['P1']='rank_6'
    elif fault=='injected_paths':value['ranked_paths']=['invented.py']
    elif fault=='unbound_citation':value['hypotheses'][0]['support_ids']=['invented']
    else:
        value['file_preferences']['P2']='rank_2';paths=['parser.py']
    with pytest.raises(ValueError):project_findings(value,evidence,paths)


def test_pilot_registration_round_robin_and_exactly_original_accepted_dev(tmp_path,context,monkeypatch):
    case,snapshot=context
    # Synthetic historical availability supplied by the fixture only.
    row=snapshot.to_dict();row.update(protocol='issue_time',cutoff='2026-01-02T00:00:00+00:00',
        availability={'status':'caller_verified_receipt','historical_certified':True,'source_id':'synthetic',
                      'receipt_sha256':'a'*64,'available_at':'2026-01-01T00:00:00+00:00'})
    from issueintel.repository.snapshot import RepositorySnapshot,SourceFile
    row['snapshot_sha256']=digest({key:value for key,value in row.items() if key!='snapshot_sha256'})
    strict=RepositorySnapshot(**dict(row,files=tuple(SourceFile(**f) for f in row['files']),skipped=tuple(row['skipped'])))
    configs=tmp_path/'configs';configs.mkdir(exist_ok=True)
    source=Path(__file__).resolve().parents[1]/'configs'
    for name in ('investigation_pilot_authorization.json','runtime.toml','prices.json','author_decisions.json'):shutil.copyfile(source/name,configs/name)
    rows=[{'case_id':f'verified:synthetic-{i:02}','repository':'repo/a' if i<20 else 'repo/b'} for i in range(30)]
    monkeypatch.setattr(pilot,'public_catalog',lambda root:{'cases':rows,'checkpoint_sha256':'c'*64})
    def accepted(root,cid):
        assert cid.startswith('verified:synthetic-')
        return {**case,'case_id':cid},strict,{'synthetic':True}
    monkeypatch.setattr(pilot,'accepted_context',accepted)
    registration=pilot.register_policy_pilot(tmp_path);identifier=registration['experiment_sha256']
    spec,directory=pilot.load_registration(tmp_path,identifier)
    assert len(spec['cases'])==25 and spec['cases'][1]['case_id']=='verified:synthetic-20'
    price=ModelPrice('openai/gpt-oss-120b',Decimal('.15'),Decimal('.6'),'2026-10-18','https://console.groq.com/docs',131072,65536)
    config=RuntimeConfig(tmp_path,price.model,16384,BudgetPolicy(2000000,Decimal('10')),configs/'prices.json',tmp_path/'ledger.sqlite','high')
    monkeypatch.setattr(RuntimeConfig,'load',classmethod(lambda cls,root:config))
    calls=[]
    class Provider:
        def __init__(self,cfg,*,call_context,on_receipt):self.config=cfg;self.context=call_context;self.publish=on_receipt
        def complete_structured(self,messages,*,schema,max_output_tokens):
            assert 'required_output_schema' not in json.loads(messages[1]['content'])
            return self.respond(messages,schema,max_output_tokens)
        def respond(self,messages,schema,max_output_tokens):
            calls.append((messages,max_output_tokens));ledger=self.config.ledger();reserved=ledger.reserve(price,1000,max_output_tokens,context=self.context)
            self.publish(ledger.receipt(reserved));response=fake_call([])('findings' if 'file_preferences' in schema['properties'] else 'decision_1',messages,schema,max_output_tokens)
            ledger.reconcile(reserved,price,{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20});self.publish(ledger.receipt(reserved));return response
    with pytest.raises(FileNotFoundError):pilot.run_policy_pilot(tmp_path,identifier,limit=25,provider_factory=Provider)
    result=pilot.run_policy_pilot(tmp_path,identifier,limit=1,provider_factory=Provider)
    assert result['completed_arms']==2 and len(calls)==4
    assert pilot.run_policy_pilot(tmp_path,identifier,limit=1,provider_factory=lambda *a,**k:pytest.fail('Paid replay'))['completed_arms']==2
    full=pilot.run_policy_pilot(tmp_path,identifier,limit=25,provider_factory=Provider)
    assert full['completed_arms']==50 and full['inference_sealed'] is True and len(calls)==100
    # Gold is loaded only by the separate scorer, filtered to the registered 25.
    import pyarrow as arrow
    import pyarrow.parquet as parquet
    data=tmp_path/'data';data.mkdir(exist_ok=True);source_path=data/'reference.parquet'
    patch='diff --git a/parser.py b/parser.py\n--- a/parser.py\n+++ b/parser.py\n@@ -1 +1 @@\n-old\n+new\ndiff --git a/unavailable.py b/unavailable.py\n--- a/unavailable.py\n+++ b/unavailable.py\n@@ -1 +1 @@\n-old\n+new\n'
    selected=[r['case_id'].removeprefix('verified:') for r in spec['cases']]
    parquet.write_table(arrow.table({'instance_id':selected+['holdout_trap'],'patch':[patch]*25+['DO_NOT_READ_HOLDOUT']}),source_path)
    reference={'path':source_path.relative_to(tmp_path).as_posix(),'sha256':sha256(source_path.read_bytes()).hexdigest()}
    monkeypatch.setattr(pilot,'development_catalog',lambda root:{'_authority':{'sources':[reference]}})
    original_read=parquet.read_table
    def guarded_read(path,**kwargs):
        assert kwargs['columns']==['instance_id','patch'] and kwargs['filters']==[('instance_id','in',selected)]
        return original_read(path,**kwargs)
    monkeypatch.setattr(parquet,'read_table',guarded_read)
    report=pilot.score_policy_pilot(tmp_path,identifier)
    assert report['aggregates'][ARMS[0]]['file_recall_at_5']==.5
    assert report['aggregates'][ARMS[0]]['scorable_cases']==25 and len(report['cases'])==50
    assert all(row['unavailable_gold_files']==['unavailable.py'] for row in report['cases'])
    assert report['semantic_quality']=='unreviewed' and len(calls)==100
    review=(directory/'blind-check-review.csv').read_text(encoding='utf-8-sig')
    assert 'DO_NOT_READ_HOLDOUT' not in review and 'general_agent' not in review
    authorization_path=configs/'investigation_pilot_authorization.json';original_authorization=authorization_path.read_bytes()
    authorization=json.loads(original_authorization)
    authorization['reliability_regression'].update(parent_experiment_sha256=identifier,case_ids=[r['case_id'] for r in spec['cases'][:4]])
    write_json(authorization_path,authorization)
    child=pilot.register_policy_pilot(tmp_path,reliability_parent=identifier)['experiment_sha256']
    child_spec,child_dir=pilot.load_registration(tmp_path,child)
    assert len(child_spec['cases'])==4 and child_spec['purpose']=='output_reliability_regression'
    pilot.run_policy_pilot(tmp_path,child,limit=1,provider_factory=Provider)
    assert pilot.run_policy_pilot(tmp_path,child,limit=4,provider_factory=Provider)['completed_arms']==8
    with pytest.raises(ValueError,match='never reads or scores gold'):pilot.score_policy_pilot(tmp_path,child)
    assert len(calls)==116
    authorization_path.write_bytes(original_authorization)
    first=spec['cases'][0]['case_id'];saved=pilot._case_dir(directory,first)/ARMS[0]/'result.json'
    state=pilot._read(saved);state['result']['findings']['ranked_paths']=['unrelated.py'];state['sha256']=digest({k:v for k,v in state.items() if k!='sha256'});write_json(saved,state)
    with pytest.raises(ValueError,match='replay'):pilot.run_policy_pilot(tmp_path,identifier,limit=1,provider_factory=Provider)
    with pytest.raises(ValueError,match='Sealed'):pilot.score_policy_pilot(tmp_path,identifier)


def test_failed_or_incomplete_paid_intent_cannot_trigger_another_call(tmp_path,monkeypatch):
    config=RuntimeConfig(tmp_path,'openai/gpt-oss-120b',16384,BudgetPolicy(100000,Decimal('1')),tmp_path/'prices.json',tmp_path/'ledger.sqlite','high')
    monkeypatch.setattr(RuntimeConfig,'load',classmethod(lambda cls,root:config))
    directory=tmp_path/'calls';directory.mkdir();messages=[{'role':'user','content':'synthetic'}];schema={'type':'object'}
    def fail(*a,**k):raise RuntimeError('Synthetic constructor failure')
    with pytest.raises(RuntimeError):pilot._owned_call(tmp_path,directory,'a'*64,'verified:fixture',ARMS[0],'findings',messages,schema,100,fail)
    with pytest.raises(ValueError,match='never retry'):pilot._owned_call(tmp_path,directory,'a'*64,'verified:fixture',ARMS[0],'findings',messages,schema,100,lambda *a,**k:pytest.fail('Retry'))
    with pytest.raises(ValueError,match='missing'):pilot._owned_call(tmp_path,directory,'a'*64,'verified:fixture',ARMS[0],'decision_1',messages,schema,100,lambda *a,**k:pytest.fail('Replacement'),allow_new=False)


@pytest.mark.parametrize('stage',['decision_1','findings','repair_findings'])
def test_owned_calls_use_exact_strict_schema_without_prompt_duplication_and_replay_without_http(tmp_path,context,monkeypatch,stage):
    from issueintel.investigation.discriminating_checks import wire_findings_schema
    case,snapshot=context
    evidence=run_policy_agent(case,snapshot,ARMS[0],fake_call([]))['evidence']
    schema=deepcopy(ACTION_SCHEMA if stage.startswith('decision_') else wire_findings_schema(evidence))
    if not stage.startswith('decision_'):
        assert 'ranked_paths' not in schema['properties'] and '"uniqueItems"' not in json.dumps(schema)
        assert set(schema['properties']['file_preferences']['properties'])=={row['id'] for row in candidate_files(evidence)}
    payload={'case':case,'evidence':evidence,'candidate_files':candidate_files(evidence)}
    messages=[{'role':'system','content':'Synthetic strict output test.'},
              {'role':'user','content':json.dumps(payload)}]
    if stage.startswith('repair_'):
        messages.append({'role':'user','content':json.dumps({'instruction':'Recheck the original context and return a valid object.'})})
    expected={'choice_id':'STOP','reason':'Summarize supplied evidence'} if stage.startswith('decision_') else wire_findings(payload)
    maximum=LIMITS['decision_output_tokens'] if stage.startswith('decision_') else LIMITS['final_output_tokens']
    config=RuntimeConfig(tmp_path,'openai/gpt-oss-120b',16384,BudgetPolicy(100000,Decimal('1')),tmp_path/'prices.json',tmp_path/'ledger.sqlite','high')
    monkeypatch.setattr(RuntimeConfig,'load',classmethod(lambda cls,root:config))
    price=ModelPrice(config.model,Decimal('.15'),Decimal('.6'),'2026-10-18','https://console.groq.com/docs',131072,65536)
    calls=[]
    class Provider:
        def __init__(self,cfg,*,call_context,on_receipt):
            self.config=cfg;self.context=call_context;self.publish=on_receipt
            assert cfg.reasoning_effort=='medium'
        def complete_function(self,*args,**kwargs):pytest.fail('Native tool transport must not be used')
        def complete_json(self,*args,**kwargs):pytest.fail('Schema-free JSON transport must not be used')
        def complete_structured(self,actual_messages,*,schema,max_output_tokens):
            calls.append((deepcopy(actual_messages),deepcopy(schema),max_output_tokens))
            assert schema==expected_schema and max_output_tokens==maximum
            if not stage.startswith('decision_'):
                assert 'ranked_paths' not in schema['properties'] and '"uniqueItems"' not in json.dumps(schema)
                assert schema['properties']['file_preferences']['additionalProperties'] is False
            assert len(actual_messages)==2
            actual_payload=json.loads(actual_messages[1]['content'])
            assert actual_payload['case']==case and actual_payload['evidence']==evidence
            assert 'required_output_schema' not in actual_payload and 'schema' not in actual_payload
            assert not any(isinstance(value,dict) and value==expected_schema for value in actual_payload.values())
            if stage.startswith('repair_'):
                assert actual_payload['repair_instructions']==['Recheck the original context and return a valid object.']
            ledger=self.config.ledger();reserved=ledger.reserve(price,1000,max_output_tokens,context=self.context)
            self.publish(ledger.receipt(reserved))
            ledger.reconcile(reserved,price,{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20})
            self.publish(ledger.receipt(reserved))
            return deepcopy(expected)
    expected_schema=deepcopy(schema)
    directory=tmp_path/'calls';directory.mkdir()
    arguments=(tmp_path,directory,'a'*64,case['case_id'],ARMS[0],stage,messages,schema,maximum)
    assert pilot._owned_call(*arguments,Provider)==expected
    saved=pilot._read(directory/('call-'+stage+'.json'))
    assert saved['status']=='returned' and saved['response_mode']=='strict_json_schema'
    assert len(saved['receipts'])==1 and saved['response']==expected
    assert pilot._owned_call(*arguments,lambda *a,**k:pytest.fail('Paid replay created a provider'),allow_new=False)==expected
    assert len(calls)==1 and schema==expected_schema


@pytest.mark.parametrize('fault',['degraded','repaired','invalid_action'])
def test_reliability_first_case_gate_rejects_completed_arms_with_any_output_recovery(tmp_path,monkeypatch,fault):
    identifier='a'*64;directory=tmp_path/'pilot';source={'synthetic':'source'}
    cases=[{'case_id':'synthetic:gate','source':source},{'case_id':'synthetic:next','source':source}]
    spec={'purpose':'output_reliability_regression','cases':cases}
    monkeypatch.setattr(pilot,'load_registration',lambda root,chosen:(spec,directory))
    monkeypatch.setattr(pilot,'_context',lambda *a:pytest.fail('A rejected first-case gate must stop before loading another case'))
    for arm in ARMS:
        result={'controller_degraded':False,'repairs':[],'actions':[{'status':'finished'}]}
        if arm==ARMS[0]:
            if fault=='degraded':result['controller_degraded']=True
            elif fault=='repaired':result['repairs']=[{'stage':'findings'}]
            else:result['actions'][0]['status']='invalid_or_repeated_tool_request'
        target=pilot._case_dir(directory,cases[0]['case_id'])/arm/'result.json'
        write_json(target,pilot._seal({'status':'completed','source':source,'result':result}))
    with pytest.raises(ValueError,match='no degradation, repair or invalid tool action'):
        pilot.run_policy_pilot(tmp_path,identifier,limit=2,provider_factory=lambda *a,**k:pytest.fail('Rejected gate spent tokens'))
    assert not pilot._case_dir(directory,cases[1]['case_id']).exists()


@pytest.mark.parametrize('fault',['none','degraded','repaired','invalid_action','failed_call','failed_arm'])
def test_completed_reports_are_not_claimed_as_clean_regression_after_recovery_or_failed_calls(tmp_path,context,monkeypatch,fault):
    case,snapshot=context;identifier='a'*64;directory=tmp_path/'pilot'
    cases=[{'case_id':'synthetic:regression-'+str(i),'source':{'synthetic':i}} for i in range(4)]
    monkeypatch.setattr(pilot,'load_registration',lambda root,chosen:({'purpose':'output_reliability_regression','cases':cases},directory))
    monkeypatch.setattr(pilot,'_context',lambda root,row:({**case,'case_id':row['case_id']},snapshot))
    def run(public,current_snapshot,arm,call):
        result={'controller_degraded':False,'repairs':[],'actions':[{'status':'finished'}]}
        # The first case passes the stage gate. A later case exposes each
        # operational fault while still producing reports where possible.
        if public['case_id']==cases[1]['case_id'] and arm==ARMS[0]:
            if fault=='failed_arm':raise RuntimeError('Synthetic provider failure')
            if fault=='degraded':result['controller_degraded']=True
            elif fault=='invalid_action':result['actions'][0]['status']='invalid_or_repeated_tool_request'
            elif fault in {'repaired','failed_call'}:
                stage='repair_findings' if fault=='repaired' else 'decision_1'
                target=pilot._case_dir(directory,public['case_id'])/arm/('call-'+stage+'.json')
                write_json(target,pilot._seal({'binding':{'stage':stage},'status':'returned' if fault=='repaired' else 'failed'}))
                if fault=='repaired':result['repairs']=[{'stage':'findings'}]
        return result
    monkeypatch.setattr(pilot,'run_policy_agent',run)
    provider=lambda *a,**k:pytest.fail('Synthetic classification audit must not call a provider')
    first=pilot.run_policy_pilot(tmp_path,identifier,limit=1,provider_factory=provider)
    assert first['completed_arms']==2 and first['clean_regression'] is False
    result=pilot.run_policy_pilot(tmp_path,identifier,limit=4,provider_factory=provider)
    assert result['clean_regression'] is (fault=='none')
    if fault=='none':assert result['completed_arms']==8 and result['inference_sealed'] is True
    elif fault=='failed_arm':assert result['failed_arms']==1 and result['inference_sealed'] is False
    else:
        assert result['completed_arms']==8 and result['inference_sealed'] is True
        key={'degraded':'degraded_arms','repaired':'repair_calls','invalid_action':'invalid_tool_actions','failed_call':'failed_call_records'}[fault]
        assert result[key]==1


@pytest.mark.parametrize('fault',['native_tool','invalid_path','wrong_choice','invalid_citation'])
def test_final_repair_keeps_original_context_and_planner_preserves_the_slot(context,fault):
    from issueintel.investigation.providers import ProviderError
    case,snapshot=context;calls=[]
    def call(stage,messages,schema,maximum):
        calls.append((stage,messages));payload=json.loads(messages[1]['content'])
        if stage=='decision_1' and fault=='native_tool':raise ProviderError('untrusted upstream text',status=400,code='tool_use_failed')
        if stage=='decision_1' and fault=='wrong_choice':return {'choice_id':'S999','reason':'An unoffered selection'}
        if stage.endswith('findings'):
            value=wire_findings(payload)
            if stage=='findings' and fault=='invalid_path':value['file_preferences']['P999']='rank_1'
            if stage=='findings' and fault=='invalid_citation':value['hypotheses'][0]['support_ids']=['made_up']
            return value
        return {'choice_id':'STOP','reason':'Enough supplied code to summarize uncertainty'}
    result=run_policy_agent(case,snapshot,ARMS[0],call)
    if fault in {'native_tool','wrong_choice'}:
        assert result['controller_degraded'] is True and result['repairs']==[] and len(calls)==2
        return
    assert len(result['repairs'])==1 and len(calls)==3 and result['findings']['ranked_paths']==['parser.py']
    repair=[row for row in calls if row[0].startswith('repair_')][0]
    original=next(row for row in calls if row[0]==repair[0].removeprefix('repair_'))
    assert repair[1][:-1]==original[1] and 'untrusted upstream text' not in json.dumps(repair)
    assert result['provider_call_count']<=LIMITS['provider_calls']


def test_final_repair_is_reserved_after_planner_rejection_and_never_maps_unknown_citations(context):
    case,snapshot=context;stages=[]
    def call(stage,messages,schema,maximum):
        stages.append(stage)
        if stage=='decision_1':return {'choice_id':'S999','reason':'An unoffered selection'}
        if stage.endswith('findings'):
            value=wire_findings(json.loads(messages[1]['content']));value['hypotheses'][0]['support_ids']=['unknown']
            return value
        return {'choice_id':'STOP','reason':'Finish'}
    with pytest.raises(ValueError):run_policy_agent(case,snapshot,ARMS[0],call)
    assert stages==['decision_1','findings','repair_findings']


@pytest.mark.parametrize('fault',['auth','rate','budget','integrity'])
def test_auth_rate_budget_and_call_integrity_errors_never_trigger_repair(context,fault):
    from issueintel.investigation.providers import ProviderError
    from issueintel.runtime.budget import BudgetDenied
    errors={'auth':ProviderError('hidden',status=401,code='invalid_api_key'),
            'rate':ProviderError('hidden',status=429,code='rate_limit_exceeded'),
            'budget':BudgetDenied('denied'),'integrity':ValueError('Saved paid call source changed')}
    calls=[]
    def call(*args):calls.append(args[0]);raise errors[fault]
    with pytest.raises(type(errors[fault])):run_policy_agent(*context,ARMS[0],call)
    assert calls==['decision_1']


@pytest.mark.parametrize('fault',['missing','binding_changed','incomplete','output_contract'])
def test_runner_stops_on_unfinished_paid_call_integrity_but_continues_after_output_contract_failure(tmp_path,context,monkeypatch,fault):
    case,snapshot=context;identifier='a'*64;directory=tmp_path/'pilot'
    source=pilot.context_binding(case,snapshot)
    spec={'cases':[{'case_id':case['case_id'],'source':source}]}
    monkeypatch.setattr(pilot,'load_registration',lambda root,chosen:(spec,directory))
    monkeypatch.setattr(pilot,'_context',lambda root,row:(case,snapshot))
    config=RuntimeConfig(tmp_path,'openai/gpt-oss-120b',16384,BudgetPolicy(100000,Decimal('1')),tmp_path/'prices.json',tmp_path/'ledger.sqlite','medium')
    monkeypatch.setattr(RuntimeConfig,'load',classmethod(lambda cls,root:config))
    provider_calls=[]
    class Provider:
        def __init__(self,*args,**kwargs):provider_calls.append(kwargs['call_context']['stage'])
        def complete_structured(self,messages,*,schema,max_output_tokens):
            if 'file_preferences' in schema['properties']:return wire_findings(json.loads(messages[1]['content']))
            return {'choice_id':'STOP','reason':'Summarize available evidence'}
    errors={
        'missing':'Completed arm has a missing paid-call record; no automatic replacement',
        'binding_changed':'Saved paid call source changed',
        'incomplete':'Previous paid attempt incomplete/failed; never retry automatically',
    }
    original=pilot._owned_call;visited=[]
    def owned(root,target,chosen,cid,arm,stage,messages,schema,maximum,provider_factory,*,allow_new=True):
        visited.append((arm,stage))
        if arm==ARMS[0]:
            # Exercise runner policy independently of the paid-call integrity
            # checks above: this unfinished arm has no saved result to replay.
            if fault!='output_contract':raise ValueError(errors[fault])
            if stage.endswith('findings'):return {}
            return {'choice_id':'STOP','reason':'Summarize available evidence'}
        return original(root,target,chosen,cid,arm,stage,messages,schema,maximum,provider_factory,allow_new=allow_new)
    monkeypatch.setattr(pilot,'_owned_call',owned)
    result=pilot.run_policy_pilot(tmp_path,identifier,limit=1,provider_factory=Provider)
    first=pilot._read(pilot._case_dir(directory,case['case_id'])/ARMS[0]/'result.json')
    assert first['status']=='failed'
    if fault=='output_contract':
        assert first['error_type']=='OutputContractRejected'
        assert visited==[(ARMS[0],'decision_1'),(ARMS[0],'findings'),(ARMS[0],'repair_findings'),
                         (ARMS[1],'decision_1'),(ARMS[1],'findings')]
        assert provider_calls==['policy_pilot_decision_1','policy_pilot_findings']
        assert result['completed_arms']==1 and result['failed_arms']==1 and result['stop_reason'] is None
    else:
        initial_provider_calls=list(provider_calls);initial_visited=list(visited)
        provider_calls.clear();visited.clear()
        resumed=pilot.run_policy_pilot(tmp_path,identifier,limit=1,provider_factory=Provider)
        assert initial_provider_calls==[], 'An integrity failure must stop before another arm can make a new provider call'
        assert initial_visited==[(ARMS[0],'decision_1')]
        assert result['completed_arms']==0 and result['failed_arms']==1 and result['stop_reason'] is not None
        assert result['inference_sealed'] is False
        assert provider_calls==[], 'Reopening a saved integrity failure must preserve the stop before any new provider call'
        assert visited==[], 'A saved terminal arm failure must stop the runner before another arm is replayed or called'
        assert resumed['completed_arms']==0 and resumed['failed_arms']==1 and resumed['stop_reason'] is not None
        assert resumed['inference_sealed'] is False
        assert not (pilot._case_dir(directory,case['case_id'])/ARMS[1]/'result.json').exists()


def test_terminal_error_metadata_replays_without_http_or_exposing_upstream_content(tmp_path,monkeypatch):
    from issueintel.investigation.providers import ProviderError,failure_metadata,output_rejected
    config=RuntimeConfig(tmp_path,'openai/gpt-oss-120b',16384,BudgetPolicy(100000,Decimal('1')),tmp_path/'prices.json',tmp_path/'ledger.sqlite','high')
    monkeypatch.setattr(RuntimeConfig,'load',classmethod(lambda cls,root:config))
    class Provider:
        def __init__(self,*args,**kwargs):pass
        def complete_structured(self,*args,**kwargs):raise ProviderError('secret-source-and-token',status=400,code='json_validate_failed')
    directory=tmp_path/'calls';directory.mkdir();arguments=(tmp_path,directory,'a'*64,'verified:fixture',ARMS[0],'findings',[{'role':'user','content':'public'}],{'type':'object'},100)
    with pytest.raises(ProviderError):pilot._owned_call(*arguments,Provider)
    saved=pilot._read(directory/'call-findings.json')
    assert saved['failure']['http_status']==400 and saved['failure']['terminal_output_error'] is True
    assert 'secret-source-and-token' not in json.dumps(saved)
    with pytest.raises(ProviderError):pilot._owned_call(*arguments,lambda *a,**k:pytest.fail('Repeated HTTP'),allow_new=False)
    safe=failure_metadata(ProviderError('secret',status='secret',code='secret',retry_after=float('nan')))
    assert 'secret' not in json.dumps(safe) and safe['retry_after_seconds'] is None
    assert not output_rejected(ProviderError('secret',status=400,code=[]))


def test_final_repair_reaches_five_call_bound_and_repaired_output_replays_without_provider(context,tmp_path,monkeypatch):
    case,snapshot=context;stages=[]
    def call(stage,messages,schema,maximum):
        stages.append(stage)
        if stage.endswith('findings'):
            value=wire_findings(json.loads(messages[1]['content']))
            if stage=='findings':value['file_preferences']['P999']='rank_1'
            return value
        choices=[row for row in json.loads(messages[1]['content'])['catalog'] if 'query' in row]
        assert choices
        return {'choice_id':choices[0]['id'],'reason':'Find related code'}
    result=run_policy_agent(case,snapshot,ARMS[0],call)
    assert len(stages)==result['provider_call_count']==LIMITS['provider_calls']==5
    assert stages[-1]=='repair_findings'
    config=RuntimeConfig(tmp_path,'openai/gpt-oss-120b',16384,BudgetPolicy(100000,Decimal('1')),tmp_path/'prices.json',tmp_path/'ledger.sqlite','high')
    monkeypatch.setattr(RuntimeConfig,'load',classmethod(lambda cls,root:config))
    from issueintel.investigation.providers import ProviderError
    requests=[]
    class Provider:
        def __init__(self,*args,**kwargs):pass
        def complete_structured(self,messages,*,schema,max_output_tokens):return self.respond(messages,schema)
        def respond(self,messages,schema):
            payload=json.loads(messages[1]['content']);requests.append(payload)
            if len(requests)==1:raise ProviderError('hidden',status=400,code='tool_use_failed',retry_after=5,diagnostics={'native_call_marker':True})
            return wire_findings(payload) if 'file_preferences' in schema['properties'] else {'reason':'Finish','choice_id':'STOP'}
    directory=tmp_path/'calls';directory.mkdir()
    def owned(factory,allow_new):
        return lambda stage,messages,schema,maximum:pilot._owned_call(tmp_path,directory,'a'*64,case['case_id'],ARMS[0],stage,messages,schema,maximum,factory,allow_new=allow_new)
    first=run_policy_agent(case,snapshot,ARMS[0],owned(Provider,True))
    replay=run_policy_agent(case,snapshot,ARMS[0],owned(lambda *a,**k:pytest.fail('Replayed HTTP'),False))
    assert first==replay and len(requests)==2 and first['controller_fallbacks'][0]['failure']['native_call_marker'] is True


@pytest.mark.parametrize('fault',['wire','provider'])
def test_exhausted_planner_output_stops_safely_but_final_contract_remains_mandatory(context,fault):
    from issueintel.investigation.providers import ProviderError
    case,snapshot=context;stages=[]
    def call(stage,messages,schema,maximum):
        stages.append(stage)
        if stage.endswith('findings'):return wire_findings(json.loads(messages[1]['content']))
        if fault=='provider':raise ProviderError('hidden',status=400,code='tool_use_failed')
        return {'choice_id':'S999','reason':'An unoffered selection'}
    result=run_policy_agent(case,snapshot,ARMS[0],call)
    assert stages==['decision_1','findings']
    assert result['controller_degraded'] is True and len(result['controller_fallbacks'])==1
    assert result['actions'][0]['origin']=='deterministic_fallback' and result['actions'][0]['new_evidence_ids']==[]
    validate_findings(result['findings'],result['evidence'],[f.path for f in snapshot.files])


def test_invalid_final_report_is_never_replaced_with_a_synthetic_success(context):
    case,snapshot=context;stages=[]
    def call(stage,messages,schema,maximum):
        stages.append(stage)
        if stage.endswith('findings'):
            value=wire_findings(json.loads(messages[1]['content']));value['hypotheses'][0]['support_ids']=['unknown']
            return value
        return {'choice_id':'STOP','reason':'Finish'}
    with pytest.raises(ValueError):run_policy_agent(case,snapshot,ARMS[0],call)
    assert stages==['decision_1','findings','repair_findings']


def test_final_repair_still_available_when_planner_has_failed(context):
    from issueintel.investigation.providers import ProviderError
    case,snapshot=context;stages=[]
    def call(stage,messages,schema,maximum):
        stages.append(stage)
        if stage=='decision_1':raise ProviderError('hidden',status=400,code='tool_use_failed')
        value=wire_findings(json.loads(messages[1]['content']))
        if stage=='findings':value['file_preferences']['P999']='rank_1'
        return value
    result=run_policy_agent(case,snapshot,ARMS[0],call)
    assert result['controller_degraded'] is True and len(result['repairs'])==1
    assert stages==['decision_1','findings','repair_findings']
