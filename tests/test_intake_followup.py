import hashlib
import json

import pytest

from issueintel.investigation.intake import inspect_issue
from issueintel.investigation.workbench import Workbench, validate_request
from issueintel.runtime.io import write_json
from test_workbench import completed, local_server, http


def test_intake_has_source_bound_cues_and_does_not_claim_completeness():
    text='Parser error on empty input\nExpected: return []\nSteps to reproduce: parse([])\nPython 3.12 on Windows'
    result=inspect_issue(text)
    assert result['intent']=='bug_report'
    assert result['classification']=='lexical_hint_unverified'
    assert not result['missing_information']
    assert result['query_sha256']==hashlib.sha256(text.encode()).hexdigest()
    for field in result['fields'].values():
        for source in field['sources']:
            assert source['text']==text.splitlines()[source['line']-1]
            assert source['text_sha256']==hashlib.sha256(source['text'].encode()).hexdigest()


def test_ambiguous_intent_and_missing_cues_stay_unknown():
    assert inspect_issue('Feature request: fix this error')['intent']=='unknown'
    assert inspect_issue('Please inspect the parser')['intent']=='unknown'
    assert set(inspect_issue('Please inspect the parser')['missing_information'])=={
        'observed_behavior','expected_behavior','reproduction','environment'}
    assert inspect_issue('Lỗi parser. Mong đợi trả về rỗng. Tái hiện: input null. Phiên bản 3.')['intent']=='bug_report'


def test_truncated_cue_preserves_both_excerpt_and_original_line_hash():
    text='error '+'x'*1000
    source=inspect_issue(text)['fields']['observed_behavior']['sources'][0]
    assert source['truncated'] and len(source['text'])==400
    assert source['source_line_sha256']==hashlib.sha256(text.encode()).hexdigest()
    assert source['text_sha256']==hashlib.sha256(source['text'].encode()).hexdigest()


@pytest.mark.parametrize('input_request',[
    {'mode':'example','additional_context':'extra'},
    {'mode':'followup','parent_job_id':'../.env','additional_context':'extra'},
    {'mode':'followup','parent_job_id':'a'*24,'additional_context':''},
    {'mode':'followup','parent_job_id':'a'*24,'additional_context':'x'*20001},
    {'mode':'followup','parent_job_id':'a'*24,'additional_context':'extra','revision':'HEAD'},
    {'mode':'followup','parent_job_id':'a'*24,'additional_context':'extra','case_id':'verified:holdout'},
])
def test_followup_public_input_cannot_override_parent(input_request):
    with pytest.raises(ValueError):validate_request(input_request)


def test_followup_is_a_new_unverified_job_with_replayed_parent_and_fixed_revision(tmp_path):
    wb=Workbench(tmp_path,provider_factory=lambda *a,**k:pytest.fail('No model authorized by this request'))
    try:
        parent=completed(wb,wb.submit({'mode':'example'})['id'])
        original=wb.result_bytes(parent['id']);old=json.loads(original)
        follow=completed(wb,wb.submit({'mode':'followup','parent_job_id':parent['id'],
                                       'additional_context':'Expected: None should return an empty list'})['id'])
        assert follow['status']=='completed'
        new=json.loads(wb.result_bytes(follow['id']))
        assert new['snapshot']['commit_sha']==old['snapshot']['commit_sha']
        assert new['followup']['parent_pack_sha256']==hashlib.sha256(original).hexdigest()
        assert new['followup']['depth']==1
        assert new['case']['case_id'].startswith('followup:')
        assert new['leakage_audit']['strict_issue_time_verified'] is False
        assert new['intake']['fields']['expected_behavior']['status']=='mentioned'
        assert wb.result_bytes(parent['id'])==original
        comparison=wb.compare({'before_id':parent['id'],'after_id':follow['id']})
        assert not comparison['same_context'] and 'query_sha256' in comparison['changed_bindings']
        assert b'Follow-up provenance' in wb.result_bytes(follow['id'],'report.md')
        with pytest.raises(ValueError):wb.preview_comment(follow['id'])
        # The export's own hash is insufficient when an ancestor is corrupted.
        path=tmp_path/parent['result']['pack.json']['path'];path.write_bytes(original+b' ')
        with pytest.raises(ValueError,match='integrity'):wb.result_bytes(follow['id'])
    finally:wb.close()


def test_invalid_parent_denied_before_provider(tmp_path,monkeypatch):
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Provider config loaded'))
    wb=Workbench(tmp_path)
    try:
        with pytest.raises(ValueError,match='existing completed follow-up source'):
            wb.submit({'mode':'followup','parent_job_id':'a'*24,'additional_context':'extra','provider':'groq'})
        assert wb.history()==[]
    finally:wb.close()


def test_intake_api_binds_read_only_inspection_to_saved_query(tmp_path):
    with local_server(tmp_path) as server:
        job=completed(server.workbench,server.workbench.submit({'mode':'example'})['id'])
        raw=server.workbench.result_bytes(job['id'])
        headers={'X-IssueIntel-Token':server.session_token}
        status,_,body=http(server,'GET','/api/jobs/'+job['id']+'/intake',headers=headers)
        result=json.loads(body)
        assert status==200 and result['source_pack_sha256']==hashlib.sha256(raw).hexdigest()
        assert result['query_sha256']==hashlib.sha256(json.loads(raw)['case']['problem_statement'].encode()).hexdigest()
        assert server.workbench.result_bytes(job['id'])==raw


def test_followup_chain_is_bounded_and_cycle_rejected(tmp_path):
    wb=Workbench(tmp_path)
    try:
        parent=completed(wb,wb.submit({'mode':'example'})['id'])
        for depth in range(1,9):
            parent=completed(wb,wb.submit({'mode':'followup','parent_job_id':parent['id'],'additional_context':'Extra context'})['id'])
            assert parent['status']=='completed'
            assert json.loads(wb.result_bytes(parent['id']))['followup']['depth']==depth
        denied=completed(wb,wb.submit({'mode':'followup','parent_job_id':parent['id'],'additional_context':'Extra context'})['id'])
        assert denied['status']=='failed'
        path=tmp_path/parent['result']['pack.json']['path'];pack=json.loads(path.read_bytes())
        pack['followup']['parent_job_id']=parent['id'];write_json(path,pack)
        job_path=wb.jobs/(parent['id']+'.json');job=json.loads(job_path.read_bytes())
        job['result']['pack.json']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();write_json(job_path,job)
        with pytest.raises(ValueError,match='lineage'):wb.result_bytes(parent['id'])
    finally:wb.close()
