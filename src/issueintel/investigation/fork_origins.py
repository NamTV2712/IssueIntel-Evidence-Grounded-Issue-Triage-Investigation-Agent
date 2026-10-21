"""Shared preparation and saved provenance for actual product fork workflows."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re

MODES={'followup','clarification','check_followup','memory_followup','context'}
MARKER='\n\n[User follow-up context; unverified]\n'


def canonical_request(request):
    if request.get('mode')=='memory_followup':
        from .memory_followups import validate_request
        return validate_request(request,saved=True)
    if request.get('mode')=='check_followup':
        from .check_followups import validate_request
        return validate_request(request,saved=True)
    from .workbench import validate_request
    return validate_request(request)


def validate_case(case,protocol,fork):
    origin=fork['origin'];request=origin['request'];identifier=origin['job_id']
    if (request.get('mode') not in MODES or not re.fullmatch('[a-f0-9]{24}',identifier)
            or protocol!='benchmark_base' or any(k in case for k in ('split','instance_id'))
            or request['depth'] not in {'quick','deep'} or canonical_request(request)!=request):
        raise ValueError('Invalid product fork recovery authority')
    if request['mode']=='context':
        from .resume import validate_product_case
        # Context selection can also follow an existing user follow-up.
        if not case['case_id'].startswith('followup:'):validate_product_case(case,protocol)
    elif case['case_id']!='followup:'+identifier:
        raise ValueError('Fork case belongs to another original job')


def prepare(host,row,*,admission=True,recorded=None,context=None,wait_for_dispatch=False):
    """Rebuild exact user/source bindings. Saved replay never acquires Git."""
    request=row['request'];mode=request['mode'];identifier=row['id']
    if mode not in MODES or canonical_request(request)!=request:raise ValueError('Invalid canonical fork request')
    if admission:host.dossiers._origin(request['parent_job_id'],max_hops=10)
    clarification=observations=memory=None;snapshot=None;repository_context=None
    if mode=='memory_followup':
        result=host.memory_followups.input(identifier,request,admission=admission,wait_for_dispatch=wait_for_dispatch)
        parent,raw,depth,memory=result[:4]
        if admission:snapshot=result[4]
    elif mode=='check_followup':
        host.check_followups.assert_intent(request,identifier,wait_for_dispatch=wait_for_dispatch)
        parent,raw,depth,observations=host.check_followups.input(request,current=admission)
    elif mode=='clarification':parent,raw,depth,clarification=host._clarification_input(request)
    elif mode=='context':
        if admission:parent,raw,depth,repository_context=host._selected_context(request)
        else:
            from issueintel.retrieval.repository_context import context_evidence,validate_selections
            parent,raw,depth=host._context_parent(request['parent_job_id'],request['parent_pack_sha256'])
            repository_context=deepcopy(context)
            context_evidence(repository_context,parent['case'],protocol='benchmark_base')
            if validate_selections(repository_context['selection']['sources'])!=request['selected_sources']:
                raise ValueError('Saved context selection differs from its original request')
    else:parent,raw,depth=host._load_pack(request['parent_job_id'])
    host._clarification_product(request['parent_job_id'],parent);host._reject_waiting_fork(parent)
    if depth>=8:raise ValueError('Follow-up depth is bounded to eight generations')
    if admission and snapshot is None:
        from .question_jobs import replay_snapshot
        snapshot=replay_snapshot(parent)
    selection=followup=None
    if mode=='context':
        case=dict(parent['case'],revision=parent['snapshot']['commit_sha'])
        selection={'schema':'product_context_selection_v1','parent_job_id':request['parent_job_id'],
            'parent_pack_sha256':sha256(raw).hexdigest(),'depth':depth+1,'selected_sources':request['selected_sources'],
            'context_sha256':repository_context['sha256'],'historical_verified':False}
        from .intake import inspect_issue
        intake=inspect_issue(case['problem_statement'])
    else:
        additional=(memory or observations or clarification or request)['additional_context']
        text=parent['case']['problem_statement']+MARKER+additional
        if len(text)>100000:raise ValueError('Combined query exceeds the public input bound')
        case={'case_id':'followup:'+identifier,'repository':parent['case']['repository'],
            'revision':parent['snapshot']['commit_sha'],'problem_statement':text,'issue_created_at':None}
        if parent['case'].get('issue_url'):case['issue_url']=parent['case']['issue_url']
        followup={'schema':'investigation_followup_v1','parent_job_id':request['parent_job_id'],
            'parent_pack_sha256':sha256(raw).hexdigest(),
            'source_case_id':(parent.get('followup') or {}).get('source_case_id',parent['case']['case_id']),
            'additional_context':additional,'context_sha256':sha256(additional.encode()).hexdigest(),
            'created_at':row['created_at'],'historical_query_verified':False,'depth':depth+1}
        if clarification:
            followup['clarification']=clarification
            if request.get('question_job_id'):followup['question_job_id']=request['question_job_id']
        if observations:followup['check_observations']=observations
        if memory:followup.update(memory_selection=memory,memory_policy_sha256=request['policy_sha256'])
        if observations:
            from .check_followups import user_cue_text
            addition=user_cue_text(observations)
        else:addition='\n'.join(r['answer'] for r in clarification['answers']) if clarification else additional
        cue=host._cue_text(parent)+(('' if memory else '\n'+addition))
        intake=host._inspect_query(text,cue)
    projection={'origin':{'job_id':identifier,'request':deepcopy(request)},'followup':followup,
                'context_selection':selection,'intake':intake}
    validate_case(case,'benchmark_base',projection)
    if recorded is not None and projection!=recorded:raise ValueError('Recorded fork source or user input changed')
    extra={}
    if snapshot is not None:extra['snapshot']=snapshot
    if repository_context is not None:extra['repository_context']=repository_context
    return {'case':case,'local':Path(parent['snapshot']['repository']),'extra':extra,'projection':projection,
            'snapshot':parent['snapshot'],'repository_context':repository_context,'depth':depth+1}


def attach(pack,projection):
    """Preserve the same report provenance for initial and recovered forks."""
    link=projection['followup'];pack['intake']=deepcopy(projection['intake'])
    if link:
        pack['followup']=deepcopy(link)
        if link.get('memory_selection'):
            receipt=link['memory_selection']
            pack['trace'].append({'stage':'selected_memory_context','selection_sha256':receipt['sha256'],
                'context_sha256':receipt['context_sha256'],'selected_items':len(receipt['items']),
                'historical_verified':False,'causal_verified':False})
        if link.get('clarification'):
            receipt=link['clarification']
            pack['trace'].append({'stage':'clarification_answers','questions_sha256':receipt['questions_sha256'],
                'answers_sha256':receipt['sha256'],'parent_pack_sha256':link['parent_pack_sha256'],
                'answer_count':len(receipt['answers']),'historical_verified':False})
        if link.get('check_observations'):
            receipt=link['check_observations']
            pack['trace'].append({'stage':'check_observation_context','plan_sha256':receipt['source']['plan_sha256'],
                'journal_sha256':receipt['journal_sha256'],'receipt_sha256':receipt['sha256'],
                'observation_ids':receipt['observation_ids'],'historical_verified':False,'user_reports_verified':False})
    if projection['context_selection']:pack['context_selection']=deepcopy(projection['context_selection'])
    return pack


def validate_output(pack,projection):
    expected=attach({'trace':[]},projection)
    if any(pack.get(k)!=expected.get(k) for k in ('followup','context_selection','intake')):
        raise ValueError('Recovered fork provenance differs from its original input')
    suffix=expected['trace']
    if suffix and pack['trace'][-len(suffix):]!=suffix:raise ValueError('Recovered fork trace provenance changed')
