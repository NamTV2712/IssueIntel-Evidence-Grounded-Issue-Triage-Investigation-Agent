"""Content-addressed text blobs avoid copying whole repositories into every pack."""
from __future__ import annotations

import gzip
import hashlib
import json
from io import BytesIO
from pathlib import Path
from collections import OrderedDict
from threading import Lock

from issueintel.runtime.io import canonical_json, write_json
from .snapshot import RepositorySnapshot, SourceFile

_DECODED = OrderedDict()
_DECODED_LOCK = Lock()
_DECODED_BYTES = 0
_DECODED_LIMIT = 64 * 1024 * 1024


def _read_gzip_bounded(path: Path, limit: int) -> bytes:
    global _DECODED_BYTES
    if path.stat().st_size > limit:
        raise ValueError("Compressed cache object exceeds size limit")
    compressed = path.read_bytes()
    if len(compressed) > limit:
        raise ValueError("Compressed cache object exceeds size limit")
    # Read/hash actual bytes on every access. No mtime-only trust or stale cache
    # after corruption; identical content avoids repeated gzip decoding.
    key = (hashlib.sha256(compressed).digest(), limit)
    with _DECODED_LOCK:
        if key in _DECODED:
            _DECODED.move_to_end(key)
            return _DECODED[key]
    with gzip.GzipFile(fileobj=BytesIO(compressed)) as handle:
        content=handle.read(limit+1)
    if len(content)>limit:
        raise ValueError("Decoded cache object exceeds size limit")
    if len(content) <= _DECODED_LIMIT:
        with _DECODED_LOCK:
            if key not in _DECODED:
                _DECODED[key] = content
                _DECODED_BYTES += len(content)
                while _DECODED_BYTES > _DECODED_LIMIT:
                    _, removed = _DECODED.popitem(last=False)
                    _DECODED_BYTES -= len(removed)
    return content


def save_snapshot(snapshot: RepositorySnapshot, directory: Path | str) -> Path:
    snapshot.verify()
    directory = Path(directory)
    descriptor = snapshot.to_dict()
    descriptors = []
    for file in snapshot.files:
        target = directory / "blobs" / file.sha256[:2] / (file.sha256 + ".txt.gz")
        content = file.text.encode("utf-8")
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(gzip.compress(content, mtime=0))
        elif hashlib.sha256(_read_gzip_bounded(target,4_000_000)).hexdigest() != file.sha256:
            raise ValueError("Cached source blob is corrupt")
        value = file.to_dict()
        value.pop("text")
        descriptors.append(value)
    descriptor["files"] = descriptors
    target = directory / "snapshots" / (snapshot.snapshot_sha256 + ".json.gz")
    content = canonical_json(descriptor).encode("utf-8")
    if target.exists() and _read_gzip_bounded(target,64_000_000) != content:
        raise ValueError("Snapshot index has conflicting content")
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(content, mtime=0))
    return target


def load_snapshot(index: Path | str, directory: Path | str) -> RepositorySnapshot:
    directory = Path(directory).resolve()
    index = Path(index).resolve()
    if not index.is_relative_to(directory / "snapshots"):
        raise ValueError("Snapshot index escapes cache")
    value = json.loads(_read_gzip_bounded(index,64_000_000) if index.suffix == ".gz" else index.read_bytes())
    files = []
    for descriptor in value["files"]:
        sha = descriptor["sha256"]
        if len(sha) != 64 or any(char not in "0123456789abcdef" for char in sha):
            raise ValueError("Invalid source digest")
        target = directory / "blobs" / sha[:2] / (sha + ".txt.gz")
        content = _read_gzip_bounded(target,4_000_000)
        if hashlib.sha256(content).hexdigest() != sha:
            raise ValueError("Cached source blob is corrupt")
        files.append(SourceFile(**descriptor, text=content.decode("utf-8")))
    value["files"] = tuple(files)
    value["skipped"] = tuple(value["skipped"])
    snapshot = RepositorySnapshot(**value)
    snapshot.verify()
    return snapshot
