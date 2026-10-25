"""Bounded journal reads keep ownership/inventory checks without large buffers."""
from pathlib import Path

import pytest

from issueintel.benchmarks import component_ablation_run as native
from issueintel.benchmarks import component_continuation as core
from issueintel.benchmarks import component_continuation_run as runner


def record(root, name='calls/a.json'):
    directory = root / 'artifacts/experiments/component_continuation' / ('a' * 64)
    path = directory / 'cases' / ('b' * 64) / name
    core.write_new(root, path, core.seal({'schema': 'synthetic_journal', 'status': 'intent', 'name': name}))
    return directory, path


def test_small_record_read_uses_actual_size_plus_growth_sentinel(tmp_path, monkeypatch):
    _, path = record(tmp_path)
    raw = path.read_bytes()
    original, requested = Path.open, []

    class Observed:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def read(self, size):
            requested.append(size)
            return self.stream.read(size)

    def opening(target, *args, **kwargs):
        stream = original(target, *args, **kwargs)
        return Observed(stream) if target == path else stream

    monkeypatch.setattr(Path, 'open', opening)
    assert core._raw(tmp_path, path) == raw
    assert requested == [len(raw) + 1]
    with pytest.raises(ValueError, match='bounded regular'):
        core._raw(tmp_path, path, max_bytes=len(raw) - 1)
    assert requested == [len(raw) + 1]


@pytest.mark.parametrize('change', ['grow', 'shrink'])
def test_file_size_change_after_stat_denies_incomplete_read(tmp_path, monkeypatch, change):
    _, path = record(tmp_path)
    raw = path.read_bytes()
    original = Path.open

    class Changed:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, size):
            return (raw + b'x' if change == 'grow' else raw[:-1])[:size]

    def opening(target, *args, **kwargs):
        return Changed() if target == path else original(target, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', opening)
    with pytest.raises(ValueError, match='changed during bounded read'):
        core._raw(tmp_path, path)


def test_overlay_reader_retains_exact_native_inventory_and_all_record_kinds(tmp_path):
    for name in ('calls/a.json', 'stages/b.json', 'arms/c.json'):
        directory, _ = record(tmp_path, name)
    expected_inventory = native._inventory(tmp_path, directory)
    expected = {kind: [] for kind in ('calls', 'stages', 'arms')}
    for name in expected_inventory:
        expected[Path(name).parts[2]].append(native._read(tmp_path, directory / name))
    inventory, arms, calls, stages = runner._overlay_records(tmp_path, directory, native)
    assert inventory == expected_inventory
    assert arms == expected['arms'] and calls == expected['calls'] and stages == expected['stages']


@pytest.mark.parametrize('name', ['calls/a.txt', 'backup/a.json', 'calls/nested/a.json'])
def test_unexpected_paid_inventory_is_not_hidden_by_fast_reader(tmp_path, name):
    directory, _ = record(tmp_path, name)
    with pytest.raises(ValueError, match='Unexpected inference inventory'):
        runner._overlay_records(tmp_path, directory, native)


@pytest.mark.parametrize('limit,value', [('MAX_FILES', 1), ('MAX_TOTAL_BYTES', 1), ('MAX_FILE_BYTES', 1)])
def test_native_inventory_and_record_limits_remain_enforced(tmp_path, monkeypatch, limit, value):
    directory, _ = record(tmp_path, 'calls/a.json')
    record(tmp_path, 'stages/b.json')
    monkeypatch.setattr(native, limit, value)
    with pytest.raises(ValueError):
        runner._overlay_records(tmp_path, directory, native)
