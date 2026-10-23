"""System inspection is local, explicit and has no data/provider side effects."""
import json

import pytest

from test_workbench import local_server,http


def test_inspection_routes_require_session_and_local_origin(tmp_path):
    with local_server(tmp_path) as server:
        headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        for method,path,body in [('GET','/api/readiness',None),('POST','/api/storage/preview','{}')]:
            assert http(server,method,path,body=body,headers={'Content-Type':'application/json'})[0]==403
            assert http(server,method,path,body=body,headers={**headers,'Origin':'https://outside.invalid'})[0]==403
            status,_,raw=http(server,method,path,body=body,headers=headers)
            assert status==200,raw
            value=json.loads(raw)
            assert value['model_calls']==0
        for path in ('/readiness.js','/readiness.css'):
            assert http(server,'GET',path)[0]==200
        assert http(server,'GET','/api/storage/preview',headers=headers)[0]==404
        assert http(server,'POST','/api/storage/delete',body='{}',headers=headers)[0]==404


@pytest.mark.parametrize('payload',[[],None,False,{'path':'..'},{'delete':True},{'limits':{'max_entries':999999}}])
def test_storage_request_cannot_change_scope_or_request_deletion(tmp_path,payload,monkeypatch):
    with local_server(tmp_path) as server:
        def deny(*args,**kwargs):pytest.fail('Invalid preview reached the scanner')
        monkeypatch.setattr('issueintel.runtime.storage.preview_storage',deny)
        headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        assert http(server,'POST','/api/storage/preview',body=json.dumps(payload),headers=headers)[0]==400


def test_inspections_preserve_receipts_and_do_not_construct_provider_or_dataset(tmp_path,monkeypatch):
    with local_server(tmp_path) as server:
        # Existing fixture constructors are done; the inspection itself must not initialize anything.
        def deny(*args,**kwargs):pytest.fail('Inspection initialized or acquired a source')
        for target in ('issueintel.runtime.budget.BudgetLedger.__init__',
                       'issueintel.runtime.typesafe.TypesafeClient.__init__',
                       'issueintel.integrations.github_sync.GitHubSync.__init__',
                       'issueintel.integrations.github_index.RepositoryIndex.__init__',
                       'issueintel.investigation.workbench.public_catalog',
                       'issueintel.investigation.workbench.acquire_repository'):
            monkeypatch.setattr(target,deny)
        server.workbench.provider_factory=deny
        protected=tmp_path/'artifacts/demo/receipts/native.json'
        protected.parent.mkdir(parents=True)
        protected.write_bytes(b'{"synthetic_receipt":"preserve exact bytes"}')
        before={path.relative_to(tmp_path).as_posix():path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
        headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        for method,path,body in [('GET','/api/readiness',None),('POST','/api/storage/preview','{}')]:
            status,_,raw=http(server,method,path,body=body,headers=headers)
            assert status==200,raw
        after={path.relative_to(tmp_path).as_posix():path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
        assert after==before


def test_inspection_lock_rejects_overlap_and_releases_after_failure(tmp_path,monkeypatch):
    with local_server(tmp_path) as server:
        wb=server.workbench
        with wb.inspection_lock:
            with pytest.raises(ValueError,match='already running'):wb.system_readiness()
            with pytest.raises(ValueError,match='already running'):wb.storage_preview({})
        def fail(*args,**kwargs):raise ValueError('Synthetic bounded inspection failure')
        monkeypatch.setattr('issueintel.runtime.storage.preview_storage',fail)
        with pytest.raises(ValueError,match='Synthetic'):wb.storage_preview({})
        assert wb.inspection_lock.acquire(blocking=False)
        wb.inspection_lock.release()
