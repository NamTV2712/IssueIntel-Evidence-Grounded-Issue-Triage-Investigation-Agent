"""Storage observations use temporary synthetic trees and metadata only."""
from collections import namedtuple
from pathlib import Path
import os
import stat
import subprocess
from types import SimpleNamespace

import pytest

from issueintel.runtime import storage


def write(root, path, size=7):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'x' * size)
    return target


def paths(report):
    return {row['path']: row for row in report['candidates']}


def protected(report):
    return {row['path']: row['reason'] for row in report['protected']}


def test_metadata_only_preview_counts_logical_bytes_and_protects_sources(tmp_path, monkeypatch):
    sizes = {'.git/objects/aa/object': 12, 'src/app.py': 17, 'tests/test_app.py': 11,
        'data/raw/issue.json': 19, 'artifacts/receipts/provider.json': 23,
        '.venv/Lib/site-packages/module.py': 13, '.env': 31, 'configs/policy.json': 5,
        'proposal.md': 29, 'other.bin': 2}
    for path, size in sizes.items(): write(tmp_path, path, size)
    def deny(*args, **kwargs): pytest.fail('Storage preview opened file contents')
    monkeypatch.setattr(Path, 'open', deny)
    monkeypatch.setattr(Path, 'read_bytes', deny)
    monkeypatch.setattr(Path, 'read_text', deny)
    report = storage.preview_storage(tmp_path)
    assert report['complete'] and report['files'] == len(sizes)
    assert report['measured_bytes'] == sum(sizes.values())
    assert sum(row['measured_bytes'] for row in report['categories']) == report['measured_bytes']
    assert sum(row['files'] for row in report['categories']) == report['files']
    assert sum(row['directories'] for row in report['categories']) == report['directories']
    assert report['candidates'] == [] and report['model_calls'] == 0 and report['deletion_supported'] is False
    assert protected(report)['data'] == 'data_private'
    assert protected(report)['artifacts'] == 'artifacts_provenance'
    assert protected(report)['.git'] == 'git_history'
    assert protected(report)['.venv'] == 'environment_in_use'
    assert protected(report)['.env'] == 'configuration'
    assert protected(report)['configs'] == 'configuration'
    assert protected(report)['src'] == 'source_files'
    assert str(tmp_path) not in str(report)


def test_only_complete_allowlisted_bytecode_and_tool_cache_shapes_are_candidates(tmp_path):
    for path in ('__pycache__/app.cpython-311.pyc', 'src/pkg/__pycache__/thing.pyc',
                 'tests/__pycache__/test_thing.pyo', '.pytest_cache/.gitignore',
                 '.pytest_cache/CACHEDIR.TAG', '.pytest_cache/README.md',
                 '.pytest_cache/v/cache/nodeids', '.ruff_cache/0.14.1/1234567890',
                 '.mypy_cache/3.11/pkg/module.data.json', '.mypy_cache/3.11/pkg/module.meta.json'):
        write(tmp_path, path)
    report = storage.preview_storage(tmp_path)
    found = paths(report)
    assert set(found) == {'__pycache__', 'src/pkg/__pycache__', 'tests/__pycache__',
                         '.pytest_cache', '.ruff_cache', '.mypy_cache'}
    assert all(row['complete'] and row['measured_bytes'] == row['files'] * 7 for row in found.values())
    assert found['src/pkg/__pycache__']['reason'] == 'python_bytecode'
    assert found['.pytest_cache']['reason'] == 'tool_cache'
    assert report['complete'] and not report['limit_reasons']


@pytest.mark.parametrize('path', ['__pycache__/receipt.json', '__pycache__/receipt.pyc', '__pycache__/source.py',
    '__pycache__/unknown/file.pyc', '.pytest_cache/receipt.json', '.pytest_cache/v/cache/unknown',
    '.pytest_cache/state.sqlite', '.ruff_cache/0.14.1/receipt', '.mypy_cache/3.11/pkg/source.meta.json',
    '.mypy_cache/3.11/cache.db'])
def test_unknown_source_receipt_or_database_names_disqualify_cache(tmp_path, path):
    write(tmp_path, path)
    report = storage.preview_storage(tmp_path)
    assert report['complete'] and report['candidates'] == []
    assert protected(report)[path.split('/')[0]] == 'cache_unknown_content'


def test_data_artifact_git_environment_and_other_pycache_remain_protected(tmp_path):
    for prefix in ('data', 'artifacts', '.git', '.venv', 'downloaded', 'build'):
        write(tmp_path, prefix + '/__pycache__/module.pyc')
    report = storage.preview_storage(tmp_path)
    assert report['candidates'] == []
    assert report['complete'] and report['files'] == 6


def test_entry_bound_is_explicit_and_partial_cache_is_protected(tmp_path):
    for index in range(5): write(tmp_path, '__pycache__/file' + str(index) + '.pyc')
    report = storage.preview_storage(tmp_path, limits=dict(storage.DEFAULT_LIMITS, max_entries=3))
    assert not report['complete'] and report['scan']['entries'] == 3
    assert 'max_entries' in report['limit_reasons']
    assert report['files'] <= 2 and report['candidates'] == []
    assert protected(report)['__pycache__'] == 'cache_incomplete'


def test_depth_bound_does_not_descend(tmp_path):
    write(tmp_path, 'src/pkg/module.py', 91)
    report = storage.preview_storage(tmp_path, limits=dict(storage.DEFAULT_LIMITS, max_depth=0))
    assert not report['complete'] and report['measured_bytes'] == 0 and report['files'] == 0
    assert report['directories'] == 1 and report['limit_reasons'] == ['max_depth']


def test_time_bound_is_explicit_without_reading_contents(tmp_path, monkeypatch):
    write(tmp_path, '__pycache__/module.pyc')
    clock = iter((0.0, 0.1, 0.2, 0.9, 1.0, 1.1))
    monkeypatch.setattr(storage.time, 'monotonic', lambda: next(clock, 2.0))
    report = storage.preview_storage(tmp_path, limits=dict(storage.DEFAULT_LIMITS, max_seconds=0.15))
    assert not report['complete'] and 'max_seconds' in report['limit_reasons']
    assert report['scan']['entries'] == 0 and report['candidates'] == []


def test_missing_root_does_not_create_it_or_report_success(tmp_path):
    root = tmp_path / 'not-created'
    report = storage.preview_storage(root)
    assert not root.exists() and not report['complete'] and report['errors'] == 1
    assert report['measured_bytes'] == 0 and report['volume'] is None
    assert protected(report) == {'.': 'unsafe_root'}


def test_root_file_is_not_traversed(tmp_path):
    root = write(tmp_path, 'file', 99)
    report = storage.preview_storage(root)
    assert not report['complete'] and 'unsafe_root' in report['limit_reasons']
    assert report['files'] == 0 and report['candidates'] == []


def test_symlink_and_linked_root_ancestor_are_not_followed(tmp_path):
    external = tmp_path / 'outside'
    write(external, 'sentinel.pyc', 101)
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    try: (workspace / '__pycache__').symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError): pytest.skip('Symlink creation unavailable')
    report = storage.preview_storage(workspace)
    assert not report['complete'] and report['skipped_links'] == 1
    assert report['measured_bytes'] == 0 and report['candidates'] == []
    report = storage.preview_storage(workspace / '__pycache__')
    assert not report['complete'] and 'unsafe_root' in report['limit_reasons']
    assert report['measured_bytes'] == 0


def test_reparse_root_ancestor_is_rejected_without_scandir(tmp_path, monkeypatch):
    original = storage.os.lstat
    ancestor = tmp_path
    def marked(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if Path(path) == ancestor:
            class Reparse:
                st_mode = info.st_mode
                st_file_attributes = storage._REPARSE
            return Reparse()
        return info
    monkeypatch.setattr(storage.os, 'lstat', marked)
    monkeypatch.setattr(storage.os, 'scandir', lambda *a: pytest.fail('Followed a reparse ancestor'))
    report = storage.preview_storage(tmp_path / 'workspace')
    assert not report['complete'] and report['skipped_links'] == 1
    assert report['files'] == 0 and report['candidates'] == []


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows junction test')
def test_actual_windows_junction_and_its_selected_descendant_are_not_followed(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    outside = tmp_path / 'outside'
    sentinel = write(outside, 'nested/sentinel.pyc', 123)
    junction = workspace / '__pycache__'
    created = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(junction), str(outside)],
                             capture_output=True, check=False)
    if created.returncode: pytest.skip('Junction creation unavailable')
    try:
        assert storage._linked(os.lstat(junction))
        report = storage.preview_storage(workspace)
        assert not report['complete'] and report['skipped_links'] == 1
        assert report['measured_bytes'] == 0 and report['candidates'] == []
        assert protected(report)['__pycache__'] == 'unsafe_link'
        report = storage.preview_storage(junction / 'nested')
        assert not report['complete'] and 'unsafe_root' in report['limit_reasons']
        assert report['measured_bytes'] == 0 and report['candidates'] == []
        assert sentinel.stat().st_size == 123
    finally:
        # Remove only the checked junction entry, never recurse through its target.
        assert Path(os.path.abspath(junction)).parent == Path(os.path.abspath(workspace))
        assert storage._linked(os.lstat(junction))
        os.rmdir(junction)
        assert sentinel.stat().st_size == 123


@pytest.mark.parametrize('kind', ['reparse', 'different_device'])
def test_descendant_reparse_and_filesystem_boundaries_are_not_traversed(tmp_path, monkeypatch, kind):
    write(tmp_path, '__pycache__/module.pyc', 81)
    original = storage.os.lstat
    original_scan = storage.os.scandir
    def marked(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if Path(path) == tmp_path / '__pycache__':
            return SimpleNamespace(st_mode=info.st_mode, st_size=info.st_size,
                st_dev=info.st_dev + (kind == 'different_device'),
                st_file_attributes=storage._REPARSE if kind == 'reparse' else 0)
        return info
    def scan(path):
        if Path(path).name == '__pycache__': pytest.fail('Traversed an unsafe directory')
        return original_scan(path)
    monkeypatch.setattr(storage.os, 'lstat', marked)
    monkeypatch.setattr(storage.os, 'scandir', scan)
    report = storage.preview_storage(tmp_path)
    expected = 'unsafe_link' if kind == 'reparse' else 'filesystem_boundary'
    assert not report['complete'] and expected in report['limit_reasons']
    assert report['files'] == 0 and report['measured_bytes'] == 0 and report['candidates'] == []


def test_hardlink_lengths_are_logical_and_not_cleanup_candidates(tmp_path):
    original = write(tmp_path, 'retained.bin', 61)
    cache = tmp_path / '__pycache__'; cache.mkdir()
    try: os.link(original, cache / 'module.pyc')
    except (OSError, NotImplementedError): pytest.skip('Hard links unavailable')
    report = storage.preview_storage(tmp_path)
    assert report['complete'] and report['files'] == 2 and report['measured_bytes'] == 122
    assert report['candidates'] == []
    assert protected(report)['__pycache__'] == 'cache_unknown_content'


def test_entry_metadata_failure_is_partial_without_exposing_exception(tmp_path, monkeypatch):
    target = write(tmp_path, 'sensitive.bin', 98)
    original = storage.os.lstat
    def denied(path, *args, **kwargs):
        if Path(path) == target: raise OSError('secret credential text')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(storage.os, 'lstat', denied)
    report = storage.preview_storage(tmp_path)
    assert not report['complete'] and report['errors'] == 1 and report['files'] == 0
    assert report['limit_reasons'] == ['metadata_error']
    assert 'secret credential' not in str(report)


def test_scandir_error_is_partial_and_disqualifies_candidate(tmp_path, monkeypatch):
    write(tmp_path, '__pycache__/module.pyc')
    original = storage.os.scandir
    def denied(path):
        if Path(path).name == '__pycache__': raise PermissionError('private path must not leak')
        return original(path)
    monkeypatch.setattr(storage.os, 'scandir', denied)
    report = storage.preview_storage(tmp_path)
    assert not report['complete'] and report['errors'] == 1
    assert 'metadata_error' in report['limit_reasons'] and report['candidates'] == []
    assert protected(report)['__pycache__'] == 'cache_incomplete'
    assert 'private path' not in str(report)


def test_volume_is_optional_metadata(tmp_path, monkeypatch):
    usage = namedtuple('usage', 'total used free')(1000, 400, 600)
    monkeypatch.setattr(storage.shutil, 'disk_usage', lambda path: usage)
    report = storage.preview_storage(tmp_path)
    assert report['volume'] == {'total_bytes': 1000, 'free_bytes': 600}
    def unavailable(path): raise OSError('disk unavailable')
    monkeypatch.setattr(storage.shutil, 'disk_usage', unavailable)
    report = storage.preview_storage(tmp_path)
    assert report['volume'] is None and report['complete']


@pytest.mark.parametrize('limits', [False, {}, dict(storage.DEFAULT_LIMITS, extra=True),
    dict(storage.DEFAULT_LIMITS, max_entries=True), dict(storage.DEFAULT_LIMITS, max_entries=25001),
    dict(storage.DEFAULT_LIMITS, max_depth=41), dict(storage.DEFAULT_LIMITS, max_seconds=float('nan')),
    dict(storage.DEFAULT_LIMITS, max_seconds=0), dict(storage.DEFAULT_LIMITS, max_seconds=4)])
def test_limits_cannot_expand_scope_or_use_ambiguous_scalars(tmp_path, limits):
    with pytest.raises(ValueError): storage.preview_storage(tmp_path, limits=limits)
