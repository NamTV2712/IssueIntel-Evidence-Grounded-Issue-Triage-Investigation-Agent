"""Deterministic product routing and replay of bounded investigation steps.

Routing describes observable retrieval gaps, never model quality or a calibrated
difficulty score. This module performs no IO, model call, evaluation or execution
of target repository code. The pipeline owns those bounded operations.
"""
from copy import deepcopy
import hashlib
import re

from issueintel.annotations.models import Annotation
from issueintel.annotations.public_inputs import model_hypotheses
from issueintel.runtime.io import digest
from .controller import Action, DecisionState, RuleController


SCHEMA='planned_investigation_v1'
POLICY='bounded_planner_v1'
GAP_SCHEMA='planned_investigation_v2'
GAP_POLICY='bounded_planner_v2'
VERSIONS={POLICY:SCHEMA,GAP_POLICY:GAP_SCHEMA}
STOP_REASONS={'provider_not_configured','insufficient_evidence','no_new_queries',
              'no_new_evidence','round_limit','controller_stopped','controller_skipped_retrieval'}
_HASH=re.compile(r'[a-f0-9]{64}')
_GITHUB=re.compile(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)')


def validate_planned_case(case, protocol, *, refinement_rounds=0, prepared_evidence=None, resumable=False):
    """Fail before snapshot/provider IO for benchmark, strict or resume inputs."""
    identifier=case.get('case_id')
    if (protocol!='benchmark_base' or 'split' in case or 'instance_id' in case
            or refinement_rounds!=0 or prepared_evidence is not None or resumable):
        raise ValueError('Planned workflow accepts only original product inputs, without refinement, prepared evidence or resume')
    if not isinstance(identifier,str) or not identifier or len(identifier)>2000:
        raise ValueError('Planned workflow needs an original product case ID')
    if identifier=='local' or (identifier.startswith(('local:','synthetic:')) and identifier.split(':',1)[1]):
        return
    match=_GITHUB.fullmatch(identifier)
    if (not match or any(part in {'.','..'} for part in match.group(1,2))
            or case.get('issue_url')!=identifier
            or case.get('repository') not in {'/'.join(match.group(1,2)),
                                             'https://github.com/'+'/'.join(match.group(1,2))}):
        raise ValueError('Planned workflow needs a canonical GitHub issue or local product case')


def _code_path(record):
    match=re.fullmatch(r'(.+):(\d+)-(\d+)',record['locator'])
    if not match or not 1<=int(match[2])<=int(match[3]):
        raise ValueError('Planned code evidence needs an exact source span')
    return match[1],int(match[2]),int(match[3])


def routing(evidence, snapshot, graph, annotation):
    """Freeze a small retrieval budget from seed observations alone."""
    descriptor=snapshot.to_dict() if hasattr(snapshot,'to_dict') else snapshot
    graph_descriptor=graph.to_dict() if hasattr(graph,'to_dict') else graph
    signals={'code_files':len({_code_path(row)[0] for row in evidence if row['kind']=='code'}),
             'not_found_hypotheses':sum(row['counter_status']=='not_found'
                                      for row in (annotation or {}).get('hypotheses',[])),
             'parse_error_count':len(graph_descriptor.get('parse_errors',[])),
             'skipped_file_count':len(descriptor.get('skipped',[]))}
    reasons=[];maximum=1
    if signals['code_files']>1:
        maximum=2;reasons.append('multiple_code_files')
    if signals['not_found_hypotheses']>=2:
        maximum=2;reasons.append('missing_counter_evidence')
    if signals['parse_error_count'] or signals['skipped_file_count']:
        maximum=3;reasons.append('snapshot_gaps')
    if signals['code_files']>=3 and signals['not_found_hypotheses']>=3:
        maximum=3;reasons.append('broad_missing_counter_evidence')
    return {'kind':'observable_heuristic','reason_codes':reasons or ['single_file_or_no_missing_counter'],
            'signals':signals,'max_retrieval_rounds':maximum}


def limits(maximum):
    if type(maximum) is not int or not 1<=maximum<=3:
        raise ValueError('Planned retrieval budget must be one to three rounds')
    return {'max_retrieval_rounds':maximum,'max_queries_per_round':3,'max_added_per_round':6,
            'max_added_total':18,'max_generation_passes':maximum+1,
            'max_verifier_passes':1,'max_provider_calls':2*(maximum+1)+1}


def start_plan(case, snapshot, graph, evidence, annotation, *, provider, model, policy=POLICY):
    if policy not in VERSIONS:
        raise ValueError('Unsupported Planned workflow policy')
    route=routing(evidence,snapshot,graph,annotation)
    return {'schema':VERSIONS[policy],'policy':policy,'source':{
                'query_sha256':hashlib.sha256(case['problem_statement'].encode()).hexdigest(),
                'commit_sha':snapshot.commit_sha,'snapshot_sha256':snapshot.snapshot_sha256},
            'provider':provider,'model':model,'routing':route,'limits':limits(route['max_retrieval_rounds']),
            'seed_evidence_ids':[row['id'] for row in evidence],
            'seed_annotation':deepcopy(annotation),'rounds':[],
            'generation_passes':int(annotation is not None),'verifier_passes':0,
            'provider_calls':(1+annotation['provenance']['repair_count']) if annotation is not None else 0,
            'final_decision':None,'stop_reason':'not_started'}


def choose_action(controller, evidence_count, hypotheses_count, missing, remaining, *, final=False):
    allowed=(Action.VERIFY,Action.STOP) if final else (Action.RETRIEVE,Action.VERIFY,Action.STOP)
    state=DecisionState(evidence_count,hypotheses_count,tuple(missing),rounds_remaining=remaining)
    fallback=False
    try:
        action=controller.decide(state,allowed)
        if not isinstance(action,Action) or action not in allowed:
            raise ValueError('Controller returned a forbidden action')
        fallback=getattr(controller,'last_fallback',False) is True
    except (KeyError,TypeError,ValueError,RuntimeError,AttributeError):
        action=RuleController().decide(state,allowed);fallback=True
    name=getattr(controller,'name','custom')
    name=name if isinstance(name,str) and name.strip() else 'custom'
    reason=('unspent_retrieval_budget' if action==Action.RETRIEVE else
            'retrieval_complete' if action==Action.VERIFY and final else
            'controller_selected_verify' if action==Action.VERIFY else 'controller_stopped')
    return action,{'action':action.value,'reason':reason,'controller':name[:80],'fallback':fallback}


def seal_plan(plan):
    result=deepcopy(plan);result.pop('sha256',None)
    result['sha256']=digest(result)
    return result


def planned_run_id(case, snapshot_sha256, evidence, annotation, plan, repository_context=None):
    return digest({'case':case,'snapshot':snapshot_sha256,'provider':plan['provider'],'model':plan['model'],
                   'evidence':[row['id'] for row in evidence],'annotation':annotation,
                   'workflow_plan_sha256':plan['sha256'],
                   **({'repository_context':repository_context['sha256']} if repository_context is not None else {})})[:24]


def _annotation(value, evidence, case_id, plan):
    parsed=Annotation.from_dict(value).validate_evidence(evidence)
    if (parsed.case_id!=case_id or parsed.source_quality or parsed.judgment
            or parsed.provenance.kind!='llm' or parsed.provenance.provider!=plan['provider']
            or parsed.provenance.model!=plan['model']
            or any(row.correctness!='unverified' for row in parsed.hypotheses)):
        raise ValueError('Planned annotation differs from public generation source')
    return parsed.to_dict()


def _list_ids(value, known=None):
    if (not isinstance(value,list) or any(not isinstance(item,str) or not item for item in value)
            or len(value)!=len(set(value)) or known is not None and not set(value)<=set(known)):
        raise ValueError('Invalid planned evidence references')
    return value


def _decision(value, *, final=False):
    if (not isinstance(value,dict) or set(value)!={'action','reason','controller','fallback'}
            or value['action'] not in ({'verify','stop'} if final else {'retrieve','verify','stop'})
            or type(value['fallback']) is not bool or not isinstance(value['controller'],str)
            or not 0<len(value['controller'])<=80):
        raise ValueError('Invalid planned controller decision')
    expected={'retrieve':{'unspent_retrieval_budget'},'verify':{'controller_selected_verify','retrieval_complete'},
              'stop':{'controller_stopped','provider_not_configured','insufficient_evidence'}}
    if value['reason'] not in expected[value['action']]:
        raise ValueError('Planned decision reason differs from action')


def _validate_search(search, hypotheses, current, added, known, source, used):
    from issueintel.retrieval.focused import POLICY as FOCUSED_POLICY
    if (not isinstance(search,dict) or set(search)!={'policy','max_queries','max_added_evidence','queries',
            'added_ids','stop_reason','counter_evidence_confirmed','snapshot_sha256',
            'excluded_query_sha256s','skipped_queries'} or search.get('policy')!=FOCUSED_POLICY
            or search.get('max_queries')!=3 or search.get('max_added_evidence')!=6
            or type(search.get('max_queries')) is not int or type(search.get('max_added_evidence')) is not int
            or search.get('counter_evidence_confirmed') is not False
            or search.get('snapshot_sha256')!=source['snapshot_sha256']
            or search.get('excluded_query_sha256s')!=sorted(used)):
        raise ValueError('Planned search policy or query history differs')
    queries=search['queries'];skipped=search['skipped_queries']
    if not isinstance(queries,list) or not isinstance(skipped,list) or not 1<=len(queries)+len(skipped)<=3:
        raise ValueError('Invalid planned query count')
    expected=hypotheses[:3]
    query_map={};skip_map={}
    for entry in queries:
        if (not isinstance(entry,dict) or set(entry)!={'seed_hypothesis_id','query','query_sha256',
                'returned_ids','added_ids','purpose'} or not isinstance(entry['seed_hypothesis_id'],str)
                or entry['seed_hypothesis_id'] in query_map):
            raise ValueError('Invalid or repeated planned query')
        query_map[entry['seed_hypothesis_id']]=entry
    for entry in skipped:
        if (not isinstance(entry,dict) or set(entry)!={'seed_hypothesis_id','query_sha256','reason','executed'}
                or not isinstance(entry['seed_hypothesis_id'],str)
                or entry['seed_hypothesis_id'] in skip_map or entry['reason']!='already_executed'
                or entry['executed'] is not False):
            raise ValueError('Invalid skipped planned query')
        skip_map[entry['seed_hypothesis_id']]=entry
    observed=set(query_map)|set(skip_map)
    prefix=expected[:len(observed)]
    if (set(query_map)&set(skip_map) or observed!={row['id'] for row in prefix}
            or len(observed)<len(expected) and len(added)!=6
            or [entry['seed_hypothesis_id'] for entry in queries]!=[row['id'] for row in prefix if row['id'] in query_map]
            or [entry['seed_hypothesis_id'] for entry in skipped]!=[row['id'] for row in prefix if row['id'] in skip_map]):
        raise ValueError('Planned query order differs from seed hypotheses')
    query_added=[]
    for hypothesis in prefix:
        query=hypothesis['statement'][:600]+'\n'+hypothesis['falsification_test'][:600]
        query_hash=hashlib.sha256(query.encode()).hexdigest()
        if hypothesis['id'] in skip_map:
            if skip_map[hypothesis['id']]['query_sha256']!=query_hash or query_hash not in used:
                raise ValueError('Skipped planned query has not been executed')
            continue
        entry=query_map[hypothesis['id']]
        if (entry['query']!=query or entry['query_sha256']!=query_hash or query_hash in used
                or entry['purpose']!='unverified_hypothesis_focus'):
            raise ValueError('Planned query repeated or differs from hypothesis')
        used.add(query_hash)
        returned=_list_ids(entry['returned_ids'])
        accepted=_list_ids(entry['added_ids'],returned)
        if len(returned)>3 or not set(accepted)<=set(added):
            raise ValueError('Planned query exceeds candidate bound')
        query_added.extend(accepted)
        for identifier in accepted:
            provenance=known[identifier]['provenance']
            if (provenance.get('search_stage')!=FOCUSED_POLICY
                    or provenance.get('seed_hypothesis_id')!=hypothesis['id']
                    or provenance.get('counter_evidence_confirmed') is not False):
                raise ValueError('Planned candidate differs from retrieval provenance')
    if query_added!=added or search.get('added_ids')!=added:
        raise ValueError('Planned search added IDs differ')
    expected_stop='new_candidates_added' if added else 'no_new_evidence' if queries else 'no_new_queries'
    if search.get('stop_reason')!=expected_stop:
        raise ValueError('Planned search stop reason differs')
    return expected_stop


def validate_planned(pack):
    """Replay saved step/source/hash/budget links, without judging model claims."""
    try:
        return _validate_planned(pack)
    except (KeyError,TypeError,AttributeError,IndexError):
        raise ValueError('Invalid saved planned investigation structure') from None


def _validate_planned(pack):
    from .comparison import compare_packs
    from .verifier import verify_hypotheses, validate_critique
    plan=pack['workflow_plan'];snapshot=pack['snapshot'];case=pack['case']
    validate_planned_case(case,snapshot['protocol'])
    if (set(plan)!={'schema','policy','source','provider','model','routing','limits','seed_evidence_ids',
                   'seed_annotation','rounds','generation_passes','verifier_passes','provider_calls',
                   'final_decision','stop_reason','sha256'}
            or plan.get('policy') not in VERSIONS or plan.get('schema')!=VERSIONS[plan['policy']]
            or not isinstance(plan.get('sha256'),str) or not _HASH.fullmatch(plan['sha256'])
            or digest({key:value for key,value in plan.items() if key!='sha256'})!=plan['sha256']
            or not isinstance(plan.get('provider'),str) or not plan['provider']
            or not isinstance(plan.get('model'),str) or not plan['model']
            or 'refinement' in pack or pack.get('resumed_from')
            or any(pack['leakage_audit'].get(key) is not False for key in
                   ('strict_issue_time_verified','snapshot_issue_time_verified','original_query_verified'))):
        raise ValueError('Planned policy or integrity seal differs')
    source={'query_sha256':hashlib.sha256(case['problem_statement'].encode()).hexdigest(),
            'snapshot_sha256':snapshot['snapshot_sha256'],'commit_sha':snapshot['commit_sha']}
    if plan['source']!=source:
        raise ValueError('Planned source binding differs')
    compare_packs(pack,pack)
    known={row['id']:row for row in pack['evidence']}
    current=_list_ids(plan['seed_evidence_ids'],known)
    if not current or [row['id'] for row in pack['evidence'][:len(current)]]!=current:
        raise ValueError('Planned seed evidence differs from final inventory')
    seed=[known[identifier] for identifier in current]
    annotation=plan['seed_annotation']
    if annotation is not None:
        _annotation(annotation,seed,case['case_id'],plan)
    route=routing(seed,snapshot,pack['graph'],annotation)
    if (plan['routing']!=route or plan['limits']!=limits(route['max_retrieval_rounds'])
            or any(type(value) is not int for value in plan['limits'].values())
            or any(type(value) is not int for value in plan['routing']['signals'].values())
            or type(plan['routing']['max_retrieval_rounds']) is not int):
        raise ValueError('Planned routing differs from seed observations')
    maximum=route['max_retrieval_rounds'];steps=plan['rounds']
    if not isinstance(steps,list) or len(steps)>maximum:
        raise ValueError('Planned retrieval round bound exceeded')
    generations=int(annotation is not None)
    calls=1+annotation['provenance']['repair_count'] if annotation is not None else 0
    total_added=0;used={source['query_sha256']};terminal=None
    for index,step in enumerate(steps,1):
        if annotation is None or terminal is not None:
            raise ValueError('Planned round follows a terminal decision')
        if set(step)!={'round','input_annotation_sha256','input_evidence_ids','input_hypotheses',
                      'action','reason','controller','fallback','search','added_ids',
                      'output_annotation_sha256','output_annotation'}:
            raise ValueError('Unexpected planned step fields')
        decision={key:step[key] for key in ('action','reason','controller','fallback')}
        _decision(decision)
        if (type(step['round']) is not int or step['round']!=index
                or step['input_evidence_ids']!=current
                or step['input_annotation_sha256']!=digest(annotation)
                or step['input_hypotheses']!=model_hypotheses(annotation['hypotheses'])):
            raise ValueError('Planned step input linkage differs')
        added=_list_ids(step['added_ids'],known)
        if set(added)&set(current) or len(added)>6:
            raise ValueError('Planned retrieval repeated evidence or exceeded bound')
        if step['action']!='retrieve':
            if added or step['search'] is not None or step['output_annotation'] is not None or step['output_annotation_sha256'] is not None:
                raise ValueError('Skipped planned search cannot generate evidence')
            terminal='controller_stopped' if step['action']=='stop' else 'controller_skipped_retrieval'
            continue
        if plan['policy']==GAP_POLICY:
            from issueintel.retrieval.gap_directed import validate_search
            outcome=validate_search(step['search'],annotation['hypotheses'],current,added,known,
                                    source,used,pack['graph'],snapshot)
        else:
            outcome=_validate_search(step['search'],annotation['hypotheses'],current,added,known,source,used)
        prior_spans=[_code_path(known[identifier]) for identifier in current if known[identifier]['kind']=='code']
        for identifier in added:
            record=known[identifier]
            if record['kind']!='code':
                raise ValueError('Planned retrieval may add only same-snapshot code')
            path,start,end=_code_path(record)
            if any(path==prior and max(start,a)<=min(end,b) for prior,a,b in prior_spans):
                raise ValueError('Planned retrieval repeated a source span')
            prior_spans.append((path,start,end))
        total_added+=len(added);current=current+added
        if added:
            output=step['output_annotation']
            _annotation(output,[known[identifier] for identifier in current],case['case_id'],plan)
            if step['output_annotation_sha256']!=digest(output):
                raise ValueError('Planned generation linkage differs')
            annotation=output;generations+=1;calls+=1+output['provenance']['repair_count']
        elif step['output_annotation'] is not None or step['output_annotation_sha256'] is not None:
            raise ValueError('Planned generation requires new evidence')
        if not added and (plan['policy']==POLICY or outcome=='no_new_queries'):
            terminal=outcome
    final=plan['final_decision'];_decision(final,final=True)
    if steps and steps[-1]['action']!='retrieve' and final!={
            key:steps[-1][key] for key in ('action','reason','controller','fallback')}:
        raise ValueError('Planned final decision differs from terminal round')
    if annotation is None:
        expected='insufficient_evidence' if not any(row['kind']=='code' for row in seed) else 'provider_not_configured'
        if (steps or final['action']!='stop' or final['reason']!=expected or pack['verification'] is not None
                or pack['annotation'] is not None or current!=[row['id'] for row in pack['evidence']]):
            raise ValueError('Offline planned workflow claimed a model pass')
        verifier_passes=0
    else:
        if current!=[row['id'] for row in pack['evidence']] or annotation!=pack['annotation']:
            raise ValueError('Planned final generation or evidence inventory differs')
        if final['action']=='stop':
            expected='controller_stopped'
        else:
            expected=terminal or ('round_limit' if len(steps)==maximum else 'controller_skipped_retrieval')
        if terminal=='controller_stopped' and final['action']!='stop':
            raise ValueError('Stopped planned controller cannot continue verification')
        structural=verify_hypotheses(annotation,pack['evidence'])
        verification=pack['verification']
        if verification['reports']!=structural['reports'] or verification.get('independent_human') is not False:
            raise ValueError('Planned verification differs from final citations')
        if verification['kind']=='llm_separate_pass':
            if final['action']!='verify':
                raise ValueError('Stopped planned workflow cannot call verifier')
            validate_critique({'reviews':verification['reviews']},annotation,pack['evidence']);verifier_passes=1
        elif verification['kind']=='structural':
            verifier_passes=0
        else:
            raise ValueError('Unknown planned verification kind')
        calls+=verifier_passes
    if (total_added>18 or generations>maximum+1 or calls>plan['limits']['max_provider_calls']
            or plan['generation_passes']!=generations or type(plan['generation_passes']) is not int
            or plan['verifier_passes']!=verifier_passes or type(plan['verifier_passes']) is not int
            or plan['provider_calls']!=calls or type(plan['provider_calls']) is not int
            or plan['stop_reason']!=expected or pack['stop_reason']!=expected
            or expected not in STOP_REASONS):
        raise ValueError('Planned pass budget or stop reason differs')
    if planned_run_id(case,snapshot['snapshot_sha256'],pack['evidence'],pack['annotation'],plan,
                      pack.get('repository_context'))!=pack['run_id']:
        raise ValueError('Planned run identity differs')
    return deepcopy(plan)
