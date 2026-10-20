"""Private storage replay, deduplication and unverified investigation lessons."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import gzip
from hashlib import sha256
import json
import os
import subprocess

import pytest

from issueintel.investigation.pipeline import investigate
from issueintel.repository.memory_store import MemoryStore, investigation_lesson
from issueintel.repository.snapshot import build_snapshot
from issueintel.runtime.io import canonical_json, digest
from test_focused_retrieval import FakeModel, fixture_repo
from test_repository_memory import memory_repo


@pytest.fixture
def lesson_source(fixture_repo):
    case,path=fixture_repo;model=FakeModel();snapshot=build_snapshot(path,case['revision'])
    pack=investigate(case,path,snapshot=snapshot,complete_json=model,provider='synthetic',model='mock')
    raw=json.dumps(pack,ensure_ascii=False,sort_keys=True,indent=2).encode()
    return case,path,snapshot,pack,raw,model


def test_architecture_capture_is_idempotent_and_shared_blobs_are_not_copied(tmp_path,memory_repo):
    _,_,capture=memory_repo;snapshot=capture();store=MemoryStore(tmp_path)
    first=store.capture_architecture(snapshot,'synthetic/repo',max_facts=4)
    before={p.relative_to(store.directory):p.read_bytes() for p in store.cache.rglob('*.gz')}
    again=MemoryStore(tmp_path).capture_architecture(snapshot,'synthetic/repo',max_facts=4)
    assert first==again and store.read(first['receipt']['id'])==first
    assert before=={p.relative_to(store.directory):p.read_bytes() for p in store.cache.rglob('*.gz')}
    changed=store.capture_architecture(snapshot,'synthetic/repo',max_facts=2)
    assert changed['receipt']['id']!=first['receipt']['id']
    with store._db() as db:
        assert db.execute('SELECT count(*) FROM captures').fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM objects').fetchone()[0]==2
    assert len(list((store.cache/'blobs').rglob('*.gz')))==len({file.sha256 for file in snapshot.files})


def test_concurrent_capture_has_one_receipt_without_partial_source_objects(tmp_path,memory_repo):
    _,_,capture=memory_repo;snapshot=capture()
    with ThreadPoolExecutor(max_workers=4) as executor:
        values=list(executor.map(lambda _:MemoryStore(tmp_path).capture_architecture(snapshot,'synthetic/repo'),range(4)))
    assert all(value==values[0] for value in values)
    with MemoryStore(tmp_path)._db() as db:
        assert db.execute('SELECT count(*) FROM captures').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM memory_search').fetchone()[0]==1


def test_saved_read_and_search_need_no_git_parser_provider_or_original_checkout(tmp_path,memory_repo,monkeypatch):
    _,_,capture=memory_repo;snapshot=capture();store=MemoryStore(tmp_path)
    saved=store.capture_architecture(snapshot,'synthetic/repo')
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('Saved memory called Git'))
    monkeypatch.setattr('issueintel.repository.memory.build_graph',lambda *a:pytest.fail('Saved memory rebuilt AST'))
    assert MemoryStore(tmp_path).read(saved['receipt']['id'])==saved
    rows=store.list('synthetic/repo',query='normalize')['results']
    assert rows[0]['id']==saved['receipt']['id'] and rows[0]['matched_terms']==['normalize']
    assert not store.list('other/repo',query='normalize')['results']


def test_different_source_jobs_share_payload_and_snapshot_but_retain_separate_receipts(tmp_path,lesson_source):
    case,_,snapshot,_,raw,_=lesson_source;store=MemoryStore(tmp_path)
    first=store.capture_architecture(snapshot,case['repository'],source_job_id='a'*24,source_raw=raw)
    second=store.capture_architecture(snapshot,case['repository'],source_job_id='b'*24,source_raw=raw)
    assert first['receipt']['id']!=second['receipt']['id'] and first['memory']==second['memory']
    assert first['receipt']['source_pack_sha256']==sha256(raw).hexdigest()
    with store._db() as db:
        assert db.execute('SELECT count(*) FROM objects').fetchone()[0]==1
    assert len(list((store.directory/'packs').rglob('*.gz')))==1


def test_lesson_keeps_verbatim_selected_hypotheses_and_exact_pack_without_truth_promotion(tmp_path,lesson_source):
    case,_,snapshot,pack,raw,model=lesson_source;store=MemoryStore(tmp_path);before=deepcopy(pack);calls=len(model.calls)
    result=store.capture_lesson(raw,snapshot,case['repository'],source_job_id='a'*24,hypothesis_ids=['h0','h2'],note='Review this guard next time')
    lesson=result['memory']
    assert [row['id'] for row in lesson['hypotheses']]==['h0','h2']
    assert lesson['hypotheses'][0]['statement']==pack['annotation']['hypotheses'][0]['statement']
    assert lesson['truth_level']=='unverified_prior_investigation' and lesson['available_at'] is None
    assert all(lesson[key] is False for key in ('runtime_verified','causal_verified','independent_human','historical_eligible'))
    assert 'correctness' not in lesson['hypotheses'][0] and 'source_quality' not in lesson
    assert all(row['id'] in pack['annotation']['hypotheses'][0]['support_ids']+pack['annotation']['hypotheses'][2]['support_ids'] for row in lesson['evidence'])
    assert lesson['source']['pack_sha256']==sha256(raw).hexdigest()
    assert pack==before and len(model.calls)==calls
    assert store.capture_lesson(raw,snapshot,case['repository'],source_job_id='a'*24,
                               hypothesis_ids=['h0','h2'],note='Review this guard next time')==result
    assert store.read(result['receipt']['id'])==result


@pytest.mark.parametrize('ids',[[],['h0','h0'],['missing'],['h2','h0'],'h0',[True]])
def test_lesson_selection_cannot_invent_duplicate_or_reorder_hypotheses(lesson_source,ids):
    case,_,snapshot,_,raw,_=lesson_source
    with pytest.raises(ValueError):investigation_lesson(raw,snapshot,case['repository'],hypothesis_ids=ids)


@pytest.mark.parametrize('mutation',[
    lambda p:p['case'].update(split='main_test'),
    lambda p:p['case'].update(instance_id='synthetic__restricted'),
    lambda p:p['case'].update(case_id='verified:restricted'),
    lambda p:p['leakage_audit'].update(gold_in_inference=True),
    lambda p:p['leakage_audit'].update(strict_issue_time_verified=True),
    lambda p:p['snapshot'].update(commit_sha='f'*40),
    lambda p:p['evidence'][1].update(text='changed source bytes'),
])
def test_product_memory_rejects_benchmark_strict_or_source_corruption(tmp_path,lesson_source,mutation):
    case,_,snapshot,pack,_,_=lesson_source;mutation(pack);raw=json.dumps(pack).encode();store=MemoryStore(tmp_path)
    with pytest.raises(ValueError):store.capture_lesson(raw,snapshot,case['repository'],hypothesis_ids=['h0'])
    assert not store.directory.exists()


def test_lesson_dependencies_change_without_rewriting_history_or_becoming_verified(tmp_path,lesson_source):
    case,path,snapshot,_,raw,_=lesson_source;store=MemoryStore(tmp_path)
    saved=store.capture_lesson(raw,snapshot,case['repository'],hypothesis_ids=['h0']);identifier=saved['receipt']['id']
    assessment=store.assess(identifier,snapshot,cutoff='2099-01-01T00:00:00Z')
    assert all(row['status']=='unchanged' for row in assessment['dependencies'])
    assert not assessment['hypothesis_verified'] and not assessment['historical_eligible']
    (path/'parser.py').write_text('def parse_list(items):\n    return []\n',encoding='utf-8')
    subprocess.run(['git','-C',str(path),'add','.'],check=True,capture_output=True)
    subprocess.run(['git','-c','core.hooksPath=','-C',str(path),'-c','user.name=Synthetic',
        '-c','user.email=test@example.invalid','-c','commit.gpgsign=false','commit','-qm','Changed source'],check=True,capture_output=True)
    current=build_snapshot(path,'HEAD');changed=store.assess(identifier,current)
    assert any(row['status']=='changed' for row in changed['dependencies'])
    assert store.read(identifier)==saved


@pytest.mark.parametrize('where',['object','receipt','blob','pack','search'])
def test_tampered_objects_receipts_and_index_bindings_fail_closed(tmp_path,lesson_source,where):
    case,_,snapshot,_,raw,_=lesson_source;store=MemoryStore(tmp_path)
    saved=store.capture_lesson(raw,snapshot,case['repository'],hypothesis_ids=['h0']);identifier=saved['receipt']['id']
    if where=='object':
        with store._db(write=True) as db:db.execute('UPDATE objects SET payload=?',(b'{}',))
    elif where=='receipt':
        receipt=deepcopy(saved['receipt']);receipt['repository']='other/repo'
        with store._db(write=True) as db:db.execute('UPDATE captures SET receipt=?',(canonical_json(receipt).encode(),))
    elif where=='blob':
        next((store.cache/'blobs').rglob('*.gz')).write_bytes(gzip.compress(b'corrupted source'))
    elif where=='pack':
        next((store.directory/'packs').rglob('*.gz')).write_bytes(gzip.compress(raw+b' '))
    else:
        with store._db(write=True) as db:db.execute('UPDATE memory_search SET text=?',('poisoned index',))
    with pytest.raises(ValueError):
        store.list(case['repository']) if where=='search' else store.read(identifier)


@pytest.mark.parametrize('identifier',['../.env','capture_'+('g'*32),'capture_'+('a'*33),None])
def test_memory_identifiers_are_not_file_paths(tmp_path,identifier):
    with pytest.raises(ValueError):MemoryStore(tmp_path).read(identifier)


def test_search_treats_user_syntax_as_terms_not_sql_or_fts_operators(tmp_path,memory_repo):
    _,_,capture=memory_repo;store=MemoryStore(tmp_path);store.capture_architecture(capture(),'synthetic/repo')
    assert store.list('synthetic/repo',query='normalize OR " "; DROP TABLE captures --')['results']
    assert store.list('synthetic/repo')['results']
    with pytest.raises(ValueError):store.list('synthetic/repo',limit=True)


def test_architecture_capture_does_not_claim_ownership_without_pack_witness(tmp_path,memory_repo):
    _,_,capture=memory_repo;store=MemoryStore(tmp_path)
    with pytest.raises(ValueError,match='exact product pack'):store.capture_architecture(capture(),'synthetic/repo',source_job_id='a'*24)
    assert not store.directory.exists()


def test_lesson_read_and_search_replay_without_git_or_model_config(tmp_path,lesson_source,monkeypatch):
    case,path,snapshot,_,raw,_=lesson_source;store=MemoryStore(tmp_path)
    value=store.capture_lesson(raw,snapshot,case['repository'],hypothesis_ids=['h0'])
    detached=path.with_name('detached_synthetic_repo')
    assert path.resolve().is_relative_to(tmp_path.resolve()) and detached.resolve().is_relative_to(tmp_path.resolve())
    path.rename(detached)
    assert not path.exists()
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('Saved lesson called Git'))
    monkeypatch.setattr('issueintel.runtime.config.RuntimeConfig.load',lambda *a:pytest.fail('Saved lesson read API configuration'))
    assert store.read(value['receipt']['id'])==value
    assert store.list(case['repository'],query='sentinel')['results'][0]['id']==value['receipt']['id']


def test_structurally_malformed_receipt_and_pack_fail_with_controlled_errors(tmp_path,lesson_source):
    case,_,snapshot,_,raw,_=lesson_source;store=MemoryStore(tmp_path)
    value=store.capture_lesson(raw,snapshot,case['repository'],hypothesis_ids=['h0'])
    with store._db(write=True) as db:db.execute('UPDATE captures SET receipt=?',(b'[]',))
    with pytest.raises(ValueError,match='database or capture structure'):store.read(value['receipt']['id'])
    with pytest.raises(ValueError,match='source structure'):store.capture_lesson(b'{}',snapshot,case['repository'],hypothesis_ids=['h0'])


@pytest.mark.parametrize('after_publish',[False,True])
def test_interrupted_object_publish_can_resume_without_partial_files_or_overwriting_hashes(tmp_path,memory_repo,monkeypatch,after_publish):
    _,_,capture=memory_repo;snapshot=capture();store=MemoryStore(tmp_path);original=os.link
    def interrupted(source,target):
        if after_publish:original(source,target)
        raise KeyboardInterrupt('Synthetic interruption before the capture transaction commits')
    monkeypatch.setattr(os,'link',interrupted)
    with pytest.raises(KeyboardInterrupt):store.capture_architecture(snapshot,'synthetic/repo')
    assert not list(store.directory.rglob('.memory-*'))
    with store._db() as db:assert db.execute('SELECT count(*) FROM captures').fetchone()[0]==0
    published={path:path.read_bytes() for path in store.cache.rglob('*.gz')}
    monkeypatch.setattr(os,'link',original)
    value=store.capture_architecture(snapshot,'synthetic/repo')
    assert value==store.read(value['receipt']['id'])
    assert all(path.read_bytes()==raw for path,raw in published.items())


def test_existing_corrupt_cache_object_is_never_rewritten_to_make_capture_pass(tmp_path,memory_repo):
    _,_,capture=memory_repo;snapshot=capture();store=MemoryStore(tmp_path)
    first=snapshot.files[0];target=store.cache/'blobs'/first.sha256[:2]/(first.sha256+'.txt.gz')
    target.parent.mkdir(parents=True);corrupt=gzip.compress(b'damaged source');target.write_bytes(corrupt)
    with pytest.raises(ValueError,match='conflicting bytes'):store.capture_architecture(snapshot,'synthetic/repo')
    assert target.read_bytes()==corrupt
    with store._db() as db:assert db.execute('SELECT count(*) FROM captures').fetchone()[0]==0
