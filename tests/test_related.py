import hashlib
import json

import pytest

from issueintel.retrieval.related import rank_related
from issueintel.investigation.workbench import Workbench
from test_workbench import completed, local_server, http


def document(identifier,text):
    return {'job_id':identifier,'case_id':'local:'+identifier,'repository':'synthetic/repo',
            'text':text,'query_sha256':hashlib.sha256(text.encode()).hexdigest(),'pack_sha256':'a'*64}


def test_related_ranks_matching_symptoms_and_keeps_source_hashes():
    docs=[document('a','parser rejects empty input with TypeError'),document('b','rendering colors and stylesheet'),
          document('c','parser accepts a value but empty input fails')]
    result=rank_related('parser empty input TypeError',docs)
    assert result[0]['job_id']=='a' and {r['job_id'] for r in result}=={'a','c'}
    assert 'typeerror' in result[0]['matched_terms']
    assert result[0]['relationship']=='candidate_unverified' and result[0]['score_is_probability'] is False
    snippet=result[0]['snippet']
    assert hashlib.sha256(snippet['text'].encode()).hexdigest()==snippet['text_sha256']


def test_unicode_ties_and_low_overlap_are_deterministic():
    docs=[document('b','bộ phân tích dữ liệu rỗng'),document('a','bộ phân tích dữ liệu rỗng')]
    assert rank_related('phân tích dữ liệu',docs)==rank_related('phân tích dữ liệu',list(reversed(docs)))
    assert rank_related('phân tích dữ liệu',docs)[0]['job_id']=='a'
    assert rank_related('parser only',[document('x','parser')])==[]


def test_corrupt_text_and_limits_are_rejected():
    item=document('a','parser empty input');item['text']='changed'
    with pytest.raises(ValueError):rank_related('parser empty',[item])
    with pytest.raises(ValueError):rank_related('parser empty',[],limit=True)
    with pytest.raises(ValueError):rank_related('parser empty',[document(str(i),'empty parser') for i in range(51)])


def make_local_jobs(wb):
    _,path=wb._example()
    def run(text,repository=None):
        result=completed(wb,wb.submit({'mode':'local','repository':str(repository or path),
                                      'revision':'HEAD','issue_text':text})['id'])
        assert result['status']=='completed'
        return result
    selected=run('parser parse_list fails on missing None input')
    candidate=run('parse_list parser rejects empty input with TypeError')
    other=run('formatting uses the wrong output colors')
    # Different request-derived IDs for the same source query are one issue.
    repeat=completed(wb,wb.submit({'mode':'local','repository':str(path),'revision':'HEAD','depth':'deep',
                    'issue_text':'parse_list parser rejects empty input with TypeError'})['id'])
    follow=completed(wb,wb.submit({'mode':'followup','parent_job_id':candidate['id'],'additional_context':'Version 3.12'})['id'])
    another=Workbench(wb.root/'artifacts/other-source')
    try:
        _,other_path=another._example()
        run('parser parse_list missing None input',repository=other_path)
    finally:another.close()
    return selected,candidate,other,repeat,follow


def test_related_scans_only_saved_runs_deduplicates_and_does_not_feed_inference(tmp_path,monkeypatch):
    wb=Workbench(tmp_path,provider_factory=lambda *a,**k:pytest.fail('Model called'))
    try:
        selected,candidate,other,repeat,follow=make_local_jobs(wb)
        monkeypatch.setattr('issueintel.investigation.workbench.accepted_context',lambda *a:pytest.fail('Benchmark loader called'))
        raw=wb.result_bytes(selected['id'])
        result=wb.related({'job_id':selected['id']})
        assert result['distinct_other_issues']==2
        assert len(result['results'])==1
        assert result['results'][0]['job_id'] in {candidate['id'],repeat['id']}
        assert result['included_in_inference'] is False and result['duplicate_confirmed'] is False
        assert result['historical_availability_for_cutoff']=='unverified'
        assert wb.result_bytes(selected['id'])==raw
        assert result['source_pack_sha256']==hashlib.sha256(raw).hexdigest()
        # Corrupt both repeated source packs: no remaining valid related case.
        for row in [candidate,repeat]:
            path=tmp_path/row['result']['pack.json']['path'];path.write_bytes(path.read_bytes()+b' ')
        result=wb.related({'job_id':selected['id']})
        assert result['results']==[] and result['skipped_integrity']==3
    finally:wb.close()


def test_related_api_rejects_extra_scope_or_client_query(tmp_path):
    with local_server(tmp_path) as server:
        job=completed(server.workbench,server.workbench.submit({'mode':'example'})['id'])
        headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        status,_,raw=http(server,'POST','/api/related',json.dumps({'job_id':job['id']}),headers)
        assert status==200 and json.loads(raw)['results']==[]
        assert http(server,'POST','/api/related',json.dumps({'job_id':job['id'],'split':'main_test'}),headers)[0]==400
