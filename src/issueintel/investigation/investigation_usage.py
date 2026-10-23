"""Replay captured investigation accounting without providers, ledgers or prices.

The snapshot is a deliberately small, declared local inventory. Hashes bind its
included bytes; they do not prove that uncaptured calls/files never existed.
Receipt ownership is immutable, and a conflicting reservation is never resolved
by preferring one copy. Completeness and settlement are separate properties.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import json
import re

from issueintel.runtime.io import digest
from issueintel.runtime.typesafe import TypesafeError, validate_receipt


SCHEMA = 'investigation_usage_v1'
SNAPSHOT_SCHEMA = 'investigation_usage_snapshot_v1'
MAX_NODES = 128
MAX_EDGES = 256
MAX_GAPS = 1024
MAX_RECEIPTS = 4096
MAX_BYTES = 16 * 1024 * 1024
PROVIDERS = ('groq', 'typesafe')
ARTIFACT_MODES = frozenset({'adaptive_questions', 'check_plan', 'claim_review', 'issue_relation'})
ROOT_MODES = frozenset({'local', 'github', 'example', 'main_dev', 'repository'})
ANCESTRY_MODES = frozenset({'followup', 'clarification', 'check_followup', 'memory_followup',
                          'context', 'planned_continuation', 'stage_resume', 'resume', 'finalization'})
# These gaps concern the scope outside an owner's own captured attempts/receipts.
# They make the lineage total unknown without erasing a complete current bill.
LINEAGE_ONLY_GAPS = frozenset({'lineage_source_unavailable', 'lineage_cycle', 'lineage_depth_bound',
                               'lineage_edge_bound', 'capture_read_bound', 'sidecar_discovery_bound',
                               'sidecar_registry_unavailable', 'sidecar_source_unavailable',
                               'missing_parent_binding', 'source_pack_unavailable'})
_STATUSES = frozenset({'queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted'})
_RECEIPT_STATUSES = frozenset({'reserved', 'settled', 'failed_bound', 'missing_usage_bound', 'breach'})
_RETAINED = frozenset({'reserved', 'failed_bound', 'missing_usage_bound'})
_GROQ_REQUIRED = frozenset({'reservation_id', 'created_at', 'model', 'input_bound', 'output_bound',
                          'tokens_charged_or_reserved', 'micro_usd_charged_or_reserved', 'status', 'price_date'})
_GROQ_ALLOWED = _GROQ_REQUIRED | {'observed_input_tokens', 'observed_output_tokens', 'stage'}
_TYPESAFE_ALLOWED = frozenset({'schema', 'provider', 'reservation_id', 'created_at', 'model', 'binding',
                             'request_sha256', 'response_sha256', 'status', 'input_bound', 'input_tokens',
                             'output_tokens', 'micro_usd_charged_or_reserved', 'price_date', 'price_source',
                             'currency', 'is_provider_invoice', 'sha256'})
_BINDING_ALLOWED = frozenset({'job_id', 'decision_index', 'query_sha256', 'snapshot_sha256',
                            'commit_sha', 'annotation_sha256', 'evidence_sha256'})
_NODE_KEYS = frozenset({'job_id', 'mode', 'job_status', 'provider', 'request_sha256', 'job_sha256',
                        'pack_sha256', 'providers'})
_CAPTURE_KEYS = frozenset({'applicable', 'tracking_available', 'attempted_calls', 'receipts'})
_EDGE_KEYS = frozenset({'child_job_id', 'parent_job_id', 'kind', 'proof_sha256', 'source_pack_sha256'})
_GAP_KEYS = frozenset({'code', 'job_id', 'provider', 'detail'})
_LIMITATIONS = [
    'Only the captured, locally declared owner inventory and typed links are replayed; missing files or calls are not independently disproved.',
    'Known subtotals include captured conservative reservations or retained bounds; they are not provider invoices or settled charges.',
    'Unknown attempt tracking, missing receipts, conflicting identities and source gaps keep the complete total unknown.',
    'Repeated references to one owner and reservation are counted once; conflicting owner or receipt bytes are not selected arbitrarily.',
    'Saved receipt prices and amounts are replayed without reading current prices, configuration, API keys or global provider ledgers.',
]


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError('Invalid accounting fields')


def _hex(value, length=64, *, nullable=False):
    if nullable and value is None:
        return
    if type(value) is not str or not re.fullmatch('[a-f0-9]{' + str(length) + '}', value):
        raise ValueError('Invalid accounting identity or digest')


def _count(value, maximum=(1 << 63) - 1):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('Invalid accounting integer')


def _text(value, maximum, *, code=False):
    if (type(value) is not str or not 1 <= len(value) <= maximum
            or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
            or code and not re.fullmatch(r'[a-z][a-z0-9_]*', value)):
        raise ValueError('Invalid accounting metadata')


def _json_size(value):
    """Reject non-JSON scalars, excessive nesting and oversized public captures."""
    def walk(item, depth=0):
        if depth > 12:
            raise ValueError('Accounting snapshot nesting exceeds bound')
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError('Invalid accounting object keys')
            for child in item.values():
                walk(child, depth + 1)
        elif type(item) is list:
            for child in item:
                walk(child, depth + 1)
        elif type(item) not in (str, int, bool, type(None)):
            raise ValueError('Accounting captures require JSON integer metadata')
    walk(value)
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, UnicodeError, TypeError):
        raise ValueError('Invalid accounting JSON') from None
    if len(raw) > MAX_BYTES:
        raise ValueError('Accounting snapshot exceeds size bound')
    return raw


def _public_receipt(receipt, provider):
    """Allow only accounting metadata, even when its native validation fails."""
    allowed = _GROQ_ALLOWED if provider == 'groq' else _TYPESAFE_ALLOWED
    if type(receipt) is not dict or set(receipt) - allowed:
        raise ValueError('Receipt includes unsupported or private fields')
    for key, value in receipt.items():
        if key == 'binding' and provider == 'typesafe':
            if type(value) is not dict or set(value) - _BINDING_ALLOWED:
                raise ValueError('Receipt includes unsupported binding fields')
            for field in value.values():
                if type(field) not in (str, int, bool, type(None)) or type(field) is str and len(field) > 512:
                    raise ValueError('Invalid public receipt metadata')
        elif type(value) not in (str, int, bool, type(None)) or type(value) is str and len(value) > 512:
            raise ValueError('Invalid public receipt metadata')


def _gap(code, job_id, provider, detail):
    return {'code': code, 'job_id': job_id, 'provider': provider, 'detail': detail}


def _validate_snapshot(snapshot):
    _json_size(snapshot)
    _keys(snapshot, {'schema', 'selected_job_id', 'nodes', 'edges', 'gaps', 'sha256'})
    if snapshot['schema'] != SNAPSHOT_SCHEMA:
        raise ValueError('Unsupported accounting snapshot')
    _hex(snapshot['selected_job_id'], 24)
    _hex(snapshot['sha256'])
    if digest({key: value for key, value in snapshot.items() if key != 'sha256'}) != snapshot['sha256']:
        raise ValueError('Changed accounting snapshot digest')
    if type(snapshot['nodes']) is not list or not 1 <= len(snapshot['nodes']) <= MAX_NODES:
        raise ValueError('Invalid accounting node bound')
    if type(snapshot['edges']) is not list or len(snapshot['edges']) > MAX_EDGES:
        raise ValueError('Invalid accounting edge bound')
    if type(snapshot['gaps']) is not list or len(snapshot['gaps']) > MAX_GAPS:
        raise ValueError('Invalid accounting gap bound')
    nodes = {}
    receipt_count = 0
    for node in snapshot['nodes']:
        _keys(node, _NODE_KEYS)
        _hex(node['job_id'], 24)
        if node['job_id'] in nodes:
            raise ValueError('Duplicate accounting owner')
        if (node['mode'] not in ROOT_MODES | ANCESTRY_MODES | ARTIFACT_MODES
                or node['job_status'] not in _STATUSES or node['provider'] not in {'groq', 'none'}):
            raise ValueError('Unsupported accounting owner')
        for field in ('request_sha256', 'job_sha256'):
            _hex(node[field])
        _hex(node['pack_sha256'], nullable=True)
        _keys(node['providers'], PROVIDERS)
        for provider in PROVIDERS:
            capture = node['providers'][provider]
            _keys(capture, _CAPTURE_KEYS)
            if type(capture['applicable']) is not bool or type(capture['tracking_available']) is not bool:
                raise ValueError('Invalid provider accounting flags')
            if capture['attempted_calls'] is not None:
                _count(capture['attempted_calls'], MAX_RECEIPTS)
            if type(capture['receipts']) is not list or len(capture['receipts']) > MAX_RECEIPTS:
                raise ValueError('Invalid captured receipt bound')
            receipt_count += len(capture['receipts'])
            if receipt_count > MAX_RECEIPTS:
                raise ValueError('Accounting receipt inventory exceeds bound')
            if not capture['applicable'] and (not capture['tracking_available']
                    or capture['attempted_calls'] != 0 or capture['receipts']):
                raise ValueError('Contradictory inapplicable provider capture')
            if provider == 'groq' and node['provider'] == 'groq' and not capture['applicable']:
                raise ValueError('Groq owner cannot declare its provider inapplicable')
            for captured in capture['receipts']:
                _keys(captured, {'receipt_sha256', 'receipt'})
                _hex(captured['receipt_sha256'])
                _public_receipt(captured['receipt'], provider)
                if digest(captured['receipt']) != captured['receipt_sha256']:
                    raise ValueError('Changed captured receipt digest')
        nodes[node['job_id']] = node
    selected = snapshot['selected_job_id']
    if selected not in nodes:
        raise ValueError('Missing selected accounting owner')
    parents = {}
    for edge in snapshot['edges']:
        _keys(edge, _EDGE_KEYS)
        child, parent = edge['child_job_id'], edge['parent_job_id']
        _hex(child, 24)
        _hex(parent, 24)
        _hex(edge['proof_sha256'])
        _hex(edge['source_pack_sha256'], nullable=True)
        if (child == parent or child not in nodes or parent not in nodes or child in parents
                or edge['kind'] != nodes[child]['mode']
                or nodes[child]['mode'] not in ANCESTRY_MODES | ARTIFACT_MODES
                or nodes[parent]['mode'] in ARTIFACT_MODES
                or edge['source_pack_sha256'] != nodes[parent]['pack_sha256']):
            raise ValueError('Invalid typed accounting edge')
        parents[child] = parent
    for identifier in nodes:
        seen = set()
        cursor = identifier
        while cursor in parents:
            if cursor in seen:
                raise ValueError('Accounting ancestry cycle')
            seen.add(cursor)
            cursor = parents[cursor]
    chain = {selected}
    cursor = selected
    while cursor in parents:
        cursor = parents[cursor]
        chain.add(cursor)
    for identifier, node in nodes.items():
        if identifier in chain:
            continue
        if node['mode'] not in ARTIFACT_MODES or parents.get(identifier) not in chain:
            raise ValueError('Unselected investigation or unrelated accounting owner')
    for gap in snapshot['gaps']:
        _keys(gap, _GAP_KEYS)
        _text(gap['code'], 96, code=True)
        _hex(gap['job_id'], 24, nullable=True)
        if gap['provider'] not in (*PROVIDERS, None):
            raise ValueError('Invalid accounting gap provider')
        _text(gap['detail'], 512)
    return nodes


def _groq_receipt(receipt):
    if set(receipt) - _GROQ_ALLOWED or not _GROQ_REQUIRED <= set(receipt):
        raise ValueError('Missing Groq receipt fields')
    _hex(receipt['reservation_id'], 32)
    _text(receipt['model'], 128)
    _text(receipt['created_at'], 64)
    _text(receipt['price_date'], 10)
    created = datetime.fromisoformat(receipt['created_at'])
    price_date = date.fromisoformat(receipt['price_date'])
    if created.tzinfo is None or created.utcoffset().total_seconds() != 0 or not 0 <= (created.date() - price_date).days <= 30:
        raise ValueError('Invalid Groq receipt dates')
    for key in ('input_bound', 'output_bound', 'tokens_charged_or_reserved', 'micro_usd_charged_or_reserved'):
        _count(receipt[key])
    if not receipt['input_bound'] or not receipt['output_bound'] or not receipt['micro_usd_charged_or_reserved']:
        raise ValueError('Invalid Groq receipt bounds')
    status = receipt['status']
    if type(status) is not str or status not in _RECEIPT_STATUSES:
        raise ValueError('Invalid Groq receipt status')
    if 'stage' in receipt:
        _text(receipt['stage'], 128)
    bound = receipt['input_bound'] + receipt['output_bound']
    tokens = receipt['tokens_charged_or_reserved']
    if status in _RETAINED and tokens != bound:
        raise ValueError('Groq retained receipt lost reserved tokens')
    if status == 'settled' and not 1 <= tokens <= bound:
        raise ValueError('Invalid Groq settled tokens')
    incoming, outgoing = receipt.get('observed_input_tokens'), receipt.get('observed_output_tokens')
    if (incoming is None) != (outgoing is None):
        raise ValueError('Incomplete Groq observed token metadata')
    if incoming is not None:
        _count(incoming)
        _count(outgoing)
        if not incoming or status in _RETAINED or tokens != incoming + outgoing:
            raise ValueError('Inconsistent Groq observed token metadata')
        if status == 'settled' and (incoming > receipt['input_bound'] or outgoing > receipt['output_bound']):
            raise ValueError('Groq settled receipt exceeded a bound')
    return receipt


def _native_receipt(receipt, provider, owner):
    if provider == 'groq':
        return _groq_receipt(receipt)
    validate_receipt(receipt)
    if receipt['binding']['job_id'] != owner:
        raise ValueError('Typesafe receipt belongs to a different owner')
    return receipt


def _records(snapshot):
    """Global identity conflicts invalidate every affected scope, including current."""
    groups = {}
    records = []
    gaps = list(deepcopy(snapshot['gaps']))
    linked_children = {edge['child_job_id'] for edge in snapshot['edges']}
    for node in snapshot['nodes']:
        owner = node['job_id']
        if node['mode'] in ANCESTRY_MODES | ARTIFACT_MODES and owner not in linked_children:
            gaps.append(_gap('missing_parent_binding', owner, None,
                             'This owner mode requires a source or parent link that is not present in the captured scope.'))
        for provider in PROVIDERS:
            capture = node['providers'][provider]
            if capture['applicable'] and not capture['tracking_available']:
                gaps.append(_gap('tracking_unavailable', owner, provider, 'Captured attempt and receipt tracking is unavailable or legacy.'))
            if capture['applicable'] and capture['attempted_calls'] is None:
                gaps.append(_gap('attempt_count_unknown', owner, provider, 'Captured receipts do not establish the number of attempted provider calls.'))
            owned_ids = set()
            for item in capture['receipts']:
                receipt = item['receipt']
                identifier = receipt.get('reservation_id')
                identity_valid = type(identifier) is str and re.fullmatch(r'[a-f0-9]{32}', identifier) is not None
                valid = True
                try:
                    _native_receipt(receipt, provider, owner)
                except (ValueError, TypesafeError, TypeError, KeyError, AttributeError, OverflowError):
                    valid = False
                    gaps.append(_gap('invalid_receipt', owner, provider, 'A captured public receipt failed native type, ownership, status or digest validation.'))
                key = (provider, identifier) if identity_valid else None
                record = {'owner': owner, 'provider': provider, 'key': key,
                          'receipt_sha256': item['receipt_sha256'], 'receipt': receipt, 'valid': valid}
                records.append(record)
                if key is not None:
                    groups.setdefault(key, []).append(record)
                    owned_ids.add(identifier)
                if valid and receipt['status'] == 'breach':
                    gaps.append(_gap('receipt_usage_breach', owner, provider, 'A captured receipt records a provider usage anomaly or exceeded bound.'))
            attempts = capture['attempted_calls']
            if attempts is not None and attempts != len(owned_ids):
                gaps.append(_gap('attempt_receipt_count_mismatch', owner, provider,
                                 'Captured attempt count differs from the number of distinct owned reservation identities.'))
    conflicts = set()
    for key, copies in groups.items():
        signatures = {(item['owner'], item['receipt_sha256']) for item in copies}
        if len(signatures) > 1:
            conflicts.add(key)
            for owner in sorted({item['owner'] for item in copies}):
                gaps.append(_gap('receipt_identity_conflict', owner, key[0],
                                 'One provider reservation has conflicting owner identities or captured receipt bytes.'))
    # Stable, exact dedup avoids repeating the same warning for duplicate captures.
    unique = {json.dumps(gap, sort_keys=True, separators=(',', ':')): gap for gap in gaps}
    return records, conflicts, [unique[key] for key in sorted(unique)]


def _summary(provider, selected_nodes, records, conflicts, gaps, *, all_scope=False):
    owner_ids = [node['job_id'] for node in selected_nodes]
    owner_set = set(owner_ids)
    captures = [node['providers'][provider] for node in selected_nodes]
    included = [item for item in records if item['provider'] == provider and item['owner'] in owner_set]
    applicable = any(capture['applicable'] for capture in captures)
    matching_gaps = [gap for gap in gaps if gap['provider'] in (provider, None)
                     and (all_scope or gap['code'] not in LINEAGE_ONLY_GAPS
                          and (gap['job_id'] is None or gap['job_id'] in owner_set))]
    unambiguous = {}
    duplicate_captures = 0
    for item in included:
        if not item['valid'] or item['key'] in conflicts:
            continue
        if item['key'] in unambiguous:
            duplicate_captures += 1
        else:
            unambiguous[item['key']] = item
    receipts = [item['receipt'] for item in unambiguous.values()]
    conflict_count = len({item['key'] for item in included if item['key'] in conflicts})
    invalid = sum(not item['valid'] for item in included)
    tracking = all(capture['tracking_available'] for capture in captures)
    attempted_calls = (sum(capture['attempted_calls'] for capture in captures)
                       if all(capture['attempted_calls'] is not None for capture in captures) else None)
    complete = tracking and attempted_calls is not None and not matching_gaps and not invalid and not conflict_count
    known = sum(receipt['micro_usd_charged_or_reserved'] for receipt in receipts)
    retained = sum(receipt['status'] in _RETAINED for receipt in receipts)
    breaches = sum(receipt['status'] == 'breach' for receipt in receipts)
    unsettled = sum(receipt['status'] != 'settled' for receipt in receipts)
    return {'provider': provider, 'applicable': applicable, 'owner_job_ids': owner_ids,
            'tracking_available': tracking, 'attempted_calls': attempted_calls,
            'captured_receipt_records': len(included), 'reservations': len(receipts),
            'duplicate_captures': duplicate_captures, 'conflicting_reservations': conflict_count,
            'invalid_receipts': invalid, 'retained_bounds': retained,
            'usage_anomalies': breaches + conflict_count + invalid, 'unsettled_reservations': unsettled,
            'inventory_complete': complete, 'all_usage_settled': complete and not unsettled,
            'known_micro_usd_charged_or_reserved': known,
            'complete_micro_usd_charged_or_reserved': known if complete else None,
            'gap_codes': sorted({gap['code'] for gap in matching_gaps})}


def _scope(selected_nodes, records, conflicts, gaps, *, all_scope=False):
    providers = {provider: _summary(provider, selected_nodes, records, conflicts, gaps, all_scope=all_scope)
                 for provider in PROVIDERS}
    complete = all(summary['inventory_complete'] for summary in providers.values())
    known = sum(summary['known_micro_usd_charged_or_reserved'] for summary in providers.values())
    return {'job_ids': [node['job_id'] for node in selected_nodes], 'providers': providers,
            'inventory_complete': complete,
            'all_usage_settled': all(summary['all_usage_settled'] for summary in providers.values()),
            'known_micro_usd_charged_or_reserved': known,
            'complete_micro_usd_charged_or_reserved': known if complete else None}


def build(snapshot):
    """Derive current/lineage totals from one captured snapshot, without any IO."""
    nodes = _validate_snapshot(snapshot)
    selected_id = snapshot['selected_job_id']
    selected = nodes[selected_id]
    records, conflicts, gaps = _records(snapshot)
    owners = []
    for node in snapshot['nodes']:
        owners.append({**{key: deepcopy(value) for key, value in node.items() if key != 'providers'},
                       'providers': {provider: _summary(provider, [node], records, conflicts, gaps)
                                     for provider in PROVIDERS}})
    bundle = {'schema': SCHEMA,
              'source': {'selected_job_id': selected_id, 'selected_pack_sha256': selected['pack_sha256'],
                         'snapshot_sha256': snapshot['sha256']},
              'snapshot': deepcopy(snapshot), 'owners': owners,
              'current': _scope([selected], records, conflicts, gaps),
              'lineage': _scope(snapshot['nodes'], records, conflicts, gaps, all_scope=True),
              'gaps': gaps, 'currency': 'USD', 'captured_receipts_only': True,
              'is_provider_invoice': False, 'absence_of_uncaptured_calls_verified': False,
              'consistency_basis': 'declared_captured_local_accounting', 'limitations': list(_LIMITATIONS)}
    bundle['sha256'] = digest(bundle)
    return bundle


def validate(bundle):
    """Replay every count, gap and binding, rejecting a modified/resealed view."""
    _json_size(bundle)
    if type(bundle) is not dict or 'snapshot' not in bundle:
        raise ValueError('Missing captured accounting source')
    expected = build(bundle['snapshot'])
    if json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(',', ':')) != json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(',', ':')):
        raise ValueError('Changed investigation accounting projection')
    return bundle


def validate_run_bindings(bundle, runs):
    """Cross-check accounting metadata against other included dossier run bytes.

    Owners outside the selected handoff remain declared accounting captures. A
    metadata-only failed run does not establish that no original pack existed,
    so a pack comparison is made only when the included run has that binding.
    Raw job bytes and typed intent proof bodies are not part of this projection.
    """
    validate(bundle)
    if type(runs) is not dict:
        raise ValueError('Invalid included accounting run inventory')
    for node in bundle['snapshot']['nodes']:
        identifier = node['job_id']
        if identifier not in runs:
            continue
        run = runs[identifier]
        if (type(run) is not dict or run.get('job_id') != identifier
                or run.get('mode') != node['mode'] or run.get('status') != node['job_status']
                or run.get('provider') != node['provider']
                or run.get('request_sha256') != node['request_sha256']):
            raise ValueError('Accounting owner differs from included run metadata')
        binding = run.get('binding')
        if binding is not None:
            if type(binding) is not dict or binding.get('pack_sha256') != node['pack_sha256']:
                raise ValueError('Accounting owner differs from included pack binding')
    for edge in bundle['snapshot']['edges']:
        child = edge['child_job_id']
        if child in runs and runs[child].get('parent_job_id') != edge['parent_job_id']:
            raise ValueError('Accounting edge differs from included run ancestry')
    return bundle


def _money(value):
    return str(value // 1000000) + '.' + str(value % 1000000).zfill(6)


def _md(value):
    return str(value).replace('\\', '\\\\').replace('|', '\\|').replace('<', '&lt;').replace('>', '&gt;').replace('\n', ' ')


def render_markdown(bundle):
    """Render the same saved accounting projection without recalculating inputs."""
    validate(bundle)
    lines = ['# Investigation accounting', '', 'Selected job: `' + bundle['source']['selected_job_id'] + '`',
             '', 'Captured local consistency only; not a provider invoice. Unknown contributors are not zero.', '',
             '| Scope | Provider | Known captured USD | Complete captured USD | Reservations | Retained bounds | Inventory complete | All settled |',
             '| --- | --- | ---: | ---: | ---: | ---: | --- | --- |']
    for scope_name in ('current', 'lineage'):
        for provider in PROVIDERS:
            summary = bundle[scope_name]['providers'][provider]
            complete = summary['complete_micro_usd_charged_or_reserved']
            lines.append('| ' + ' | '.join((scope_name, provider, _money(summary['known_micro_usd_charged_or_reserved']),
                         'unknown' if complete is None else _money(complete), str(summary['reservations']),
                         str(summary['retained_bounds']), str(summary['inventory_complete']).lower(),
                         str(summary['all_usage_settled']).lower())) + ' |')
    lines.extend(['', '## Captured owners', ''])
    for owner in bundle['owners']:
        lines.append('- `' + owner['job_id'] + '` — ' + _md(owner['mode']) + ' / ' + _md(owner['job_status']))
    if bundle['gaps']:
        lines.extend(['', '## Gaps and anomalies', ''])
        for gap in bundle['gaps']:
            lines.append('- `' + gap['code'] + '` (' + (gap['provider'] or 'scope') + ', '
                         + (gap['job_id'] or 'scope') + '): ' + _md(gap['detail']))
    lines.extend(['', '## Limits', ''])
    lines.extend('- ' + _md(item) for item in bundle['limitations'])
    lines.extend(['', 'Snapshot SHA256: `' + bundle['source']['snapshot_sha256'] + '`',
                  '', 'Accounting SHA256: `' + bundle['sha256'] + '`', ''])
    return '\n'.join(lines)
