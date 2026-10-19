"""Portable product handoffs are private selections, not Git or truth proofs."""
import io
import json
import subprocess
import sys
import threading
import zipfile

import pytest

from issueintel.interfaces.cli import main
from issueintel.investigation.portable_verify import verify,sha,encoded
from issueintel.investigation.handoff import _viewer
from issueintel.runtime.io import digest,write_json
from test_dossier import family,relation_request
from test_check_plan_jobs import checking
from test_check_observation_workbench import append
from test_product_finalization import finalization_source,approval
from test_workbench import completed,http
from issueintel.interfaces.web import make_server


def selection(wb,identifier,**changes):
    dossier=wb.dossier(identifier)
    return dict({'source_pack_sha256':dossier['source']['selected_pack_sha256'],
                 'dossier_sha256':dossier['sha256'],'job_ids':[identifier]},**changes)


def exported(wb,identifier,request=None):
    request=request or selection(wb,identifier)
    preview=wb.handoff_preview(identifier,request)
    return wb.handoff_export(identifier,{'selection':request,'preview_sha256':preview['sha256'],
                                       'confirmation':'export_selected_private_handoff'})


def contents(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:return {name:archive.read(name) for name in archive.namelist()}


def test_family_portable_selection_has_required_ancestors_and_no_new_calls(family,tmp_path,monkeypatch):
    wb,provider,parent,plan,journal,child=family
    unrelated=completed(wb,wb.submit({'mode':'example'})['id'])
    before=wb.result_bytes(child['id']);calls=(len(provider.calls),len(provider.check_calls))
    wb.provider_factory=lambda *a,**k:pytest.fail('No handoff provider')
    monkeypatch.setattr('issueintel.investigation.workbench.RuntimeConfig.load',lambda *_:pytest.fail('No config read'))
    monkeypatch.setattr('issueintel.investigation.pipeline.build_snapshot',lambda *a,**k:pytest.fail('No Git'))
    request=selection(wb,child['id']);preview,raw=exported(wb,child['id'],request)
    assert preview['automatic_ancestor_job_ids']==[parent['id']]
    assert preview['included_job_ids']==sorted([parent['id'],child['id']])
    assert preview['archive_bytes']==len(raw) and preview['archive_sha256']==sha(raw)
    files=contents(raw);view=json.loads(files['handoff.json'])
    assert unrelated['id'] not in {r['job_id'] for r in view['runs']}
    assert view['plans'][0]['plan_job_id']==plan['id'] and view['observations'][0]['journal']['sha256']==journal['sha256']
    assert files['runs/'+child['id']+'/pack.json']==before
    assert all(not any(x in name for x in ('.env','.sqlite','.git/')) for name in files)
    assert (len(provider.calls),len(provider.check_calls))==calls and wb.result_bytes(child['id'])==before
    moved=tmp_path/'a-new-machine';moved.mkdir();archive=moved/'bundle.zip';archive.write_bytes(raw)
    script=moved/'verify.py';script.write_bytes(files['verify.py'])
    result=subprocess.run([sys.executable,'-I',str(script),str(archive),'--expected-sha256',sha(raw)],cwd=moved,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['integrity']=='consistent'
    assert verify(archive,expected_sha256=sha(raw))['semantic_verified'] is False
    assert exported(wb,child['id'],request)==(preview,raw)


def test_bundle_includes_manual_corrections_without_rewriting_original_events(family):
    wb,_,parent,_,_,child=family
    dossier=wb.dossier(child['id'])
    saved=wb.dossier_link(child['id'],relation_request(dossier,parent,child))
    updated=wb.dossier_link(child['id'],relation_request(saved,parent,child,supersedes='link_1',reason='Corrected unverified interpretation'))
    _,raw=exported(wb,child['id'])
    view=json.loads(contents(raw)['handoff.json'])
    assert view['hypothesis_links']['events']==updated['hypothesis_links']['events']
    assert view['hypothesis_links']['source_state_sha256']==updated['hypothesis_links']['sha256']


@pytest.mark.parametrize('mutation',['pack','dossier','empty','duplicate','foreign','omit_selected','confirmation','preview'])
def test_stale_unavailable_or_unconfirmed_selection_cannot_export(checking,mutation):
    wb,_,source=checking;request=selection(wb,source['id'])
    if mutation=='pack':request['source_pack_sha256']='a'*64
    elif mutation=='dossier':request['dossier_sha256']='a'*64
    elif mutation=='empty':request['job_ids']=[]
    elif mutation=='duplicate':request['job_ids']*=2
    elif mutation=='foreign':request['job_ids'].append('a'*24)
    elif mutation=='omit_selected':request['job_ids']=['a'*24]
    if mutation in {'confirmation','preview'}:
        preview=wb.handoff_preview(source['id'],request)
        body={'selection':request,'preview_sha256':preview['sha256'],'confirmation':'export_selected_private_handoff'}
        body['confirmation' if mutation=='confirmation' else 'preview_sha256']='invalid'
        with pytest.raises(ValueError):wb.handoff_export(source['id'],body)
    else:
        with pytest.raises(ValueError):wb.handoff_preview(source['id'],request)


def test_observation_change_invalidates_preview_before_export(family):
    wb,_,_,plan,_,child=family
    request=selection(wb,child['id']);preview=wb.handoff_preview(child['id'],request)
    append(wb,plan,outcome='B',supersedes='observation_1')
    with pytest.raises(ValueError,match='changed'):
        wb.handoff_export(child['id'],{'selection':request,'preview_sha256':preview['sha256'],'confirmation':'export_selected_private_handoff'})


def repack(files):
    result=io.BytesIO()
    with zipfile.ZipFile(result,'w') as archive:
        for name,raw in files.items():archive.writestr(name,raw)
    return result.getvalue()


@pytest.mark.parametrize('mutation',['tamper','extra','traversal','absolute','backslash','duplicate','missing','symlink','rehashed_report'])
def test_offline_verifier_rejects_corruption_and_unsafe_archives(checking,tmp_path,mutation):
    wb,_,source=checking;_,raw=exported(wb,source['id']);files=contents(raw)
    if mutation=='tamper':files['index.html']+=b'changed'
    elif mutation=='extra':files['unknown.txt']=b'unknown'
    elif mutation in {'traversal','absolute','backslash'}:files[{'traversal':'../escape','absolute':'/absolute','backslash':'a\\b'}[mutation]]=b'bad'
    elif mutation=='missing':files.pop('handoff.json')
    elif mutation=='rehashed_report':
        path='runs/'+source['id']+'/report.json';report=json.loads(files[path]);report['source']['pack_sha256']='b'*64
        report['sha256']=digest({k:v for k,v in report.items() if k!='sha256'});files[path]=encoded(report)
        manifest=json.loads(files['manifest.json'])
        for row in manifest['entries']:
            if row['path']==path:row.update(bytes=len(files[path]),sha256=sha(files[path]))
        manifest['sha256']=digest({k:v for k,v in manifest.items() if k!='sha256'});files['manifest.json']=encoded(manifest)
    raw=repack(files)
    if mutation in {'duplicate','symlink'}:
        buffer=io.BytesIO(raw)
        with zipfile.ZipFile(buffer,'a') as archive:
            if mutation=='duplicate':
                with pytest.warns(UserWarning):archive.writestr('index.html',files['index.html'])
            else:
                info=zipfile.ZipInfo('link');info.external_attr=0o120777<<16;archive.writestr(info,'target')
        raw=buffer.getvalue()
    path=tmp_path/'bad.zip';path.write_bytes(raw)
    with pytest.raises(ValueError):verify(path)
    assert not (tmp_path.parent/'escape').exists()


def test_recovery_includes_failed_parent_and_exact_checkpoint_bytes(finalization_source,tmp_path):
    wb,service,source,_=finalization_source
    child=completed(wb,service.submit(source['id'],approval(service.preview(source['id'])))['id'])
    preview,raw=exported(wb,child['id']);files=contents(raw)
    assert preview['automatic_ancestor_job_ids']==[source['id']]
    for stage in ('verification_input','verification'):
        assert files['runs/'+source['id']+'/stages/'+stage+'.json']==wb.stage_bytes(source['id'],stage)
    path=tmp_path/'recovery.zip';path.write_bytes(raw)
    assert verify(path)['runs']==2


def test_cli_verification_never_reads_env(checking,tmp_path,monkeypatch,capsys):
    wb,_,source=checking;_,raw=exported(wb,source['id']);path=tmp_path/'handoff.zip';path.write_bytes(raw)
    monkeypatch.setattr('issueintel.interfaces.cli.load_local_env',lambda *_:pytest.fail('No credentials read'))
    assert main(['verify-handoff',str(path),'--expected-sha256',sha(raw)])==0
    assert json.loads(capsys.readouterr().out)['integrity']=='consistent'
    assert main(['verify-handoff',str(path),'--expected-sha256','a'*64])==2


def test_offline_viewer_escapes_content_and_has_no_external_dependencies(checking):
    wb,_,source=checking;_,raw=exported(wb,source['id']);view=json.loads(contents(raw)['handoff.json'])
    view['warnings']=[{'reason':'<script>alert(1)</script><img src="https://example.invalid/leak">'}]
    rendered=_viewer(view).decode()
    assert '<script' not in rendered and '<img' not in rendered and '<link' not in rendered
    assert '&lt;script&gt;' in rendered and "default-src 'none'" in rendered
    assert 'Integrity has not been checked by opening this page.' in rendered


def test_export_enforces_bound_and_benchmark_authority(checking,monkeypatch):
    wb,_,source=checking;request=selection(wb,source['id'])
    with monkeypatch.context() as patch:
        patch.setattr('issueintel.investigation.portable_verify.MAX_FILES',2)
        with pytest.raises(ValueError,match='exceeds'):wb.handoff_preview(source['id'],request)
    row=json.loads(wb._job_path(source['id']).read_bytes());row['mode']=row['request']['mode']='main_dev'
    row['request']['case_id']='verified:synthetic';write_json(wb._job_path(source['id']),row)
    with pytest.raises(ValueError):wb.handoff_preview(source['id'],request)


def test_handoff_http_requires_session_and_explicit_preview(checking):
    wb,_,source=checking;server=make_server(wb.root,port=0,workbench=wb)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        headers={'Content-Type':'application/json','X-IssueIntel-Token':server.session_token}
        route='/api/jobs/'+source['id']
        request=selection(wb,source['id'])
        assert http(server,'POST',route+'/handoff-preview',json.dumps(request),{'Content-Type':'application/json'})[0]==403
        status,_,raw=http(server,'POST',route+'/handoff-preview',json.dumps(request),headers)
        assert status==200
        preview=json.loads(raw)
        status,response,data=http(server,'POST',route+'/handoff-export',json.dumps({'selection':request,'preview_sha256':preview['sha256'],'confirmation':'export_selected_private_handoff'}),headers)
        assert status==200 and response['Content-Type']=='application/zip'
        assert sha(data)==preview['archive_sha256'] and len(data)==preview['archive_bytes']
        assert response['X-IssueIntel-Dossier-SHA256']==request['dossier_sha256']
    finally:server.shutdown();server.server_close();thread.join(5)
