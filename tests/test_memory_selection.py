"""Source-bound public memory hints: current static replay and no truth promotion."""
from copy import deepcopy
from hashlib import sha256
import json
import subprocess

import pytest

from issueintel.investigation.memory_selection import preview_memory_selection,validate_selections,MAX_CONTEXT
from issueintel.investigation.pipeline import investigate
from issueintel.repository.memory_store import MemoryStore
from issueintel.repository.snapshot import build_snapshot
from issueintel.runtime.io import digest
from test_repository_memory_store import lesson_source
from test_focused_retrieval import fixture_repo,FakeModel


def choice(value,item):
    return {'capture_id':value['receipt']['id'],'receipt_sha256':value['receipt']['sha256'],
            'item_id':item['id'],'item_sha256':item.get('sha256') or digest(item)}


def test_preview_keeps_local_lineage_out_of_hints_and_no_calls_or_source_mutation(tmp_path,lesson_source):
    case,_,snapshot,pack,raw,model=lesson_source;store=MemoryStore(tmp_path);calls=len(model.calls);before=deepcopy(pack)
    architecture=store.capture_architecture(snapshot,case['repository'],source_raw=raw,source_job_id='a'*24)
    lesson=store.capture_lesson(raw,snapshot,case['repository'],source_job_id='b'*24,hypothesis_ids=['h0'],note='An unverified note')
    selections=[choice(architecture,architecture['memory']['facts'][0]),choice(lesson,lesson['memory']['hypotheses'][0])]
    result=preview_memory_selection(store,raw,snapshot,selections)
    assert result['source']['pack_sha256']==sha256(raw).hexdigest()
    assert result['context_sha256']==sha256(result['additional_context'].encode()).hexdigest()
    assert result['model_calls']==0 and result['automatic_inference'] is False
    assert len(result['additional_context'])<=MAX_CONTEXT and result['selections']==selections
    assert result['items'][0]['hint']['applicability']=='revalidated_static_only'
    prior=result['items'][1]['hint']
    assert prior['truth_level']=='unverified_prior_investigation' and prior['historical_eligible'] is False
    assert prior['statement']==pack['annotation']['hypotheses'][0]['statement']
    public=json.loads(result['additional_context'].split('\n\n',1)[1])
    assert public['repository']==case['repository']
    for item in public['items']:
        assert not ({'support_ids','counter_ids','correctness','source_quality','source_job_id','capture_id','receipt_sha256'}&set(item))
    assert 'a'*24 not in result['additional_context'] and 'b'*24 not in result['additional_context']
    assert len(model.calls)==calls and pack==before
    assert preview_memory_selection(store,raw,snapshot,selections)==result


@pytest.mark.parametrize('selections',[None,[],{},[{}],['a'],[{'capture_id':'../.env'}]])
def test_invalid_selections_are_not_inferred_or_expanded(selections):
    with pytest.raises(ValueError):validate_selections(selections)


def test_duplicate_and_seventh_selection_are_rejected(tmp_path,lesson_source):
    case,_,snapshot,_,raw,_=lesson_source;store=MemoryStore(tmp_path)
    saved=store.capture_architecture(snapshot,case['repository']);item=choice(saved,saved['memory']['facts'][0])
    with pytest.raises(ValueError,match='distinct'):validate_selections([item,item])
    with pytest.raises(ValueError,match='1–6'):validate_selections([item]*7)


@pytest.mark.parametrize('change',[
    lambda s:s.update(receipt_sha256='f'*64),
    lambda s:s.update(item_sha256='e'*64),
    lambda s:s.update(item_id='unknown_item'),
])
def test_receipt_item_and_hash_must_all_match_source(tmp_path,lesson_source,change):
    case,_,snapshot,_,raw,_=lesson_source;store=MemoryStore(tmp_path)
    saved=store.capture_architecture(snapshot,case['repository']);selection=choice(saved,saved['memory']['facts'][0]);change(selection)
    with pytest.raises(ValueError):preview_memory_selection(store,raw,snapshot,[selection])


def test_memory_from_another_repository_is_denied(tmp_path,lesson_source):
    _,_,snapshot,_,raw,_=lesson_source;store=MemoryStore(tmp_path)
    saved=store.capture_architecture(snapshot,'other/repo')
    with pytest.raises(ValueError,match='another repository'):
        preview_memory_selection(store,raw,snapshot,[choice(saved,saved['memory']['facts'][0])])


def changed_source(case,path):
    (path/'parser.py').write_text('def parse_list(items):\n    return []\n',encoding='utf-8')
    subprocess.run(['git','-C',str(path),'add','.'],check=True,capture_output=True)
    subprocess.run(['git','-c','core.hooksPath=','-C',str(path),'-c','user.name=Synthetic','-c','user.email=test@example.invalid',
                    '-c','commit.gpgsign=false','commit','-qm','Changed synthetic parser'],check=True,capture_output=True)
    snapshot=build_snapshot(path,'HEAD');new_case=dict(case,revision=snapshot.commit_sha)
    pack=investigate(new_case,path,snapshot=snapshot)
    return snapshot,json.dumps(pack,sort_keys=True).encode()


def test_changed_architecture_item_is_denied_while_unaffected_observation_can_be_selected(tmp_path,lesson_source):
    case,path,snapshot,_,raw,_=lesson_source;store=MemoryStore(tmp_path)
    value=store.capture_architecture(snapshot,case['repository'])
    parser=next(row for row in value['memory']['facts'] if row['source']['path']=='parser.py')
    other=next(row for row in value['memory']['facts'] if row['source']['path']=='formatting.py')
    current,current_raw=changed_source(case,path)
    with pytest.raises(ValueError,match='does not revalidate'):
        preview_memory_selection(store,current_raw,current,[choice(value,parser)])
    result=preview_memory_selection(store,current_raw,current,[choice(value,other)])
    assert result['items'][0]['hint']['source_commit_sha']!=result['items'][0]['hint']['viewed_commit_sha']
    assert result['items'][0]['hint']['applicability']=='revalidated_static_only'
    assert store.read(value['receipt']['id'])==value


def test_prior_hypothesis_with_changed_code_is_explicitly_old_unverified_search_context(tmp_path,lesson_source):
    case,path,snapshot,_,raw,_=lesson_source;store=MemoryStore(tmp_path)
    value=store.capture_lesson(raw,snapshot,case['repository'],hypothesis_ids=['h0'])
    current,current_raw=changed_source(case,path)
    result=preview_memory_selection(store,current_raw,current,[choice(value,value['memory']['hypotheses'][0])])
    hint=result['items'][0]['hint']
    assert hint['applicability']=='source_changed' and hint['source_commit_sha']==snapshot.commit_sha
    assert hint['viewed_commit_sha']==current.commit_sha and hint['historical_eligible'] is False
    assert hint['runtime_verified'] is False and hint['causal_verified'] is False
    assert not ({'support_ids','counter_ids','correctness'}&set(hint))


def test_context_bound_rejects_large_selection_without_silently_dropping_or_rewriting_it(tmp_path,lesson_source):
    case,path,snapshot,pack,raw,_=lesson_source;store=MemoryStore(tmp_path)
    for h in pack['annotation']['hypotheses']:h['statement']='X'*7000
    # Rebuild valid synthetic annotation using the shared generator; hidden
    # labels and source-quality are never used to make the hints fit.
    model=FakeModel(focus='X'*7000)
    pack=investigate(case,path,snapshot=snapshot,complete_json=model,provider='synthetic',model='mock')
    raw=json.dumps(pack).encode();value=store.capture_lesson(raw,snapshot,case['repository'],hypothesis_ids=['h0','h1','h2'])
    selections=[choice(value,h) for h in value['memory']['hypotheses']]
    with pytest.raises(ValueError,match='context bound'):preview_memory_selection(store,raw,snapshot,selections)
    assert store.read(value['receipt']['id'])==value


def test_product_preflight_blocks_benchmark_before_store_access(tmp_path,lesson_source,monkeypatch):
    _,_,snapshot,pack,_,_=lesson_source;store=MemoryStore(tmp_path)
    pack['case']['split']='main_test'
    monkeypatch.setattr(store,'read',lambda *a:pytest.fail('Restricted source read memory'))
    selection={'capture_id':'capture_'+'a'*32,'receipt_sha256':'b'*64,'item_id':'H1','item_sha256':'c'*64}
    with pytest.raises(ValueError,match='product source outside benchmark'):
        preview_memory_selection(store,json.dumps(pack).encode(),snapshot,[selection])


def test_snapshot_must_match_exact_target_pack(tmp_path,lesson_source):
    case,path,snapshot,_,raw,_=lesson_source;store=MemoryStore(tmp_path);value=store.capture_architecture(snapshot,case['repository'])
    current,_=changed_source(case,path)
    with pytest.raises(ValueError,match='revision differs'):
        preview_memory_selection(store,raw,current,[choice(value,value['memory']['facts'][0])])


def test_machine_repository_path_is_not_projected_into_public_hints(tmp_path,lesson_source):
    case,path,snapshot,_,_,_=lesson_source
    local_case=dict(case,repository=str(path));pack=investigate(local_case,path,snapshot=snapshot)
    raw=json.dumps(pack).encode();store=MemoryStore(tmp_path);value=store.capture_architecture(snapshot,str(path),source_raw=raw)
    preview=preview_memory_selection(store,raw,snapshot,[choice(value,value['memory']['facts'][0])])
    assert str(path) not in preview['additional_context']
    assert json.loads(preview['additional_context'].split('\n\n',1)[1])['repository']=='local_repository'
