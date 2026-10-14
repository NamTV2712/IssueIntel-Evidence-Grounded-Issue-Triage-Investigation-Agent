from io import BytesIO
from types import SimpleNamespace
import hashlib
import hmac
import json

import pytest

from issueintel.integrations.github import signed_command, post_reviewed_comment
from issueintel.investigation.workbench import Workbench, validate_request
from test_workbench import completed


def payload():
    return {'action':'created','repository':{'full_name':'synthetic/example','private':False},
            'issue':{'html_url':'https://github.com/synthetic/example/issues/5','number':5},
            'comment':{'body':'/issueintel investigate abc123','author_association':'OWNER'}}


def signed(data):
    raw = json.dumps(data).encode()
    return raw, 'sha256='+hmac.new(b'synthetic-secret', raw, hashlib.sha256).hexdigest()


def parse(data):
    raw, signature = signed(data)
    return signed_command(raw, signature=signature, event='issue_comment',
                          delivery='00000000-0000-0000-0000-000000000001', secret='synthetic-secret')


def test_signed_collaborator_command_does_not_authorize_a_model_or_write():
    request = parse(payload())
    assert request == {'mode':'github','issue_url':'https://github.com/synthetic/example/issues/5',
                       'revision':'abc123','provider':'none','depth':'quick'}
    raw, signature = signed(payload())
    with pytest.raises(ValueError, match='signature'):
        signed_command(raw+b' ', signature=signature, event='issue_comment',
                       delivery='00000000-0000-0000-0000-000000000001', secret='synthetic-secret')


@pytest.mark.parametrize('change', ['outsider','private','pull','wrong_repo','unsupported_command'])
def test_webhook_rejects_unsafe_commands(change):
    data = payload()
    if change=='outsider': data['comment']['author_association']='NONE'
    if change=='private': data['repository']['private']=True
    if change=='pull': data['issue']['pull_request']={}
    if change=='wrong_repo': data['repository']['full_name']='different/repo'
    if change=='unsupported_command': data['comment']['body']='/issueintel publish everything'
    with pytest.raises(ValueError):
        parse(data)


def preview():
    body = 'Synthetic reviewed evidence report'
    return {'issue_url':'https://github.com/synthetic/example/issues/5', 'repository':'synthetic/example',
            'issue_number':5,'body':body,'body_sha256':hashlib.sha256(body.encode()).hexdigest()}


def response(data, status=201):
    stream=BytesIO(json.dumps(data).encode());stream.status=status
    return stream


def test_exact_comment_approval_target_headers_and_receipt():
    draft = preview(); calls=[]
    def transport(request, timeout):
        calls.append(request)
        assert request.full_url=='https://api.github.com/repos/synthetic/example/issues/5/comments'
        assert request.get_method()=='POST' and timeout==30
        assert json.loads(request.data)=={'body':draft['body']}
        assert request.get_header('X-github-api-version')=='2026-03-10'
        return response({'id':7,'body':draft['body'],'html_url':'https://github.com/synthetic/example/issues/5#issuecomment-7'})
    receipt = post_reviewed_comment(draft, approved_sha256=draft['body_sha256'], token='synthetic-key', transport=transport)
    assert receipt['comment_id']==7 and len(calls)==1
    with pytest.raises(ValueError, match='Approved'):
        post_reviewed_comment({**draft,'body':'changed'}, approved_sha256=draft['body_sha256'], token='synthetic-key',
                              transport=lambda *a,**k:pytest.fail('Unapproved write'))
    with pytest.raises(ValueError, match='Approved'):
        post_reviewed_comment({**draft,'issue_number':6}, approved_sha256=draft['body_sha256'], token='synthetic-key',
                              transport=lambda *a,**k:pytest.fail('Wrong target write'))


def test_publication_rejects_bad_receipt_and_missing_key():
    draft = preview()
    with pytest.raises(ValueError, match='token'):
        post_reviewed_comment(draft, approved_sha256=draft['body_sha256'], token='',
                              transport=lambda *a,**k:pytest.fail('Missing key write'))
    with pytest.raises(ValueError, match='identity'):
        post_reviewed_comment(draft, approved_sha256=draft['body_sha256'], token='synthetic-key',
            transport=lambda *a,**k:response({'id':7,'body':draft['body'],'html_url':'https://attacker.invalid/comment'}))
    with pytest.raises(ValueError, match='confirm'):
        post_reviewed_comment(draft, approved_sha256=draft['body_sha256'], token='synthetic-key',
            transport=lambda *a,**k:response({'id':7},status=202))


def github_workbench(tmp_path, monkeypatch):
    wb = Workbench(tmp_path)
    case, local = wb._example()
    monkeypatch.setattr('issueintel.investigation.workbench.fetch_github_issue', lambda url: {
        'url':'https://github.com/synthetic/example/issues/5','repository':'synthetic/example',
        'problem_statement':case['problem_statement'],'created_at':'2020-01-01T00:00:00Z'})
    monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository', lambda *a,**k:SimpleNamespace(path=local))
    return wb, case['revision']


def test_github_preview_has_real_code_links_and_one_reviewed_attempt(tmp_path, monkeypatch):
    wb, revision = github_workbench(tmp_path, monkeypatch)
    monkeypatch.setenv('GITHUB_TOKEN','synthetic-key')
    posted=[]
    monkeypatch.setattr('issueintel.investigation.workbench.post_reviewed_comment', lambda draft,**kw:
                        posted.append(draft) or {'comment_id':7,'comment_url':'https://github.com/synthetic/example/issues/5#issuecomment-7'})
    try:
        job = completed(wb,wb.submit({'mode':'github','issue_url':'https://github.com/synthetic/example/issues/5','revision':revision})['id'])
        assert job['status']=='completed'
        draft=wb.preview_comment(job['id'])
        assert f'/blob/{revision}/parser.py#L' in draft['body']
        assert not posted
        approval={'approved_sha256':draft['body_sha256'],'confirmation':'publish_reviewed_comment'}
        with pytest.raises(ValueError): wb.publish_comment(job['id'],{**approval,'approved_sha256':'0'*64})
        assert not posted
        wb.publish_comment(job['id'],approval)
        assert len(posted)==1
        with pytest.raises(ValueError,match='already attempted'):wb.publish_comment(job['id'],approval)
        assert len(posted)==1
    finally: wb.close()


def test_duplicate_signed_delivery_creates_one_job_without_publication(tmp_path,monkeypatch):
    wb,revision=github_workbench(tmp_path,monkeypatch)
    data=payload();data['comment']['body']='/issueintel investigate '+revision
    raw,signature=signed(data)
    try:
        kwargs={'signature':signature,'event':'issue_comment','delivery':'00000000-0000-0000-0000-000000000001','secret':'synthetic-secret'}
        first=wb.receive_webhook(raw,**kwargs); second=wb.receive_webhook(raw,**kwargs)
        assert first['id']==second['id']
        assert completed(wb,first['id'])['status']=='completed'
        assert len(wb.history())==1
        assert not list(wb.directory.rglob('github_publish_intent.json'))
    finally:wb.close()


def test_github_identity_cannot_be_overridden():
    with pytest.raises(ValueError):
        validate_request({'mode':'github','issue_url':'https://github.com/synthetic/example/issues/5','issue_text':'different'})
    with pytest.raises(ValueError):
        validate_request({'mode':'github','issue_url':'https://attacker.invalid/issues/5'})
