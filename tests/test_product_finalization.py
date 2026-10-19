"""Synthetic export recovery; the original two paid passes are never repeated."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace
import hashlib
import json
import threading

import pytest

from issueintel.investigation.finalization import replay_checkpoints
from issueintel.investigation.workbench import Workbench
from issueintel.runtime.io import write_json
from test_workbench import SyntheticProvider, completed


@pytest.fixture
def finalization_source(tmp_path,monkeypatch):
    config = SimpleNamespace(reasoning_effort='high',model='openai/gpt-oss-120b',max_output_tokens=512)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    provider = SyntheticProvider()
    wb = Workbench(tmp_path,provider_factory=lambda *a,**k:provider)
    service = wb.finalizations
    source = completed(wb,wb.submit({'mode':'example','provider':'groq','depth':'deep'})['id'])
    assert source['status']=='completed',source
    row = json.loads(wb._job_path(source['id']).read_bytes())
    wb._save(row,status='failed',stage='export')
    try:
        yield wb,service,source,provider
    finally:
        wb.close()


def approval(preview):
    return {key:preview[key] for key in ('verification_input_sha256','verification_sha256')} | {
        'confirmation':'finalize_saved_checkpoints_without_api'}


def stages(wb,source):
    return [json.loads(wb.stage_bytes(source['id'],stage)) for stage in ('verification_input','verification')]


def test_finalizes_with_zero_provider_no_git_and_reuses_exact_passes(finalization_source,monkeypatch):
    wb,service,source,provider = finalization_source
    inputs,verified = stages(wb,source)
    source_bytes = wb._job_path(source['id']).read_bytes()
    wb.provider_factory = lambda *a,**k:pytest.fail('Provider must not be constructed')
    monkeypatch.setattr('issueintel.investigation.resume.build_snapshot',lambda *a,**k:pytest.fail('No Git replay'))
    monkeypatch.setattr('issueintel.investigation.pipeline.build_snapshot',lambda *a,**k:pytest.fail('No Git replay'))
    preview = service.preview(source['id'])
    assert preview['available'] and preview['model_calls']==0 and preview['no_api_required'] is True
    child = completed(wb,service.submit(source['id'],approval(preview))['id'])
    assert child['status']=='completed',child
    pack = json.loads(wb.result_bytes(child['id']))
    request = json.loads(wb._job_path(child['id']).read_bytes())['request']
    service.validate_child(child['id'],pack,request)
    assert pack['annotation']==inputs['payload']['prepared']['annotation']
    assert pack['evidence']==inputs['payload']['prepared']['evidence']
    assert pack['verification']==verified['payload']['verification']
    assert 'stage_checkpoints' not in pack
    assert [step['stage'] for step in pack['trace'][-3:]]==['generation_reused','verification_reused','export_finalized']
    assert pack['finalized_from']['full_git_replay_performed'] is False
    assert wb._job_path(source['id']).read_bytes()==source_bytes
    assert len(provider.calls)==2
    assert service.submit(source['id'],approval(preview))['id']==child['id']
    with pytest.raises(ValueError):wb.preview_comment(child['id'])


def test_saved_replay_uses_recorded_policy_but_admission_uses_current(finalization_source,monkeypatch):
    wb,service,source,provider = finalization_source
    child = completed(wb,service.submit(source['id'],approval(service.preview(source['id'])))['id'])
    pack = json.loads(wb.result_bytes(child['id']))
    request = json.loads(wb._job_path(child['id']).read_bytes())['request']
    monkeypatch.setattr('issueintel.investigation.finalization_jobs.resume_policy',lambda:'f'*64)
    assert service.preview(source['id'])['available'] is False
    assert service.validate_child(child['id'],pack,request)==pack
    with pytest.raises(ValueError):service.submit(source['id'],approval(pack['finalized_from']))
    assert len(provider.calls)==2


def test_real_export_failure_recovers_without_source_result_or_new_api(tmp_path,monkeypatch):
    config = SimpleNamespace(reasoning_effort='high',model='openai/gpt-oss-120b',max_output_tokens=512)
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda _:config)
    def fail_export(*args,**kwargs):raise OSError('Synthetic disk/export interruption')
    monkeypatch.setattr('issueintel.investigation.workbench.export_pack',fail_export)
    provider = SyntheticProvider();wb = Workbench(tmp_path,provider_factory=lambda *a,**k:provider)
    try:
        source = completed(wb,wb.submit({'mode':'example','provider':'groq'})['id'])
        assert source['status']=='failed' and not source.get('result')
        assert {'verification_input','verification'} <= set(source['stage_checkpoints'])
        service = wb.finalizations
        wb.provider_factory = lambda *a,**k:pytest.fail('No replacement provider')
        child = completed(wb,service.submit(source['id'],approval(service.preview(source['id'])))['id'])
        assert child['status']=='completed',child
        pack = json.loads(wb.result_bytes(child['id']))
        request = json.loads(wb._job_path(child['id']).read_bytes())['request']
        service.validate_child(child['id'],pack,request)
        assert len(provider.calls)==2 and wb.job(source['id'])['status']=='failed'
    finally:wb.close()


@pytest.mark.parametrize('mutation',[
    'input_extra','verification_extra','record_mismatch','policy','output_bool','query','annotation_provider',
    'source_quality','strict_flag','graph_binding','independent_human_int','bias_int','review_extra','review_missing','reports_bool',
])
def test_replay_rejects_corruption_and_truth_type_changes(finalization_source,mutation):
    wb,service,source,provider = finalization_source
    inputs,verified = stages(wb,source)
    prepared = inputs['payload']['prepared'];verification = verified['payload']['verification']
    if mutation=='input_extra':inputs['unexpected']=True
    elif mutation=='verification_extra':verification['unexpected']=True
    elif mutation=='record_mismatch':verified['query_sha256']='f'*64
    elif mutation=='policy':inputs['payload']['policy_sha256']='f'*64
    elif mutation=='output_bool':inputs['payload']['max_output_tokens']=True
    elif mutation=='query':prepared['case']['problem_statement']='Changed query'
    elif mutation=='annotation_provider':prepared['annotation']['provenance']['provider']='other'
    elif mutation=='source_quality':prepared['annotation']['source_quality']={'label':'human'}
    elif mutation=='strict_flag':prepared['leakage_audit']['strict_issue_time_verified']=0
    elif mutation=='graph_binding':prepared['graph']['snapshot_sha256']='f'*64
    elif mutation=='independent_human_int':verification['independent_human']=0
    elif mutation=='bias_int':verification['same_model_bias_possible']=1
    elif mutation=='review_extra':verification['reviews'][0]['is_verified']=True
    elif mutation=='review_missing':verification['reviews'].pop()
    else:verification['reports'][0]['citation_integrity']=True
    from issueintel.investigation.resume import resume_policy
    with pytest.raises(ValueError):replay_checkpoints(inputs,verified,expected_policy=resume_policy())
    assert len(provider.calls)==2


def test_concurrent_submissions_and_restart_preserve_one_intent(finalization_source):
    wb,service,source,provider = finalization_source
    body = approval(service.preview(source['id']))
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = list(pool.map(lambda _:service.submit(source['id'],body),range(3)))
    assert len({job['id'] for job in jobs})==1
    child = completed(wb,jobs[0]['id'])
    assert child['status']=='completed',child
    wb2 = Workbench(wb.root,provider_factory=lambda *a,**k:pytest.fail('No API'))
    try:
        service2 = wb2.finalizations
        assert service2.submit(source['id'],body)['id']==child['id']
        pack = json.loads(wb2.result_bytes(child['id']))
        request = json.loads(wb2._job_path(child['id']).read_bytes())['request']
        service2.validate_child(child['id'],pack,request)
    finally:wb2.close()
    assert len(provider.calls)==2


@pytest.mark.parametrize('malformed',['trace_entry','case','snapshot','snapshot_file','annotation','verifier_payload'])
def test_malformed_saved_structures_raise_public_value_error(finalization_source,malformed):
    wb,service,source,provider = finalization_source
    inputs,verified = stages(wb,source)
    prepared = inputs['payload']['prepared']
    if malformed=='trace_entry':prepared['trace'][-1]='not an object'
    elif malformed=='case':prepared['case']=[]
    elif malformed=='snapshot':prepared['snapshot']=[]
    elif malformed=='snapshot_file':prepared['snapshot']['files'][0]['path']=True
    elif malformed=='annotation':prepared['annotation']=[]
    else:verified['payload']=[]
    with pytest.raises(ValueError):replay_checkpoints(inputs,verified)
    assert len(provider.calls)==2


@pytest.mark.parametrize('change',['cancel','checkpoint','request','child_request','child_authority'])
def test_queued_finalization_rechecks_source_and_cancellation(finalization_source,change):
    wb,service,source,provider = finalization_source
    release = threading.Event();started = threading.Event()
    def block():started.set();assert release.wait(10)
    wb.executor.submit(block);assert started.wait(5)
    try:
        child = service.submit(source['id'],approval(service.preview(source['id'])))
        if change=='cancel':wb.cancel(child['id'])
        elif change=='checkpoint':
            path = wb.root/source['stage_checkpoints']['verification']['path']
            path.write_bytes(path.read_bytes()+b' ')
        elif change=='request':
            row = json.loads(wb._job_path(source['id']).read_bytes())
            row['request']['depth']='quick';row['depth']='quick';write_json(wb._job_path(source['id']),row)
        else:
            row = json.loads(wb._job_path(child['id']).read_bytes())
            if change=='child_request':row['request']['source_request_sha256']='f'*64
            else:row['provider']='groq'
            write_json(wb._job_path(child['id']),row)
        release.set()
        state = completed(wb,child['id'])
        assert state['status']==('cancelled' if change=='cancel' else 'failed'),state
        assert not (wb.directory/'runs'/child['id']).exists()
        assert len(provider.calls)==2
    finally:release.set()


@pytest.mark.parametrize('body',[{}, {'confirmation':'yes'},
    {'verification_input_sha256':'f'*64,'verification_sha256':'f'*64,'confirmation':'finalize_saved_checkpoints_without_api'}])
def test_explicit_hash_bound_confirmation_required(finalization_source,body):
    wb,service,source,provider = finalization_source
    with pytest.raises(ValueError):service.submit(source['id'],body)
    assert len(provider.calls)==2
