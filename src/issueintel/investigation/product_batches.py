"""Bounded product batch contracts; no execution, acquisition or paid calls.

The preview is a declaration over captured local sources, not historical proof,
an authorization to spend, or a prediction of an invoice. Runtime services own
source acquisition and durable scheduling; this module only checks data.
"""
from copy import deepcopy
from datetime import datetime
import json
import ntpath
import posixpath
import re

from issueintel.runtime.io import digest


MANIFEST_SCHEMA = 'product_batch_manifest_v1'
SOURCE_SCHEMA = 'product_batch_source_v1'
PREVIEW_SCHEMA = 'product_batch_preview_v1'
READ_SCHEMA = 'product_batch_v1'
MODEL = 'openai/gpt-oss-120b'
MAX_ITEMS = 20
MAX_MANIFEST_BYTES = 512 * 1024
MAX_SOURCE_BYTES = 512 * 1024
MAX_PREVIEW_BYTES = 8 * 1024 * 1024
MAX_READ_BYTES = 16 * 1024 * 1024
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')
_HASH = re.compile(r'[a-f0-9]{64}')
_COMMIT = re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})')
_JOB = re.compile(r'[a-f0-9]{24}')


def _object(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError('Invalid '+label+' fields')


def _text(value, maximum, label, *, empty=False):
    if (type(value) is not str or len(value) > maximum or '\x00' in value
            or (not empty and not value.strip())):
        raise ValueError('Invalid '+label)


def _identifier(value, maximum, label):
    if type(value) is not str or len(value) > maximum or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError('Invalid '+label)


def _hash(value, label):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError('Invalid '+label+' hash')


def _json_bytes(value, maximum, label):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise ValueError('Invalid '+label+' JSON') from None
    if len(raw) > maximum:
        raise ValueError(label+' exceeds the size bound')
    return raw


def _sealed(value):
    return dict(value, sha256=digest(value))


def _check_seal(value, label):
    _hash(value.get('sha256'), label)
    if value['sha256'] != digest({key: item for key, item in value.items() if key != 'sha256'}):
        raise ValueError(label+' digest changed')


def canonical_request(value, *, request_validator=None):
    """Use the shared product validator while excluding forks and benchmark data."""
    if type(value) is not dict or set(value) - {
            'mode', 'repository', 'revision', 'issue_text', 'issue_url',
            'provider', 'depth', 'controller'}:
        raise ValueError('A batch accepts original product investigation fields only')
    for field, default, choices in (
            ('mode', 'local', {'local', 'github', 'example'}),
            ('provider', 'none', {'none', 'groq'}),
            ('depth', 'quick', {'quick', 'deep', 'planned'}),
            ('controller', 'rules', {'rules'})):
        if type(value.get(field, default)) is not str or value.get(field, default) not in choices:
            raise ValueError('Unsupported batch '+field)
    if value.get('mode', 'local') not in {'local', 'github', 'example'}:
        raise ValueError('A batch accepts original local, GitHub or example investigations only')
    if value.get('controller', 'rules') != 'rules':
        raise ValueError('Product batches use the rules controller only')
    if request_validator is None:
        from .workbench import validate_request
        request_validator = validate_request
    checked = request_validator(deepcopy(value))
    if type(checked) is not dict:
        raise ValueError('The shared request validator returned invalid data')
    result = deepcopy(checked)
    # Explicit rules and omitted rules express the same authority. The shared
    # validator has already rejected an explicit controller on a non-Planned job.
    result.pop('controller', None)
    if result.get('mode') == 'local':
        result['repository'] = result['repository'].strip()
        result['revision'] = result['revision'].strip()
    elif result.get('mode') == 'github':
        from issueintel.integrations.github import github_input
        source = github_input(result['issue_url'])
        result['issue_url'] = ('https://github.com/'+source.repository.lower()
                               +'/issues/'+str(source.issue_number))
        result['revision'] = result['revision'].strip()
    _json_bytes(result, MAX_MANIFEST_BYTES, 'Batch request')
    return result


def validate_manifest(value, *, request_validator=None):
    """Normalize defaults and reject duplicated author keys or requests."""
    _json_bytes(value, MAX_MANIFEST_BYTES, 'Batch manifest')
    _object(value, {'schema', 'run_key', 'items'}, 'batch manifest')
    if value['schema'] != MANIFEST_SCHEMA:
        raise ValueError('Unsupported product batch manifest')
    _identifier(value['run_key'], 100, 'batch run key')
    rows = value['items']
    if type(rows) is not list or not 1 <= len(rows) <= MAX_ITEMS:
        raise ValueError('Choose one to twenty product batch items')
    keys = set()
    requests = set()
    items = []
    for row in rows:
        _object(row, {'key', 'request'}, 'batch item')
        _identifier(row['key'], 64, 'batch item key')
        request = canonical_request(row['request'], request_validator=request_validator)
        request_sha = digest(request)
        if row['key'] in keys or request_sha in requests:
            raise ValueError('Batch item keys and canonical requests must be distinct')
        keys.add(row['key'])
        requests.add(request_sha)
        items.append({'key': row['key'], 'request': request})
    result = {'schema': MANIFEST_SCHEMA, 'run_key': value['run_key'], 'items': items}
    _json_bytes(result, MAX_MANIFEST_BYTES, 'Canonical batch manifest')
    return result


def parse_manifest(raw, *, request_validator=None):
    """Decode bounded JSON without silently accepting duplicate object fields."""
    if type(raw) is bytes:
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError('Batch manifest exceeds the size bound')
        try:
            raw = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError('Batch manifest must be UTF-8 JSON') from None
    if type(raw) is not str:
        raise ValueError('Batch manifest must be JSON text')
    try:
        if len(raw.encode('utf-8')) > MAX_MANIFEST_BYTES:
            raise ValueError('Batch manifest exceeds the size bound')
        def pairs(rows):
            result = {}
            for key, item in rows:
                if key in result:
                    raise ValueError('Duplicate batch JSON field')
                result[key] = item
            return result
        def invalid_constant(_value):
            raise ValueError('Non-finite batch JSON number')
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (json.JSONDecodeError, UnicodeEncodeError, RecursionError):
        raise ValueError('Batch manifest must be bounded UTF-8 JSON') from None
    return validate_manifest(value, request_validator=request_validator)


def call_upper_bound(request):
    """Conservative model-call envelope, independent of pricing or availability."""
    request = canonical_request(request)
    return 0 if request['provider'] == 'none' else {'quick': 4, 'deep': 6, 'planned': 9}[request['depth']]


def batch_id(run_key):
    _identifier(run_key, 100, 'batch run key')
    return digest({'schema': 'product_batch_identity_v1', 'run_key': run_key})[:24]


def stable_child_id(run_key, item_key):
    _identifier(run_key, 100, 'batch run key')
    _identifier(item_key, 64, 'batch item key')
    return digest({'schema': 'product_batch_child_identity_v1',
                   'run_key': run_key, 'item_key': item_key})[:24]


def manifest_sha256(value):
    return digest(validate_manifest(value))


def _timestamp(value, label):
    _text(value, 100, label)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise ValueError('Invalid '+label+' timestamp') from None


def validate_source(value, *, request=None):
    """Replay the captured issue/revision binding, without reading the repository.

    A full Git object ID pins a requested analysis revision. It is not evidence
    that the revision or current issue body existed before the issue opened.
    """
    _json_bytes(value, MAX_SOURCE_BYTES, 'Batch source')
    _object(value, {'schema', 'mode', 'repository_path', 'requested_revision',
                    'revision', 'case', 'issue_provenance', 'sha256'}, 'batch source')
    if value['schema'] != SOURCE_SCHEMA or value['mode'] not in ('local', 'github', 'example'):
        raise ValueError('Unsupported product batch source')
    _text(value['repository_path'], 2000, 'batch repository path')
    if (any(ord(c) < 32 for c in value['repository_path'])
            or not (ntpath.isabs(value['repository_path']) or posixpath.isabs(value['repository_path']))):
        raise ValueError('A batch source requires an absolute repository path')
    if type(value['revision']) is not str or _COMMIT.fullmatch(value['revision']) is None:
        raise ValueError('A batch source requires a resolved full commit object ID')
    mode = value['mode']
    if mode == 'example':
        if value['requested_revision'] is not None:
            raise ValueError('The fixed example has no requested revision')
    else:
        _text(value['requested_revision'], 200, 'requested batch revision')
        if any(ord(c) < 32 for c in value['requested_revision']):
            raise ValueError('Invalid requested batch revision')
    case = value['case']
    fields = {'case_id', 'repository', 'revision', 'problem_statement', 'issue_created_at'}
    _object(case, fields | ({'issue_url'} if mode == 'github' else set()), 'batch source case')
    _text(case['repository'], 2000, 'batch case repository')
    _text(case['problem_statement'], 100000, 'batch issue text')
    _text(case['case_id'], 2000, 'batch case identity')
    if case['revision'] != value['revision']:
        raise ValueError('Batch source case revision changed')
    if mode == 'github':
        from issueintel.integrations.github import github_input
        identity = github_input(case['issue_url'])
        canonical_url = ('https://github.com/'+identity.repository.lower()
                         +'/issues/'+str(identity.issue_number))
        if (case['issue_url'] != canonical_url or case['case_id'] != canonical_url
                or case['repository'].lower() != identity.repository.lower()):
            raise ValueError('Batch GitHub issue identity changed')
        _timestamp(case['issue_created_at'], 'issue creation')
        provenance = value['issue_provenance']
        _object(provenance, {'source_url', 'response_sha256', 'fetched_at',
                             'body_version_at_creation_verified', 'limitation'}, 'batch issue provenance')
        expected_url = ('https://api.github.com/repos/'+identity.repository
                        +'/issues/'+str(identity.issue_number))
        if provenance['source_url'] != expected_url or provenance['body_version_at_creation_verified'] is not False:
            raise ValueError('Batch current issue provenance cannot assert opening-version proof')
        _hash(provenance['response_sha256'], 'GitHub response')
        _timestamp(provenance['fetched_at'], 'issue capture')
        _text(provenance['limitation'], 2000, 'current issue limitation')
    else:
        if case['issue_created_at'] is not None or value['issue_provenance'] is not None:
            raise ValueError('Local and example sources cannot assert GitHub issue provenance')
        if mode == 'local' and re.fullmatch(r'local:[a-f0-9]{16}', case['case_id']) is None:
            raise ValueError('Invalid local batch case identity')
        if mode == 'example' and (case['case_id'] != 'synthetic:parse-list'
                                  or case['repository'] != 'synthetic/example'):
            raise ValueError('The batch example has fixed synthetic identity')
    if request is not None:
        request = canonical_request(request)
        if request['mode'] != mode:
            raise ValueError('Batch source mode changed')
        if mode != 'example' and request['revision'] != value['revision']:
            raise ValueError('Batch resolved request revision changed')
        if mode == 'local' and (
                case['repository'] != request['repository']
                or case['problem_statement'] != request['issue_text']
                or case['case_id'] != 'local:'+digest(request)[:16]):
            raise ValueError('Batch local issue body or request binding changed')
        if mode == 'github' and case['issue_url'] != request['issue_url']:
            raise ValueError('Batch GitHub source request identity changed')
    _check_seal(value, 'Batch source')
    return deepcopy(value)


def seal_source(value, *, request=None):
    value = deepcopy(value)
    value.pop('sha256', None)
    return validate_source(_sealed(value), request=request)


def _error(value):
    if value is None:
        return None
    _object(value, {'code', 'message'}, 'batch item error')
    _identifier(value['code'], 100, 'batch error code')
    _text(value['message'], 2000, 'batch error message')
    return deepcopy(value)


def _resolved_identity(request, source):
    path = source['repository_path']
    # Windows drive/UNC paths have case-insensitive spelling. POSIX paths keep
    # their case; do not consult this machine's filesystem or resolve symlinks.
    if ntpath.splitdrive(path)[0] or path.startswith('\\'):
        path = ntpath.normcase(ntpath.normpath(path))
    else:
        path = posixpath.normpath(path)
    return digest({'mode': request['mode'], 'provider': request['provider'],
                   'depth': request['depth'], 'repository_path': path,
                   'revision': source['revision'],
                   'issue_url': source['case'].get('issue_url'),
                   'issue_text': source['case']['problem_statement']})


def build_preview(manifest, items, *, policy_sha256):
    """Build a no-model preview from service-acquired, individually bounded inputs."""
    manifest = validate_manifest(manifest)
    _hash(policy_sha256, 'Batch policy')
    if type(items) is not list or len(items) != len(manifest['items']):
        raise ValueError('Every batch item requires a source result')
    rows = []
    resolved_requests = set()
    for original, item in zip(manifest['items'], items):
        _object(item, {'key', 'resolved_request', 'source', 'error'}, 'batch preview input')
        if item['key'] != original['key']:
            raise ValueError('Batch source results must retain manifest order and keys')
        error = _error(item['error'])
        if error is not None:
            if item['resolved_request'] is not None or item['source'] is not None:
                raise ValueError('A failed batch source cannot retain executable authority')
            resolved = source = None
        else:
            resolved = canonical_request(item['resolved_request'])
            source = validate_source(item['source'], request=resolved)
            expected = dict(original['request'])
            if expected['mode'] != 'example':
                expected['revision'] = source['revision']
                if source['requested_revision'] != original['request']['revision']:
                    raise ValueError('The batch requested revision witness changed')
            if resolved != expected:
                raise ValueError('The batch resolved request changed more than its pinned revision')
            resolved_sha = _resolved_identity(resolved, source)
            if resolved_sha in resolved_requests:
                error = {'code': 'duplicate_resolved_source',
                         'message': 'Another batch item resolves to the same canonical investigation request.'}
                resolved = source = None
            else:
                resolved_requests.add(resolved_sha)
        rows.append({'key': original['key'], 'request_sha256': digest(original['request']),
                     'resolved_request': resolved, 'source_sha256': None if source is None else source['sha256'],
                     'source': source, 'error': error, 'call_upper_bound': call_upper_bound(original['request'])})
    requires_api = any(row['request']['provider'] == 'groq' for row in manifest['items'])
    result = _sealed({'schema': PREVIEW_SCHEMA, 'manifest': manifest,
                      'manifest_sha256': digest(manifest), 'policy_sha256': policy_sha256,
                      'items': rows, 'can_start': all(row['error'] is None for row in rows),
                      'requires_api': requires_api, 'currency': 'USD',
                      'cost_bound_micro_usd': None if requires_api else 0,
                      'concurrency': 1, 'model_calls': 0})
    _json_bytes(result, MAX_PREVIEW_BYTES, 'Batch preview')
    return result


def validate_preview(value):
    _json_bytes(value, MAX_PREVIEW_BYTES, 'Batch preview')
    _object(value, {'schema', 'manifest', 'manifest_sha256', 'policy_sha256', 'items',
                    'can_start', 'requires_api', 'currency', 'cost_bound_micro_usd',
                    'concurrency', 'model_calls', 'sha256'}, 'batch preview')
    if value['schema'] != PREVIEW_SCHEMA or type(value['items']) is not list:
        raise ValueError('Unsupported product batch preview')
    inputs = []
    for item in value['items']:
        _object(item, {'key', 'request_sha256', 'resolved_request', 'source_sha256',
                      'source', 'error', 'call_upper_bound'}, 'batch preview item')
        if type(item['call_upper_bound']) is not int:
            raise ValueError('Invalid batch model call upper bound')
        inputs.append({key: item[key] for key in ('key', 'resolved_request', 'source', 'error')})
    if (type(value['can_start']) is not bool or type(value['requires_api']) is not bool
            or type(value['concurrency']) is not int or type(value['model_calls']) is not int
            or (value['cost_bound_micro_usd'] is not None and type(value['cost_bound_micro_usd']) is not int)):
        raise ValueError('Invalid batch preview scalar types')
    expected = build_preview(value['manifest'], inputs, policy_sha256=value['policy_sha256'])
    if expected != value:
        raise ValueError('Batch preview binding, totals or digest changed')
    return deepcopy(value)


def _markdown_text(value):
    # Treat untrusted issue/error text as display, never markup or a URL target.
    value = str(value).replace('\r\n', '\n').replace('\r', '\n').replace('\n', ' / ')
    value = value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return re.sub(r'([\\`*_{}\[\]()|])', r'\\\1', value)


def render_preview_markdown(value):
    value = validate_preview(value)
    lines = ['# IssueIntel product batch preview', '',
             '- Run key: `'+value['manifest']['run_key']+'`',
             '- Preview SHA256: `'+value['sha256']+'`',
             '- Concurrency: 1', '- Model calls during preview: 0',
             '- USD upper bound: '+('unknown; runtime budget checks still apply' if value['cost_bound_micro_usd'] is None else '0'),
             '- Can start: '+str(value['can_start']).lower(), '',
             '| Item | Provider | Depth | Call upper bound | Analysis revision | Source |',
             '| --- | --- | --- | ---: | --- | --- |']
    for original, item in zip(value['manifest']['items'], value['items']):
        source = item['source']
        status = ('captured; not historical availability proof' if item['error'] is None
                  else item['error']['code']+': '+item['error']['message'])
        lines.append('| '+' | '.join(_markdown_text(text) for text in (
            item['key'], original['request']['provider'], original['request']['depth'],
            item['call_upper_bound'], source['revision'] if source else 'unresolved', status))+' |')
    lines += ['', 'A preview pins captured issue text and a code revision. It is not an invoice,',
              'opening-version proof, a verified diagnosis, or permission to bypass provider budget checks.', '']
    return '\n'.join(lines)


ITEM_STATUSES = ('pending', 'queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted')
BATCH_STATUSES = ('queued', 'running', 'paused', 'completed', 'partial_success',
                  'failed', 'cancelled', 'interrupted')


def batch_status(items, desired_state):
    """Reduce captured item states without retrying or inspecting child files."""
    if type(desired_state) is not str or desired_state not in ('running', 'paused', 'cancelled'):
        raise ValueError('Invalid desired batch state')
    if type(items) is not list or not 1 <= len(items) <= MAX_ITEMS:
        raise ValueError('Invalid batch items')
    statuses = []
    for item in items:
        if type(item) is not dict or type(item.get('status')) is not str or item['status'] not in ITEM_STATUSES:
            raise ValueError('Invalid batch item status')
        statuses.append(item['status'])
    if any(status in ('pending', 'queued', 'running') for status in statuses):
        if 'running' in statuses:
            return 'running'
        if desired_state == 'paused':
            return 'paused'
        return 'queued'
    if all(status == 'completed' for status in statuses):
        return 'completed'
    if 'completed' in statuses:
        return 'partial_success'
    if all(status == 'cancelled' for status in statuses):
        return 'cancelled'
    if 'interrupted' in statuses:
        return 'interrupted'
    if 'failed' in statuses:
        return 'failed'
    return 'failed'


def validate_read(value):
    """Check a bounded public projection; child reports remain separate outputs."""
    _json_bytes(value, MAX_READ_BYTES, 'Batch read')
    _object(value, {'schema', 'id', 'run_key', 'manifest_sha256', 'preview_sha256',
                    'policy_sha256', 'created_at', 'updated_at', 'desired_state', 'status',
                    'requires_api', 'concurrency', 'model_calls', 'counts', 'items', 'sha256'}, 'batch read')
    if value['schema'] != READ_SCHEMA:
        raise ValueError('Unsupported product batch read')
    _identifier(value['run_key'], 100, 'batch run key')
    if value['id'] != batch_id(value['run_key']):
        raise ValueError('Batch identity changed')
    for key in ('manifest_sha256', 'preview_sha256', 'policy_sha256'):
        _hash(value[key], 'Batch '+key)
    for key in ('created_at', 'updated_at'):
        _timestamp(value[key], key)
    if datetime.fromisoformat(value['created_at'].replace('Z', '+00:00')) > datetime.fromisoformat(value['updated_at'].replace('Z', '+00:00')):
        raise ValueError('Batch update precedes creation')
    if (type(value['requires_api']) is not bool or type(value['concurrency']) is not int
            or value['concurrency'] != 1 or type(value['model_calls']) is not int or value['model_calls'] != 0):
        raise ValueError('Invalid batch read scalar types')
    items = value['items']
    if type(items) is not list or not 1 <= len(items) <= MAX_ITEMS:
        raise ValueError('Invalid batch read item count')
    counts = {status: 0 for status in ITEM_STATUSES}
    keys = set()
    job_ids = set()
    request_hashes = set()
    requires_api = False
    for item in items:
        _object(item, {'key', 'job_id', 'job_available', 'request_sha256', 'source_sha256', 'mode',
                       'provider', 'depth', 'revision', 'status', 'stage', 'error'}, 'batch read item')
        _identifier(item['key'], 64, 'batch item key')
        if (item['key'] in keys or item['job_id'] != stable_child_id(value['run_key'], item['key'])
                or item['job_id'] in job_ids):
            raise ValueError('Batch child identity changed or duplicated')
        keys.add(item['key'])
        job_ids.add(item['job_id'])
        if type(item['job_available']) is not bool:
            raise ValueError('Batch child availability requires an explicit boolean')
        for key in ('request_sha256', 'source_sha256'):
            _hash(item[key], 'Batch item '+key)
        if item['request_sha256'] in request_hashes:
            raise ValueError('Batch read requests are duplicated')
        request_hashes.add(item['request_sha256'])
        for key, choices in (('mode', ('local', 'github', 'example')),
                             ('provider', ('none', 'groq')), ('depth', ('quick', 'deep', 'planned')),
                             ('status', ITEM_STATUSES)):
            if type(item[key]) is not str or item[key] not in choices:
                raise ValueError('Unsupported batch read '+key)
        if type(item['revision']) is not str or _COMMIT.fullmatch(item['revision']) is None:
            raise ValueError('Batch read requires a pinned revision')
        _text(item['stage'], 200, 'batch item stage', empty=True)
        if item['error'] is not None:
            _text(item['error'], 2000, 'batch item error')
        counts[item['status']] += 1
        requires_api = requires_api or item['provider'] == 'groq'
    expected_counts = dict(total=len(items), **counts)
    _object(value['counts'], expected_counts, 'batch counts')
    if any(type(number) is not int or number < 0 for number in value['counts'].values()):
        raise ValueError('Batch counts require nonnegative integers')
    if (value['counts'] != expected_counts or value['requires_api'] != requires_api
            or value['status'] != batch_status(items, value['desired_state'])):
        raise ValueError('Batch status, counts or provider summary changed')
    _check_seal(value, 'Batch read')
    return deepcopy(value)


def render_markdown(value):
    if type(value) is dict and value.get('schema') == PREVIEW_SCHEMA:
        return render_preview_markdown(value)
    value = validate_read(value)
    lines = ['# IssueIntel product batch', '',
             '- Run key: `'+value['run_key']+'`', '- Batch: `'+value['id']+'`',
             '- Read SHA256: `'+value['sha256']+'`', '- Status: '+value['status'],
             '- Concurrency: 1', '- Model calls for this read: 0', '',
             '| Item | Child job | Saved job available | Status | Provider | Depth | Analysis revision | Stage / error |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for item in value['items']:
        detail = item['stage']+(' / '+item['error'] if item['error'] else '')
        lines.append('| '+' | '.join(_markdown_text(text) for text in (
            item['key'], item['job_id'], str(item['job_available']).lower(), item['status'],
            item['provider'], item['depth'], item['revision'], detail))+' |')
    lines += ['', 'Each child keeps a separate result and cost history. A failed or interrupted child is not',
              'retried by reading this batch. This projection does not evaluate benchmark data or verify a fix.', '']
    return '\n'.join(lines)
