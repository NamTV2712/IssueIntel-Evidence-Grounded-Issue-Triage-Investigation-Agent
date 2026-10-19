"""Finalize saved public generation and critique without Git or a provider.

This replays authenticated stage bytes, not the full Git repository. Snapshot
content integrity remains the original pipeline checkpoint's trusted boundary.
The caller owns admission policy checks and immutable byte/hash authentication.
"""
from copy import deepcopy
import hashlib
import re

from issueintel.annotations.models import Annotation
from issueintel.annotations.public_inputs import MODEL_INPUT_PROJECTION_VERSION
from issueintel.annotations.structured import critic_schema, _validate_wire_schema
from issueintel.repository.snapshot import validate_repo_path
from issueintel.runtime.io import digest
from .comparison import compare_packs
from .pipeline import finish_pack
from .resume import validate_product_case
from .verifier import verify_hypotheses, validate_critique


SCHEMA = 'product_export_finalization_v1'
MODEL = 'openai/gpt-oss-120b'
_META = {'schema','stage','case_id','query_sha256','snapshot_sha256','commit_sha',
         'provider','model','refinement_rounds','payload','evaluation_performed','automatic_retry'}
_PREPARED = {'schema_version','case','intake','snapshot','graph','evidence','annotation',
             'verification','leakage_audit','trace','stop_reason','recommended_steps','confidence','run_id'}


def _exact(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError('Invalid '+label+' fields')


def _hex(value, size=64):
    return type(value) is str and re.fullmatch('[a-f0-9]{'+str(size)+'}', value) is not None


def _text(value):
    return type(value) is str and bool(value.strip()) and '\x00' not in value


def _canonical_equal(left, right):
    """Keep booleans distinct from integers when comparing security assertions."""
    return digest(left) == digest(right)


def _snapshot_descriptor(snapshot):
    """Validate saved metadata, without pretending it contains original blobs."""
    _exact(snapshot, {'repository','commit_sha','tree_sha','files','protocol','cutoff',
                      'availability','skipped','committer_time','snapshot_sha256'}, 'snapshot descriptor')
    if (not _text(snapshot['repository']) or not _text(snapshot['committer_time'])
            or not _hex(snapshot['snapshot_sha256'])
            or not any(_hex(snapshot['commit_sha'],n) and _hex(snapshot['tree_sha'],n) for n in (40,64))
            or snapshot['protocol'] != 'benchmark_base' or snapshot['cutoff'] is not None
            or type(snapshot['files']) is not list or type(snapshot['skipped']) is not list
            or type(snapshot['availability']) is not dict
            or snapshot['availability'].get('status') != 'unknown'
            or snapshot['availability'].get('historical_certified') is not False):
        raise ValueError('Invalid saved product snapshot descriptor')
    paths = set()
    for file in snapshot['files']:
        _exact(file, {'path','sha256','blob_sha','raw_sha256','encoding'}, 'saved source file')
        path = validate_repo_path(file['path'])
        if (path in paths or not _hex(file['sha256']) or not _hex(file['raw_sha256'])
                or not any(_hex(file['blob_sha'],n) for n in (40,64)) or not _text(file['encoding'])):
            raise ValueError('Invalid saved source file identity')
        paths.add(path)


def replay_checkpoints(verification_input, verification_record, *, expected_policy=None):
    """Replay two authenticated stage objects. No key, Git or network is read.

    Admission supplies today's resume_policy() as expected_policy. A saved child
    is replayed against the recorded policy instead, preserving historical reads.
    """
    try:
        return _replay_checkpoints(verification_input,verification_record,expected_policy=expected_policy)
    except (KeyError,TypeError,AttributeError):
        raise ValueError('Invalid saved finalization checkpoint structure') from None


def _replay_checkpoints(verification_input, verification_record, *, expected_policy):
    for record, stage in ((verification_input,'verification_input'), (verification_record,'verification')):
        _exact(record, _META, 'finalization checkpoint')
        if (record['schema'] != 'product_investigation_stage_v1' or record['stage'] != stage
                or record['provider'] != 'groq' or record['model'] != MODEL
                or record['evaluation_performed'] is not False or record['automatic_retry'] is not False
                or type(record['refinement_rounds']) is not int or record['refinement_rounds'] not in (0,1)
                or not _text(record['case_id']) or not _hex(record['query_sha256'])
                or not _hex(record['snapshot_sha256'])
                or not any(_hex(record['commit_sha'],n) for n in (40,64))):
            raise ValueError('Checkpoint is not an original supported public stage')
    if not _canonical_equal({k:v for k,v in verification_input.items() if k not in {'stage','payload'}},
                            {k:v for k,v in verification_record.items() if k not in {'stage','payload'}}):
        raise ValueError('Verification checkpoint source binding changed')
    payload = verification_input['payload']
    _exact(payload, {'prepared','policy_sha256','max_output_tokens'}, 'verification input payload')
    if (not _hex(payload['policy_sha256']) or (expected_policy is not None and payload['policy_sha256'] != expected_policy)
            or type(payload['max_output_tokens']) is not int or not 0 < payload['max_output_tokens'] <= 65536):
        raise ValueError('Saved finalization policy or output bound changed')
    prepared = deepcopy(payload['prepared'])
    if (type(prepared) is not dict or not _PREPARED <= set(prepared)
            or set(prepared) - _PREPARED - {'refinement','repository_context','stage_checkpoints'}):
        raise ValueError('Unsupported original prepared pack fields')
    case, snapshot = prepared['case'], prepared['snapshot']
    validate_product_case(case, snapshot['protocol'])
    _snapshot_descriptor(snapshot)
    if (type(prepared['schema_version']) is not int or prepared['schema_version'] != 1
            or not _hex(prepared['run_id'],24) or prepared['stop_reason'] != 'verification_pending'
            or prepared['verification'] is not None
            or verification_input['case_id'] != case['case_id']
            or verification_input['commit_sha'] != snapshot['commit_sha']
            or verification_input['snapshot_sha256'] != snapshot['snapshot_sha256']
            or verification_input['query_sha256'] != hashlib.sha256(case['problem_statement'].encode()).hexdigest()
            or type(prepared['trace']) is not list or not prepared['trace']
            or prepared['trace'][-1].get('stage') != 'decision' or prepared['trace'][-1].get('action') != 'verify'
            or not _canonical_equal(prepared['confidence'], {'level':'unverified','probability':None})):
        raise ValueError('Prepared source/query or truth binding changed')
    leakage = prepared['leakage_audit']
    _exact(leakage, {'gold_in_inference','snapshot_protocol','strict_issue_time_verified','snapshot_issue_time_verified',
                     'original_query_verified','issue_edit_history','snapshot_hash_verified','limitations'}, 'leakage audit')
    if (any(leakage[key] is not False for key in ('gold_in_inference','strict_issue_time_verified',
                                                'snapshot_issue_time_verified','original_query_verified'))
            or leakage['snapshot_hash_verified'] is not True or leakage['snapshot_protocol'] != 'benchmark_base'
            or leakage['issue_edit_history'] != 'unknown' or type(leakage['limitations']) is not list
            or any(not _text(item) for item in leakage['limitations'])):
        raise ValueError('Saved public truth assertions changed')
    graph = prepared['graph']
    if (type(graph) is not dict or graph.get('snapshot_sha256') != snapshot['snapshot_sha256']
            or graph.get('commit_sha') != snapshot['commit_sha']):
        raise ValueError('Prepared graph belongs to another snapshot')
    compare_packs(prepared, prepared)
    annotation = prepared['annotation']
    parsed = Annotation.from_dict(annotation).validate_evidence(prepared['evidence'])
    if (not _canonical_equal(parsed.to_dict(), annotation) or parsed.provenance.provider != 'groq'
            or parsed.provenance.model != MODEL or parsed.abstention.provenance.provider != 'groq'
            or parsed.abstention.provenance.model != MODEL
            or parsed.abstention.provenance.kind != 'llm'):
        raise ValueError('Saved public annotation provenance changed')
    _exact(verification_record['payload'], {'verification'}, 'verification payload')
    verification = deepcopy(verification_record['payload']['verification'])
    _exact(verification, {'kind','reports','reviews','model_input_projection','independent_human','same_model_bias_possible'}, 'saved verifier')
    structural = verify_hypotheses(annotation, prepared['evidence'])
    if (verification['kind'] != 'llm_separate_pass' or verification['independent_human'] is not False
            or verification['same_model_bias_possible'] is not True
            or verification['model_input_projection'] != MODEL_INPUT_PROJECTION_VERSION
            or not _canonical_equal(verification['reports'], structural['reports'])):
        raise ValueError('Saved verifier assertions differ from structural replay')
    reviews = {'reviews': verification['reviews']}
    _validate_wire_schema(reviews, critic_schema([h['id'] for h in annotation['hypotheses']],
                                               [e['id'] for e in prepared['evidence']]))
    validate_critique(reviews, annotation, prepared['evidence'])
    # Reuse the ordinary assembly's public annotation and correctness gates.
    finish_pack(prepared, annotation, verification, model=MODEL)
    return prepared, verification


def bind_source_request(request, prepared, *, refinement_rounds):
    """Bind the original saved product authority, without fetching issue/Git."""
    from .workbench import validate_request
    if not _canonical_equal(validate_request(request), request):
        raise ValueError('Original source request is not canonical')
    mode = request['mode']
    if (mode not in {'local','github','example'} or request['provider'] != 'groq'
            or request['depth'] not in {'quick','deep'}
            or refinement_rounds != (1 if request['depth'] == 'deep' else 0)):
        raise ValueError('Finalization requires original product Quick or Deep')
    case = prepared['case']
    if mode == 'local':
        expected = {'case_id':'local:'+digest(request)[:16], 'repository':request['repository'].strip(),
                    'revision':request['revision'].strip(),'problem_statement':request['issue_text'],'issue_created_at':None}
        if not _canonical_equal(case, expected):
            raise ValueError('Local checkpoint differs from its saved request')
    elif mode == 'github':
        from issueintel.integrations.github import github_input
        source = github_input(request['issue_url'])
        canonical = github_input(case.get('issue_url',''))
        if (case['case_id'] != canonical.issue_url or canonical.repository.casefold() != source.repository.casefold()
                or canonical.issue_number != source.issue_number
                or case['repository'].casefold() != source.repository.casefold()
                or case['revision'] != request['revision']):
            raise ValueError('GitHub checkpoint differs from its saved request')
    elif (case['case_id'] != 'synthetic:parse-list' or case['repository'] != 'synthetic/example'
            or case['revision'] != prepared['snapshot']['commit_sha'] or case.get('issue_created_at') is not None
            or case['problem_statement'] != 'parser.parse_list fails on None input. Inspect how missing values are handled.'):
        raise ValueError('Example checkpoint differs from its fixed product input')


def build_finalized_pack(verification_input, verification_record, *, source_job_id, source_request_sha256,
                         verification_input_sha256, verification_sha256, expected_policy=None):
    if not _hex(source_job_id,24) or any(not _hex(value) for value in
            (source_request_sha256,verification_input_sha256,verification_sha256)):
        raise ValueError('Invalid finalization source hash binding')
    prepared, verification = replay_checkpoints(verification_input, verification_record, expected_policy=expected_policy)
    pack = finish_pack(prepared, prepared['annotation'], verification, model=MODEL)
    pack.pop('stage_checkpoints',None)  # Child never owns the parent's checkpoint files.
    pack['finalized_from'] = {'schema':SCHEMA,'source_job_id':source_job_id,
        'source_request_sha256':source_request_sha256,'verification_input_sha256':verification_input_sha256,
        'verification_sha256':verification_sha256,'prepared_sha256':digest(prepared),
        'verification_payload_sha256':digest(verification), 'policy_sha256':verification_input['payload']['policy_sha256'],
        'generation_reused':True,'verification_reused':True,'model_calls':0,'no_api_required':True,
        'automatic_retry':False,'full_git_replay_performed':False}
    pack['trace'] = deepcopy(prepared['trace']) + [
        {'stage':'generation_reused','model':MODEL,'source_job_id':source_job_id},
        {'stage':'verification_reused','kind':verification['kind'],'source_job_id':source_job_id},
        {'stage':'export_finalized','model_calls':0,'full_git_replay_performed':False}]
    pack['run_id'] = digest({'assembled_run_id':pack['run_id'],'finalized_from':pack['finalized_from']})[:24]
    return pack
