"""Synthetic immutable architecture memory and strict temporal admission."""
from copy import deepcopy
from dataclasses import replace
import json
import subprocess

import pytest

from issueintel.repository import memory as module
from issueintel.repository.memory import (build_architecture_memory,validate_architecture_memory,
    assess_architecture_memory,historical_availability)
from issueintel.repository.snapshot import build_snapshot
from issueintel.runtime.io import digest


@pytest.fixture
def memory_repo(tmp_path):
    def git(*args):
        return subprocess.check_output(['git','-c','core.hooksPath=','-c','commit.gpgsign=false',
            '-C',str(tmp_path),*args],stderr=subprocess.PIPE,text=True).strip()
    git('init','-q');git('config','user.name','Synthetic');git('config','user.email','test@example.invalid')
    git('config','core.autocrlf','false')
    (tmp_path/'helpers.py').write_text('def normalize(value):\n    return value.strip()\n',encoding='utf-8')
    (tmp_path/'main.py').write_text('from helpers import normalize\n\nclass Base:\n    pass\n\n'
        'class Handler(Base):\n    def handle(self, value):\n        return normalize(value)\n',encoding='utf-8')
    (tmp_path/'broken.py').write_text('def broken(\n',encoding='utf-8')
    def snapshot(**kwargs):
        git('add','.');git('commit','--allow-empty','-qm','Synthetic revision')
        return build_snapshot(tmp_path,git('rev-parse','HEAD'),**kwargs)
    return tmp_path,git,snapshot


def seal(value):
    value['sha256']=digest({k:v for k,v in value.items() if k!='sha256'})
    return value


def certified(snapshot,available='2020-01-01T00:00:00Z',cutoff='2020-01-03T00:00:00Z'):
    receipt={'commit_sha':snapshot.commit_sha,'source_id':'synthetic:independent-event',
             'available_at':available,'verified':True}
    availability={'status':'caller_verified_receipt','historical_certified':True,
                  'available_at':available,'source_id':receipt['source_id'],
                  'receipt_sha256':digest(receipt),'receipt':receipt}
    value=snapshot.to_dict();value.update(protocol='issue_time',cutoff=cutoff,availability=availability)
    value.pop('snapshot_sha256')
    return replace(snapshot,protocol='issue_time',cutoff=cutoff,availability=availability,snapshot_sha256=digest(value))


def test_definitions_and_static_relations_have_exact_bound_sources_and_no_runtime_claim(memory_repo):
    _,_,capture=memory_repo;snapshot=capture()
    value=build_architecture_memory(snapshot)
    assert {row['kind'] for row in value['facts']}=={'definition','imports','calls','inherits'}
    assert any(row['kind']=='calls' and row['inferred'] for row in value['facts'])
    assert not value['runtime_verified'] and not value['causal_verified'] and value['model_calls']==0
    assert value['parse_errors'][0]['path']=='broken.py'
    assert validate_architecture_memory(json.loads(json.dumps(value,sort_keys=True)),snapshot)==value


@pytest.mark.parametrize('limit',[0,True,1001,'3',None])
def test_capture_requires_a_real_bounded_integer(memory_repo,limit):
    _,_,capture=memory_repo
    with pytest.raises(ValueError,match='bound'):build_architecture_memory(capture(),max_facts=limit)


def test_fact_limit_is_explicit_not_proof_that_other_entities_are_absent(memory_repo):
    _,_,capture=memory_repo;snapshot=capture();all_facts=build_architecture_memory(snapshot)
    limited=build_architecture_memory(snapshot,max_facts=1)
    assert limited['facts']==all_facts['facts'][:1]
    assert limited['facts_omitted']==len(all_facts['facts'])-1
    validate_architecture_memory(limited,snapshot)
    balanced=build_architecture_memory(snapshot,max_facts=4)
    assert {row['kind'] for row in balanced['facts']}=={'definition','imports','calls','inherits'}
    assert sum(balanced['fact_counts'].values())==len(balanced['facts'])+balanced['facts_omitted']


def test_read_replays_old_producer_without_git_or_current_graph_or_policy(memory_repo,monkeypatch):
    _,_,capture=memory_repo;snapshot=capture();value=build_architecture_memory(snapshot)
    value['source'].update(parser_version='python-ast-old',graph_policy_sha256='a'*64);seal(value)
    monkeypatch.setattr(module,'build_graph',lambda *a:pytest.fail('Saved read built current AST'))
    monkeypatch.setattr(module,'_graph_policy',lambda:pytest.fail('Saved read inspected producer code'))
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('Saved read called Git'))
    assert validate_architecture_memory(value,snapshot) is value


@pytest.mark.parametrize('change',[
    lambda v:v['source'].update(commit_sha='a'*40),
    lambda v:v['facts'][0]['source'].update(sha256='a'*64),
    lambda v:v['facts'][0]['dependencies'][0].update(sha256='a'*64),
    lambda v:v.update(runtime_verified=True),
    lambda v:v['facts'].append(deepcopy(v['facts'][0])),
    lambda v:v['facts'][0]['source'].update(path='../.env'),
])
def test_resealed_source_span_dependency_and_truth_tampering_is_rejected(memory_repo,change):
    _,_,capture=memory_repo;snapshot=capture();value=build_architecture_memory(snapshot);change(value);seal(value)
    with pytest.raises(ValueError):validate_architecture_memory(value,snapshot)


def reseal_fact(fact):
    payload={k:v for k,v in fact.items() if k not in {'id','sha256'}}
    fact.update(id='memory_'+digest(payload)[:24],sha256=digest(payload))


def test_resealing_all_hashes_cannot_change_bytes_bound_to_a_source_span(memory_repo):
    _,_,capture=memory_repo;snapshot=capture();value=build_architecture_memory(snapshot)
    fact=value['facts'][0];fact['source']['sha256']='a'*64;reseal_fact(fact);seal(value)
    with pytest.raises(ValueError,match='source span or content'):validate_architecture_memory(value,snapshot)


def test_recorded_relationship_hash_is_not_enough_for_static_or_historical_admission(memory_repo):
    _,_,capture=memory_repo;source=certified(capture());value=build_architecture_memory(source)
    fact=next(row for row in value['facts'] if row['kind']=='calls')
    fact['reference']='a fabricated relation';reseal_fact(fact);seal(value)
    # Captured projections can be inspected, but current AST must independently
    # reproduce a relation before it may be reused, even with valid time proof.
    validate_architecture_memory(value,source)
    assessment=assess_architecture_memory(value,source,source,cutoff='2020-01-02T00:00:00Z')
    row=next(row for row in assessment['facts'] if row['fact_id']==fact['id'])
    assert row['status']=='relationship_changed' and not row['static_eligible'] and not row['historical_eligible']


def test_dependency_changes_invalidate_affected_facts_and_preserve_original_capture(memory_repo):
    path,_,capture=memory_repo;source=capture();memory=build_architecture_memory(source);before=deepcopy(memory)
    (path/'helpers.py').write_text('def normalize(value):\n    return value.lower().strip()\n',encoding='utf-8')
    current=capture();assessment=assess_architecture_memory(memory,source,current)
    by_id={row['id']:row for row in memory['facts']}
    assert all(row['status']=='source_changed' for row in assessment['facts']
               if any(d['path']=='helpers.py' for d in by_id[row['fact_id']]['dependencies']))
    assert any(row['status']=='revalidated_static' for row in assessment['facts'])
    assert memory==before and assessment['source']['commit_sha']!=assessment['current']['commit_sha']


def test_unchanged_dependencies_do_not_prove_resolution_when_module_inventory_changes(memory_repo):
    path,_,capture=memory_repo;source=capture();memory=build_architecture_memory(source)
    (path/'src').mkdir();(path/'src/helpers.py').write_text('def normalize(value):\n    return value\n',encoding='utf-8')
    assessment=assess_architecture_memory(memory,source,capture())
    changed=[row for row in assessment['facts'] if row['status']=='relationship_changed']
    assert changed and all(all(d['status']=='unchanged' for d in row['dependencies']) for row in changed)
    assert all(not row['static_eligible'] for row in changed)


def test_missing_and_skipped_dependencies_remain_distinct(memory_repo):
    path,_,capture=memory_repo;source=capture();memory=build_architecture_memory(source)
    (path/'helpers.py').unlink();missing=capture()
    assert 'source_missing' in assess_architecture_memory(memory,source,missing)['counts']
    (path/'helpers.py').write_bytes(b'\0binary source is unreadable')
    unavailable=capture()
    assert 'source_unavailable' in assess_architecture_memory(memory,source,unavailable)['counts']


def test_producer_change_requires_revalidation_but_keeps_old_memory_readable(memory_repo,monkeypatch):
    _,_,capture=memory_repo;snapshot=capture();memory=build_architecture_memory(snapshot)
    monkeypatch.setattr(module,'_graph_policy',lambda:'f'*64)
    assert validate_architecture_memory(memory,snapshot)==memory
    assessment=assess_architecture_memory(memory,snapshot,snapshot)
    assert set(assessment['counts'])=={'parser_changed'}
    assert all(not row['static_eligible'] for row in assessment['facts'])


def test_current_snapshot_and_complete_source_proof_must_both_precede_cutoff(memory_repo):
    _,_,capture=memory_repo;snapshot=capture();source=certified(snapshot);memory=build_architecture_memory(source)
    assert historical_availability(memory,source,'2020-01-02T00:00:00Z')['eligible']
    assert not historical_availability(memory,source,'2020-01-01T00:00:00Z')['eligible']
    assert not assess_architecture_memory(memory,source,snapshot,cutoff='2020-01-02T00:00:00Z')['historical_availability']['eligible']
    good=assess_architecture_memory(memory,source,source,cutoff='2020-01-02T00:00:00Z')
    assert all(row['historical_eligible'] for row in good['facts'])
    late=certified(snapshot,available='2020-01-02T00:00:00Z')
    bad=assess_architecture_memory(memory,source,late,cutoff='2020-01-02T00:00:00Z')
    assert all(not row['historical_eligible'] for row in bad['facts'])


def test_git_dates_and_receipt_hash_without_full_receipt_cannot_authorize_historical_memory(memory_repo):
    _,_,capture=memory_repo;snapshot=capture();memory=build_architecture_memory(snapshot)
    assert not historical_availability(memory,snapshot,'2099-01-01T00:00:00Z')['eligible']
    source=certified(snapshot);availability=deepcopy(source.availability);availability.pop('receipt')
    value=source.to_dict();value['availability']=availability;value.pop('snapshot_sha256')
    missing=replace(source,availability=availability,snapshot_sha256=digest(value));missing.verify()
    assert not historical_availability(build_architecture_memory(missing),missing,'2020-01-02T00:00:00Z')['eligible']


def test_cross_repository_assessment_is_rejected(memory_repo):
    _,_,capture=memory_repo;snapshot=capture();memory=build_architecture_memory(snapshot)
    value=snapshot.to_dict();value['repository']='another repository';value.pop('snapshot_sha256')
    other=replace(snapshot,repository='another repository',snapshot_sha256=digest(value))
    with pytest.raises(ValueError,match='same repository'):assess_architecture_memory(memory,snapshot,other)
