"""Semantic caution is preserved even when every citation is structurally valid."""
from copy import deepcopy
import json

import pytest

from issueintel.investigation.claim_review import build_source,generate,validate,assess,render_markdown,RUBRIC
from issueintel.investigation.providers import ProviderError
from issueintel.runtime.io import digest
from test_check_plan_jobs import checking


def review_response(source):
    evidence=next(e for e in source['evidence'] if e['kind']=='code')
    quote=evidence['text'].split('\n')[0]
    citation={'evidence_id':evidence['id'],'start':0,'end':len(quote),'quote':quote}
    return {'reviews':[{'hypothesis_id':h['id'],'rationale':'Static context alone may not explain the reported failure.',
        'claims':[{'id':'claim_1','start':0,'end':len(h['statement']),'quote':h['statement'],'kind':'mechanism',
            'assessment':'supported','reason':'The excerpt mentions the parser but the mechanism remains unresolved.',
            'support':[deepcopy(citation)],'counter':[],
            'assumptions':[{'condition':'The issue uses this caller and input at runtime.','status':'unverified','citations':[]}],
            'contextual_fit':'unknown','inferential_link':'missing'}]} for h in source['hypotheses']]}


@pytest.fixture
def source(checking):
    wb,_,job=checking
    import hashlib
    raw=wb.result_bytes(job['id'])
    return build_source(json.loads(raw),job['id'],hashlib.sha256(raw).hexdigest())


def produce(source,transform=lambda r:r):
    return generate(source,lambda *a,**k:transform(review_response(source)),model='openai/gpt-oss-120b')


def reseal(value):return dict(value,sha256=digest({k:v for k,v in value.items() if k!='sha256'}))


def test_valid_quotes_with_missing_mechanism_and_assumptions_cannot_imply_support(source):
    bundle=produce(source)
    for row in bundle['assessment']:
        assert row['assessment']=='uncertain' and row['semantic_support']=='unverified'
        claim=row['claims'][0]
        assert claim['model_assessment']=='supported' and claim['assessment']=='uncertain'
        assert {'contextual_fit_unknown','inferential_link_missing','unverified_assumptions'}<={g['code'] for g in claim['gaps']}
        assert all(g['reason'].strip() for g in claim['gaps'])
        citation=claim['support'][0]
        assert citation['evidence_sha256'] and citation['quote_sha256'] and citation['revision']==source['commit_sha']
        assert row['causal_verified'] is False and row['independent_human'] is False
    assert bundle['rubric']==RUBRIC and bundle['source']==source
    text=render_markdown(bundle)
    assert 'Semantic support: **unverified**' in text and 'Assumption (unverified)' in text


@pytest.mark.parametrize('kind,assessment,support,counter,expected,gap',[
    ('source_fact','supported',True,False,'supported','no_counter_span_in_supplied_scope'),
    ('mechanism','supported',False,False,'unsupported','no_exact_support_span'),
    ('causal','supported',True,False,'uncertain','independent_execution_proof_absent'),
    ('source_fact','contradicted',True,False,'uncertain','contradiction_lacks_exact_counter_span'),
    ('source_fact','contradicted',False,True,'contradicted','no_exact_support_span'),
])
def test_sufficiency_does_not_confuse_model_assessment_with_execution_proof(source,kind,assessment,support,counter,expected,gap):
    reviews=review_response(source)['reviews']
    for row in reviews:
        claim=row['claims'][0];refs=deepcopy(claim['support'])
        claim.update(kind=kind,assessment=assessment,support=refs if support else [],counter=refs if counter else [],
                     assumptions=[],contextual_fit='demonstrated',inferential_link='demonstrated')
    result=assess(reviews,source)
    assert all(r['claims'][0]['assessment']==expected for r in result)
    assert all(gap in {g['code'] for g in r['claims'][0]['gaps']} for r in result)
    assert all(r['semantic_support']=='unverified' and r['causal_verified'] is False for r in result)


def test_no_decomposition_and_uncovered_claim_text_remain_visible_and_uncertain(source):
    reviews=review_response(source)['reviews'];reviews[0]['claims']=[]
    claim=reviews[1]['claims'][0];claim['end']=9;claim['quote']=claim['quote'][:9]
    result=assess(reviews,source)
    assert result[0]['assessment']=='uncertain' and result[0]['unreviewed_fragments'][0]['quote']==source['hypotheses'][0]['statement']
    assert result[1]['unreviewed_fragments'][0]['quote']==source['hypotheses'][1]['statement'][9:]


@pytest.mark.parametrize('assessment,context,expected_gap',[
    ('supported','demonstrated','no_code_support_for_inference'),
    ('contradicted','demonstrated','no_code_counter_for_inference'),
    ('contradicted','unknown','contextual_fit_unknown')])
def test_issue_words_alone_or_unknown_context_cannot_establish_a_mechanism(source,assessment,context,expected_gap):
    issue=next(e for e in source['evidence'] if e['kind']=='issue')
    ref={'evidence_id':issue['id'],'start':0,'end':len(issue['text']),'quote':issue['text']}
    reviews=review_response(source)['reviews']
    for row in reviews:
        claim=row['claims'][0];claim.update(assessment=assessment,assumptions=[],contextual_fit=context,inferential_link='demonstrated',
            support=[deepcopy(ref)] if assessment=='supported' else [],counter=[deepcopy(ref)] if assessment=='contradicted' else [])
    result=assess(reviews,source)
    assert all(r['assessment']=='uncertain' for r in result)
    assert all(expected_gap in {g['code'] for g in r['claims'][0]['gaps']} for r in result)


def test_assumption_citations_remain_exact_and_visible_in_the_markdown(source):
    def transform(response):
        for row in response['reviews']:
            claim=row['claims'][0];claim['assumptions'][0].update(status='source_supported',citations=deepcopy(claim['support']))
        return response
    bundle=produce(source,transform)
    assumption=bundle['assessment'][0]['claims'][0]['assumptions'][0]
    assert assumption['citations'][0]['quote_sha256'] and assumption['citations'][0]['evidence_sha256']
    assert 'Assumption source:' in render_markdown(bundle)


@pytest.mark.parametrize('mutation',['invented_quote','offset','bool_offset','duplicate_claim','same_span_both_roles','assumption_no_citation','missing_hypothesis','hidden','duplicate_ref','new_hypothesis'])
def test_invalid_claim_binding_never_gets_repaired_by_mapping_ids(source,mutation):
    response=review_response(source);claim=response['reviews'][0]['claims'][0]
    if mutation=='invented_quote':claim['support'][0]['quote']='Not present in source'
    elif mutation=='offset':claim['support'][0]['start']=1
    elif mutation=='bool_offset':claim['start']=False
    elif mutation=='duplicate_claim':response['reviews'][0]['claims'].append(deepcopy(claim))
    elif mutation=='same_span_both_roles':claim['counter']=deepcopy(claim['support'])
    elif mutation=='assumption_no_citation':claim['assumptions'][0]['status']='source_supported'
    elif mutation=='missing_hypothesis':response['reviews'].pop()
    elif mutation=='hidden':claim['gold_patch']='hidden'
    elif mutation=='duplicate_ref':claim['support']*=2
    elif mutation=='new_hypothesis':response['reviews'][0]['hypothesis_id']='invented'
    calls=[]
    with pytest.raises(ValueError):generate(source,lambda *a,**k:(calls.append(1) or response),model='openai/gpt-oss-120b')
    assert len(calls)==2


def test_one_output_repair_is_bounded_and_transport_errors_are_terminal(source):
    calls=[]
    def provider(*args,**kwargs):
        calls.append(1);return {} if len(calls)==1 else review_response(source)
    bundle=generate(source,provider,model='openai/gpt-oss-120b')
    assert len(calls)==2 and bundle['provenance']['repaired'] is True
    calls.clear()
    def transport(*args,**kwargs):calls.append(1);raise ProviderError('synthetic timeout')
    with pytest.raises(ProviderError):generate(source,transport,model='openai/gpt-oss-120b')
    assert len(calls)==1


@pytest.mark.parametrize('mutation',['effective_support','rubric','truth','source','provenance','gap_reason'])
def test_rehashing_cannot_forge_sufficiency_or_truth_flags(source,mutation):
    bundle=produce(source)
    if mutation=='effective_support':bundle['assessment'][0]['claims'][0]['assessment']='supported'
    elif mutation=='rubric':bundle['rubric']['version']='relaxed'
    elif mutation=='truth':bundle['causal_verified']=0
    elif mutation=='source':bundle['source']['report_sha256']='a'*64;bundle['source']=reseal(bundle['source'])
    elif mutation=='provenance':bundle['provenance']['repaired']=0
    elif mutation=='gap_reason':bundle['assessment'][0]['claims'][0]['gaps'][0]['reason']='Enough evidence'
    with pytest.raises(ValueError):validate(reseal(bundle),source)


def test_unicode_offsets_are_codepoints_not_utf8_bytes_or_utf16_units(source):
    source=deepcopy(source);source['hypotheses'][0]['statement']='😀 Cơ chế chưa biết';source=reseal(source)
    response=review_response(source);claim=response['reviews'][0]['claims'][0]
    claim.update(start=2,end=len(source['hypotheses'][0]['statement']),quote='Cơ chế chưa biết')
    assert assess(response['reviews'],source)[0]['claims'][0]['quote']=='Cơ chế chưa biết'
    claim['start']=3
    with pytest.raises(ValueError):assess(response['reviews'],source)
