"""Captured lineage accounting remains inspectable on another machine."""
from copy import deepcopy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from issueintel.investigation import portable_verify as portable
from issueintel.investigation import investigation_usage as accounting
from issueintel.runtime.io import digest
from test_dossier import family
from test_check_plan_jobs import checking
from test_handoff import contents,exported,repack
from test_investigation_usage import _node,_groq,_typesafe,_snapshot,_id,_edge


def test_artifact_call_bounds_are_bound_to_recovery_and_comparison_policy(monkeypatch):
    from issueintel.investigation.stage_jobs import job_policy_sha256
    from issueintel.investigation.issue_relation_jobs import policy_sha256
    original=Path.read_bytes
    before=(job_policy_sha256(),policy_sha256())
    def changed(path):
        raw=original(path)
        return raw+b'\n# Synthetic accounting authority change\n' if path.name=='artifact_accounting.py' else raw
    monkeypatch.setattr(Path,'read_bytes',changed)
    after=(job_policy_sha256(),policy_sha256())
    assert all(left!=right for left,right in zip(before,after))


@pytest.mark.parametrize('provider',['groq','typesafe'])
@pytest.mark.parametrize('status',['reserved','settled','failed_bound','missing_usage_bound','breach'])
def test_frozen_accounting_matches_native_receipt_and_total_rules(provider,status):
    kwargs=({'receipts':[_groq(1,status=status)]} if provider=='groq'
            else {'typesafe':[_typesafe(1,_id(1),status=status)]})
    snapshot=_snapshot([_node(1,**kwargs)])
    bundle=accounting.build(snapshot)
    assert portable._acct_validate(bundle)==bundle
    assert portable._acct_build(snapshot)==bundle
    assert portable._acct_render_markdown(bundle)==accounting.render_markdown(bundle)
    changed=deepcopy(bundle);changed['lineage']['known_micro_usd_charged_or_reserved']+=1;seal(changed)
    with pytest.raises(ValueError):portable._acct_validate(changed)


@pytest.mark.parametrize('change',['pack','mode','status','provider','request','parent'])
def test_frozen_accounting_rejects_conflicts_with_other_included_run_bytes(change):
    parent=_node(1,receipts=[_groq(1)]);child=_node(2,mode='followup')
    snapshot=_snapshot([parent,child],[_edge(child,parent)],selected=2)
    runs={n['job_id']:{'job_id':n['job_id'],'mode':n['mode'],'status':n['job_status'],'provider':n['provider'],
        'request_sha256':n['request_sha256'],'binding':{'pack_sha256':n['pack_sha256']},
        'parent_job_id':parent['job_id'] if n['job_id']==child['job_id'] else None} for n in (parent,child)}
    bundle=accounting.build(snapshot)
    assert portable._acct_validate_run_bindings(bundle,runs)==bundle
    modified=deepcopy(snapshot);root=modified['nodes'][0]
    if change=='pack':root['pack_sha256']='f'*64;modified['edges'][0]['source_pack_sha256']='f'*64
    elif change=='mode':root['mode']='example'
    elif change=='status':root['job_status']='failed'
    elif change=='provider':root['provider']='none'
    elif change=='request':root['request_sha256']='f'*64
    else:root['job_id']='f'*24;modified['edges'][0]['parent_job_id']='f'*24
    seal(modified);changed=accounting.build(modified)
    assert portable._acct_validate(changed)==changed
    with pytest.raises(ValueError):portable._acct_validate_run_bindings(changed,runs)


@pytest.mark.parametrize('case',['missing_attempts','duplicate','conflicting_owner','conflicting_bytes','lineage_gap'])
def test_frozen_accounting_replays_partial_tracking_dedup_and_scope_gaps(case):
    parent=_node(1,receipts=[_groq(1)]);nodes=[parent];edges=[];gaps=[]
    if case=='missing_attempts':parent['providers']['groq']['attempted_calls']=None
    elif case=='duplicate':parent['providers']['groq']['receipts']*=2
    elif case=='conflicting_owner':
        child=_node(2,mode='followup',receipts=[_groq(1)]);nodes.append(child);edges.append(_edge(child,parent))
    elif case=='conflicting_bytes':
        other=_groq(1,micro=202);parent['providers']['groq']['receipts'].append({'receipt_sha256':digest(other),'receipt':other})
    else:gaps=[{'code':'lineage_source_unavailable','job_id':parent['job_id'],'provider':None,'detail':'The recorded parent is unavailable.'}]
    bundle=accounting.build(_snapshot(nodes,edges,selected=2 if case=='conflicting_owner' else 1,gaps=gaps))
    assert portable._acct_build(bundle['snapshot'])==bundle
    assert portable._acct_validate(bundle)==bundle
    if case=='duplicate':assert bundle['lineage']['known_micro_usd_charged_or_reserved']==101
    elif case=='lineage_gap':assert bundle['current']['complete_micro_usd_charged_or_reserved']==101
    assert case=='duplicate' or bundle['lineage']['complete_micro_usd_charged_or_reserved'] is None


def seal(value):
    value['sha256']=digest({key:item for key,item in value.items() if key!='sha256'})


def reseal_archive(files,handoff):
    files=deepcopy(files);seal(handoff);files['handoff.json']=json.dumps(handoff,ensure_ascii=False).encode()
    manifest=json.loads(files['manifest.json']);manifest['selection_sha256']=handoff['sha256']
    manifest['entries']=[dict(row,bytes=len(files[row['path']]),sha256=portable.sha(files[row['path']]))
        for row in manifest['entries'] if row['path'] in files]
    seal(manifest);files['manifest.json']=json.dumps(manifest).encode()
    return repack(files)


def test_selected_lineage_accounting_has_no_provider_or_git_read_and_verifies_isolated(family,tmp_path,monkeypatch):
    wb,provider,parent,plan,_,child=family
    before=wb.result_bytes(child['id']);calls=(len(provider.calls),len(provider.check_calls))
    wb.provider_factory=lambda *a,**k:pytest.fail('Accounting export provider')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Accounting export config'))
    monkeypatch.setattr('issueintel.investigation.pipeline.build_snapshot',lambda *a,**k:pytest.fail('Accounting export Git'))
    expected=wb.investigation_usage(child['id'])
    preview,raw=exported(wb,child['id']);files=contents(raw);handoff=json.loads(files['handoff.json'])
    assert handoff['accounting']==[{'job_id':child['id'],'bundle':expected}]
    prefix='accounting/'+child['id']+'/'
    assert json.loads(files[prefix+'usage.json'])==expected
    assert json.loads(files[prefix+'snapshot.json'])==expected['snapshot']
    assert prefix+'usage.md' in files and b'provider invoice' in files['README.txt']
    assert parent['id'] in expected['lineage']['job_ids'] and plan['id'] in expected['lineage']['job_ids']
    assert wb.result_bytes(child['id'])==before and calls==(len(provider.calls),len(provider.check_calls))
    moved=tmp_path/'another-machine';moved.mkdir();archive=moved/'bundle.zip';archive.write_bytes(raw)
    script=moved/'verify.py';script.write_bytes(files['verify.py'])
    result=subprocess.run([sys.executable,'-I',str(script),str(archive),'--expected-sha256',preview['archive_sha256']],
        cwd=moved,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['integrity']=='consistent'
    assert exported(wb,child['id'])==(preview,raw)


@pytest.mark.parametrize('change',['total','source','snapshot','current','truth'])
def test_rehashed_accounting_outputs_cannot_change_totals_scope_or_source(family,tmp_path,change):
    wb,_,_,_,_,child=family;_,raw=exported(wb,child['id']);files=contents(raw)
    handoff=json.loads(files['handoff.json']);bundle=handoff['accounting'][0]['bundle']
    if change=='total':bundle['lineage']['known_micro_usd_charged_or_reserved']+=1
    elif change=='source':bundle['source']['selected_job_id']='f'*24
    elif change=='snapshot':bundle['snapshot']['selected_job_id']='f'*24;seal(bundle['snapshot'])
    elif change=='current':bundle['current']['job_ids']=bundle['lineage']['job_ids']
    else:bundle['is_provider_invoice']=True
    seal(bundle);prefix='accounting/'+child['id']+'/'
    files[prefix+'usage.json']=json.dumps(bundle).encode()
    files[prefix+'snapshot.json']=json.dumps(bundle['snapshot']).encode()
    archive=tmp_path/'forged-accounting.zip';archive.write_bytes(reseal_archive(files,handoff))
    with pytest.raises(ValueError):portable.verify(archive)


def test_prior_handoff_without_accounting_extension_remains_readable(family,tmp_path):
    wb,_,_,_,_,child=family;_,raw=exported(wb,child['id']);files=contents(raw)
    handoff=json.loads(files['handoff.json']);handoff.pop('accounting')
    files={name:value for name,value in files.items() if not name.startswith('accounting/')}
    archive=tmp_path/'prior-format.zip';archive.write_bytes(reseal_archive(files,handoff))
    assert portable.verify(archive)['integrity']=='consistent'
