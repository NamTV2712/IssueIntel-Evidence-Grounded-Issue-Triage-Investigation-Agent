"""Bounded, metadata-only local storage observations; never a deletion plan.

File lengths are logical bytes, including separate names for hard links. They
are not a measurement of allocated blocks or bytes that deletion would reclaim.
Limits are cooperative between filesystem calls, not filesystem I/O deadlines.
"""
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import shutil
import stat
import time


DEFAULT_LIMITS = {'max_entries': 25000, 'max_depth': 40, 'max_seconds': 3.0}
CATEGORIES = ('git', 'source', 'environment', 'tool_cache', 'data', 'artifacts', 'other')
_TOOL_CACHES = {'.pytest_cache', '.ruff_cache', '.mypy_cache'}
_SOURCE = {'src', 'tests', 'docs', '.github'}
_CONFIG = {'configs', '.env', '.env.example', 'pyproject.toml', 'setup.cfg', 'setup.py',
           'requirements.txt', 'requirements-dev.txt', '.gitignore', '.gitattributes'}
_SOURCE_FILES = {'README.md', 'proposal.md', 'AGENTS.md', 'CLAUDE.md', 'run.py', 'LICENSE'}
_RECEIPT_NAME = re.compile(r'receipt|provenance|manifest|ledger|budget|reservation|checkpoint|source', re.I)
_REPARSE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)


def _limits(value):
    if value is None:
        return dict(DEFAULT_LIMITS)
    if type(value) is not dict or set(value) != set(DEFAULT_LIMITS):
        raise ValueError('Choose explicit bounded storage limits')
    result = dict(value)
    for key, minimum in (('max_entries', 1), ('max_depth', 0)):
        if type(result[key]) is not int or not minimum <= result[key] <= DEFAULT_LIMITS[key]:
            raise ValueError('Invalid storage traversal limit')
    seconds = result['max_seconds']
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= DEFAULT_LIMITS['max_seconds']:
        raise ValueError('Invalid storage time limit')
    return result


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & _REPARSE)


def _safe_ancestry(path):
    """lstat before traversal, including ancestors outside the workspace root."""
    current = None
    for parent in reversed((path, *path.parents)):
        current = os.lstat(parent)
        if _linked(current):
            return None, 'unsafe_link'
        if not stat.S_ISDIR(current.st_mode):
            return None, 'unsupported_entry'
    return current, None


def _portable(name):
    return (name not in ('', '.', '..') and not any(char in name for char in ('/', '\\', ':'))
            and not any(ord(char) < 32 for char in name))


def _category(parts):
    top = parts[0]
    if top == '.git': return 'git'
    if top == 'data': return 'data'
    if top == 'artifacts': return 'artifacts'
    if top in {'.venv', 'venv', 'env'} or top == '.env': return 'environment'
    if top in _TOOL_CACHES or top == '__pycache__' or top in {'src', 'tests'} and '__pycache__' in parts:
        return 'tool_cache'
    if top in _SOURCE or top in _CONFIG or top in _SOURCE_FILES: return 'source'
    return 'other'


def _protected_reason(top):
    if top == '.git': return 'git_history'
    if top == 'data': return 'data_private'
    if top == 'artifacts': return 'artifacts_provenance'
    if top in {'.venv', 'venv', 'env'}: return 'environment_in_use'
    if top in _CONFIG: return 'configuration'
    if top in _SOURCE or top in _SOURCE_FILES: return 'source_files'
    return 'protected_by_default'


def _candidate_kind(parts):
    if len(parts) == 1 and parts[0] in _TOOL_CACHES:
        return 'tool_cache'
    if parts[-1] == '__pycache__' and (len(parts) == 1 or parts[0] in {'src', 'tests'}):
        return 'python_bytecode'
    return None


def _cache_member(candidate, parts, *, directory):
    """Recognize only ordinary tool cache shapes; names never imply safe data."""
    relative = parts[len(candidate['parts']):]
    if not relative:
        return True
    if any(_RECEIPT_NAME.search(part) for part in relative):
        return False
    if candidate['reason'] == 'python_bytecode':
        return not directory and len(relative) == 1 and relative[0].endswith(('.pyc', '.pyo'))
    tool = candidate['parts'][0]
    joined = '/'.join(relative)
    if tool == '.pytest_cache':
        return joined in ({'v', 'v/cache'} if directory else
            {'.gitignore', 'CACHEDIR.TAG', 'README.md', 'v/cache/nodeids', 'v/cache/lastfailed', 'v/cache/stepwise'})
    if not directory and len(relative) == 1 and relative[0] in {'.gitignore', 'CACHEDIR.TAG'}:
        return True
    if tool == '.ruff_cache':
        return (bool(re.fullmatch(r'\d+(?:\.\d+){1,3}', relative[0])) and
                (len(relative) == 1 if directory else len(relative) == 2 and
                 re.fullmatch(r'[a-fA-F0-9]+', relative[1]) is not None))
    # Mypy metadata has a documented paired suffix; DB caches stay protected.
    if tool == '.mypy_cache':
        if not re.fullmatch(r'\d+\.\d+', relative[0]): return False
        if directory:
            return all(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', part) for part in relative[1:])
        return (len(relative) >= 2 and all(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', part)
                for part in relative[1:-1]) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*\.(?:data|meta)\.json', relative[-1]) is not None)
    return False


def preview_storage(root, *, limits=None):
    """Observe one local tree without opening file contents or following links.

    Every path starts protected. Only fully observed, narrow tool cache or
    bytecode directory shapes appear as candidates. This function never deletes,
    opens databases, loads credentials, creates directories or starts runtimes.
    Concurrent filesystem changes may make the observation partial; it is not a
    cleanup authorization or a race-free promise about a future deletion.
    """
    limits = _limits(limits)
    start = time.monotonic()
    root = Path(os.path.abspath(os.fspath(root)))
    result = {'schema': 'product_storage_preview_v1',
        'scanned_at': datetime.now(timezone.utc).isoformat(),
        'measured_bytes': 0, 'files': 0, 'directories': 0, 'complete': True,
        'limit_reasons': [], 'skipped_links': 0, 'errors': 0,
        'categories': [{'id': name, 'measured_bytes': 0, 'files': 0, 'directories': 0} for name in CATEGORIES],
        'candidates': [], 'protected': [], 'volume': None, 'limits': limits,
        'scan': {'entries': 0, 'elapsed_ms': 0}, 'deletion_supported': False, 'model_calls': 0}
    categories = {row['id']: row for row in result['categories']}
    protected = {}
    candidates = []
    stopped = False

    def protect(path, reason):
        protected[path] = reason

    def partial(reason):
        result['complete'] = False
        if reason not in result['limit_reasons']:
            result['limit_reasons'].append(reason)

    def budget():
        nonlocal stopped
        if time.monotonic() - start >= limits['max_seconds']:
            partial('max_seconds'); stopped = True
        if result['scan']['entries'] >= limits['max_entries']:
            partial('max_entries'); stopped = True
        return not stopped

    def unsafe(reason, relative, candidate=None):
        partial(reason)
        protect(relative, 'unsafe_root' if relative == '.' else reason)
        if reason == 'unsafe_link': result['skipped_links'] += 1
        if candidate is not None:
            candidate['eligible'] = False
            candidate['disqualified'] = 'cache_unsafe_entry'

    try:
        root_info, reason = _safe_ancestry(root)
        if reason:
            unsafe(reason, '.')
            partial('unsafe_root')
            root_info = None
    except OSError:
        root_info = None
        result['errors'] += 1
        partial('unsafe_root')
        protect('.', 'unsafe_root')
    if root_info is not None:
        try:
            volume = shutil.disk_usage(root)
            result['volume'] = {'total_bytes': volume.total, 'free_bytes': volume.free}
        except OSError:
            pass  # Volume metadata is optional and is not a tree measurement.

    def visit(path, parts, depth, candidate=None):
        if not budget(): return False
        try:
            info, reason = _safe_ancestry(path)
            if reason:
                unsafe(reason, '/'.join(parts) or '.', candidate)
                return False
            if info.st_dev != root_info.st_dev:
                unsafe('filesystem_boundary', '/'.join(parts) or '.', candidate)
                return False
            with os.scandir(path) as iterator:
                while budget():
                    try:
                        entry = next(iterator)
                    except StopIteration:
                        return True
                    result['scan']['entries'] += 1
                    if not _portable(entry.name):
                        unsafe('unsupported_entry', '/'.join(parts) or '.', candidate)
                        continue
                    child_parts = (*parts, entry.name)
                    relative = '/'.join(child_parts)
                    if len(child_parts) == 1:
                        protect(relative, _protected_reason(entry.name))
                    try:
                        # Windows DirEntry.stat may return st_dev/st_nlink=0.
                        # Fresh lstat both preserves boundary checks and avoids
                        # treating its directory enumeration cache as authority.
                        metadata = os.lstat(entry.path)
                    except OSError:
                        result['errors'] += 1; partial('metadata_error')
                        if candidate is not None:
                            candidate['eligible'] = False; candidate['disqualified'] = 'cache_incomplete'
                        continue
                    if _linked(metadata):
                        unsafe('unsafe_link', relative, candidate); continue
                    if metadata.st_dev != root_info.st_dev:
                        unsafe('filesystem_boundary', relative, candidate); continue
                    directory = stat.S_ISDIR(metadata.st_mode)
                    regular = stat.S_ISREG(metadata.st_mode)
                    if not directory and not regular:
                        unsafe('unsupported_entry', relative, candidate); continue
                    category = categories[_category(child_parts)]
                    field = 'directories' if directory else 'files'
                    result[field] += 1; category[field] += 1
                    if regular:
                        size = metadata.st_size
                        result['measured_bytes'] += size; category['measured_bytes'] += size
                        if candidate is not None:
                            candidate['files'] += 1; candidate['measured_bytes'] += size
                    if candidate is not None and (not _cache_member(candidate, child_parts, directory=directory)
                            or regular and getattr(metadata, 'st_nlink', 1) > 1):
                        candidate['eligible'] = False
                        candidate['disqualified'] = 'cache_unknown_content'
                    if not directory:
                        continue
                    own_candidate = candidate
                    kind = _candidate_kind(child_parts) if candidate is None else None
                    if kind:
                        own_candidate = {'path': relative, 'parts': child_parts, 'reason': kind,
                            'measured_bytes': 0, 'files': 0, 'complete': False, 'eligible': True,
                            'disqualified': None}
                        candidates.append(own_candidate)
                    if depth >= limits['max_depth']:
                        partial('max_depth')
                        if own_candidate is not None:
                            own_candidate['eligible'] = False; own_candidate['disqualified'] = 'cache_incomplete'
                        continue
                    done = visit(Path(entry.path), child_parts, depth + 1, own_candidate)
                    if kind:
                        own_candidate['complete'] = done
                    elif not done and own_candidate is not None:
                        own_candidate['eligible'] = False; own_candidate['disqualified'] = 'cache_incomplete'
                return False
        except OSError:
            result['errors'] += 1; partial('metadata_error')
            if candidate is not None:
                candidate['eligible'] = False; candidate['disqualified'] = 'cache_incomplete'
            return False

    if root_info is not None:
        visit(root, (), 0)
    for row in candidates:
        if row['eligible'] and row['complete']:
            result['candidates'].append({key: row[key] for key in ('path', 'measured_bytes', 'files', 'reason', 'complete')})
            protected.pop(row['path'], None)
        else:
            protect(row['path'], row['disqualified'] or 'cache_incomplete')
    result['candidates'].sort(key=lambda row: row['path'])
    result['protected'] = [{'path': path, 'reason': reason} for path, reason in sorted(protected.items())]
    result['scan']['elapsed_ms'] = max(0, int((time.monotonic() - start) * 1000))
    return result
