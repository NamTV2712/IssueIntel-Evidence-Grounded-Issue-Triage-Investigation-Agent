"""Download a small, fixed set of public source files with content verification."""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VERIFIED_DATASET = "princeton-nlp/SWE-bench_Verified"
VERIFIED_REVISION = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
LIVE_DATASET = "SWE-bench-Live/SWE-bench-Live"
LIVE_REVISION = "b51a86422e10cfd403beb4773e5a2947953e36ec"
ANNOTATIONS_SHA256 = "b9297c31f6c2ef712218b5bb2830bd47a7f600dedf6b927191d0170177266ab9"
ANNOTATIONS_URL = (
    "https://cdn.openai.com/introducing-swe-bench-verified/"
    "swe-bench-annotation-results.zip"
)


@dataclass(frozen=True)
class SourceFile:
    source: str
    dataset_id: str
    revision: str
    filename: str
    sha256: str
    size: int
    url: str


def _hub_file(source: str, dataset: str, revision: str, filename: str,
              digest: str, size: int) -> SourceFile:
    return SourceFile(
        source, dataset, revision, filename, digest, size,
        f"https://huggingface.co/datasets/{dataset}/resolve/{revision}/{filename}?download=true",
    )


SOURCE_FILES = (
    _hub_file("verified", VERIFIED_DATASET, VERIFIED_REVISION,
              "data/test-00000-of-00001.parquet",
              "a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd", 2096679),
    _hub_file("live", LIVE_DATASET, LIVE_REVISION,
              "data/full-00000-of-00002.parquet",
              "9371afaef65bb6e21417fb76e5cbd89a5bc5241e197db3ead4eb0353007e740c", 48133221),
    _hub_file("live", LIVE_DATASET, LIVE_REVISION,
              "data/full-00001-of-00002.parquet",
              "12e4956a1761efaeb121afe13a5371d8d439a7708b4638397d0b3d6cce1e8407", 50103053),
    SourceFile("source_human", "OpenAI/SWE-bench-annotations", ANNOTATIONS_SHA256,
               "swe-bench-annotation-results.zip", ANNOTATIONS_SHA256, 2397074,
               ANNOTATIONS_URL),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inside_root(root: Path, path: Path) -> Path:
    """Reject output paths that escape through a symlink/junction."""
    # Windows can briefly fail to canonicalize a concurrently created parent.
    # Retry resolution, never fall back to a lexical containment decision.
    for attempt in range(3):
        resolved, anchor = path.resolve(), root.resolve()
        if resolved.is_relative_to(anchor):
            return resolved
        if attempt < 2:
            time.sleep(0.01 * (attempt + 1))
    raise ValueError(f"Output path escapes benchmark root: {path}; resolved={resolved}; root={anchor}")


def source_path(root: Path, source: SourceFile) -> Path:
    return inside_root(
        root, root / "data" / "upstream" / source.source / source.revision / source.filename
    )


def _download(root: Path, source: SourceFile) -> Path:
    destination = source_path(root, source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.stat().st_size != source.size or sha256_file(destination) != source.sha256:
            raise ValueError(f"Pinned upstream file has changed: {destination}")
        return destination
    request = urllib.request.Request(
        source.url, headers={"User-Agent": "IssueIntel/0.2 benchmark-bootstrap"}
    )
    temporary: Path | None = None
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=".download-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                digest = hashlib.sha256()
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > source.size:
                        raise ValueError(f"Upstream file exceeds its pinned size: {source.url}")
                    digest.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        if size != source.size or digest.hexdigest() != source.sha256:
            raise ValueError(f"Upstream checksum mismatch: {source.url}")
        # An interrupted/corrupted download never replaces the pinned file.
        os.replace(temporary, destination)
        temporary = None
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def download_sources(root: str | Path) -> dict[str, Any]:
    """Fetch only Verified, the Live full split and public source annotations."""
    root = Path(root).resolve()
    paths = [_download(root, source) for source in SOURCE_FILES]
    return {
        "verified_path": paths[0],
        "live_paths": paths[1:3],
        "annotations_path": paths[3],
    }
