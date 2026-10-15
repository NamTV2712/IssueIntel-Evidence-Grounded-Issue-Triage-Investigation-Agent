from copy import deepcopy
from hashlib import sha256
import json

import pytest

from test_github_index import connected, Reader, REPO, BASE, SHA, STAMP, issue
from test_focused_retrieval import FakeModel
from test_workbench import completed
from issueintel.integrations.github_sync import GitHubSync
from issueintel.investigation.workbench import Workbench
from issueintel.investigation.pipeline import investigate, export_pack
from issueintel.investigation.comparison import compare_packs
from issueintel.retrieval.repository_context import collect_context, context_evidence
from issueintel.runtime.io import digest


@pytest.fixture
def context_repo(connected):
    index,builder=connected
    pull=issue(2,pull=True,body='Unicode parser candidate. References #1. Not a verified fix.')
    pull.update(base={'sha':SHA,'repo':{'full_name':REPO}},head={'sha':SHA})
    comment={'id':7,'issue_url':BASE+'/issues/1','html_url':f'https://github.com/{REPO}/issues/1#issuecomment-7',
             'body':'Reproduce Unicode parser issue with ß and tiếng Việt. '+('body '*500),
             'created_at':STAMP,'updated_at':STAMP}
    reader=Reader({'/issues?':([issue(1),issue(2,pull=True)],None,digest('issues')),
                   '/issues/comments?':([comment],None,digest(comment)),
                   '/pulls?':([pull],None,digest(pull)),
                   '/pulls/2/files?':([{'filename':'parser.py','sha':SHA,'status':'modified'},
                                      {'filename':'other.py','sha':SHA,'status':'modified'}],None,digest('files'))})
    index.sync(reader=reader,snapshot_builder=builder)
    descriptor=index.status()['current_snapshot']
    snapshot=index._verify_snapshot(descriptor)
    case={'case_id':f'https://github.com/{REPO}/issues/1','issue_url':f'https://github.com/{REPO}/issues/1',
          'repository':REPO,'revision':snapshot.commit_sha,'problem_statement':'Unicode parser fails on input'}
    return index,case,snapshot


def test_current_context_has_typed_source_links_and_bounded_replayable_excerpts(context_repo):
    index,case,_=context_repo
    context=collect_context(index,case['issue_url'],case['problem_statement'])
    assert 'issue:1' not in [s['record']['key'] for s in context['sources']]
    assert {'pull:2','comments:7','file:2:parser.py'} <= {s['record']['key'] for s in context['sources']}
    evidence=context_evidence(context,case,protocol='benchmark_base')
    assert 1<=len(evidence)<=6
    assert len({e['locator'] for e in evidence})==len(evidence)
    assert sum(len(e['text']) for e in evidence)<=7200
    for record in evidence:
        item=next(s for s in context['sources'] if s['record']['key']==record['provenance']['source_key'])
        if item['record']['kind']!='files':
            text=item['record']['title']+'\n\n'+item['record']['body']
            start,end=record['provenance']['start_character'],record['provenance']['end_character']
            assert record['text']==text[start:end]
        assert record['sha256']==sha256(record['text'].encode()).hexdigest()
        assert record['available_at'] is None and record['revision'] is None


def test_pipeline_uses_selected_context_in_generation_and_critic_without_private_receipts(context_repo):
    index,case,snapshot=context_repo;model=FakeModel()
    context=collect_context(index,case['issue_url'],case['problem_statement'])
    pack=investigate(case,snapshot.repository,snapshot=snapshot,repository_context=context,
                     complete_json=model,provider='synthetic',model='fixture',refinement_rounds=1)
    assert pack['repository_context']['sha256']==context['sha256']
    assert not pack['leakage_audit']['strict_issue_time_verified']
    for call in model.calls:
        sources=[e for e in call['evidence'] if e['kind']=='repository_context']
        assert sources and sum(len(e['text']) for e in sources)<=7200
        assert not any('receipt_sha256' in e or 'sources' in e for e in sources)
        assert all('receipt_sha256' not in e.get('provenance',{}) for e in sources)
    assert compare_packs(pack,deepcopy(pack))['same_context']
    output=export_pack(pack,index.root/'artifacts/context-test')
    assert 'Current repository context' in output.read_text()


@pytest.mark.parametrize('mutation', ['query','repo','receipt','text','link','bound','source_url'])
def test_context_tampering_rejected_before_any_provider(context_repo,mutation):
    index,case,snapshot=context_repo
    context=collect_context(index,case['issue_url'],case['problem_statement'])
    if mutation=='query':context['query_sha256']='0'*64
    elif mutation=='repo':context['repository']='other/repo'
    elif mutation=='receipt':context['sources'][0]['receipt_sha256']='0'*64
    elif mutation=='text':context['sources'][0]['record']['body']='forged'
    elif mutation=='link':context['sources'][0]['links']=[]
    elif mutation=='source_url':context['sources'][0]['record']['url']='https://evil.test/'
    else:context['sources']*=3
    context['sha256']=digest({k:v for k,v in context.items() if k!='sha256'})
    with pytest.raises(ValueError):
        investigate(case,snapshot.repository,snapshot=snapshot,repository_context=context,
                    complete_json=lambda *_a,**_k:pytest.fail('Provider called'))


@pytest.mark.parametrize('scope', ['issue_time','main_dev','main_test','temporal_candidate','verified'])
def test_benchmark_and_strict_context_denied_before_snapshot_or_model(context_repo,scope):
    index,case,snapshot=context_repo
    context=collect_context(index,case['issue_url'],case['problem_statement'])
    protocol='issue_time' if scope=='issue_time' else 'benchmark_base'
    if scope in {'main_dev','main_test','temporal_candidate'}:case={**case,'split':scope}
    if scope=='verified':case={**case,'case_id':'verified:synthetic__repo-1'}
    with pytest.raises(ValueError,match='benchmark or strict'):
        investigate(case,'nonexistent-repo',repository_context=context,protocol=protocol,
                    complete_json=lambda *_a,**_k:pytest.fail('Provider called'))


def test_saved_pack_context_cannot_be_changed_independently_from_evidence(context_repo):
    index,case,snapshot=context_repo
    context=collect_context(index,case['issue_url'],case['problem_statement'])
    pack=investigate(case,snapshot.repository,snapshot=snapshot,repository_context=context)
    changed=deepcopy(pack)
    changed['evidence']=[e for e in changed['evidence'] if e['kind']!='repository_context']
    with pytest.raises(ValueError,match='incomplete'):
        compare_packs(pack,changed)
    plain=investigate(case,snapshot.repository,snapshot=snapshot)
    assert pack['run_id']!=plain['run_id']
    assert 'repository_context_sha256' in compare_packs(plain,pack)['changed_bindings']
    from issueintel.integrations.github import comment_preview
    preview=comment_preview(pack)
    assert 'current repository context' in preview['body']
    assert 'https://github.com/owner/project/pull/2' in preview['body']


def test_workbench_only_connected_github_issues_gain_memory(context_repo,monkeypatch):
    from types import SimpleNamespace
    index,case,snapshot=context_repo
    sync=GitHubSync(index.root);sync.connect(REPO)
    sync.change(REPO,phase='ready',repository_id=42)
    # Keep this test focused on investigation; no automatic network worker.
    monkeypatch.setattr(GitHubSync,'due',lambda self:[])
    monkeypatch.setattr('issueintel.investigation.workbench.fetch_github_issue',lambda url:{
        'url':case['issue_url'],'repository':REPO,'problem_statement':case['problem_statement'],'created_at':STAMP})
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository',lambda *_a,**_k:SimpleNamespace(path=snapshot.repository))
    wb=Workbench(index.root)
    try:
        job=completed(wb,wb.submit({'mode':'github','issue_url':case['issue_url'],'revision':case['revision']})['id'])
        assert job['status']=='completed'
        pack=json.loads(wb.result_bytes(job['id']))
        assert any(e['kind']=='repository_context' for e in pack['evidence'])
        assert not pack['leakage_audit']['strict_issue_time_verified']
        second=completed(wb,wb.submit({'mode':'github','issue_url':case['issue_url'],'revision':case['revision']})['id'])
        assert wb.compare({'before_id':job['id'],'after_id':second['id']})['same_context']
    finally:wb.close()
