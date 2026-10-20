"""Pure v3 waiting/continuation replay with synthetic Git and mock models."""
from copy import deepcopy
import hashlib
import json

import pytest

from issueintel.annotations.generator import generate_annotation
from issueintel.annotations.public_inputs import model_hypotheses
from issueintel.investigation.pipeline import investigate
from issueintel.investigation.planner import planned_run_id, seal_plan
from issueintel.investigation.planned_waiting import (
    SCHEMA, POLICY, start_plan, totals, remaining, used_queries, can_generate,
    maybe_waiting, build_continuation, continued_context, validate_plan,
)
from issueintel.investigation.verifier import verify_hypotheses
from issueintel.repository.graph import build_graph
from issueintel.repository.snapshot import build_snapshot
from issueintel.retrieval.gap_directed import expand_for_gaps
from issueintel.runtime.io import digest
from test_planned_pipeline import PlannedModel, planned_repository
from test_repository_context import context_repo
from test_github_index import connected


class RepairModel(PlannedModel):
    def __init__(self):
        super().__init__(focuses=['parser parse_list items'])
        self.first=True

    def __call__(self,messages,max_output_tokens):
        import json
        if self.first and 'case' in json.loads(messages[1]['content']):
            self.first=False
            return {'hypotheses':[],'abstention':{'label':'uncertain','reason':'Malformed mock output'}}
        return super().__call__(messages,max_output_tokens)


def seal(pack):
    pack['workflow_plan']=seal_plan(pack['workflow_plan'])
    pack['run_id']=planned_run_id(pack['case'],pack['snapshot']['snapshot_sha256'],
        pack['evidence'],pack['annotation'],pack['workflow_plan'],pack.get('repository_context'))
    return pack


def paused_root(fixture):
    case,path=fixture
    model=PlannedModel(focuses=['parser parse_list items'])
    original=investigate(case,path,top_k=1,planned_workflow=True,
        planner_policy='bounded_planner_v2',complete_json=model,provider='groq',model='openai/gpt-oss-120b')
    snapshot=build_snapshot(path,case['revision']);graph=build_graph(snapshot)
    pack=deepcopy(original)
    plan=start_plan(case,snapshot,graph,pack['evidence'],pack['annotation'],
                    provider='groq',model='openai/gpt-oss-120b')
    plan['rounds']=deepcopy(original['workflow_plan']['rounds'][:1])
    assert not plan['rounds'][0]['added_ids']
    plan['waiting']=maybe_waiting(case,pack['annotation'],plan)
    plan.update(final_decision={'action':'ask','reason':'unreviewed_user_input','controller':'rules','fallback':False},
                stop_reason='waiting_for_user')
    pack.update(workflow_plan=plan,stop_reason='waiting_for_user',
                verification=verify_hypotheses(pack['annotation'],pack['evidence']))
    return seal(pack),snapshot,graph


def child_of(parent,snapshot,graph,*,field='reproduction',answer='Unknown',wait=True,repair=False):
    built=build_continuation(parent,[{'question_id':'missing:'+field,'answer':answer}],'en')
    case=built['case'];statement=case['problem_statement']
    issue={'id':'issue_'+digest(statement)[:20],'kind':'issue','locator':case.get('issue_url') or case['case_id'],
           'text':statement,'sha256':hashlib.sha256(statement.encode()).hexdigest(),
           'revision':None,'available_at':None,'provenance':{'kind':'input','edit_history':'unknown'}}
    evidence=[issue]+deepcopy([row for row in parent['evidence'] if row['kind']=='code'])
    model=RepairModel() if repair else PlannedModel(focuses=['parser parse_list items'])
    annotation=generate_annotation(case,evidence,model,
        provider='groq',model='openai/gpt-oss-120b').to_dict()
    plan=start_plan(case,snapshot,graph,evidence,annotation,provider='groq',model='openai/gpt-oss-120b',parent=parent)
    pack=deepcopy(parent)
    link={'schema':'product_planned_continuation_v1','parent_job_id':'a'*24,
          'parent_pack_sha256':'b'*64,'parent_plan_sha256':parent['workflow_plan']['sha256'],
          'waiting_sha256':parent['workflow_plan']['waiting']['sha256'],
          'policy_sha256':plan['policy_sha256'],'answers_receipt':built['receipt'],
          'cue_text':built['cue_text'],'answered_fields':built['answered_fields']}
    link=dict(link,sha256=digest(link))
    pack.update(case=case,evidence=evidence,annotation=annotation,workflow_plan=plan,
                continued_from=link,verification=verify_hypotheses(annotation,evidence))
    if wait:
        added,search=expand_for_gaps(annotation['hypotheses'],evidence,snapshot,graph,
                                    exclude_query_sha256s=used_queries(plan))
        assert not added
        plan['rounds'].append({'round':1,'input_annotation_sha256':digest(annotation),
            'input_evidence_ids':[row['id'] for row in evidence],
            'input_hypotheses':model_hypotheses(annotation['hypotheses']),
            'action':'retrieve','reason':'unspent_retrieval_budget','controller':'rules','fallback':False,
            'search':search,'added_ids':[],'output_annotation_sha256':None,'output_annotation':None})
        plan['waiting']=maybe_waiting(case,annotation,plan,cue_text=built['cue_text'],
                                     answered_fields=built['answered_fields'])
        assert plan['waiting'] is not None
        plan.update(final_decision={'action':'ask','reason':'unreviewed_user_input','controller':'rules','fallback':False},
                    stop_reason='waiting_for_user')
    else:
        plan.update(final_decision={'action':'verify','reason':'retrieval_complete','controller':'rules','fallback':False},
                    stop_reason='controller_skipped_retrieval')
    pack['stop_reason']=plan['stop_reason']
    return seal(pack)


def test_waiting_replays_without_critic_or_current_policy_io(planned_repository,monkeypatch):
    pack,_,_=paused_root(planned_repository)
    monkeypatch.setattr('issueintel.investigation.planned_waiting.policy_sha256',lambda:pytest.fail('Saved read touched code'))
    plan=validate_plan(pack)
    assert plan['schema']==SCHEMA and plan['policy']==POLICY
    assert plan['verifier_passes']==0 and plan['provider_calls']==1
    assert pack['verification']['kind']=='structural'
    assert plan['waiting']['remaining']==remaining(plan)
    assert plan['waiting']['input_annotation_sha256']==digest(pack['annotation'])
    assert plan['waiting']['after_round']==1


def test_lexical_missing_information_alone_does_not_pause(planned_repository):
    pack,_,_=paused_root(planned_repository);plan=deepcopy(pack['workflow_plan']);plan['rounds']=[]
    assert maybe_waiting(pack['case'],pack['annotation'],plan) is None
    plan['rounds']=deepcopy(pack['workflow_plan']['rounds'])
    plan['rounds'][0]['search']['queries']=[]
    assert maybe_waiting(pack['case'],pack['annotation'],plan) is None


def test_unknown_answer_is_kept_unverified_and_excluded_from_future_prompts(planned_repository):
    parent,snapshot,graph=paused_root(planned_repository)
    child=child_of(parent,snapshot,graph,answer='Unknown')
    validate_plan(child,parent=parent)
    link=child['continued_from']
    assert link['answers_receipt']['answers'][0]['answer']=='Unknown'
    assert 'reproduction' in link['answered_fields']
    assert 'reproduction' not in [row['field'] for row in child['workflow_plan']['waiting']['questions']['questions']]
    assert 'Guided clarification answers' not in link['cue_text']
    assert child['case']['issue_created_at'] is None
    assert child['leakage_audit']['strict_issue_time_verified'] is False


def test_changed_query_gets_fresh_generation_and_preserves_global_route_and_history(planned_repository):
    parent,snapshot,graph=paused_root(planned_repository)
    before=deepcopy(parent)
    child=child_of(parent,snapshot,graph)
    validate_plan(child,parent=parent)
    plan=child['workflow_plan']
    assert parent==before
    assert child['case']['case_id']==parent['case']['case_id']
    assert plan['routing']==parent['workflow_plan']['routing']
    assert plan['limits']==parent['workflow_plan']['limits']
    assert totals(plan)['generation_passes']==2 and totals(plan)['retrieval_rounds']==2
    assert child['evidence'][0]['id']!=parent['evidence'][0]['id']
    assert set(child['annotation']['evidence_ids'])=={row['id'] for row in child['evidence']}
    assert plan['source']['query_sha256'] not in used_queries(plan)
    assert parent['workflow_plan']['source']['query_sha256'] in used_queries(plan)
    assert plan['rounds'][0]['search']['excluded_query_sha256s']==sorted(used_queries(parent['workflow_plan']))
    assert continued_context(parent,child['case']) is None


def test_sorted_json_roundtrip_preserves_answer_presentation_and_parent_binding(planned_repository):
    parent,snapshot,graph=paused_root(planned_repository)
    child=child_of(parent,snapshot,graph)
    loaded_parent=json.loads(json.dumps(parent,sort_keys=True))
    loaded_child=json.loads(json.dumps(child,sort_keys=True))
    assert validate_plan(loaded_child,parent=loaded_parent)==loaded_child['workflow_plan']


def test_multiple_partial_answers_share_budget_instead_of_resetting(planned_repository):
    parent,snapshot,graph=paused_root(planned_repository)
    child=child_of(parent,snapshot,graph,field='reproduction')
    grandchild=child_of(child,snapshot,graph,field='environment')
    validate_plan(child,parent=parent);validate_plan(grandchild,parent=child)
    counts=totals(grandchild['workflow_plan'])
    assert counts['retrieval_rounds']==3 and counts['generation_passes']==3
    assert remaining(grandchild['workflow_plan'])['retrieval_rounds']==0
    terminal=child_of(grandchild,snapshot,graph,field='expected_behavior',wait=False)
    validate_plan(terminal,parent=grandchild)
    assert totals(terminal['workflow_plan'])['generation_passes']==4
    assert not can_generate(terminal['workflow_plan'])
    assert totals(terminal['workflow_plan'])['provider_calls']==4


def test_generation_repair_and_final_critic_are_reserved_before_asking(planned_repository):
    pack,_,_=paused_root(planned_repository)
    plan=deepcopy(pack['workflow_plan'])
    plan['provider_calls']=plan['limits']['max_provider_calls']-2
    assert not can_generate(plan)
    assert maybe_waiting(pack['case'],pack['annotation'],plan) is None
    plan=deepcopy(pack['workflow_plan']);plan['generation_passes']=plan['limits']['max_generation_passes']
    assert not can_generate(plan)
    plan=deepcopy(pack['workflow_plan']);plan['verifier_passes']=1
    assert maybe_waiting(pack['case'],pack['annotation'],plan) is None


def test_four_repaired_generations_and_one_final_critic_fill_nine_calls_across_lineage(planned_repository):
    parent,snapshot,graph=paused_root(planned_repository)
    annotation=generate_annotation(parent['case'],parent['evidence'],RepairModel(),
        provider='groq',model='openai/gpt-oss-120b').to_dict()
    parent['annotation']=annotation;plan=parent['workflow_plan']
    plan.update(seed_annotation=deepcopy(annotation),provider_calls=2)
    plan['rounds'][0]['input_annotation_sha256']=digest(annotation)
    plan['waiting']=maybe_waiting(parent['case'],annotation,plan)
    parent['verification']=verify_hypotheses(annotation,parent['evidence']);seal(parent)
    validate_plan(parent)
    child=child_of(parent,snapshot,graph,field='reproduction',repair=True)
    grandchild=child_of(child,snapshot,graph,field='environment',repair=True)
    terminal=child_of(grandchild,snapshot,graph,field='expected_behavior',wait=False,repair=True)
    terminal['verification']=verify_hypotheses(terminal['annotation'],terminal['evidence'],PlannedModel())
    terminal['workflow_plan'].update(verifier_passes=1,provider_calls=3,stop_reason='round_limit')
    terminal['stop_reason']='round_limit';seal(terminal)
    validate_plan(child,parent=parent);validate_plan(grandchild,parent=child)
    validate_plan(terminal,parent=grandchild)
    counts=totals(terminal['workflow_plan'])
    assert counts['generation_passes']==4 and counts['provider_calls']==9 and counts['verifier_passes']==1
    assert all(value>=0 for value in remaining(terminal['workflow_plan']).values())
    assert remaining(terminal['workflow_plan'])['provider_calls']==0


@pytest.mark.parametrize('mutate',[
    lambda p:p['workflow_plan']['waiting']['questions']['questions'][0]['prompts'].update(en='Invented prompt'),
    lambda p:p['workflow_plan']['waiting']['remaining'].update(provider_calls=100),
    lambda p:p['workflow_plan'].update(provider_calls=0),
    lambda p:p['workflow_plan'].update(verifier_passes=1),
    lambda p:p['workflow_plan']['final_decision'].update(action='verify'),
    lambda p:p['workflow_plan']['rounds'][0]['search']['queries'][0].update(gap_code='verified_cause'),
    lambda p:p['workflow_plan']['source'].update(query_sha256='0'*64),
    lambda p:p['workflow_plan'].update(policy_sha256='not-a-hash'),
])
def test_resealed_waiting_tampering_is_rejected(planned_repository,mutate):
    pack,_,_=paused_root(planned_repository);mutate(pack);seal(pack)
    with pytest.raises(ValueError):validate_plan(pack)


@pytest.mark.parametrize('mutate',[
    lambda p:p['workflow_plan']['inherited']['counts'].update(generation_passes=0),
    lambda p:p['workflow_plan']['inherited'].update(used_query_sha256s=[]),
    lambda p:p['continued_from'].update(cue_text='Forged cue text'),
    lambda p:p['continued_from']['answers_receipt']['answers'][0].update(answer='Different answer'),
    lambda p:p['continued_from'].update(answered_fields=[]),
    lambda p:p['continued_from'].update(parent_plan_sha256='0'*64),
    lambda p:p['workflow_plan'].update(seed_annotation=None),
])
def test_resealed_child_source_and_budget_tampering_is_rejected(planned_repository,mutate):
    parent,snapshot,graph=paused_root(planned_repository);child=child_of(parent,snapshot,graph)
    mutate(child)
    link=child['continued_from'];link.pop('sha256');link['sha256']=digest(link)
    seal(child)
    with pytest.raises(ValueError):validate_plan(child,parent=parent)


@pytest.mark.parametrize('case_change',[{'split':'main_dev'},{'split':None},{'instance_id':'hidden-case'},
                                      {'case_id':'followup:abc'}])
def test_waiting_replay_rejects_benchmark_and_generic_followup(planned_repository,case_change):
    pack,_,_=paused_root(planned_repository);pack['case'].update(case_change);seal(pack)
    with pytest.raises(ValueError):validate_plan(pack)


def test_answer_must_be_from_exact_offered_question(planned_repository):
    parent,_,_=paused_root(planned_repository)
    with pytest.raises(ValueError,match='not offered'):
        build_continuation(parent,[{'question_id':'missing:observed_behavior','answer':'Unknown'}],'en')


def test_continued_history_reuses_exact_receipted_sources_and_rebinds_query(context_repo):
    from issueintel.retrieval.repository_context import collect_context, context_evidence
    index,case,_=context_repo
    context=collect_context(index,case['issue_url'],case['problem_statement'])
    original=deepcopy(context)
    answered=dict(case,problem_statement=case['problem_statement']+'\nUnverified answer: unknown')
    rebound=continued_context({'repository_context':context},answered)
    assert context==original and rebound['sources']==original['sources']
    assert rebound['sha256']!=original['sha256']
    assert rebound['query_sha256']==hashlib.sha256(answered['problem_statement'].encode()).hexdigest()
    evidence=context_evidence(rebound,answered,protocol='benchmark_base')
    assert evidence and all(row['provenance']['historical_verified'] is False for row in evidence)
    broken=deepcopy(context);broken['sources'][0]['receipt_sha256']='0'*64
    with pytest.raises(ValueError):continued_context({'repository_context':broken},answered)
