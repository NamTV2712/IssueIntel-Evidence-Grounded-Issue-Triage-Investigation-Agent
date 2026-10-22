"""A pair remains inspectable offline without mixing the candidate into lineage."""
from copy import deepcopy
import json
import subprocess
import sys

import pytest

from issueintel.investigation import portable_verify as portable
from issueintel.runtime.io import digest
from test_handoff import contents,exported,repack
from test_issue_relations import source,generate as core_generate,response,ref
from test_issue_relation_jobs import relating,synced_relating,generate
from test_github_index import connected
from test_workbench import completed


def seal(value):
    value['sha256']=digest({k:v for k,v in value.items() if k!='sha256'})


def changed_archive(files,mutation):
    files=deepcopy(files);handoff=json.loads(files['handoff.json'])
    entry=handoff['issue_relations'][0];identifier=entry['relation_job_id'];prefix='relations/'+identifier+'/'
    bundle=entry['bundle'];witness=json.loads(files[prefix+'inputs.json'])
    mutation(bundle,witness)
    seal(bundle);seal(witness);seal(handoff)
    files[prefix+'comparison.json']=json.dumps(bundle,ensure_ascii=False).encode()
    files[prefix+'inputs.json']=json.dumps(witness,ensure_ascii=False).encode()
    files['handoff.json']=json.dumps(handoff,ensure_ascii=False).encode()
    manifest=json.loads(files['manifest.json']);manifest['selection_sha256']=handoff['sha256']
    for row in manifest['entries']:
        row.update(bytes=len(files[row['path']]),sha256=portable.sha(files[row['path']]))
    seal(manifest);files['manifest.json']=json.dumps(manifest).encode()
    return repack(files)


def test_standalone_contract_replays_version_unknown_candidate_with_its_gaps(source):
    value=response(source)
    value['versions']={side:{'status':'unknown','value':None,'citations':[]} for side in ('left','right')}
    value['dimensions'][2].update(judgment='unknown',support=[],contextual_fit='unknown')
    value['unknowns']=['Affected releases require follow-up.']
    bundle=core_generate(source,value)
    assert portable._rel_validate(bundle,source)==bundle
    assert bundle['assessment']['classification']=='possible_duplicate'
    assert {'left_affected_version_unknown','right_affected_version_unknown'}<={g['code'] for g in bundle['assessment']['gaps']}


@pytest.mark.parametrize('mutation',['quote','side','unicode','assessment','bound_hash','rubric','truth','attempt_type'])
def test_standalone_contract_rejects_resealed_citation_or_assessment_changes(source,mutation):
    bundle=core_generate(source)
    if mutation=='quote':bundle['comparison']['dimensions'][1]['support'][0]['quote']='not in source'
    elif mutation=='side':bundle['comparison']['dimensions'][1]['support'][0]['side']='right'
    elif mutation=='unicode':bundle['comparison']['dimensions'][0]['support'][0].update(start=0,end=2,quote='😀')
    elif mutation=='assessment':bundle['assessment']['classification']='insufficient'
    elif mutation=='bound_hash':bundle['assessment']['dimensions'][1]['support'][0]['source_sha256']='a'*64
    elif mutation=='rubric':bundle['rubric']['version']='invented-rubric'
    elif mutation=='truth':bundle['duplicate_verified']=0
    else:bundle['provenance']['attempts']=True
    seal(bundle)
    with pytest.raises(ValueError):portable._rel_validate(bundle,source)


def test_dossier_pair_and_isolated_handoff_keep_candidate_out_of_issue_lineage(relating,tmp_path,monkeypatch):
    wb,provider,left,right=relating;row,bundle=generate(wb,left,right)
    before=[wb._job_path(r['id']).read_bytes() for r in (left,right,row)]
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *a:pytest.fail('Saved config'))
    monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot',lambda *a,**k:pytest.fail('Saved Git'))
    monkeypatch.setattr('issueintel.integrations.github_index.RepositoryIndex.item',lambda *a:pytest.fail('Saved index'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Saved model')
    dossier=wb.dossier(left['id'])
    assert dossier['issue_relations']==[{'relation_job_id':row['id'],'source_job_id':left['id'],'bundle':bundle}]
    assert right['id'] not in {r['job_id'] for r in dossier['runs']}
    assert any(e['kind']=='issue_relation' and e['sha256']==bundle['sha256'] for e in dossier['timeline'])
    preview,raw=exported(wb,left['id']);files=contents(raw);prefix='relations/'+row['id']+'/'
    assert preview['included_job_ids']==[left['id']]
    assert {'inputs.json','comparison.json','comparison.md'}=={name.removeprefix(prefix) for name in files if name.startswith(prefix)}
    moved=tmp_path/'other-machine';moved.mkdir();archive=moved/'handoff.zip';archive.write_bytes(raw)
    verifier=moved/'verify.py';verifier.write_bytes(files['verify.py'])
    result=subprocess.run([sys.executable,'-I',str(verifier),str(archive),'--expected-sha256',portable.sha(raw)],
        cwd=moved,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert portable.verify(archive)['runs']==1 and portable.verify(archive)['semantic_verified'] is False
    assert before==[wb._job_path(r['id']).read_bytes() for r in (left,right,row)]
    assert len(provider.calls)==1


@pytest.mark.parametrize('mutation',['side','classification','quote','witness_owner','candidate_pack','candidate_authority','policy'])
def test_pair_handoff_rejects_changes_after_every_container_is_rehashed(relating,tmp_path,mutation):
    wb,_,left,right=relating;generate(wb,left,right);_,raw=exported(wb,left['id']);files=contents(raw)
    def change(bundle,witness):
        if mutation=='side':bundle['comparison']['dimensions'][0]['support'][0]['side']='right'
        elif mutation=='classification':bundle['assessment']['classification']='possible_duplicate'
        elif mutation=='quote':bundle['comparison']['dimensions'][0]['support'][0]['quote']='not in either side'
        elif mutation=='witness_owner':witness['owner_job_id']='f'*24
        elif mutation=='candidate_pack':witness['right']['pack_json']+=' '
        elif mutation=='candidate_authority':
            witness['right']['job']['mode']='main_dev';witness['right']['job']['request']['mode']='main_dev'
        else:witness['policy_sha256']='f'*64
    archive=tmp_path/'forged-pair.zip';archive.write_bytes(changed_archive(files,change))
    with pytest.raises(ValueError):portable.verify(archive)


def test_synced_issue_handoff_uses_frozen_record_receipt_without_current_index(synced_relating,tmp_path,monkeypatch):
    wb,_,_,provider,left,body=synced_relating
    row=completed(wb,wb.submit_issue_relation(body)['id']);assert row['status']=='completed',row
    monkeypatch.setattr('issueintel.integrations.github_index.RepositoryIndex.item',lambda *a:pytest.fail('Current index'))
    monkeypatch.setattr('issueintel.investigation.question_jobs.build_snapshot',lambda *a,**k:pytest.fail('Saved Git'))
    wb.provider_factory=lambda *a,**k:pytest.fail('Saved provider')
    _,raw=exported(wb,left['id']);archive=tmp_path/'synced-pair.zip';archive.write_bytes(raw)
    assert portable.verify(archive)['runs']==1 and len(provider.calls)==1
    def change(_,witness):witness['right']['item']['receipt']['repository_id']=999
    archive.write_bytes(changed_archive(contents(raw),change))
    with pytest.raises(ValueError):portable.verify(archive)
