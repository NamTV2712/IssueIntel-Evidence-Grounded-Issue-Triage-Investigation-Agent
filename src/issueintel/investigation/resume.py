"""Explicit verifier continuation from immutable product-only checkpoints."""
from copy import deepcopy
import hashlib
from pathlib import Path

from issueintel.runtime.io import digest
from issueintel.repository.snapshot import build_snapshot


def resume_policy():
    package=Path(__file__).resolve().parents[1]
    paths=('investigation/resume.py','investigation/pipeline.py','investigation/verifier.py',
           'annotations/models.py','annotations/public_inputs.py','annotations/structured.py',
           'retrieval/repository_context.py','repository/snapshot.py',
           'investigation/providers.py','runtime/budget.py','runtime/config.py')
    return digest({name:hashlib.sha256((package/name).read_bytes()).hexdigest() for name in paths})


def validate_product_case(case,protocol):
    from .pipeline import public_case
    case=public_case(case)
    if (protocol!='benchmark_base' or case.get('split') is not None
            or not case['case_id'].startswith(('local:','synthetic:','https://github.com/'))):
        raise ValueError('Only original product investigations can resume verification')


def replay_verification_input(record):
    from .comparison import compare_packs
    from .pipeline import validate_prepared_evidence, finish_pack
    from .verifier import verify_hypotheses
    if (record.get('schema')!='product_investigation_stage_v1' or record.get('stage')!='verification_input'
            or record.get('provider')!='groq' or record.get('evaluation_performed') is not False
            or record.get('automatic_retry') is not False):
        raise ValueError('Not a resumable public checkpoint')
    payload=record['payload'];prepared=deepcopy(payload['prepared'])
    case=prepared['case'];snapshot=prepared['snapshot']
    validate_product_case(case,snapshot['protocol'])
    if (payload.get('policy_sha256')!=resume_policy() or prepared.get('followup') or prepared.get('resumed_from')
            or prepared['stop_reason']!='verification_pending' or prepared['verification'] is not None
            or prepared['leakage_audit']['strict_issue_time_verified'] is not False
            or record['case_id']!=case['case_id'] or record['commit_sha']!=snapshot['commit_sha']
            or record['snapshot_sha256']!=snapshot['snapshot_sha256']
            or record['query_sha256']!=hashlib.sha256(case['problem_statement'].encode()).hexdigest()
            or not prepared['trace'] or prepared['trace'][-1].get('action')!='verify'):
        raise ValueError('Checkpoint policy or public source binding changed')
    maximum=payload['max_output_tokens']
    if type(maximum) is not int or not 0<maximum<=65536:
        raise ValueError('Invalid saved output bound')
    compare_packs(prepared,prepared)
    actual=build_snapshot(snapshot['repository'],snapshot['commit_sha'])
    descriptor=actual.to_dict()
    descriptor['files']=[{k:v for k,v in file.to_dict().items() if k!='text'} for file in actual.files]
    if digest(descriptor)!=digest(snapshot):
        raise ValueError('Saved snapshot no longer replays')
    validate_prepared_evidence(case,[e for e in prepared['evidence'] if e['kind']!='repository_context'],actual)
    annotation=prepared['annotation']
    if annotation['provenance'].get('provider')!=record['provider']:
        raise ValueError('Generation provider changed')
    structural=verify_hypotheses(annotation,prepared['evidence'])
    finish_pack(prepared,annotation,structural,model=record['model'])
    return prepared,maximum


def continue_verification(record,complete_json,*,on_stage=None):
    from .pipeline import finish_pack
    from .verifier import verify_hypotheses
    prepared,maximum=replay_verification_input(record)
    verification=verify_hypotheses(prepared['annotation'],prepared['evidence'],complete_json,max_output_tokens=maximum)
    if on_stage is not None:on_stage('verification',{'verification':verification})
    pack=finish_pack(prepared,prepared['annotation'],verification,model=record['model'])
    pack['trace']=deepcopy(prepared['trace'])+[
        {'stage':'generation_reused','model':record['model'],'policy_sha256':record['payload']['policy_sha256']},
        {'stage':'verification','kind':verification['kind'],'resumed':True}]
    return pack
