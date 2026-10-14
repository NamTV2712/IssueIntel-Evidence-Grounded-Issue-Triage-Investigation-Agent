from copy import deepcopy
import hashlib
import json

import pytest

from issueintel.investigation.comparison import compare_packs
from issueintel.investigation.workbench import Workbench
from test_workbench import completed, local_server, http


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def pack():
    query = 'Parser fails on None input'
    code = 'return value.strip()'
    return {'case': {'case_id': 'synthetic:parser', 'repository': 'synthetic/repo',
                     'problem_statement': query},
            'snapshot': {'commit_sha': 'a'*40, 'snapshot_sha256': 'b'*64,
                         'protocol': 'benchmark_base', 'cutoff': None, 'availability': {}},
            'evidence': [{'id': 'issue_1', 'kind': 'issue', 'locator': 'synthetic:parser',
                          'text': query, 'sha256': sha(query), 'revision': None,
                          'available_at': None, 'provenance': {'kind': 'input'}},
                         {'id': 'e1', 'kind': 'code', 'locator': 'parser.py:1-1',
                          'text': code, 'sha256': sha(code), 'revision': 'a'*40,
                          'provenance': {'snapshot_sha256': 'b'*64}}],
            'annotation': {'hypotheses': [{'id': 'h1', 'statement': 'None reaches strip',
                'support_ids': ['e1'], 'counter_ids': [], 'counter_status': 'not_found',
                'falsification_test': 'Run None input', 'correctness': 'unverified'}]}}


def test_same_context_renumbered_ids_do_not_create_semantic_changes():
    before = pack(); after = deepcopy(before)
    after['evidence'][1]['id'] = 'e9'
    after['annotation']['hypotheses'][0].update(id='h9', support_ids=['e9'])
    result = compare_packs(before, after)
    assert result['same_context'] is True
    assert result['evidence_counts']['unchanged'] == 2
    assert result['hypotheses']['unchanged'] == [{'before_id':'h1','after_id':'h9','statement':'None reaches strip'}]
    assert result['hypotheses']['added'] == result['hypotheses']['removed'] == []
    assert result['evaluation_performed'] is False and result['correctness'] == 'unverified'


def test_different_snapshot_identical_excerpt_requires_rebinding():
    before = pack(); after = deepcopy(before)
    after['snapshot'].update(commit_sha='c'*40, snapshot_sha256='d'*64)
    after['evidence'][1].update(revision='c'*40, provenance={'snapshot_sha256':'d'*64})
    result = compare_packs(before, after)
    assert result['same_context'] is False
    assert set(result['changed_bindings']) == {'commit_sha','snapshot_sha256'}
    assert result['evidence_counts']['rebinding_required'] == 2
    assert not result['hypotheses']['unchanged']
    assert len(result['hypotheses']['added']) == len(result['hypotheses']['removed']) == 1


@pytest.mark.parametrize('change', ['query','proof','cutoff','availability','protocol'])
def test_changes_in_query_or_temporal_authority_prevent_reuse(change):
    before = pack(); after = deepcopy(before)
    if change == 'query':
        text = 'Parser fails on empty input'
        after['case']['problem_statement'] = text
        after['evidence'][0].update(text=text, sha256=sha(text))
    elif change == 'proof':
        after['evidence'][0]['provenance']['receipt'] = {'strict_dev_proof_sha256':'c'*64}
    elif change == 'cutoff':
        after['snapshot']['cutoff'] = '2026-01-01T00:00:00Z'
    elif change == 'availability':
        after['snapshot']['availability'] = {'receipt_sha256':'c'*64}
    else:
        after['snapshot']['protocol'] = 'issue_time'
    result = compare_packs(before, after)
    assert not result['same_context']
    assert not result['hypotheses']['unchanged']
    assert next(e for e in result['evidence'] if e['kind']=='code')['status'] == 'rebinding_required'


def test_content_and_range_changes_are_descriptive_not_line_alignment():
    before = pack(); after = deepcopy(before)
    after['evidence'][1].update(text='return value', sha256=sha('return value'))
    assert compare_packs(before, after)['evidence_counts']['content_changed'] == 1
    after['evidence'][1]['locator'] = 'parser.py:2-2'
    result = compare_packs(before, after)
    assert result['evidence_counts']['added'] == result['evidence_counts']['removed'] == 1


def test_custom_jobs_match_equal_query_and_repo_despite_request_derived_ids():
    before = pack(); after = deepcopy(before)
    before['case']['case_id']='local:a'; after['case']['case_id']='local:b'
    assert compare_packs(before, after)['same_context']
    after['case']['repository']='other/repo'
    with pytest.raises(ValueError, match='same issue'):
        compare_packs(before, after)


@pytest.mark.parametrize('corruption', ['hash','revision','provenance','duplicate','citation','identity','gold'])
def test_corruption_or_unrelated_issue_is_rejected(corruption):
    before = pack(); after = deepcopy(before)
    if corruption=='hash': after['evidence'][1]['text']='tampered'
    elif corruption=='revision': after['evidence'][1]['revision']='c'*40
    elif corruption=='provenance': after['evidence'][1]['provenance']['snapshot_sha256']='c'*64
    elif corruption=='duplicate': after['evidence'].append(deepcopy(after['evidence'][1]))
    elif corruption=='citation': after['annotation']['hypotheses'][0]['support_ids']=['missing']
    elif corruption=='identity': after['case']['case_id']='synthetic:another_issue'
    else: after['case']['patch']='hidden'
    with pytest.raises(ValueError):
        compare_packs(before, after)


def test_workbench_comparison_checks_both_exports_and_never_calls_provider(tmp_path):
    wb=Workbench(tmp_path,provider_factory=lambda *a,**k:pytest.fail('Unexpected model call'))
    try:
        ids=[completed(wb,wb.submit({'mode':'example','depth':depth})['id'])['id'] for depth in ('quick','deep')]
        request={'before_id':ids[0],'after_id':ids[1]}
        result=wb.compare(request)
        assert result['same_context'] and result['sources']['before']['job_id']==ids[0]
        assert len(result['sources']['after']['pack_sha256'])==64
        assert wb.compare(request)==result
        with pytest.raises(ValueError): wb.compare({'before_id':ids[0],'after_id':ids[0]})
        path=tmp_path/wb.job(ids[1])['result']['pack.json']['path']
        path.write_bytes(path.read_bytes()+b' ')
        with pytest.raises(ValueError,match='integrity'): wb.compare(request)
    finally: wb.close()


@pytest.mark.parametrize('malformed', [None, {}, {'case':None}, {'case':{'problem_statement':3}}])
def test_malformed_saved_pack_has_bounded_validation_error(malformed):
    with pytest.raises(ValueError):
        compare_packs(pack(), malformed)


def test_http_comparison_requires_session_and_two_complete_jobs(tmp_path):
    with local_server(tmp_path) as server:
        headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        ids=[completed(server.workbench,server.workbench.submit({'mode':'example'})['id'])['id'] for _ in range(2)]
        body=json.dumps({'before_id':ids[0],'after_id':ids[1]})
        # Authentication precedes body parsing. No unread body avoids a Windows
        # socket reset after the handler closes an unauthenticated connection.
        assert http(server,'POST','/api/comparisons',headers={'Content-Type':'application/json'})[0]==403
        status,_,raw=http(server,'POST','/api/comparisons',body,headers)
        assert status==200 and json.loads(raw)['same_context']
        assert http(server,'POST','/api/comparisons',json.dumps({'before_id':ids[0],'after_id':None}),headers)[0]==400
        assert http(server,'GET','/comparison.css')[0]==200
