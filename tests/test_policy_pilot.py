"""Synthetic tool parity, check partitions and durable paid-stage ownership."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
import json
import shutil

import pytest

from issueintel.investigation.discriminating_checks import validate_findings,compare_checks
from issueintel.investigation.policy_agent import run_policy_agent,ARMS,LIMITS
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


@pytest.fixture
def context(repository):
    snapshot=build_snapshot(repository[0],repository[1])
    return {'case_id':'synthetic:policy','repository':'synthetic/example','revision':repository[1],
            'problem_statement':'parser.parse_list fails on None input','split':'main_dev'},snapshot


def fake_call(stages,mode='finish'):
    def call(stage,messages,schema,maximum):
        stages.append((stage,messages,schema,maximum));payload=json.loads(messages[1]['content'])
        if stage=='findings':return findings(payload)
        return {'action':mode,'argument':'parser.py:1-2' if mode=='read_code' else 'parse_list','reason':'Inspect the parser implementation'}
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
    assert results[0]['actions'][1]['status']=='invalid_or_repeated_tool_request'
    assert results[0]['comparison']['recommended_check_id']=='check_1'
    assert results[0]['comparison']['predictions_verified'] is False


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
            calls.append((messages,max_output_tokens));ledger=self.config.ledger();reserved=ledger.reserve(price,1000,max_output_tokens,context=self.context)
            self.publish(ledger.receipt(reserved));response=fake_call([])('findings' if 'ranked_paths' in schema['properties'] else 'decision_1',messages,schema,max_output_tokens)
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
