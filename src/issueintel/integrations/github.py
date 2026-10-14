"""GitHub input, signed command validation and reviewed comment publication."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.parse import quote, urlsplit

from issueintel.repository.acquire import parse_github_reference


def github_input(url):
    source = parse_github_reference(url)
    if source.issue_number is None:
        raise ValueError('Require a GitHub issue URL')
    return source


def comment_preview(pack):
    source = github_input(pack['case'].get('issue_url', ''))
    revision = pack['snapshot']['commit_sha']
    lines = ['## IssueIntel investigation', '',
             f'Snapshot: `{revision}`. Automatic analysis; causal correctness is unverified.',
             '', '### Evidence']
    for evidence in pack['evidence']:
        if evidence['kind'] != 'code':
            continue
        match = re.fullmatch(r'(.+):(\d+)-(\d+)', evidence['locator'])
        if not match:
            raise ValueError('Invalid code locator')
        url = f'https://github.com/{source.repository}/blob/{revision}/{quote(match[1], safe="/")}#L{match[2]}-L{match[3]}'
        lines.append(f'- [{evidence["id"]}]({url}) — SHA256 `{evidence["sha256"]}`')
    if pack['annotation']:
        lines += ['', '### Competing hypotheses']
        for hypothesis in pack['annotation']['hypotheses']:
            lines += [f'**{hypothesis["id"]}**: {hypothesis["statement"]}',
                      f'Support: {", ".join(hypothesis["support_ids"])}.',
                      f'Counter: {", ".join(hypothesis["counter_ids"]) or hypothesis["counter_status"]}.',
                      f'Check: {hypothesis["falsification_test"]}', '']
    else:
        lines += ['', 'No model hypothesis was generated; this report contains retrieved evidence.']
    lines += ['', '### Next checks', *[f'- {step}' for step in pack['recommended_steps']],
              '', 'Citation integrity and model critique do not establish causal truth.',
              'No independent human validation is claimed.']
    body = '\n'.join(lines)+'\n'
    if len(body.encode()) > 60000:
        raise ValueError('Comment exceeds the reviewable size bound')
    return {'issue_url': source.issue_url, 'repository': source.repository,
            'issue_number': source.issue_number, 'body': body,
            'body_sha256': hashlib.sha256(body.encode()).hexdigest()}


def signed_command(raw, *, signature, event, delivery, secret):
    """Validate before JSON decode; command does not authorize an automatic write."""
    if not secret or not isinstance(secret, str):
        raise ValueError('Webhook secret is not configured')
    expected = 'sha256='+hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise ValueError('Invalid webhook signature')
    if event != 'issue_comment' or not re.fullmatch(r'[a-fA-F0-9-]{36}', delivery or ''):
        raise ValueError('Unsupported webhook event or delivery ID')
    payload = json.loads(raw)
    comment, issue, repository = payload.get('comment', {}), payload.get('issue', {}), payload.get('repository', {})
    if (payload.get('action') != 'created' or repository.get('private') is not False
            or 'pull_request' in issue
            or comment.get('author_association') not in {'OWNER', 'MEMBER', 'COLLABORATOR'}):
        raise ValueError('Command requires a public issue and repository collaborator')
    command = re.fullmatch(r'/issueintel investigate(?:\s+([A-Za-z0-9_./-]{1,200}))?\s*', comment.get('body', ''))
    if not command:
        raise ValueError('Unsupported IssueIntel command')
    source = github_input(issue.get('html_url', ''))
    if source.repository != repository.get('full_name') or source.issue_number != issue.get('number'):
        raise ValueError('Issue and repository identities differ')
    return {'mode': 'github', 'issue_url': source.issue_url, 'revision': command.group(1) or 'HEAD',
            'provider': 'none', 'depth': 'quick'}


class NoWriteRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Refuse a redirect during GitHub publication')


def post_reviewed_comment(preview, *, approved_sha256, token=None, transport=None):
    """Caller must durably record the publication intent before this one attempt."""
    source = github_input(preview['issue_url'])
    actual = hashlib.sha256(preview['body'].encode()).hexdigest()
    if (approved_sha256 != actual or actual != preview['body_sha256']
            or source.repository != preview['repository'] or source.issue_number != preview['issue_number']
            or not 0 < len(preview['body'].encode()) <= 60000):
        raise ValueError('Approved comment or target has changed')
    key = os.environ.get('GITHUB_TOKEN', '') if token is None else token
    if not key or any(ord(c) < 32 for c in key):
        raise ValueError('A local GitHub token with issue-comment permission is required')
    url = f'https://api.github.com/repos/{source.repository}/issues/{source.issue_number}/comments'
    request = Request(url, json.dumps({'body': preview['body']}).encode(),
        {'Authorization': 'Bearer '+key, 'Accept': 'application/vnd.github+json',
         'Content-Type':'application/json', 'X-GitHub-Api-Version':'2026-03-10',
         'User-Agent':'IssueIntel-reviewed-comment'}, method='POST')
    opener = transport or build_opener(NoWriteRedirect()).open
    with opener(request, timeout=30) as response:
        if getattr(response, 'status', None) != 201:
            raise ValueError('GitHub did not confirm comment creation')
        raw = response.read(2000001)
    if len(raw) > 2000000:
        raise ValueError('Publication response exceeds bound')
    data = json.loads(raw)
    parsed = urlsplit(data.get('html_url', ''))
    if (data.get('body') != preview['body'] or type(data.get('id')) is not int or data['id'] <= 0 or parsed.scheme != 'https'
            or parsed.netloc != 'github.com' or parsed.path != f'/{source.repository}/issues/{source.issue_number}'
            or parsed.fragment != f'issuecomment-{data["id"]}'):
        raise ValueError('Publication receipt identity differs from the approved issue')
    return {'comment_id':data['id'], 'comment_url':data['html_url'],
            'response_sha256':hashlib.sha256(raw).hexdigest(), 'body_sha256':actual,
            'posted_at':datetime.now(timezone.utc).isoformat()}
