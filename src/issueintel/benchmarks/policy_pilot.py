"""Preregistered DEV-only policy ablation. Inference never loads restricted gold."""
from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
import csv
import json
import re
import time

from issueintel.annotations.structured import dispatch
from issueintel.investigation.policy_agent import ARMS,LIMITS,POLICIES,run_policy_agent,context_binding
from issueintel.investigation.discriminating_checks import FINAL_INSTRUCTION
from issueintel.investigation.providers import GroqProvider
from issueintel.investigation.providers import ProviderError
from issueintel.runtime.budget import BudgetDenied
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import digest,write_json
from .dev_context import public_catalog,accepted_context,PARENT,development_catalog
from .strict_dev import _lock,_checked


SCOPE='accepted_original_main_dev_paired_policy_diagnostic_v1'


def _seal(value):return dict(value,sha256=digest(value))


def _read(path):
    row=json.loads(path.read_bytes())
    if row.get('sha256')!=digest({k:v for k,v in row.items() if k!='sha256'}):raise ValueError('Diagnostic content hash changed')
    return row


def _fingerprint(root):
    paths=sorted((root/'src/issueintel').rglob('*.py'))
    paths += [root/'configs'/name for name in ('runtime.toml','prices.json','author_decisions.json','investigation_pilot_authorization.json')]
    return {path.relative_to(root).as_posix():sha256(path.read_bytes()).hexdigest() for path in paths}


def _authorization(root):
    value=json.loads((root/'configs/investigation_pilot_authorization.json').read_bytes())
    if (value['scope']!=SCOPE or value['provider']!='groq' or value['model']!='openai/gpt-oss-120b'
        or value['selected_case_count']!=25 or value['max_provider_calls_per_arm']!=LIMITS['provider_calls']
        or value['max_tool_rounds_per_arm']!=LIMITS['tool_rounds'] or value['reasoning_effort']!='high'
        or any(value.get(key) is not False for key in ('holdout_access_authorized','live_access_authorized','new_freeze_authorized','bulk_annotation_restart_authorized','jev_spending_authorized'))
        or value.get('gold_allowed_only_in_separate_dev_scoring') is not True):
        raise ValueError('Missing matching DEV diagnostic authorization')
    return value


def _directory(root,identifier):
    if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{64}',identifier):raise ValueError('Choose exact registered diagnostic SHA')
    path=(root/'artifacts/experiments/policy_pilot'/identifier).resolve()
    if not path.is_relative_to(root):raise ValueError('Diagnostic path outside workspace')
    return path


def register_policy_pilot(root):
    root=Path(root).resolve();_authorization(root)
    catalog=public_catalog(root);groups=defaultdict(list)
    for row in catalog['cases']:groups[row['repository']].append(row)
    selected=[]
    while len(selected)<25 and any(groups.values()):
        for repo in sorted(groups):
            if groups[repo] and len(selected)<25:selected.append(groups[repo].pop(0))
    if len(selected)!=25:raise ValueError('Need 25 accepted original Main Dev cases')
    cases=[]
    for row in selected:
        case,snapshot,receipt=accepted_context(root,row['case_id'])
        if case['split']!='main_dev' or snapshot.protocol!='issue_time':raise ValueError('Diagnostic must retain accepted strict Main Dev')
        cases.append({'case_id':row['case_id'],'repository':row['repository'],
                      'source':context_binding(case,snapshot),'query_receipt_sha256':digest(receipt)})
    spec={'schema':'paired_policy_pilot_v1','scope':SCOPE,'registered_at':datetime.now(timezone.utc).isoformat(),
          'parent_cohort':PARENT,'checkpoint_sha256':catalog['checkpoint_sha256'],
          'selection':'first_25_round_robin_repository_then_case_id_among_current_accepted_original_dev_no_outcome_filter',
          'cases':cases,'fingerprint':_fingerprint(root),'policies':POLICIES,'final_instruction':FINAL_INSTRUCTION,
          'limits':LIMITS,'model':'openai/gpt-oss-120b','reasoning_effort':'high',
          'tools':['search_code','read_code'],'order':'alternate_first_arm_by_selected_case_index',
          'comparison_scope':'prompt_policy_ablation_on_shared_harness_not_complete_product_or_external_agent_comparison',
          'primary_metrics':['file_recall_at_5_all_selected','completion_coverage','tokens_and_cost','provider_and_total_latency'],
          'secondary_metrics':['model_asserted_check_separation_not_semantic_quality'],
          'semantic_quality':'requires_separate_review_unreviewed_by_default','trials_per_case_arm':1,
          'holdout_or_live_access':False,'new_cohort_freeze':False,'bulk_annotation':False}
    sealed=_seal(spec);directory=_directory(root,sealed['sha256']);directory.mkdir(parents=True,exist_ok=True)
    write_json(directory/'registration.json',sealed)
    return {'experiment_sha256':sealed['sha256'],'selected_cases':len(cases),'model':spec['model'],'registration':str(directory/'registration.json')}


def load_registration(root,identifier):
    root=Path(root).resolve();_authorization(root);directory=_directory(root,identifier)
    spec=_read(directory/'registration.json')
    if (spec['sha256']!=identifier or spec['scope']!=SCOPE or spec['fingerprint']!=_fingerprint(root)
        or spec['policies']!=POLICIES or spec['final_instruction']!=FINAL_INSTRUCTION or spec['limits']!=LIMITS
        or spec['holdout_or_live_access'] is not False or spec['new_cohort_freeze'] is not False
        or spec['bulk_annotation'] is not False or len(spec['cases'])!=25
        or len({row['case_id'] for row in spec['cases']})!=25):raise ValueError('Diagnostic registration/policy changed')
    return spec,directory


def _context(root,row):
    case,snapshot,receipt=accepted_context(root,row['case_id'])
    if case['split']!='main_dev' or snapshot.protocol!='issue_time' or context_binding(case,snapshot)!=row['source'] or digest(receipt)!=row['query_receipt_sha256']:
        raise ValueError('Registered DEV context changed')
    return case,snapshot


def _case_dir(directory,cid):return directory/'cases'/sha256(cid.encode()).hexdigest()[:24]


def _owned_call(root,directory,identifier,cid,arm,stage,messages,schema,maximum,provider_factory,*,allow_new=True):
    path=directory/('call-'+stage+'.json')
    request_hash=digest({'messages':messages,'schema':schema,'max_output_tokens':maximum})
    binding={'experiment_sha256':identifier,'case_id':cid,'arm':arm,'stage':stage,'request_sha256':request_hash}
    config=replace(RuntimeConfig.load(root),max_output_tokens=LIMITS['final_output_tokens'],reasoning_effort='high')
    if path.exists():
        saved=_read(path)
        if saved['binding']!=binding:raise ValueError('Saved paid call source changed')
        for receipt in saved['receipts'].values():
            if config.ledger().receipt(receipt['reservation_id'])!=receipt:raise ValueError('Paid receipt changed')
        if saved['status']=='returned':return saved['response']
        raise ValueError('Previous paid attempt incomplete/failed; never retry automatically')
    if not allow_new:raise ValueError('Completed arm has a missing paid-call record; no automatic replacement')
    state={'schema':'policy_pilot_call_v1','binding':binding,'status':'intent','receipts':{},'response':None,'latency_seconds':None}
    write_json(path,_seal(state))
    def receipt(record):
        state['receipts'][record['reservation_id']]=record
        write_json(path,_seal(state))  # Ownership must persist before HTTP.
    provider=provider_factory(config,call_context={'category':'ARTIFACT_GENERATION_COST','stage':'policy_pilot_'+stage,'reasoning_effort':'high','diagnostic':True},on_receipt=receipt)
    started=time.monotonic()
    try:
        response=dispatch(provider,messages,max_output_tokens=maximum,schema=schema)
        state.update(status='returned',response=response,latency_seconds=round(time.monotonic()-started,6))
        write_json(path,_seal(state));return response
    except Exception:
        state.update(status='failed',latency_seconds=round(time.monotonic()-started,6));write_json(path,_seal(state));raise


def run_policy_pilot(root,identifier,*,limit=25,provider_factory=GroqProvider):
    if type(limit) is not int or not 1<=limit<=25:raise ValueError('Run 1–25 registered cases')
    root=Path(root).resolve();spec,directory=load_registration(root,identifier)
    results=[];stop=None
    with _lock(root,{'policy_pilot':identifier}):
        if limit>1:
            for arm in ARMS:
                gate=_read(_case_dir(directory,spec['cases'][0]['case_id'])/arm/'result.json')
                if gate['status']!='completed' or gate['source']!=spec['cases'][0]['source']:
                    raise ValueError('Both first-case arms must pass the transport/contract pilot before the remaining cases')
        for index,row in enumerate(spec['cases'][:limit]):
            try:case,snapshot=_context(root,row)
            except (ValueError,OSError,KeyError):
                stop='accepted_source_replay_failed';break
            order=ARMS if index%2==0 else tuple(reversed(ARMS))
            for arm in order:
                target=_case_dir(directory,row['case_id'])/arm;target.mkdir(parents=True,exist_ok=True)
                path=target/'result.json'
                if path.exists():
                    saved=_read(path)
                    if saved['source']!=row['source'] or saved['arm']!=arm:raise ValueError('Saved arm source changed')
                    if saved['status']=='completed':
                        replay=run_policy_agent(case,snapshot,arm,lambda stage,messages,schema,maximum:_owned_call(
                            root,target,identifier,row['case_id'],arm,stage,messages,schema,maximum,provider_factory,allow_new=False))
                        if replay!=saved['result']:raise ValueError('Completed arm does not replay from saved calls and snapshot')
                    results.append({'case_id':row['case_id'],'arm':arm,'status':saved['status']});continue
                started=time.monotonic()
                try:
                    response=run_policy_agent(case,snapshot,arm,lambda stage,messages,schema,maximum:_owned_call(
                        root,target,identifier,row['case_id'],arm,stage,messages,schema,maximum,provider_factory))
                    saved={'schema':'policy_pilot_arm_v1','experiment_sha256':identifier,'case_id':row['case_id'],
                           'arm':arm,'source':row['source'],'status':'completed','result':response,
                           'total_latency_seconds':round(time.monotonic()-started,6)}
                except Exception as error:
                    saved={'schema':'policy_pilot_arm_v1','experiment_sha256':identifier,'case_id':row['case_id'],
                           'arm':arm,'source':row['source'],'status':'failed','error_type':type(error).__name__,
                           'total_latency_seconds':round(time.monotonic()-started,6)}
                    if isinstance(error,(ProviderError,BudgetDenied,OSError)):
                        stop='provider_or_storage_failure_no_automatic_retry'
                write_json(path,_seal(saved));results.append({'case_id':row['case_id'],'arm':arm,'status':saved['status']})
                if stop:break
            progress=_seal({'experiment_sha256':identifier,'results':results,'stop_reason':stop})
            write_json(directory/'progress.json',progress)
            if stop:break
        if len(results)==50:
            refs={str(path.relative_to(directory)):sha256(path.read_bytes()).hexdigest() for path in sorted((directory/'cases').rglob('*.json'))}
            write_json(directory/'inference-sealed.json',_seal({'experiment_sha256':identifier,'files':refs,'gold_read':False}))
    return {'experiment_sha256':identifier,'completed_arms':sum(row['status']=='completed' for row in results),
            'failed_arms':sum(row['status']=='failed' for row in results),'stop_reason':stop,'inference_sealed':(directory/'inference-sealed.json').exists()}


def score_policy_pilot(root,identifier):
    """Separate offline DEV scorer. Not imported or called by model inference."""
    import pyarrow.parquet as parquet
    from issueintel.evaluation.metrics import localization_metrics
    from .localization import _path
    root=Path(root).resolve();spec,directory=load_registration(root,identifier)
    seal=_read(directory/'inference-sealed.json')
    if seal['experiment_sha256']!=identifier or seal['gold_read'] is not False:raise ValueError('Inference not sealed')
    for name,checksum in seal['files'].items():
        path=(directory/name).resolve()
        if not path.is_relative_to(directory) or sha256(path.read_bytes()).hexdigest()!=checksum:raise ValueError('Sealed inference changed')
    catalog=development_catalog(root)
    ids=[row['case_id'].removeprefix('verified:') for row in spec['cases']]
    reference=_checked(root,catalog['_authority']['sources'][0])
    rows=parquet.read_table(reference,columns=['instance_id','patch'],filters=[('instance_id','in',ids)]).to_pylist()
    if len(rows)!=25 or {row['instance_id'] for row in rows}!=set(ids):raise ValueError('DEV gold selection mismatch')
    gold={}
    for row in rows:
        files=[];header=False;old=None
        for line in row['patch'].splitlines():
            if line.startswith('diff --git '):header=True;old=None
            elif line.startswith('@@ '):header=False
            elif header and line.startswith('--- '):old=_path(line[4:])
            elif header and line.startswith('+++ '):
                path=old or _path(line[4:])
                if path and path not in files:files.append(path)
            elif header and line.startswith('rename from '):
                path=_path(line[len('rename from '):],strip_prefix=False)
                if path not in files:files.append(path)
        gold['verified:'+row['instance_id']]=files
    details=[];review=[]
    for item in spec['cases']:
        for arm in ARMS:
            target=_case_dir(directory,item['case_id'])/arm;saved=_read(target/'result.json')
            result=saved.get('result');calls=[_read(path) for path in sorted(target.glob('call-*.json'))]
            receipts=[receipt for call in calls for receipt in call['receipts'].values()]
            details.append({'case_id':item['case_id'],'arm':arm,'status':saved['status'],
                'localization':localization_metrics(result['findings']['ranked_paths'] if result else [],gold[item['case_id']],k=5),
                'unavailable_gold_files':[path for path in gold[item['case_id']] if path not in {f.path for f in _context(root,item)[1].files}],
                'provider_calls':len(calls),'tool_actions':sum(action['action']!='finish' for action in result['actions']) if result else None,
                'charged_or_reserved_tokens':sum(r['tokens_charged_or_reserved'] for r in receipts),
                'charged_or_reserved_micro_usd':sum(r['micro_usd_charged_or_reserved'] for r in receipts),
                'provider_latency_seconds':sum(call['latency_seconds'] or 0 for call in calls),
                'total_latency_seconds':saved['total_latency_seconds'],
                'recommended_check':result['comparison']['recommended_check_id'] if result else None,
                'semantic_check_quality':'unreviewed'})
            if not result:continue
            review.append({'review_id':digest({'experiment':identifier,'case':item['case_id'],'arm':arm})[:20],
                'case_id':item['case_id'],'findings_json':json.dumps(result['findings'],ensure_ascii=False),
                'evidence_json':json.dumps(model_review_evidence(result['evidence']),ensure_ascii=False),
                'specific_procedure':'','predictions_grounded':'','outcomes_distinct':'','controls_adequate':'','useful_next_action':'','reviewer':''})
    aggregates={}
    for arm in ARMS:
        values=[row for row in details if row['arm']==arm]
        recall=[row['localization']['recall_at_k'] for row in values if row['localization']['recall_at_k'] is not None]
        aggregates[arm]={'cases':len(values),'scorable_cases':len(recall),'completed':sum(row['status']=='completed' for row in values),'file_recall_at_5':sum(recall)/len(recall) if recall else None,
            'mrr':sum(row['localization']['mrr'] for row in values if row['localization']['mrr'] is not None)/len(recall) if recall else None,
            'provider_calls':sum(row['provider_calls'] for row in values),'tokens':sum(row['charged_or_reserved_tokens'] for row in values),
            'usd_charged_or_reserved':sum(row['charged_or_reserved_micro_usd'] for row in values)/1000000,
            'provider_latency_seconds':sum(row['provider_latency_seconds'] for row in values),
            'total_latency_seconds':sum(row['total_latency_seconds'] for row in values),
            'proposed_recommendations':sum(row['recommended_check'] is not None for row in values)}
    report=_seal({'schema':'paired_policy_diagnostic_report_v1','experiment_sha256':identifier,
        'scope':spec['comparison_scope'],'selected_cases':25,'aggregates':aggregates,'cases':details,
        'localization_is_patch_file_proxy':True,'semantic_quality':'unreviewed','trials_per_arm_case':1,
        'holdout_or_live_access':False,'production_or_external_agent_performance_claim':False})
    write_json(directory/'diagnostic-report.json',report)
    lines=['# IssueIntel DEV policy diagnostic','',
           'Experiment SHA256: '+identifier,
           '25 original accepted Main Dev cases; one trial per case/arm. Holdout and Live were not accessed.',
           'This compares two prompt policies in the same read-only harness, not complete IssueIntel against Copilot/Claude or another external product.',
           'Both arms have the same model, effort, initial code context, tools, output contract and upper budgets. Actual usage may differ.',
           'File Recall@5 is a patch-file localization proxy, not causal accuracy. Unavailable files stay in the denominator.',
           'Check predictions, separation and estimated effort are model assertions. Their semantic utility is unreviewed.',
           'No human agreement, statistically significant advantage or production performance is claimed.','',
           '| Arm | Completed / selected | File Recall@5 | MRR | Calls | Tokens charged/reserved | USD charged/reserved |',
           '| --- | --- | --- | --- | --- | --- | --- |']
    for arm,metrics in aggregates.items():
        lines.append(f"| {arm} | {metrics['completed']} / 25 | {metrics['file_recall_at_5']} | {metrics['mrr']} | {metrics['provider_calls']} | {metrics['tokens']} | {metrics['usd_charged_or_reserved']} |")
    lines+=['','Totals use attached durable local receipts; they are not provider invoices.',
            'Arm elapsed time includes inference/retrieval/IO and excludes accepted-source preparation. Provider latency is reported separately in JSON.',
            'Review specific procedure, grounded predictions, distinct outcomes, controls and useful next action in blind-check-review.csv. Blank fields are not human labels.',
            'Diagnostic report SHA256: '+report['sha256'],'']
    (directory/'diagnostic-report.md').write_text('\n'.join(lines),encoding='utf-8')
    with (directory/'blind-check-review.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(review[0]));writer.writeheader();writer.writerows(sorted(review,key=lambda row:row['review_id']))
    return report


def model_review_evidence(evidence):
    from issueintel.annotations.public_inputs import model_evidence
    return model_evidence(evidence)
