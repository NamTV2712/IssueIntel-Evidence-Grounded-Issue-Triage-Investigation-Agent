"""Authenticated event receipts and bounded, canonical REST reconciliation."""
from hashlib import sha256
import hmac
import json
import re

from issueintel.repository.acquire import parse_github_reference
from issueintel.runtime.io import canonical_json, digest
from .github_index import GitHubReader, GitHubReadError, _positive, _sha, _writer_lock, now


EVENTS = {'issues', 'issue_comment', 'pull_request', 'pull_request_review_comment', 'push'}


def authenticate(raw, signature, delivery, secret):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 2_000_000 or not isinstance(secret, str) or not secret:
        raise ValueError('Invalid webhook envelope or missing secret')
    expected = 'sha256=' + hmac.new(secret.encode(), raw, sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
        raise ValueError('Invalid webhook signature')
    if not isinstance(delivery, str) or not re.fullmatch(r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}', delivery):
        raise ValueError('Invalid webhook delivery ID')
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError('Expected webhook object')
    return payload


def repository_identity(value):
    if not isinstance(value, dict) or value.get('private') is not False:
        raise ValueError('Only selected public repositories may sync')
    source = parse_github_reference(value.get('full_name', ''))
    if source.issue_number is not None:
        raise ValueError('Invalid repository identity')
    return {'repository': source.repository.lower(), 'repository_id': _positive(value.get('id'))}


def event_update(payload, event, delivery, raw_sha):
    identity = repository_identity(payload.get('repository'))
    action = payload.get('action')
    operations = []
    def operation(kind, subject, *, remove=False):
        _positive(subject)
        operations.append({'kind': kind, 'subject': subject, 'remove': remove, 'done': False, 'count': 0})
    if event == 'push':
        if not isinstance(payload.get('ref'), str) or len(payload['ref']) > 300:
            raise ValueError('Invalid push ref')
        if not payload.get('deleted'):
            _sha(payload.get('after'))
            operations.append({'kind': 'snapshot', 'ref': payload['ref'], 'done': False})
    elif event == 'issues':
        if action not in {'opened','edited','deleted','closed','reopened','transferred','labeled','unlabeled','assigned','unassigned','milestoned','demilestoned','locked','unlocked','typed','untyped'}:
            raise ValueError('Unsupported issue action')
        issue = payload.get('issue', {})
        if 'pull_request' in issue:
            raise ValueError('Issues event cannot impersonate a pull request')
        operation('issues', issue.get('number'), remove=action == 'deleted')
    elif event == 'pull_request':
        if action not in {'opened','edited','closed','reopened','synchronize','ready_for_review','converted_to_draft','labeled','unlabeled','assigned','unassigned','locked','unlocked','milestoned','demilestoned','enqueued','dequeued','review_requested','review_request_removed','auto_merge_enabled','auto_merge_disabled'}:
            raise ValueError('Unsupported pull request action')
        number = _positive(payload.get('pull_request', {}).get('number'))
        if payload.get('number') != number:
            raise ValueError('Pull request number mismatch')
        for kind in ('pulls', 'files', 'commits'):
            operation(kind, number)
    elif event in {'issue_comment', 'pull_request_review_comment'}:
        if action not in {'created', 'edited', 'deleted'}:
            raise ValueError('Unsupported comment action')
        comment = payload.get('comment', {})
        operation('comments' if event == 'issue_comment' else 'review_comments', comment.get('id'), remove=action == 'deleted')
        target = payload.get('issue' if event == 'issue_comment' else 'pull_request', {})
        operation('issues' if event == 'issue_comment' else 'pulls', target.get('number'))
    else:
        raise ValueError('Unsupported repository update event')
    return {**identity, 'delivery': delivery.lower(), 'event': event, 'action': action,
            'payload_sha256': raw_sha, 'received_at': now(), 'operations': operations,
            'installation_id': (payload.get('installation') or {}).get('id')}


def enqueue_update(index, value):
    if value['repository'] != index.repository:
        raise ValueError('Webhook repository mismatch')
    fingerprint = _fingerprint(value)
    with index.connect() as db:
        old = db.execute('SELECT sha FROM updates WHERE id=?', (value['delivery'],)).fetchone()
        if old:
            if old[0] != fingerprint:
                raise ValueError('Webhook delivery identity reused with different content')
            return False
        if db.execute('SELECT count(*) FROM updates WHERE done=0').fetchone()[0] >= 1000:
            raise RuntimeError('Repository webhook inbox is full')
        db.execute('INSERT INTO updates(id,sha,value) VALUES (?,?,?)',
                   (value['delivery'], fingerprint, canonical_json(value)))
    return True


def _fingerprint(value):
    fields = {k:v for k,v in value.items() if k not in {'received_at','operations'}}
    fields['operations'] = [{k:v for k,v in op.items() if k in {'kind','subject','remove','ref'}} for op in value['operations']]
    return digest(fields)


def _url(index, operation):
    kind, number = operation['kind'], operation['subject']
    suffix = {'issues': f'/issues/{number}', 'pulls': f'/pulls/{number}',
              'comments': f'/issues/comments/{number}', 'review_comments': f'/pulls/comments/{number}',
              'files': f'/pulls/{number}/files', 'commits': f'/pulls/{number}/commits'}[kind]
    return index._url(suffix, **({'per_page': 100, 'page': 1} if kind in {'files','commits'} else {}))


def process_updates(index, *, revision='HEAD', max_requests=20, reader=None, snapshot_builder=None, check_cancelled=None):
    if type(max_requests) is not int or not 2 <= max_requests <= 500:
        raise ValueError('Invalid incremental sync request budget')
    check_cancelled = check_cancelled or (lambda: None)
    reader = reader or GitHubReader(index.repository)
    with _writer_lock(index.directory/'writer.lock'), index.connect() as db:
        check_cancelled()
        metadata, _, _ = reader.get(index._url(''))
        identity = repository_identity(metadata)
        expected = index._meta(db, 'github_repository_id')
        if identity != {'repository': index.repository, 'repository_id': expected}:
            raise ValueError('Repository identity changed before incremental update')
        if isinstance(reader, GitHubReader):
            reader.repository_id = expected
        if revision == 'HEAD':
            branch = metadata.get('default_branch')
            tracked_ref = 'refs/heads/' + branch if isinstance(branch, str) else None
        elif re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', revision):
            tracked_ref = None
        else:
            tracked_ref = revision if revision.startswith('refs/heads/') else 'refs/heads/' + revision
        requests, operations = 1, 0
        while requests < max_requests and operations < 50:
            check_cancelled()
            row = db.execute('SELECT * FROM updates WHERE done=0 ORDER BY rowid LIMIT 1').fetchone()
            if row is None:
                break
            update = json.loads(row['value'])
            if _fingerprint(update) != row['sha'] or update['repository_id'] != expected:
                raise ValueError('Webhook global repository ID mismatch')
            operation = next((op for op in update['operations'] if not op['done']), None)
            if operation is None:
                db.execute('UPDATE updates SET done=1 WHERE id=?', (row['id'],)); db.commit()
                continue
            kind = operation['kind']
            receipt = {'schema': 'github_incremental_page_v1', 'delivery': row['id'],
                       'event_payload_sha256': update['payload_sha256'], 'repository_id': expected,
                       'fetched_at': now(), 'records': {}}
            records, next_url = [], None
            unavailable = []
            snapshot = None
            if kind == 'snapshot':
                if operation['ref'] == tracked_ref:
                    # Resolve the tracked ref now, so late deliveries cannot roll back code.
                    snapshot = (snapshot_builder or index._build_snapshot)(revision)
                    index._verify_snapshot(snapshot)
                receipt['tracked_ref'] = tracked_ref
                receipt['snapshot'] = snapshot
            else:
                key = {'issues': 'issue', 'pulls': 'pull', 'comments': 'comments', 'review_comments': 'review_comments'}.get(kind)
                key = f'{key}:{operation["subject"]}' if key else None
                if operation.get('remove'):
                    unavailable.append(key)
                    receipt['source'] = 'authenticated_webhook_deletion'
                else:
                    url = operation.get('next_url') or _url(index, operation)
                    receipt['source_url'] = url
                    try:
                        value, next_url, response_sha = reader.get(url)
                        requests += 1
                        receipt['response_sha256'] = response_sha
                        listing = kind in {'files','commits'}
                        if listing:
                            if not isinstance(value, list) or len(value) > 100:
                                raise ValueError('Invalid incremental list page')
                            index._next_page(url, next_url, expected)
                        elif next_url or not isinstance(value, dict):
                            raise ValueError('Invalid incremental detail response')
                        records = [index._record(kind, item, operation['subject']) for item in (value if listing else [value])]
                        if key and kind != 'issues' and any(r['key'] != key for r in records):
                            raise ValueError('Incremental response belongs to a different record')
                        if kind == 'issues' and any(r['number'] != operation['subject'] for r in records):
                            raise ValueError('Incremental issue number mismatch')
                    except GitHubReadError as error:
                        if error.status not in {404, 410} or key is None:
                            raise
                        requests += 1
                        unavailable.append(key)
                        receipt.update(http_status=error.status, source='api_currently_unavailable')
            receipt['records'] = {r['key']: digest(r) for r in records}
            receipt['next_url'] = next_url
            receipt_sha = digest(receipt)
            with db:
                db.execute('INSERT OR IGNORE INTO receipts VALUES (?,?)', (receipt_sha, canonical_json(receipt)))
                run = index._meta(db, 'active_run')
                for record in records:
                    index._save_record(db, record, receipt_sha, run)
                    db.execute('INSERT OR IGNORE INTO update_seen VALUES (?,?,?,?)', (row['id'], kind, operation.get('subject'), record['key']))
                cap = 3000 if kind == 'files' else 250 if kind == 'commits' else None
                capped = cap and operation.get('count',0)+len(records) >= cap
                if kind in {'files', 'commits'} and next_url is None and not capped:
                    for old in db.execute('SELECT key,value FROM documents WHERE kind=?', (kind,)):
                        if json.loads(old['value'])['number'] == operation['subject'] and not db.execute(
                            'SELECT 1 FROM update_seen WHERE delivery=? AND kind=? AND subject=? AND key=?',
                            (row['id'], kind, operation['subject'], old['key'])).fetchone():
                            unavailable.append(old['key'])
                for key in unavailable:
                    db.execute('INSERT OR REPLACE INTO unavailable VALUES (?,?,?)',
                               (key, 'not_observed_in_current_source', receipt_sha))
                    db.execute('DELETE FROM search WHERE key=?', (key,))
                if snapshot:
                    index._set(db, 'current_snapshot', snapshot)
                operation.update(done=next_url is None, next_url=next_url,
                                 count=operation.get('count', 0)+len(records), receipt_sha256=receipt_sha)
                cap = 3000 if kind == 'files' else 250 if kind == 'commits' else None
                if cap and operation['count'] >= cap:
                    operation['api_cap_reached'] = cap
                db.execute('UPDATE updates SET value=?,done=? WHERE id=?',
                           (canonical_json(update), int(all(op['done'] for op in update['operations'])), row['id']))
                index._set(db, 'last_update', {'delivery': row['id'], 'event': update['event'], 'applied_at': now()})
            operations += 1
            if kind == 'snapshot':
                break  # At most one Git fetch/snapshot per batch.
        return index.status()
