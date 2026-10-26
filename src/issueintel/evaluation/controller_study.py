"""Private registration for a fixed, original-product controller comparison.

Registration pins bytes and native replay; it does not grant provider authority.
No function here reads credentials, opens a provider, or opens benchmark data.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import tomllib

from issueintel.investigation import product_study


PURPOSE = 'paired_product_controller_comparison'
PROTOCOL_ID = 'controller_rules_jev_v1'
SCHEMA = 'controller_study_registration_v1'
MAX_FILE_BYTES = 16_000_000
MAX_TOTAL_BYTES = 128_000_000
MAX_FILES = 4000
_HASH = re.compile(r'[a-f0-9]{64}')
_UNIT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')
_JOB = re.compile(r'[a-f0-9]{24}')
_UNSET = object()
_CONFIGS = ('configs/runtime.toml', 'configs/typesafe.toml',
            'configs/author_decisions.json')
_FORBIDDEN_PACK = {'followup', 'continued_from', 'resumed_from', 'recovered_from',
    'finalized_from', 'context_selection', 'repository_context', 'historical_sources',
    'historical_proof', 'historical_source_audit', 'prepared_evidence', 'experiment_policy', 'query_receipt'}


def _encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError('Study data must be finite UTF-8 JSON') from None


def digest(value):
    return sha256(_encoded(value)).hexdigest()


def seal(value):
    if not isinstance(value, dict) or 'sha256' in value:
        raise ValueError('Study seal requires an unsealed object')
    return {**deepcopy(value), 'sha256': digest(value)}


def _unseal(value, schema=None):
    if (not isinstance(value, dict) or not _hash(value.get('sha256'))
            or schema is not None and value.get('schema') != schema
            or digest({key: item for key, item in value.items() if key != 'sha256'}) != value['sha256']):
        raise ValueError('Study object seal or schema changed')
    return value


def _hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _safe_absolute(path):
    requested = Path(path)
    if '..' in requested.parts:
        raise ValueError('Study paths cannot contain parent traversal')
    path = Path(os.path.abspath(requested))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Study paths cannot traverse links or reparse points')
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError('Study file has an aliased hardlink')
        if part != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError('Study ancestor is not a regular directory')
    return path


def safe_path(root, value, *, artifacts=False, exists=False, directory=False):
    root = _safe_absolute(root)
    requested = Path(value)
    candidate = _safe_absolute(requested if requested.is_absolute() else root / requested)
    anchor = root / 'artifacts' if artifacts else root
    if candidate == anchor or not candidate.is_relative_to(anchor):
        raise ValueError('Study path must stay inside its workspace scope')
    if exists:
        info = candidate.lstat()
        regular = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not regular:
            raise ValueError('Study input is not a regular file or directory')
    return candidate


def read_bytes(path, *, maximum=MAX_FILE_BYTES):
    path = _safe_absolute(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise ValueError('Study input is not a bounded regular file')
    with path.open('rb') as stream:
        raw = stream.read(info.st_size + 1)
    if len(raw) != info.st_size:
        raise ValueError('Study file changed during bounded read')
    _safe_absolute(path)
    return raw


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate study JSON key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Non-finite study JSON value')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError('Invalid bounded UTF-8 study JSON') from None
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 48 or count > 250000:
            raise ValueError('Study JSON exceeds structural bounds')
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    _encoded(value)
    return value


def read_json(root, path=None, *, maximum=MAX_FILE_BYTES):
    path = _safe_absolute(root) if path is None else safe_path(root, path)
    return _json(read_bytes(path, maximum=maximum))


def write_new(root, path, value=_UNSET):
    """Publish a complete fsynced JSON file atomically without replacing bytes."""
    if value is _UNSET:
        path, value = _safe_absolute(root), path
    else:
        path = safe_path(root, path)
    raw = _encoded(value) + b'\n'
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('Study output exceeds byte bound')
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe_absolute(path.parent)
    temporary = _safe_absolute(path.with_name('.' + path.name + '.' + secrets.token_hex(12) + '.partial'))
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _safe_absolute(path)
        # link is an atomic create-if-absent on both Windows and POSIX. Rename
        # would overwrite an existing destination on POSIX.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def inventory(root, directory=None, *, maximum_files=MAX_FILES, maximum_bytes=MAX_TOTAL_BYTES,
              exclude_names=()):
    directory = _safe_absolute(root) if directory is None else safe_path(root, directory, exists=True, directory=True)
    if not directory.is_dir():
        raise ValueError('Study inventory directory is missing')
    if (not isinstance(exclude_names, (tuple, list, set, frozenset))
            or any(not isinstance(name, str) for name in exclude_names)):
        raise ValueError('Inventory exclusions require exact relative file names')
    excluded = {_relative(name) for name in exclude_names}
    result, pending, total, entries = {}, [directory], 0, 0
    while pending:
        current = pending.pop()
        for path in sorted(current.iterdir()):
            entries += 1
            if entries > maximum_files:
                raise ValueError('Study inventory exceeds entry bound')
            path = _safe_absolute(path)
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                name = path.relative_to(directory).as_posix()
                if info.st_size > MAX_FILE_BYTES:
                    raise ValueError('Study inventory file exceeds byte bound')
                total += info.st_size
                if total > maximum_bytes:
                    raise ValueError('Study inventory exceeds byte bound')
                if name in excluded:
                    # A currently held Windows lease cannot be read. Retain its
                    # measured size and all path/alias checks, without inventing
                    # a content hash for this caller-owned control file.
                    result[name] = {'bytes': info.st_size}
                else:
                    raw = read_bytes(path)
                    if len(raw) != info.st_size:
                        raise ValueError('Study inventory file changed during its bounded read')
                    result[name] = {'sha256': sha256(raw).hexdigest(), 'bytes': len(raw)}
            else:
                raise ValueError('Unexpected special file in study inventory')
    return dict(sorted(result.items()))


def _relative(value):
    if (not isinstance(value, str) or '\\' in value or ':' in value or '\x00' in value
            or any(part in {'', '.', '..'} for part in value.split('/'))
            or PurePosixPath(value).is_absolute()):
        raise ValueError('Study reference must be workspace-relative')
    return value


def validate_selection(value):
    if (not isinstance(value, dict) or set(value) != {'schema', 'purpose', 'protocol_id', 'cases'}
            or value['schema'] != 'controller_study_selection_v1'
            or not isinstance(value['purpose'], str) or not value['purpose'].strip() or len(value['purpose']) > 1000
            or value['protocol_id'] != PROTOCOL_ID
            or not isinstance(value['cases'], list) or len(value['cases']) != 5):
        raise ValueError('Study selection requires exactly five fixed original product cases')
    units, jobs, paths = set(), set(), set()
    for row in value['cases']:
        if (not isinstance(row, dict) or set(row) != {'unit_id', 'source_job_id', 'pack'}
                or not isinstance(row['unit_id'], str) or not _UNIT.fullmatch(row['unit_id'])
                or row['unit_id'] in units or not isinstance(row['source_job_id'], str)
                or not _JOB.fullmatch(row['source_job_id']) or row['source_job_id'] in jobs
                or not isinstance(row['pack'], dict) or set(row['pack']) != {'path', 'sha256'}
                or not _hash(row['pack']['sha256'])):
            raise ValueError('Study selection identities or source references are invalid')
        name = _relative(row['pack']['path'])
        if not name.startswith('artifacts/') or not name.endswith('.json') or name in paths:
            raise ValueError('Study packs must be unique JSON sources under ignored artifacts')
        units.add(row['unit_id']); jobs.add(row['source_job_id']); paths.add(name)
    return deepcopy(value)


def _file_ref(root, path):
    path = safe_path(root, path, exists=True)
    return {'path': path.relative_to(Path(root).absolute()).as_posix(),
            'sha256': sha256(read_bytes(path)).hexdigest()}


def _runtime(root):
    from issueintel.runtime.config import RuntimeConfig
    from issueintel.runtime.typesafe import TypesafeConfig, _price
    root = _safe_absolute(root)
    # Validate paths before native readers can resolve aliases.
    for name in _CONFIGS:
        raw = read_bytes(safe_path(root, name, exists=True))
        if name.endswith('.toml'):
            config = tomllib.loads(raw.decode('utf-8'))
            for field in ('price_file', 'ledger'):
                safe_path(root, _relative(config[field]))
        else:
            _json(raw)
    groq, typesafe = RuntimeConfig.load(root), TypesafeConfig.load(root)
    if groq.max_output_tokens != 4096 or groq.reasoning_effort != 'low':
        raise ValueError('Study requires the native Groq low/4096 recipe')
    if (typesafe.enabled is not False or typesafe.daily_requests != 0
            or typesafe.daily_usd != 0 or typesafe.monthly_usd != 0):
        raise ValueError('Study preserves disabled zero-budget Typesafe product defaults')
    for path in (groq.price_file, typesafe.price_file):
        read_json(safe_path(root, path, exists=True))
    groq_price, typesafe_date = groq.price(), _price(typesafe.price_file)
    if groq_price.context_tokens <= 4096 or groq_price.max_output_tokens < 4096:
        raise ValueError('Dated native model limits cannot support the registered recipe')
    result = {'schema': 'controller_study_runtime_v1'}
    policy = {key: str(value) if isinstance(value, Decimal) else value
              for key, value in vars(groq.policy).items()}
    descriptors = {
        'groq': {'provider': 'groq', 'model': groq.model, 'reasoning_effort': 'low',
            'max_output_tokens': 4096, 'policy': policy,
            'price_file': groq.price_file.relative_to(root).as_posix(), 'ledger_file': 'native/groq.sqlite'},
        'typesafe': {'provider': 'typesafe', 'model': typesafe.model, 'enabled': True,
            'daily_requests': 20, 'daily_usd': '0.053760', 'monthly_usd': '0.053760',
            'price_file': typesafe.price_file.relative_to(root).as_posix(), 'ledger_file': 'native/typesafe.sqlite'}}
    for name, config, checked in (('groq', groq, groq_price.checked_at), ('typesafe', typesafe, typesafe_date)):
        raw = _file_ref(root, 'configs/' + ('runtime' if name == 'groq' else 'typesafe') + '.toml')
        result[name] = {'raw_config': raw, 'raw_config_sha256': raw['sha256'],
            'descriptor': descriptors[name], 'derived_config_sha256': digest(descriptors[name]),
            'price': {**_file_ref(root, config.price_file), 'checked_at': checked}}
    return seal(result)


def source_fingerprint(root, runtime=None):
    root = _safe_absolute(root)
    source = safe_path(root, 'src', exists=True, directory=True)
    pending, names, entries = [source], [], 0
    while pending:
        for path in sorted(pending.pop().iterdir()):
            entries += 1
            if entries > MAX_FILES:
                raise ValueError('Source tree exceeds inventory entry bound')
            path = _safe_absolute(path)
            if path.is_dir():
                pending.append(path)
            elif path.suffix == '.py':
                names.append(path.relative_to(root).as_posix())
    if not names:
        raise ValueError('Study requires a complete Python source tree')
    runtime = runtime or _runtime(root)
    names += list(_CONFIGS) + [runtime[name]['price']['path'] for name in ('groq', 'typesafe')]
    total, files = 0, {}
    for name in sorted(set(names)):
        raw = read_bytes(safe_path(root, name, exists=True))
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError('Source fingerprint exceeds byte bound')
        files[name] = sha256(raw).hexdigest()
    return seal({'schema': 'controller_study_source_fingerprint_v1', 'files': files})


def _deny(*args, **kwargs):
    raise ValueError('Registration cannot construct a provider')


def _native_source_loader(root, row, pack):
    from issueintel.investigation.workbench import Workbench
    from issueintel.investigation.question_jobs import replay_snapshot
    from issueintel.repository.graph import build_graph
    expected = 'artifacts/demo/runs/' + row['source_job_id'] + '/pack.json'
    if row['pack']['path'] != expected:
        raise ValueError('Selected pack is not the original native job export')
    job_path = safe_path(root, 'artifacts/demo/jobs/' + row['source_job_id'] + '.json', artifacts=True, exists=True)
    job_raw, job = read_bytes(job_path), read_json(job_path)
    if (not isinstance(job, dict) or job.get('id') != row['source_job_id'] or job.get('status') != 'completed'
            or not isinstance(job.get('request'), dict) or job['request'].get('mode') != 'github'
            or job['request'].get('issue_url') != pack['case']['case_id']):
        raise ValueError('Study requires original completed GitHub job authority')
    # The native Workbench initializes local product databases even with sync
    # disabled. Check those incidental paths and SQLite sidecars before it can
    # traverse them; none is a provider or study-budget ledger.
    for name in ('artifacts/demo/plans.sqlite', 'data/github_sync/connections.sqlite'):
        for suffix in ('', '-journal', '-wal', '-shm'):
            safe_path(root, name + suffix)
    host = Workbench(root, provider_factory=_deny, typesafe_factory=_deny, automatic_sync=False)
    try:
        loaded, raw, depth = host._load_pack(row['source_job_id'])
        host._clarification_product(row['source_job_id'], loaded)
        if depth != 0 or sha256(raw).hexdigest() != row['pack']['sha256'] or digest(loaded) != digest(pack):
            raise ValueError('Native source pack or original authority changed')
        snapshot = replay_snapshot(pack)
        graph = build_graph(snapshot)
        if read_bytes(job_path) != job_raw:
            raise ValueError('Original source job changed during replay')
        return {'case': pack['case'], 'snapshot': snapshot, 'graph': graph,
                'authority': {'path': job_path.relative_to(Path(root).absolute()).as_posix(),
                              'sha256': sha256(job_raw).hexdigest(), 'mode': 'github'}}
    finally:
        host.executor.shutdown(wait=True)


def _load_source(root, row, loader):
    path = safe_path(root, row['pack']['path'], artifacts=True, exists=True)
    raw = read_bytes(path)
    if sha256(raw).hexdigest() != row['pack']['sha256']:
        raise ValueError('Selected product pack raw SHA256 changed')
    pack = _json(raw)
    if (not isinstance(pack, dict) or not isinstance(pack.get('case'), dict)
            or not isinstance(pack.get('snapshot'), dict) or not isinstance(pack.get('leakage_audit'), dict)
            or set(pack) & _FORBIDDEN_PACK
            or pack.get('snapshot', {}).get('protocol') != 'benchmark_base'
            or pack.get('leakage_audit', {}).get('strict_issue_time_verified') is not False
            or not isinstance(pack.get('evidence'), list)
            or any(not isinstance(item, dict) or item.get('kind') not in {'issue', 'code'} for item in pack['evidence'])):
        raise ValueError('Study sources exclude benchmark, fork, recovery and historical context')
    product_study.validate_case(pack['case'])
    context = (loader or _native_source_loader)(Path(root), deepcopy(row), deepcopy(pack))
    if not isinstance(context, dict) or set(context) != {'case', 'snapshot', 'graph', 'authority'}:
        raise ValueError('Source loader must return exact native source context')
    case = product_study.validate_case(context['case'])
    if digest(case) != digest(pack['case']):
        raise ValueError('Source loader changed the selected original case')
    authority = context['authority']
    if (not isinstance(authority, dict) or set(authority) != {'path', 'sha256', 'mode'}
            or authority['mode'] != 'github' or not _hash(authority['sha256'])):
        raise ValueError('Source loader lacks original GitHub job byte authority')
    authority_path = _relative(authority['path'])
    if authority_path != 'artifacts/demo/jobs/' + row['source_job_id'] + '.json':
        raise ValueError('Original source job authority path differs')
    job_raw = read_bytes(safe_path(root, authority_path, artifacts=True, exists=True))
    job = _json(job_raw)
    if (not isinstance(job, dict) or sha256(job_raw).hexdigest() != authority['sha256']
            or job.get('id') != row['source_job_id'] or job.get('status') != 'completed'
            or not isinstance(job.get('request'), dict) or job['request'].get('mode') != 'github'
            or job['request'].get('issue_url') != case['case_id']):
        raise ValueError('Source job authority changed')
    context['snapshot'].verify()
    product_study.context_binding(case, context['snapshot'], context['graph'])
    return context


def _protocol():
    return {'protocol_id': PROTOCOL_ID, 'purpose': PURPOSE, 'case_count': 5,
        'arms': ['rules', 'jev'], 'recipe': deepcopy(product_study.RECIPE),
        'execution_order': 'alternate_selected_index_rules_first_even_jev_first_odd',
        'selection_method': 'explicit_original_product_inputs',
        'outcomes': ['completion', 'grounding', 'controller_trace', 'cost', 'latency'],
        'authority_granted': False, 'human_review': False, 'benchmark_evaluation': False}


def study_directory(root, study_id):
    if not _hash(study_id):
        raise ValueError('Study ID must be an exact SHA256')
    return safe_path(root, 'artifacts/controller-studies/' + study_id, artifacts=True)


def _identity(selection, runtime, fingerprint, protocol):
    return digest({'selection': selection, 'runtime_sha256': runtime['sha256'],
                   'source_sha256': fingerprint['sha256'], 'protocol': protocol})


def _budget(study_id, cases, runtime):
    return {'schema': 'study_budget_policy_v1', 'study_id': study_id,
        'config_sha256': runtime['sha256'],
        'providers': [{'provider': name, 'model': runtime[name]['descriptor']['model'],
            'config_sha256': runtime[name]['derived_config_sha256'],
            'price_sha256': runtime[name]['price']['sha256'],
            'price_date': runtime[name]['price']['checked_at'],
            'max_attempts': 90 if name == 'groq' else 20} for name in ('groq', 'typesafe')],
        'arms': [{'arm_id': row['unit_id'] + ':' + arm, 'max_micro_usd': 193536,
                  'max_attempts': 9 if arm == 'rules' else 13}
                 for row in cases for arm in ('rules', 'jev')],
        'max_owned_attempts': 110, 'max_owned_micro_usd': 1935360}


def register_study(root, selection_path, *, source_loader=None):
    root = _safe_absolute(root)
    selection_path = safe_path(root, selection_path, artifacts=True, exists=True)
    raw = read_bytes(selection_path, maximum=100000)
    selection = validate_selection(_json(raw))
    ref = {**_file_ref(root, selection_path), 'canonical_sha256': digest(selection)}
    runtime, protocol = _runtime(root), _protocol()
    fingerprint = source_fingerprint(root, runtime)
    study_id = _identity(ref, runtime, fingerprint, protocol)
    cases, case_ids = [], set()
    for row in selection['cases']:
        context = _load_source(root, row, source_loader)
        if context['case']['case_id'] in case_ids:
            raise ValueError('Five selected units must be distinct canonical original issues')
        case_ids.add(context['case']['case_id'])
        policy = product_study.make_policy(context['case'], context['snapshot'], context['graph'],
            study_id=study_id, source_sha256=fingerprint['sha256'], runtime_sha256=runtime['sha256'])
        cases.append({**deepcopy(row), 'authority': deepcopy(context['authority']),
                      'context': deepcopy(policy['context']), 'product_policy': policy})
    if _runtime(root) != runtime or source_fingerprint(root, runtime) != fingerprint:
        raise ValueError('Source or runtime changed while registering the study')
    spec = seal({'schema': SCHEMA, 'study_id': study_id, 'selection': ref,
        'protocol': protocol, 'runtime': runtime, 'source_fingerprint': fingerprint,
        'cases': cases, 'budget_policy': _budget(study_id, cases, runtime),
        'bounds_are_authority': False})
    directory = study_directory(root, study_id)
    if directory.exists():
        raise FileExistsError('Study registration already exists; inspect its original bytes')
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = safe_path(root, directory.with_name('.' + study_id + '.' + secrets.token_hex(12) + '.partial'), artifacts=True)
    staging.mkdir()
    write_new(staging / 'selection.json', selection)
    write_new(staging / 'registration.json', spec)
    safe_path(root, staging, artifacts=True, exists=True, directory=True)
    study_directory(root, study_id)
    if directory.exists():
        raise FileExistsError('Study destination appeared during publication')
    # Directories are never replaced; a failed publication retains its staging.
    os.rename(staging, directory)
    return spec


def _validate_registration_value(spec, selection):
    _unseal(spec, SCHEMA)
    if set(spec) != {'schema', 'study_id', 'selection', 'protocol', 'runtime',
            'source_fingerprint', 'cases', 'budget_policy', 'bounds_are_authority', 'sha256'}:
        raise ValueError('Registration fields changed')
    validate_selection(selection)
    if (not _hash(spec['study_id']) or spec['bounds_are_authority'] is not False
            or spec['protocol'] != _protocol() or digest(selection) != spec['selection']['canonical_sha256']
            or not isinstance(spec['cases'], list) or len(spec['cases']) != 5):
        raise ValueError('Registration scope or frozen selection changed')
    _unseal(spec['runtime'], 'controller_study_runtime_v1')
    _unseal(spec['source_fingerprint'], 'controller_study_source_fingerprint_v1')
    if _identity(spec['selection'], spec['runtime'], spec['source_fingerprint'], spec['protocol']) != spec['study_id']:
        raise ValueError('Registration identity changed')
    if spec['budget_policy'] != _budget(spec['study_id'], selection['cases'], spec['runtime']):
        raise ValueError('Study proposed finite allowance changed')
    seen = set()
    for row, selected in zip(spec['cases'], selection['cases']):
        if (not isinstance(row, dict) or set(row) != {'unit_id', 'source_job_id', 'pack', 'authority', 'context', 'product_policy'}
                or {key: row[key] for key in selected} != selected):
            raise ValueError('Registered source selection changed')
        authority = row['authority']
        if (not isinstance(authority, dict) or set(authority) != {'path', 'sha256', 'mode'}
                or authority['mode'] != 'github' or not _hash(authority['sha256'])
                or authority['path'] != 'artifacts/demo/jobs/' + row['source_job_id'] + '.json'):
            raise ValueError('Registered original job authority changed')
        policy = product_study.validate_policy(row['product_policy'])
        if (policy['study_id'] != spec['study_id']
                or policy['source_sha256'] != spec['source_fingerprint']['sha256']
                or policy['runtime_sha256'] != spec['runtime']['sha256'] or row['context'] != policy['context']
                or row['context']['case_id'] in seen):
            raise ValueError('Registered native policy context changed')
        seen.add(row['context']['case_id'])


def _validate_registration(spec, selection):
    try:
        _validate_registration_value(spec, selection)
    except (KeyError, TypeError, AttributeError, IndexError):
        raise ValueError('Invalid registered study structure') from None


def load_case(root, spec, unit_id, *, source_loader=None):
    _unseal(spec, SCHEMA)
    matches = [row for row in spec['cases'] if row['unit_id'] == unit_id]
    if len(matches) != 1:
        raise ValueError('Unknown registered product unit')
    row = matches[0]
    context = _load_source(root, {key: row[key] for key in ('unit_id', 'source_job_id', 'pack')}, source_loader)
    if context['authority'] != row['authority']:
        raise ValueError('Registered original job authority changed')
    product_study.validate_context(row['product_policy'], context['case'], context['snapshot'], context['graph'])
    return context


def runtime_configs(root, spec):
    """Return exact scoped native configs without a key, provider or ledger."""
    from issueintel.runtime.config import RuntimeConfig
    from issueintel.runtime.typesafe import TypesafeConfig
    _unseal(spec, SCHEMA)
    runtime = _runtime(root)
    if runtime != spec['runtime']:
        raise ValueError('Registered native configuration or dated prices changed')
    directory = study_directory(root, spec['study_id'])
    groq = replace(RuntimeConfig.load(root), ledger_file=safe_path(root, directory / 'native/groq.sqlite', artifacts=True))
    typesafe = replace(TypesafeConfig.load(root), enabled=True, daily_requests=20,
        daily_usd=Decimal('0.053760'), monthly_usd=Decimal('0.053760'),
        ledger_file=safe_path(root, directory / 'native/typesafe.sqlite', artifacts=True))
    return groq, typesafe


def runtime_config_descriptor(root, config, *, provider, study_id):
    """Describe an actual native config, rejecting a foreign ledger or model."""
    from issueintel.runtime.config import RuntimeConfig
    from issueintel.runtime.typesafe import TypesafeConfig
    root = _safe_absolute(root)
    expected_type = {'groq': RuntimeConfig, 'typesafe': TypesafeConfig}.get(provider)
    if expected_type is None or type(config) is not expected_type or _safe_absolute(config.root) != root:
        raise ValueError('Study requires an exact native scoped runtime configuration')
    expected = safe_path(root, study_directory(root, study_id) / ('native/' + provider + '.sqlite'), artifacts=True)
    if _safe_absolute(config.ledger_file) != expected:
        raise ValueError('Native provider ledger escaped this study')
    price = safe_path(root, config.price_file, exists=True).relative_to(root).as_posix()
    if provider == 'groq':
        policy = {key: str(value) if isinstance(value, Decimal) else value
                  for key, value in vars(config.policy).items()}
        return {'provider': provider, 'model': config.model, 'reasoning_effort': config.reasoning_effort,
            'max_output_tokens': config.max_output_tokens, 'policy': policy,
            'price_file': price, 'ledger_file': 'native/groq.sqlite'}
    return {'provider': provider, 'model': config.model, 'enabled': config.enabled,
        'daily_requests': config.daily_requests, 'daily_usd': str(config.daily_usd),
        'monthly_usd': str(config.monthly_usd), 'price_file': price, 'ledger_file': 'native/typesafe.sqlite'}


def load_study(root, study_id, *, source_loader=None, replay_sources=True):
    if type(replay_sources) is not bool:
        raise ValueError('Choose an explicit source replay policy')
    directory = study_directory(root, study_id)
    spec = read_json(directory / 'registration.json')
    selection = read_json(directory / 'selection.json', maximum=100000)
    _validate_registration(spec, selection)
    if spec['study_id'] != study_id:
        raise ValueError('Study directory and registration ID differ')
    if _runtime(root) != spec['runtime'] or source_fingerprint(root, spec['runtime']) != spec['source_fingerprint']:
        raise ValueError('Registered source code, raw configuration or prices changed')
    source = safe_path(root, spec['selection']['path'], artifacts=True, exists=True)
    if sha256(read_bytes(source)).hexdigest() != spec['selection']['sha256']:
        raise ValueError('Original selection raw bytes changed')
    total = 0
    for row in spec['cases']:
        for ref in (row['pack'], row['authority']):
            raw = read_bytes(safe_path(root, ref['path'], artifacts=True, exists=True))
            total += len(raw)
            if total > MAX_TOTAL_BYTES or sha256(raw).hexdigest() != ref['sha256']:
                raise ValueError('Registered source bytes or total byte bound changed')
    if replay_sources:
        for row in spec['cases']:
            load_case(root, spec, row['unit_id'], source_loader=source_loader)
    return deepcopy(spec), directory


def inspect_study(root, study_id, *, source_loader=None):
    """Report missing separately from stale, without provider/ledger construction."""
    directory = study_directory(root, study_id)
    report = {'schema': 'controller_study_inspection_v1', 'study_id': study_id,
        'registration_present': False, 'valid': False, 'missing': [], 'stale': [],
        'provider_called': False, 'authority_granted': False}
    try:
        spec = read_json(directory / 'registration.json')
        report['registration_present'] = True
        selection = read_json(directory / 'selection.json', maximum=100000)
        _validate_registration(spec, selection)
    except FileNotFoundError as error:
        report['missing'].append(str(error.filename or 'registration'))
        return report
    except (ValueError, OSError, KeyError, TypeError) as error:
        report['stale'].append(type(error).__name__ + ': ' + str(error))
        return report
    references = {spec['selection']['path']: spec['selection']['sha256'], **spec['source_fingerprint']['files']}
    for row in spec['cases']:
        references.update({row['pack']['path']: row['pack']['sha256'], row['authority']['path']: row['authority']['sha256']})
    for name, expected in sorted(references.items()):
        try:
            raw = read_bytes(safe_path(root, name, exists=True))
            if sha256(raw).hexdigest() != expected:
                report['stale'].append(name)
        except FileNotFoundError:
            report['missing'].append(name)
        except (ValueError, OSError):
            report['stale'].append(name)
    if not report['missing'] and not report['stale']:
        try:
            load_study(root, study_id, source_loader=source_loader)
        except FileNotFoundError as error:
            report['missing'].append(str(error.filename or 'native source'))
        except (ValueError, OSError, KeyError, TypeError) as error:
            report['stale'].append(type(error).__name__ + ': ' + str(error))
    report['valid'] = not report['missing'] and not report['stale']
    report['case_count'] = len(spec['cases'])
    report['registration_sha256'] = spec['sha256']
    return report
