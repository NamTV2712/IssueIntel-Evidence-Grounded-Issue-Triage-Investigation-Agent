from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from issueintel.investigation.evidence_graph import evidence_graph
from issueintel.investigation.workbench import Workbench
from test_comparison import pack as source_pack
from test_workbench import SyntheticProvider, completed, local_server, http


def with_review():
    pack=source_pack()
    pack['verification']={'kind':'llm_separate_pass','reviews':[{
        'hypothesis_id':'h1','assessment':'uncertain','reason':'Not reproduced', 'evidence_ids':['e1']}]}
    return pack


def test_graph_distinguishes_retrieval_claimed_citations_and_model_assessment():
    pack=with_review();before=deepcopy(pack)
    graph=evidence_graph(pack)
    assert pack==before
    assert graph['model_calls']==0 and graph['independent_human'] is False
    assert graph['correctness']=='unverified' and graph['evaluation_performed'] is False
    assert {row['relation'] for row in graph['edges']}=={'retrieved_context','claimed_support','model_assessment','critic_citation'}
    assert 'claimed_counter' not in {row['relation'] for row in graph['edges']}
    hypothesis=next(node for node in graph['nodes'] if node['kind']=='hypothesis')
    assert hypothesis['counter_status']=='not_found' and hypothesis['correctness']=='unverified'
    review=next(node for node in graph['nodes'] if node['kind']=='model_review')
    assert review['assessment']=='uncertain' and review['independent_human'] is False
    code=next(node for node in graph['nodes'] if node['kind']=='code')
    assert code['sha256']==pack['evidence'][1]['sha256'] and code['revision']=='a'*40


@pytest.mark.parametrize('mutation',['hash','revision','query','citation','review_id','review_source','review_kind'])
def test_invalid_saved_bindings_or_reviews_are_not_drawn(mutation):
    pack=with_review()
    if mutation=='hash':pack['evidence'][1]['text']='corrupt'
    elif mutation=='revision':pack['evidence'][1]['revision']='c'*40
    elif mutation=='query':pack['case']['problem_statement']='Changed query'
    elif mutation=='citation':pack['annotation']['hypotheses'][0]['support_ids']=['missing']
    elif mutation=='review_id':pack['verification']['reviews'][0]['hypothesis_id']='missing'
    elif mutation=='review_source':pack['verification']['reviews'][0]['evidence_ids']=['missing']
    else:pack['verification']['kind']='structural'
    with pytest.raises(ValueError):evidence_graph(pack)


def test_no_model_output_yields_only_query_and_retrieved_sources():
    pack=source_pack();pack['annotation']=None
    graph=evidence_graph(pack)
    assert {node['kind'] for node in graph['nodes']}=={'code','query'}
    assert [edge['relation'] for edge in graph['edges']]==['retrieved_context']


def test_explicit_counter_citation_is_distinct_from_critic_citation():
    pack=with_review()
    counter=deepcopy(pack['evidence'][1]);counter.update(id='counter',locator='other.py:1-1',text='return 0')
    counter['sha256']=hashlib.sha256(counter['text'].encode()).hexdigest()
    pack['evidence'].append(counter)
    pack['annotation']['hypotheses'][0].update(counter_ids=['counter'],counter_status='found')
    graph=evidence_graph(pack)
    assert {'source':'hypothesis:h1','target':'evidence:counter','relation':'claimed_counter'} in graph['edges']
    assert not any(edge['target']=='evidence:counter' and edge['relation']=='critic_citation' for edge in graph['edges'])


def test_projection_is_bounded_and_has_no_dangling_edges(monkeypatch):
    pack=with_review()
    pack['case']['problem_statement']='query '*2000
    pack['evidence'][0]['text']=pack['case']['problem_statement']
    pack['evidence'][0]['sha256']=hashlib.sha256(pack['evidence'][0]['text'].encode()).hexdigest()
    full=evidence_graph(pack)
    query=next(node for node in full['nodes'] if node['kind']=='query')
    assert query['text_truncated'] and len(query['text'])==8000
    assert query['sha256']==pack['evidence'][0]['sha256']
    monkeypatch.setattr('issueintel.investigation.evidence_graph.MAX_NODES',3)
    monkeypatch.setattr('issueintel.investigation.evidence_graph.MAX_EDGES',1)
    bounded=evidence_graph(pack)
    assert bounded['omitted_nodes']==1 and bounded['omitted_edges']==len(full['edges'])-1
    ids={node['id'] for node in bounded['nodes']}
    assert all(edge['source'] in ids and edge['target'] in ids for edge in bounded['edges'])


def test_graph_read_never_repeats_generation_and_binds_source_pack(tmp_path,monkeypatch):
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda root:SimpleNamespace(reasoning_effort='high'))
    provider=SyntheticProvider()
    wb=Workbench(tmp_path,provider_factory=lambda *a,**k:provider)
    try:
        job=completed(wb,wb.submit({'mode':'example','provider':'groq'})['id'])
        assert job['status']=='completed' and len(provider.calls)==2
        graph=wb.evidence_graph(job['id'])
        assert graph==wb.evidence_graph(job['id']) and len(provider.calls)==2
        assert graph['source']=={'job_id':job['id'],'pack_sha256':hashlib.sha256(wb.result_bytes(job['id'])).hexdigest()}
        assert len([node for node in graph['nodes'] if node['kind']=='model_review'])==3
        path=tmp_path/job['result']['pack.json']['path']
        path.write_bytes(path.read_bytes()+b' ')
        with pytest.raises(ValueError):wb.evidence_graph(job['id'])
    finally:wb.close()


def test_graph_http_is_private_and_existing_sources_stay_unchanged(tmp_path):
    with local_server(tmp_path) as server:
        source=completed(server.workbench,server.workbench.submit({'mode':'example'})['id'])
        route='/api/jobs/'+source['id']+'/evidence-graph'
        assert http(server,'GET',route)[0]==403
        headers={'X-IssueIntel-Token':server.session_token}
        status,_,raw=http(server,'GET',route,headers=headers)
        assert status==200 and json.loads(raw)['model_calls']==0
        assert json.loads(raw)['source']['job_id']==source['id']
        assert http(server,'GET','/evidence-graph.js')[0]==200
        assert http(server,'GET','/evidence-graph.css')[0]==200
