"""Explicit current-context curation uses synthetic index records only."""
from copy import deepcopy
from hashlib import sha256

import pytest

from test_github_index import connected
from test_repository_context import context_repo
from issueintel.retrieval.repository_context import (
    collect_context, collect_selected_context, context_evidence, search_context,
    validate_selections,
)
from issueintel.runtime.io import digest


def choice(index,key):
    item=index.item(key)
    return {'key':key,'record_sha256':item['record_sha256'],
            'receipt_sha256':item['receipt_sha256']}


def rehash(context):
    context['sha256']=digest({key:value for key,value in context.items() if key!='sha256'})


def test_selected_context_is_exact_order_without_implicit_pr_membership(context_repo):
    index,case,_=context_repo
    selections=[choice(index,'comments:7'),choice(index,'pull:2')]
    context=collect_selected_context(index,case['issue_url'],case['problem_statement'],selections)
    assert [item['record']['key'] for item in context['sources']]==['comments:7','pull:2']
    assert not any(item['record']['kind']=='files' for item in context['sources'])
    assert context['selection']=={'kind':'user_selected','repository_id':42,'sources':selections}
    assert context['sha256']==digest({key:value for key,value in context.items() if key!='sha256'})
    evidence=context_evidence(context,case,protocol='benchmark_base')
    assert len(evidence)==2
    for source,item in zip(evidence,context['sources']):
        assert source['provenance']['source_record_sha256']==item['record_sha256']
        assert source['provenance']['receipt_sha256']==item['receipt_sha256']
        assert item['receipt_sha256']==digest(item['receipt'])
        assert source['available_at'] is None
    selections[0]['key']='issue:999'
    assert context['selection']['sources'][0]['key']=='comments:7'


@pytest.mark.parametrize('value',[
    None,{},[],(),[{}],[{'key':'pull:2','record_sha256':'a'*64}],
    [{'key':'pull:2','record_sha256':'a'*64,'receipt_sha256':'b'*64,'extra':True}],
    [{'key':key,'record_sha256':'a'*64,'receipt_sha256':'b'*64} for key in ('pull:2','pull:2')],
    [{'key':f'issue:{number}','record_sha256':'a'*64,'receipt_sha256':'b'*64} for number in range(1,8)],
    *[[{'key':key,'record_sha256':'a'*64,'receipt_sha256':'b'*64}] for key in (
        'issue:0','pull:-1','issue:1\x00','comments:1\n','../../secret',
        'file:2:../private','file:2:/absolute','file:2:some\\file','file:2:.git/config',
        'file:2:'+('x'*300),'commit:2:abc','other:2',
    )],
    [{'key':'pull:2','record_sha256':'A'*64,'receipt_sha256':'b'*64}],
    [{'key':'pull:2','record_sha256':'a'*64,'receipt_sha256':None}],
])
def test_selection_contract_rejects_unsafe_or_ambiguous_input(value):
    with pytest.raises(ValueError):
        validate_selections(value)


@pytest.mark.parametrize('key',['issue:1','pull:1'])
def test_target_issue_or_pr_cannot_be_selected(context_repo,key):
    index,case,_=context_repo
    with pytest.raises(ValueError,match='target issue/PR'):
        collect_selected_context(index,case['issue_url'],case['problem_statement'],[
            {'key':key,'record_sha256':'a'*64,'receipt_sha256':'b'*64}])


@pytest.mark.parametrize('field',['record_sha256','receipt_sha256'])
def test_changed_source_or_receipt_requires_fresh_selection(context_repo,field):
    index,case,_=context_repo
    selected=choice(index,'pull:2')
    selected[field]='0'*64
    with pytest.raises(ValueError,match='changed'):
        collect_selected_context(index,case['issue_url'],case['problem_statement'],[selected])


def test_unavailable_selected_record_is_rejected_not_silently_dropped(context_repo):
    index,case,_=context_repo
    selected=choice(index,'pull:2')
    with index.connect() as db:
        db.execute('INSERT INTO unavailable VALUES (?,?,?)',('pull:2','deleted_or_inaccessible','synthetic'))
    with pytest.raises(ValueError,match='integrity'):
        collect_selected_context(index,case['issue_url'],case['problem_statement'],[selected])


@pytest.mark.parametrize('mutation',['selection_record','selection_receipt','reorder','repository_id','links','blank','duplicate_locator'])
def test_saved_selected_context_replays_every_binding(context_repo,mutation):
    index,case,_=context_repo
    context=collect_selected_context(index,case['issue_url'],case['problem_statement'],[
        choice(index,'comments:7'),choice(index,'pull:2')])
    if mutation=='selection_record':
        context['selection']['sources'][0]['record_sha256']='0'*64
    elif mutation=='selection_receipt':
        context['selection']['sources'][0]['receipt_sha256']='0'*64
    elif mutation=='reorder':
        context['sources'].reverse()
    elif mutation=='repository_id':
        context['selection']['repository_id']=43
    elif mutation=='links':
        context['sources'][0]['links']=[]
    else:
        # Build an internally hash-consistent but blank/duplicate source. The
        # curation replay must still reject it instead of discarding a choice.
        item=context['sources'][0]
        if mutation=='blank':
            item['record']['title']='';item['record']['body']=''
        else:
            context['sources'][1]['record']['url']=item['record']['url']
            item=context['sources'][1]
        from issueintel.integrations.github_index import record_links
        item['record_sha256']=digest(item['record'])
        item['links']=record_links(item['record'])
        item['receipt']['records'][item['record']['key']]=item['record_sha256']
        item['receipt_sha256']=digest(item['receipt'])
        context['selection']['sources']=[{
            'key':entry['record']['key'],'record_sha256':entry['record_sha256'],
            'receipt_sha256':entry['receipt_sha256']} for entry in context['sources']]
    rehash(context)
    with pytest.raises(ValueError):
        context_evidence(context,case,protocol='benchmark_base')


def test_selected_collection_rejects_another_repository(context_repo):
    index,case,_=context_repo
    with pytest.raises(ValueError,match='another issue/repository'):
        collect_selected_context(index,'https://github.com/other/repo/issues/1',case['problem_statement'],[
            choice(index,'pull:2')])


def test_old_automatic_context_still_replays_without_selection_metadata(context_repo):
    index,case,_=context_repo
    context=collect_context(index,case['issue_url'],case['problem_statement'])
    assert 'selection' not in context
    assert context_evidence(deepcopy(context),case,protocol='benchmark_base')


def test_search_context_is_bounded_readonly_and_receipt_bound(context_repo,monkeypatch):
    index,case,_=context_repo
    monkeypatch.setattr(index,'sync',lambda **_kwargs:pytest.fail('Search tried to sync'))
    result=search_context(index,case['issue_url'],'Unicode parser')
    assert result['query']=='Unicode parser'
    assert result['query_sha256']==sha256(b'Unicode parser').hexdigest()
    assert result['github_repository_id']==42
    assert result['historical_verified'] is False
    assert result['sha256']==digest({key:value for key,value in result.items() if key!='sha256'})
    assert 1<=len(result['results'])<=20
    assert 'issue:1' not in [hit['key'] for hit in result['results']]
    for hit in result['results']:
        assert len(hit['excerpt'])<=1200
        assert len(hit['title'])<=300
        assert hit['excerpt_sha256']==sha256(hit['excerpt'].encode()).hexdigest()
        item=index.item(hit['key'])
        assert hit['record_sha256']==item['record_sha256']
        assert hit['receipt_sha256']==item['receipt_sha256']
        assert 'record' not in hit and 'receipt' not in hit
        assert len(hit['matched_terms'])<=32


@pytest.mark.parametrize('field',['record_sha256','receipt_sha256'])
def test_search_rechecks_record_and_receipt_after_index_hit(context_repo,monkeypatch,field):
    index,case,_=context_repo
    hit=choice(index,'pull:2');hit[field]='0'*64
    monkeypatch.setattr(index,'search',lambda *_args,**_kwargs:{'results':[hit]})
    with pytest.raises(ValueError,match='changed'):
        search_context(index,case['issue_url'],'Unicode parser')


def test_search_discards_a_record_that_became_unavailable(context_repo,monkeypatch):
    index,case,_=context_repo
    hit=choice(index,'pull:2')
    monkeypatch.setattr(index,'search',lambda *_args,**_kwargs:{'results':[hit]})
    with index.connect() as db:
        db.execute('INSERT INTO unavailable VALUES (?,?,?)',('pull:2','deleted_or_inaccessible','synthetic'))
    assert search_context(index,case['issue_url'],'Unicode parser')['results']==[]
