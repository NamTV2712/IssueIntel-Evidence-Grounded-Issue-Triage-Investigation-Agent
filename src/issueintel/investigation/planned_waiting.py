"""Pure replay and bounded user continuations for product Planned policy v3.

An answered query needs fresh generation; an immutable ancestor keeps the old
query, annotation and receipts.  This module never calls a provider or opens a
dataset.  Only ``policy_sha256`` reads the installed implementation for admission;
saved-read validation checks the recorded hash's syntax, not today's files.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from issueintel.annotations.public_inputs import model_hypotheses
from issueintel.runtime.io import digest
from .clarifications import questions_for, prepare_answers, validate_answers, PROMPTS, _CONTEXT_NOTICE
from .intake import FIELDS


SCHEMA = 'planned_investigation_v3'
POLICY = 'bounded_planner_v3'
WAITING_SCHEMA = 'planned_waiting_v1'
LINK_SCHEMA = 'product_planned_continuation_v1'
COUNT_KEYS = ('retrieval_rounds', 'generation_passes', 'verifier_passes',
              'provider_calls', 'added_evidence')
_HASH = re.compile(r'[a-f0-9]{64}')
_MARKER = '\n\n[Planned continuation answers; unverified]\n'
_BASE_FIELDS = {'schema','policy','source','provider','model','routing','limits',
                'seed_evidence_ids','seed_annotation','rounds','generation_passes',
                'verifier_passes','provider_calls','final_decision','stop_reason','sha256'}
_INHERITED_FIELDS = {'parent_plan_sha256','root_source','routing','limits','counts',
                     'used_query_sha256s','answered_fields'}
_LINK_FIELDS = {'schema','parent_job_id','parent_pack_sha256','parent_plan_sha256',
                'waiting_sha256','policy_sha256','answers_receipt','cue_text',
                'answered_fields','sha256'}
STOP_REASONS = {'provider_not_configured','insufficient_evidence','no_new_queries',
                'no_new_evidence','round_limit','controller_stopped',
                'controller_skipped_retrieval','waiting_for_user','generation_limit'}


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _sealed(value):
    return dict(value, sha256=digest(value))


def _is_hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def policy_sha256():
    """Fingerprint admission code; reading old packs never calls this helper."""
    root = Path(__file__).resolve().parents[1]
    names = ('investigation/planned_waiting.py','investigation/planner.py',
             'investigation/pipeline.py','investigation/clarifications.py',
             'investigation/intake.py','investigation/verifier.py',
             'retrieval/gap_directed.py','annotations/generator.py',
             'annotations/models.py','annotations/public_inputs.py',
             'annotations/structured.py','repository/snapshot.py',
             'investigation/planned_continuations.py','investigation/workbench.py',
             'investigation/stage_jobs.py','investigation/stage_recovery.py',
             'investigation/providers.py','runtime/budget.py','runtime/config.py')
    return digest({'version':POLICY, 'files':{
        name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}})


def _fields(value):
    if (not isinstance(value, (list,tuple)) or any(field not in FIELDS for field in value)
            or len(value) != len(set(value))):
        raise ValueError('Invalid Planned answered fields')
    return [field for field in FIELDS if field in value]


def _counts(value):
    if (not isinstance(value,dict) or set(value) != set(COUNT_KEYS)
            or any(type(value[key]) is not int or value[key] < 0 for key in COUNT_KEYS)):
        raise ValueError('Invalid Planned cumulative counts')
    return value


def totals(plan):
    """Return inherited plus current-segment use, never reset at a child."""
    prior = _counts(plan['inherited']['counts']) if plan.get('inherited') is not None else dict.fromkeys(COUNT_KEYS,0)
    local = {'retrieval_rounds':len(plan['rounds']),
             'generation_passes':plan['generation_passes'],
             'verifier_passes':plan['verifier_passes'],
             'provider_calls':plan['provider_calls'],
             'added_evidence':sum(len(step['added_ids']) for step in plan['rounds'])}
    _counts(local)
    return {key:prior[key]+local[key] for key in COUNT_KEYS}


def remaining(plan):
    limits=plan['limits']; used=totals(plan)
    maxima = {'retrieval_rounds':limits['max_retrieval_rounds'],
              'generation_passes':limits['max_generation_passes'],
              'verifier_passes':limits['max_verifier_passes'],
              'provider_calls':limits['max_provider_calls'],
              'added_evidence':limits['max_added_total']}
    return {key:maxima[key]-used[key] for key in COUNT_KEYS}


def recovery_call_debit(pack):
    """Extra root attempts absent from logical completed-stage plan counts.

    The owning service authenticates the journal/attempts and receipts. This
    pure projection never promotes declared attempts to verified billing or
    rewrites the saved plan (including its original waiting preview).
    """
    link=pack.get('recovered_from')
    if link is None:return 0
    plan=pack['workflow_plan'];value=pack.get('stage_recovery') or {};source=link.get('source') or {}
    prior=value.get('inherited_provider_calls');new=value.get('new_provider_calls')
    if (link.get('schema')!='product_stage_resume_v1' or link.get('automatic_retry') is not False
            or value.get('schema')!='product_pipeline_recovery_v1'
            or plan.get('inherited') is not None or pack.get('continued_from')
            or source.get('provider')!='groq' or source.get('depth')!='planned'
            or source.get('context_sha256')!=value.get('context_sha256')
            or source.get('core_policy_sha256')!=value.get('policy_sha256')
            or source.get('prior_provider_calls')!=prior
            or source.get('sha256')!=digest({k:v for k,v in source.items() if k!='sha256'})
            or type(prior) is not int or type(new) is not int or min(prior,new)<0
            or value.get('max_provider_calls')!=9 or value.get('call_count_kind')!='attempt_upper_bound'
            or value.get('automatic_retry') is not False or value.get('billing_verified') is not False
            or not plan['provider_calls']<=prior+new<=plan['limits']['max_provider_calls']):
        raise ValueError('Unknown or inconsistent recovered Planned attempt budget')
    return prior+new-plan['provider_calls']


def continuation_remaining(pack):
    left=remaining(pack['workflow_plan'])
    left['provider_calls']-=recovery_call_debit(pack)
    return left


def can_generate(plan):
    """Reserve a fresh pass, its one repair, and the sole final critic."""
    left=remaining(plan)
    return (left['generation_passes'] >= 1 and left['provider_calls'] >= 3
            and left['verifier_passes'] == 1 and all(value >= 0 for value in left.values()))


def used_queries(plan):
    inherited=plan.get('inherited')
    used=set(inherited['used_query_sha256s']) if inherited is not None else {plan['source']['query_sha256']}
    for step in plan['rounds']:
        if step.get('search') is not None:
            used.update(row['query_sha256'] for row in step['search']['queries'])
    if len(used)>10 or any(not _is_hash(value) for value in used):
        raise ValueError('Invalid Planned attempted query history')
    return used


def _prior_answered(pack):
    return _fields((pack.get('continued_from') or {}).get('answered_fields', []))


def _inherit(parent):
    plan=parent['workflow_plan']
    counts=totals(plan)
    extra=recovery_call_debit(parent);counts['provider_calls']+=extra
    inherited={'parent_plan_sha256':plan['sha256'],
            'root_source':deepcopy((plan.get('inherited') or {}).get('root_source',plan['source'])),
            'routing':deepcopy(plan['routing']), 'limits':deepcopy(plan['limits']),
            'counts':counts, 'used_query_sha256s':sorted(used_queries(plan)),
            'answered_fields':_prior_answered(parent)}
    prior=plan.get('inherited') or {}
    # Old v3 continuations retain their exact shape. Above the historical
    # completed-pass call bound, carry an explicit debit bound to the supplied
    # parent; descendants preserve it even once their ratio falls below 2/pass.
    if 'recovery_attempt_debit' in prior or counts['provider_calls']>2*counts['generation_passes']:
        inherited['recovery_attempt_debit']=prior.get('recovery_attempt_debit',0)+extra
    return inherited


def start_plan(case, snapshot, graph, evidence, annotation, *, provider, model, parent=None):
    from .planner import start_plan as original_start, GAP_POLICY
    plan=original_start(case,snapshot,graph,evidence,annotation,provider=provider,model=model,policy=GAP_POLICY)
    plan.update(schema=SCHEMA,policy=POLICY,inherited=None,policy_sha256=policy_sha256(),waiting=None)
    if parent is not None:
        validate_plan(parent)
        if parent['workflow_plan']['waiting'] is None or parent['workflow_plan']['stop_reason'] != 'waiting_for_user':
            raise ValueError('Planned continuation requires a waiting parent')
        plan['inherited']=_inherit(parent)
        plan['routing']=deepcopy(parent['workflow_plan']['routing'])
        plan['limits']=deepcopy(parent['workflow_plan']['limits'])
        if any(value < 0 for value in remaining(plan).values()):
            raise ValueError('Planned continuation exceeded the inherited budget')
    return plan


def maybe_waiting(case, annotation, plan, *, cue_text=None, answered_fields=()):
    """Offer bounded missing-cue review only after an attempted empty search."""
    if (annotation is None or not plan['rounds'] or not can_generate(plan)
            or totals(plan)['verifier_passes'] != 0):
        return None
    last=plan['rounds'][-1]
    if (last.get('action') != 'retrieve' or last.get('added_ids')
            or not isinstance(last.get('search'),dict) or not last['search'].get('queries')):
        return None
    answered=set(_fields(answered_fields)) | set((plan.get('inherited') or {}).get('answered_fields',[]))
    questions=questions_for(case['problem_statement'],cue_text=cue_text)
    questions['questions']=[row for row in questions['questions'] if row['field'] not in answered]
    if not questions['questions']:
        return None
    questions.pop('sha256');questions=_sealed(questions)
    return _sealed({'schema':WAITING_SCHEMA,'state':'waiting_for_user',
        'reason':'no_new_evidence_with_unreviewed_input','after_round':last['round'],
        'input_annotation_sha256':digest(annotation),'questions':questions,'remaining':remaining(plan)})


def build_continuation(parent, answers, language):
    """Construct the new query and immutable unverified answer receipt."""
    validate_plan(parent)
    plan=parent['workflow_plan'];waiting=plan['waiting']
    if (waiting is None or plan['stop_reason'] != 'waiting_for_user' or not can_generate(plan)
            or continuation_remaining(parent)['provider_calls']<3):
        raise ValueError('Planned source is not waiting with a remaining generation budget')
    receipt=prepare_answers(waiting['questions'],answers,language)
    case=deepcopy(parent['case'])
    case.update(revision=parent['snapshot']['commit_sha'],issue_created_at=None,
                problem_statement=parent['case']['problem_statement']+_MARKER+receipt['additional_context'])
    prior_cue=(parent.get('continued_from') or {}).get('cue_text',parent['case']['problem_statement'])
    cue_text=prior_cue+'\n'+'\n'.join(row['answer'] for row in receipt['answers'])
    if len(case['problem_statement'])>100000 or len(cue_text)>100000:
        raise ValueError('Combined Planned query exceeds the public input bound')
    answered=_fields(list(dict.fromkeys(_prior_answered(parent)+[row['field'] for row in receipt['answers']])))
    return {'case':case,'receipt':receipt,'cue_text':cue_text,'answered_fields':answered}


def continued_context(parent, case):
    """Rebind only already admitted history sources to the answered query."""
    context=deepcopy(parent.get('repository_context'))
    if context is None:return None
    from issueintel.retrieval.repository_context import context_evidence
    context['query_sha256']=_hash(case['problem_statement'])
    context.pop('sha256',None);context=_sealed(context)
    context_evidence(context,case,protocol='benchmark_base')
    return context


def _validate_link(pack, parent):
    link=pack.get('continued_from')
    if (not isinstance(link,dict) or set(link)!=_LINK_FIELDS or link['schema']!=LINK_SCHEMA
            or not isinstance(link['parent_job_id'],str) or not re.fullmatch(r'[a-f0-9]{24}',link['parent_job_id'])
            or any(not _is_hash(link[key]) for key in ('parent_pack_sha256','parent_plan_sha256',
                                                      'waiting_sha256','policy_sha256','sha256'))
            or digest({key:value for key,value in link.items() if key!='sha256'})!=link['sha256']
            or not isinstance(link['cue_text'],str) or not 0<len(link['cue_text'])<=100000
            or link['answered_fields']!=_fields(link['answered_fields'])):
        raise ValueError('Invalid Planned continuation source link')
    receipt=link['answers_receipt']
    if (not isinstance(receipt,dict) or set(receipt)!={'schema','questions_sha256','query_sha256',
            'language','answers','additional_context','sha256'}
            or receipt['schema']!='issue_clarification_answers_v1'
            or receipt['language'] not in {'en','vi'}
            or any(not _is_hash(receipt[key]) for key in ('questions_sha256','query_sha256','sha256'))
            or receipt['sha256']!=digest({key:value for key,value in receipt.items() if key!='sha256'})
            or not isinstance(receipt['answers'],list)):
        raise ValueError('Invalid Planned unverified answer receipt')
    answers=[];fields=[]
    for row in receipt['answers']:
        if (not isinstance(row,dict) or set(row)!={'question_id','field','question','answer'}
                or row['field'] not in FIELDS or row['question_id']!='missing:'+row['field']
                or row['question']!=PROMPTS[row['field']][receipt['language']]):
            raise ValueError('Planned answer differs from the fixed offered template')
        fields.append(row['field']);answers.append({'question_id':row['question_id'],'answer':row['answer']})
    validate_answers(answers)
    # Exported JSON sorts keys. Rebuild the fixed presentation order used by
    # prepare_answers instead of treating object insertion order as evidence.
    ordered_rows=[{key:row[key] for key in ('question_id','field','question','answer')}
                  for row in receipt['answers']]
    context=_CONTEXT_NOTICE+'\n\n'+json.dumps(ordered_rows,ensure_ascii=False,indent=2)
    suffix=_MARKER+context
    if (fields!=_fields(fields) or not set(fields)<=set(link['answered_fields'])
            or receipt['additional_context']!=context
            or not pack['case']['problem_statement'].endswith(suffix)
            or _hash(pack['case']['problem_statement'][:-len(suffix)])!=receipt['query_sha256']):
        raise ValueError('Planned answer query or context binding differs')
    if parent is not None:
        expected=build_continuation(parent,answers,receipt['language'])
        if (link['parent_plan_sha256']!=parent['workflow_plan']['sha256']
                or link['waiting_sha256']!=parent['workflow_plan']['waiting']['sha256']
                or link['policy_sha256']!=pack['workflow_plan']['policy_sha256']
                or link['policy_sha256']!=parent['workflow_plan']['policy_sha256']
                or expected['case']!=pack['case'] or expected['receipt']!=receipt
                or expected['cue_text']!=link['cue_text'] or expected['answered_fields']!=link['answered_fields']
                or digest(pack['snapshot'])!=digest(parent['snapshot'])
                or pack.get('repository_context')!=continued_context(parent,pack['case'])):
            raise ValueError('Planned continuation differs from its parent and answers')
    return link


def validate_plan(pack, parent=None):
    """Replay saved v3 segment and cumulative budgets without IO or inference.

    Workbench authenticates job/pack bytes and recursively supplies each parent.
    An inherited plan without a supplied parent is structurally checked, never
    interpreted as independent proof that its ancestry exists.
    """
    try:
        return _validate_plan(pack,parent)
    except (KeyError,TypeError,AttributeError,IndexError):
        raise ValueError('Invalid saved Planned waiting structure') from None


def _validate_plan(pack,parent):
    from .planner import (validate_planned_case, routing, limits, _annotation,
                          _list_ids, _code_path, planned_run_id)
    from .comparison import compare_packs
    from .verifier import verify_hypotheses, validate_critique
    from issueintel.retrieval.gap_directed import validate_search
    plan=pack['workflow_plan'];case=pack['case'];snapshot=pack['snapshot']
    validate_planned_case(case,snapshot['protocol'])
    if (not isinstance(plan,dict) or set(plan)!=_BASE_FIELDS|{'inherited','policy_sha256','waiting'}
            or plan['schema']!=SCHEMA or plan['policy']!=POLICY
            or not _is_hash(plan['policy_sha256']) or not _is_hash(plan['sha256'])
            or digest({key:value for key,value in plan.items() if key!='sha256'})!=plan['sha256']
            or not isinstance(plan['provider'],str) or not plan['provider']
            or not isinstance(plan['model'],str) or not plan['model']
            or 'refinement' in pack or pack.get('resumed_from') or pack.get('followup')
            or 'controller_decisions' in pack
            or any(pack['leakage_audit'].get(key) is not False for key in
                   ('strict_issue_time_verified','snapshot_issue_time_verified','original_query_verified'))):
        raise ValueError('Planned v3 policy or integrity seal differs')
    source={'query_sha256':_hash(case['problem_statement']),
            'snapshot_sha256':snapshot['snapshot_sha256'],'commit_sha':snapshot['commit_sha']}
    if plan['source']!=source:
        raise ValueError('Planned v3 source binding differs')
    compare_packs(pack,pack)
    inherited=plan['inherited']
    if inherited is None:
        if parent is not None or pack.get('continued_from'):
            raise ValueError('Original Planned result cannot claim a continuation')
        used={source['query_sha256']}; prior=dict.fromkeys(COUNT_KEYS,0)
    else:
        if (not isinstance(inherited,dict) or set(inherited)!=_INHERITED_FIELDS|({'recovery_attempt_debit'} if 'recovery_attempt_debit' in inherited else set())
                or not _is_hash(inherited['parent_plan_sha256'])
                or set(inherited['root_source'])!=set(source)
                or not _is_hash(inherited['root_source']['query_sha256'])
                or any(inherited['root_source'][key]!=source[key] for key in ('commit_sha','snapshot_sha256'))
                or inherited['routing']!=plan['routing'] or inherited['limits']!=plan['limits']
                or inherited['answered_fields']!=_fields(inherited['answered_fields'])
                or not isinstance(inherited['used_query_sha256s'],list)
                or inherited['used_query_sha256s']!=sorted(set(inherited['used_query_sha256s']))
                or not 1<=len(inherited['used_query_sha256s'])<=10
                or any(not _is_hash(item) for item in inherited['used_query_sha256s'])
                or inherited['root_source']['query_sha256'] not in inherited['used_query_sha256s']):
            raise ValueError('Planned inherited routing or query history differs')
        prior=_counts(inherited['counts'])
        debit=inherited.get('recovery_attempt_debit',0)
        if (prior['generation_passes']<1 or prior['verifier_passes']!=0 or prior['retrieval_rounds']<1
                or type(debit) is not int or debit<0 or 'recovery_attempt_debit' in inherited and debit==0
                or not prior['generation_passes']<=prior['provider_calls']-debit<=2*prior['generation_passes']
                or prior['added_evidence']>6*prior['retrieval_rounds']):
            raise ValueError('Planned inherited counts cannot precede a waiting result')
        link=_validate_link(pack,parent)
        if link['parent_plan_sha256']!=inherited['parent_plan_sha256']:
            raise ValueError('Planned inherited parent binding differs')
        if parent is not None:
            validate_plan(parent)
            if inherited!=_inherit(parent):
                raise ValueError('Planned cumulative ancestry differs')
        used=set(inherited['used_query_sha256s'])
    known={row['id']:row for row in pack['evidence']}
    current=_list_ids(plan['seed_evidence_ids'],known)
    if not current or [row['id'] for row in pack['evidence'][:len(current)]]!=current:
        raise ValueError('Planned seed evidence differs from final inventory')
    seed=[known[identifier] for identifier in current];annotation=plan['seed_annotation']
    if parent is not None:
        from issueintel.retrieval.repository_context import context_evidence
        statement=case['problem_statement']
        expected_seed=[{'id':'issue_'+digest(statement)[:20],'kind':'issue',
            'locator':case.get('issue_url') or case['case_id'],'text':statement,
            'sha256':_hash(statement),'revision':None,'available_at':None,
            'provenance':{'kind':'input','edit_history':'unknown'}}]
        expected_seed.extend(deepcopy(row) for row in parent['evidence'] if row['kind']=='code')
        context=continued_context(parent,case)
        if context is not None:expected_seed.extend(context_evidence(context,case,protocol='benchmark_base'))
        if seed!=expected_seed:
            raise ValueError('Planned continuation seed differs from admitted parent sources')
    if annotation is not None:_annotation(annotation,seed,case['case_id'],plan)
    route=plan['routing']
    if inherited is None and route!=routing(seed,snapshot,pack['graph'],annotation):
        raise ValueError('Planned route differs from root seed observations')
    if (not isinstance(route,dict) or set(route)!={'kind','reason_codes','signals','max_retrieval_rounds'}
            or plan['limits']!=limits(route['max_retrieval_rounds'])
            or any(type(value) is not int for value in plan['limits'].values())
            or any(type(value) is not int or value<0 for value in route['signals'].values())
            or type(route['max_retrieval_rounds']) is not int):
        raise ValueError('Planned root limits differ')
    if inherited is not None:
        root_route=routing(seed,snapshot,pack['graph'],annotation)
        if (set(route['signals'])!=set(root_route['signals']) or route['kind']!='observable_heuristic'
                or not isinstance(route['reason_codes'],list) or not route['reason_codes']):
            raise ValueError('Invalid inherited Planned route')
    steps=plan['rounds']
    if not isinstance(steps,list) or len(steps)+prior['retrieval_rounds']>plan['limits']['max_retrieval_rounds']:
        raise ValueError('Planned retrieval round bound exceeded')
    generations=int(annotation is not None)
    calls=1+annotation['provenance']['repair_count'] if annotation is not None else 0
    terminal=None
    for index,step in enumerate(steps,1):
        if annotation is None or terminal is not None:
            raise ValueError('Planned round follows a terminal decision')
        if set(step)!={'round','input_annotation_sha256','input_evidence_ids','input_hypotheses',
                      'action','reason','controller','fallback','search','added_ids',
                      'output_annotation_sha256','output_annotation'}:
            raise ValueError('Unexpected Planned v3 step fields')
        if (type(step['round']) is not int or step['round']!=index
                or step['input_evidence_ids']!=current or step['input_annotation_sha256']!=digest(annotation)
                or step['input_hypotheses']!=model_hypotheses(annotation['hypotheses'])
                or step['controller']!='rules' or step['fallback'] is not False
                or step['action']!='retrieve' or step['reason']!='unspent_retrieval_budget'):
            raise ValueError('Planned v3 step input or rule decision differs')
        added=_list_ids(step['added_ids'],known)
        if set(added)&set(current) or len(added)>6:
            raise ValueError('Planned retrieval repeated evidence or exceeded bound')
        outcome=validate_search(step['search'],annotation['hypotheses'],current,added,known,source,used,
                                pack['graph'],snapshot)
        spans=[_code_path(known[identifier]) for identifier in current if known[identifier]['kind']=='code']
        for identifier in added:
            if known[identifier]['kind']!='code':raise ValueError('Planned retrieval adds only same-snapshot code')
            path,start,end=_code_path(known[identifier])
            if any(path==prior_path and max(start,a)<=min(end,b) for prior_path,a,b in spans):
                raise ValueError('Planned retrieval repeated a source span')
            spans.append((path,start,end))
        current=current+added
        if added:
            output=step['output_annotation'];_annotation(output,[known[item] for item in current],case['case_id'],plan)
            if step['output_annotation_sha256']!=digest(output):raise ValueError('Planned generation linkage differs')
            annotation=output;generations+=1;calls+=1+output['provenance']['repair_count']
        elif step['output_annotation'] is not None or step['output_annotation_sha256'] is not None:
            raise ValueError('Planned generation requires new evidence')
        if outcome=='no_new_queries':terminal=outcome
    final=plan['final_decision']
    if (not isinstance(final,dict) or set(final)!={'action','reason','controller','fallback'}
            or final['controller']!='rules' or final['fallback'] is not False):
        raise ValueError('Invalid Planned v3 final decision')
    stop=plan['stop_reason']
    if stop not in STOP_REASONS or pack['stop_reason']!=stop:
        raise ValueError('Unknown Planned v3 stop reason')
    verifier_passes=0
    if annotation is None:
        expected='insufficient_evidence' if not any(row['kind']=='code' for row in seed) else 'provider_not_configured'
        if (inherited is not None or steps or stop!=expected or plan['waiting'] is not None
                or final!={'action':'stop','reason':expected,'controller':'rules','fallback':False}
                or pack['annotation'] is not None or pack['verification'] is not None):
            raise ValueError('Offline Planned result claimed a model pass')
    else:
        if current!=[row['id'] for row in pack['evidence']] or annotation!=pack['annotation']:
            raise ValueError('Planned final annotation or inventory differs')
        structural=verify_hypotheses(annotation,pack['evidence']);verification=pack['verification']
        if verification['reports']!=structural['reports'] or verification.get('independent_human') is not False:
            raise ValueError('Planned verification differs from citations')
        if verification['kind']=='llm_separate_pass':
            if final['action']!='verify' or plan['waiting'] is not None:
                raise ValueError('Waiting Planned result cannot call the final critic')
            validate_critique({'reviews':verification['reviews']},annotation,pack['evidence']);verifier_passes=1
        elif verification['kind']!='structural':raise ValueError('Unknown Planned verification kind')
        if stop=='waiting_for_user':
            if final!={'action':'ask','reason':'unreviewed_user_input','controller':'rules','fallback':False}:
                raise ValueError('Planned waiting decision differs')
            cue=(pack.get('continued_from') or {}).get('cue_text')
            answered=(pack.get('continued_from') or {}).get('answered_fields',[])
            expected_waiting=maybe_waiting(case,annotation,plan,cue_text=cue,answered_fields=answered)
            if plan['waiting'] is not None:_counts(plan['waiting']['remaining'])
            if expected_waiting is None or plan['waiting']!=expected_waiting or verifier_passes:
                raise ValueError('Planned waiting questions or remaining budget differ')
        else:
            if plan['waiting'] is not None or final['action'] not in {'verify','stop'}:
                raise ValueError('Terminal Planned result cannot retain a waiting request')
            reasons={'verify':{'retrieval_complete','controller_selected_verify'},'stop':{'controller_stopped'}}
            if final['reason'] not in reasons[final['action']]:raise ValueError('Planned final reason differs')
            pre_critic=deepcopy(plan)
            pre_critic['provider_calls']-=verifier_passes
            pre_critic['verifier_passes']-=verifier_passes
            if stop=='generation_limit' and can_generate(pre_critic):raise ValueError('Planned generation budget was not exhausted')
            if stop=='round_limit' and len(steps)+prior['retrieval_rounds']!=plan['limits']['max_retrieval_rounds']:
                raise ValueError('Planned round limit was not reached')
            if stop in {'no_new_evidence','no_new_queries'} and (not steps or steps[-1]['search']['stop_reason']!=stop):
                raise ValueError('Planned empty-search stop differs')
            if stop=='controller_stopped' and final['action']!='stop':raise ValueError('Planned stop action differs')
    calls+=verifier_passes
    if (plan['generation_passes']!=generations or type(plan['generation_passes']) is not int
            or plan['provider_calls']!=calls or type(plan['provider_calls']) is not int
            or plan['verifier_passes']!=verifier_passes or type(plan['verifier_passes']) is not int
            or any(value<0 for value in remaining(plan).values())):
        raise ValueError('Planned cumulative pass budget differs')
    if inherited is not None and annotation is None:raise ValueError('Answered Planned query needs a fresh generation')
    if planned_run_id(case,snapshot['snapshot_sha256'],pack['evidence'],pack['annotation'],plan,
                      pack.get('repository_context'))!=pack['run_id']:
        raise ValueError('Planned run identity differs')
    return deepcopy(plan)
