"""Read exact Git objects without checking out or executing target code.

A benchmark's base commit identifies a buggy checkout. It does not prove that
the checkout was publicly available before the issue was created. Git author
and committer dates are recorded as diagnostics, never as availability proof.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha1, sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tokenize
from typing import Mapping


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def source_lines(text: str) -> list[str]:
    """LF-based Git/Python lines, preserving embedded form-feed characters."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def validate_repo_path(path: str) -> str:
    """Require a relative Git path; nothing here is resolved on the filesystem."""
    if (not path or "\\" in path or "\x00" in path or ":" in path
            or any(ord(c) < 32 for c in path) or path.startswith("/")):
        raise ValueError(f"Unsafe repository path: {path!r}")
    parts = PurePosixPath(path).parts
    if any(part in {"..", ".", ".git"} for part in parts) or "/".join(parts) != path:
        raise ValueError(f"Unsafe repository path: {path!r}")
    return path


def _timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Temporal timestamps must include a timezone")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class SourceFile:
    path: str
    text: str
    sha256: str
    blob_sha: str = ""
    raw_sha256: str = ""
    encoding: str = "utf-8"

    def __post_init__(self) -> None:
        validate_repo_path(self.path)
        if self.sha256 != sha256(self.text.encode("utf-8")).hexdigest():
            raise ValueError(f"Source text digest mismatch: {self.path}")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RepositorySnapshot:
    repository: str
    commit_sha: str
    tree_sha: str
    files: tuple[SourceFile, ...]
    protocol: str
    cutoff: str | None
    availability: dict
    skipped: tuple[dict, ...]
    committer_time: str
    snapshot_sha256: str

    def to_dict(self) -> dict:
        return asdict(self)

    def verify(self) -> None:
        payload = self.to_dict()
        payload.pop("snapshot_sha256")
        if _digest(payload) != self.snapshot_sha256:
            raise ValueError("Repository snapshot digest mismatch")
        if self.protocol not in {"benchmark_base", "issue_time"}:
            raise ValueError("Unknown snapshot protocol")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.commit_sha) or not re.fullmatch(
                r"[0-9a-f]{40}|[0-9a-f]{64}", self.tree_sha):
            raise ValueError("Snapshot requires full commit and tree object IDs")
        cutoff = _timestamp(self.cutoff) if self.cutoff is not None else None
        _timestamp(self.committer_time)
        status = self.availability.get("status")
        certified = self.availability.get("historical_certified")
        if not isinstance(certified, bool) or status not in {"unknown", "caller_verified_receipt"}:
            raise ValueError("Invalid snapshot availability status")
        if status == "caller_verified_receipt":
            source_id = self.availability.get("source_id")
            receipt_sha = self.availability.get("receipt_sha256", "")
            if not isinstance(source_id, str) or not source_id.strip() or not re.fullmatch(r"[0-9a-f]{64}", receipt_sha):
                raise ValueError("Availability requires source_id and receipt_sha256")
            available_at = _timestamp(self.availability.get("available_at", ""))
            if cutoff is not None and available_at >= cutoff:
                raise ValueError("Repository availability must be strictly before cutoff")
            receipt = self.availability.get("receipt")
            if receipt is not None:
                if (_digest(receipt) != receipt_sha or receipt.get("commit_sha") != self.commit_sha
                        or not receipt.get("verified") or receipt.get("source_id") != source_id
                        or receipt.get("available_at") != self.availability.get("available_at")):
                    raise ValueError("Availability receipt content/binding mismatch")
        if certified and (status != "caller_verified_receipt" or cutoff is None):
            raise ValueError("Historical certification requires cutoff and verified availability receipt")
        if self.protocol == "issue_time" and (cutoff is None or not certified or status != "caller_verified_receipt"):
            raise ValueError("issue_time requires independently verified availability before cutoff")
        paths = [file.path for file in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("Duplicate paths in repository snapshot")
        for file in self.files:
            validate_repo_path(file.path)
            if sha256(file.text.encode("utf-8")).hexdigest() != file.sha256:
                raise ValueError(f"Source text digest mismatch: {file.path}")


def _git(repository: Path, *arguments: str, timeout: int = 60,
         input_data: bytes | None = None) -> bytes:
    environment = os.environ.copy()
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments], check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment, timeout=timeout, input=input_data,
    )
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Git inspection failed: {message[:400]}")
    return result.stdout


def _read_blobs(repository: Path, objects: dict[str, int]) -> dict[str, bytes]:
    """One bounded batch process, with framing and content-address verification.

    ``objects`` contains only tree-listed regular blobs whose combined size was
    admitted by the snapshot limits. Duplicate blob IDs are read only once.
    No filters, working-tree smudging or target programs are requested.
    """
    if not objects:
        return {}
    requested = list(objects)
    output = _git(repository, "cat-file", "--batch",
                  input_data="".join(blob + "\n" for blob in requested).encode("ascii"))
    blobs: dict[str, bytes] = {}
    offset = 0
    for blob in requested:
        newline = output.find(b"\n", offset)
        if newline < 0:
            raise ValueError("Truncated Git batch header")
        header = output[offset:newline].decode("ascii").split()
        if len(header) != 3 or header[0] != blob or header[1] != "blob":
            raise ValueError("Unexpected Git batch object identity/type")
        size = int(header[2])
        if size != objects[blob]:
            raise ValueError("Git blob size differs from tree metadata")
        offset = newline + 1
        raw = output[offset:offset + size]
        if len(raw) != size or output[offset + size:offset + size + 1] != b"\n":
            raise ValueError("Truncated Git batch blob")
        hash_function = sha1 if len(blob) == 40 else sha256
        digest = hash_function(f"blob {size}\0".encode("ascii") + raw).hexdigest()
        if digest != blob:
            raise ValueError("Git blob bytes do not match the content-addressed object ID")
        blobs[blob] = raw
        offset += size + 1
    if offset != len(output):
        raise ValueError("Unexpected trailing Git batch data")
    return blobs


def _availability(commit_sha: str, cutoff: str | None, protocol: str,
                  receipt: Mapping | None) -> dict:
    result = {
        "status": "unknown", "historical_certified": False,
        "reason": "Git timestamps and benchmark base_commit do not prove historical availability.",
    }
    if cutoff is not None:
        _timestamp(cutoff)
    if receipt is not None:
        receipt = dict(receipt)
        if receipt.get("commit_sha") != commit_sha:
            raise ValueError("Availability receipt is bound to a different commit")
        if not receipt.get("verified") or not receipt.get("source_id") or not receipt.get("available_at"):
            raise ValueError("Availability requires a verified receipt, source_id and available_at")
        available_at = _timestamp(str(receipt["available_at"]))
        if cutoff is not None and available_at >= _timestamp(cutoff):
            raise ValueError("Repository availability must be strictly before cutoff")
        result = {
            "status": "caller_verified_receipt", "historical_certified": cutoff is not None,
            "available_at": receipt["available_at"], "source_id": receipt["source_id"],
            "receipt_sha256": _digest(receipt),
            "receipt": receipt,
            "reason": "Receipt authenticity is the trusted caller's responsibility; Git does not prove it.",
        }
    if protocol == "issue_time" and (cutoff is None or not result["historical_certified"]):
        raise ValueError("issue_time requires a cutoff and independently verified availability receipt")
    return result


def build_snapshot(local_repo: str | Path, revision: str, cutoff: str | None = None,
                   protocol: str = "benchmark_base", availability: Mapping | None = None,
                   *, max_files: int = 10000, max_file_bytes: int = 512000,
                   max_total_bytes: int = 25000000) -> RepositorySnapshot:
    """Read a revision's tracked text with explicit size limits and exclusions.

    ``benchmark_base`` permits a pinned checkout with unverified historical
    availability. ``issue_time`` fails closed unless a trusted caller supplies
    an independent, revision-bound availability receipt strictly before cutoff.
    Symlinks and submodules are reported and never followed. Target imports,
    setup scripts, hooks, tests and patches are never executed.
    """
    if protocol not in {"benchmark_base", "issue_time"}:
        raise ValueError("Unknown snapshot protocol")
    if any(value <= 0 for value in (max_files, max_file_bytes, max_total_bytes)):
        raise ValueError("Snapshot limits must be positive")
    repository = Path(local_repo).resolve()
    if not repository.is_dir():
        raise ValueError("Require an existing local Git repository")
    if not revision or any(ord(c) < 32 for c in revision):
        raise ValueError("Require a nonempty Git revision")
    commit = _git(repository, "rev-parse", "--verify", "--end-of-options",
                  revision + "^{commit}").decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise ValueError("Git did not return a full commit object ID")
    tree = _git(repository, "rev-parse", commit + "^{tree}").decode("ascii").strip()
    committer_time = _git(repository, "show", "-s", "--format=%cI", commit).decode("ascii").strip()
    availability_status = _availability(commit, cutoff, protocol, availability)
    listing = _git(repository, "ls-tree", "-r", "-l", "-z", "--full-tree", commit)
    files: list[SourceFile] = []
    candidates: list[tuple[str, str, int]] = []
    skipped: list[dict] = []
    total = 0
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, blob, size = metadata.decode("ascii").split()
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError:
            skipped.append({"path_bytes_hex": raw_path.hex(), "reason": "undecodable_path"})
            continue
        try:
            validate_repo_path(path)
        except ValueError:
            skipped.append({"path": path, "reason": "unsafe_path"})
            continue
        reason = None
        if mode == "120000":
            reason = "symlink_not_followed"
        elif mode == "160000" or kind != "blob":
            reason = "submodule_not_followed"
        elif len(candidates) >= max_files:
            reason = "file_count_limit"
        elif int(size) > max_file_bytes:
            reason = "file_size_limit"
        elif total + int(size) > max_total_bytes:
            reason = "total_size_limit"
        if reason:
            skipped.append({"path": path, "reason": reason})
            continue
        candidates.append((path, blob, int(size)))
        total += int(size)
    blobs = _read_blobs(repository, {blob: size for _, blob, size in candidates})
    for path, blob, size in candidates:
        raw = blobs[blob]
        if b"\0" in raw:
            skipped.append({"path": path, "reason": "binary"})
            continue
        try:
            encoding = tokenize.detect_encoding(BytesIO(raw).readline)[0] if path.endswith(".py") else "utf-8-sig"
            text = raw.decode(encoding)
        except (UnicodeError, SyntaxError, LookupError):
            skipped.append({"path": path, "reason": "undecodable_text"})
            continue
        files.append(SourceFile(path, text, sha256(text.encode("utf-8")).hexdigest(),
                                blob, sha256(raw).hexdigest(), encoding))
    payload = dict(
        repository=str(repository), commit_sha=commit, tree_sha=tree, files=tuple(files),
        protocol=protocol, cutoff=cutoff, availability=availability_status,
        skipped=tuple(skipped), committer_time=committer_time,
    )
    preliminary = RepositorySnapshot(**payload, snapshot_sha256="")
    serialized = preliminary.to_dict()
    serialized.pop("snapshot_sha256")
    snapshot = RepositorySnapshot(**payload, snapshot_sha256=_digest(serialized))
    snapshot.verify()
    return snapshot
