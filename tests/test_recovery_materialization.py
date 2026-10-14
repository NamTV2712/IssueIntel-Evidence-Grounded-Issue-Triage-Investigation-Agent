"""Snapshot downloads must not truncate a previously complete Git graph."""
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from issueintel.benchmarks.strict_recovery import _prefetch_snapshot_objects


def git(path, *arguments):
    return subprocess.run(["git", "-C", str(path), *arguments], check=True,
        capture_output=True).stdout.decode().strip()


@pytest.mark.parametrize("fail_after_fetch", [False, True])
@pytest.mark.parametrize("original_boundary", [False, True])
def test_materialization_preserves_complete_ancestry(tmp_path, monkeypatch, fail_after_fetch, original_boundary):
    import issueintel.repository.acquire as acquisition
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "--quiet", "--template=")
    git(origin, "config", "user.name", "Synthetic")
    git(origin, "config", "user.email", "fixture@example.invalid")
    commits = []
    for index in range(4):
        (origin / "fixture.txt").write_text(str(index))
        git(origin, "add", "fixture.txt")
        git(origin, "commit", "--quiet", "-m", "Synthetic fixture")
        commits.append(git(origin, "rev-parse", "HEAD"))
    cache = tmp_path / "data" / "repos" / "synthetic__project.git"
    cache.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "--quiet", "--bare", str(origin), str(cache)],
        check=True, capture_output=True)
    before = (commits[0] + "\n").encode() if original_boundary else None
    if before:
        (cache / "shallow").write_bytes(before)
    expected = git(cache, "rev-list", commits[-1])
    monkeypatch.setattr(acquisition, "acquire_repository", lambda *args, **kwargs: SimpleNamespace(path=cache))

    def fetch(path, arguments, timeout, runner):
        git(path, *arguments)
        assert (path / "shallow").exists()
        if fail_after_fetch:
            raise TimeoutError("Synthetic failure after download")
    monkeypatch.setattr(acquisition, "_run_git", fetch)
    report = {"selected_snapshot_revision": commits[1], "query_time": "2020-01-01T00:00:00Z",
        "revision_availability": {"source_url": "https://example.invalid/witness", "available_at": "2019-01-01T00:00:00Z"}}
    call = lambda: _prefetch_snapshot_objects(tmp_path, {"repository": "synthetic/project"}, report, {"sha256": "0" * 64})
    if fail_after_fetch:
        with pytest.raises(TimeoutError):
            call()
    else:
        call()
    assert ((cache / "shallow").read_bytes() if (cache / "shallow").exists() else None) == before
    assert git(cache, "rev-list", commits[-1]) == expected
    assert git(cache, "show", commits[1] + ":fixture.txt") == "1"
