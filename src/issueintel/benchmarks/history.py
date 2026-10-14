"""Bounded acquisition of individual historical events from GH Archive.

Official hourly archives contain newline-delimited GitHub event JSON. Only a
matched event is retained. A missing match, byte/time cap, or archive gap does
not prove absence. No BigQuery service or language-model call is involved.
"""

from __future__ import annotations

import gzip
import base64
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import asdict, dataclass
from contextvars import ContextVar
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .models import utc_timestamp
from .sources import inside_root


@dataclass(frozen=True)
class ArchiveLimits:
    max_compressed_bytes: int = 32 * 1024 * 1024
    max_uncompressed_bytes: int = 256 * 1024 * 1024
    max_line_bytes: int = 512 * 1024
    max_records: int = 500_000
    max_seconds: float = 60.0
    read_timeout: float = 20.0

    def __post_init__(self) -> None:
        if any(value <= 0 for value in asdict(self).values()):
            raise ValueError("Every archive limit must be positive")


class _LimitReached(Exception):
    pass


# Opt-in DEV cache only. Existing recipes with no context keep exact behavior.
ARCHIVE_CACHE = ContextVar("issueintel_archive_cache", default=None)
EVENT_OBSERVER = ContextVar("issueintel_event_observer", default=None)


class _CompressedReader:
    def __init__(self, stream: Any, maximum: int, deadline: float):
        self.stream, self.maximum, self.deadline = stream, maximum, deadline
        self.count, self.eof = 0, False
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        if time.monotonic() >= self.deadline:
            raise _LimitReached("time_cap")
        remaining = self.maximum - self.count
        if remaining <= 0:
            raise _LimitReached("compressed_byte_cap")
        requested = min(remaining, size if size >= 0 else 64 * 1024)
        data = self.stream.read(requested)
        if len(data) > requested:
            raise ValueError("Archive transport returned more bytes than requested")
        self.count += len(data)
        self.digest.update(data)
        if not data:
            self.eof = True
        return data


def _hour(value: str | datetime) -> datetime:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{1,2}", value):
        day, hour = value.rsplit("-", 1)
        parsed = datetime.strptime(day, "%Y-%m-%d").replace(hour=int(hour), tzinfo=timezone.utc)
    else:
        normalized = utc_timestamp(value)
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def archive_hour_url(value: str | datetime) -> str:
    hour = _hour(value)
    return f"https://data.gharchive.org/{hour:%Y-%m-%d}-{hour.hour}.json.gz"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def scan_archive_hour(
    hour: str | datetime, predicate: Callable[[Mapping[str, Any]], bool], *,
    limits: ArchiveLimits | None = None,
) -> dict[str, Any]:
    context = ARCHIVE_CACHE.get()
    if context is not None:
        from .history_cache import scan_cached_hour
        return scan_cached_hour(hour, predicate, limits or ArchiveLimits(), context, _scan_archive_hour)
    return _scan_archive_hour(hour, predicate, limits=limits)


def _scan_archive_hour(hour, predicate, *, limits=None):
    """Stream one explicit hour and stop at the first matching raw event."""
    limits = limits or ArchiveLimits()
    url = archive_hour_url(hour)
    started = time.monotonic()
    deadline = started + limits.max_seconds
    stats: dict[str, Any] = {
        "source_url": url,
        "archive_hour": _hour(hour).isoformat().replace("+00:00", "Z"),
        "limits": asdict(limits),
        "compressed_bytes_read": 0,
        "uncompressed_bytes_read": 0,
        "records_read": 0,
        "malformed_records": 0,
        "archive_scanned_to_end": False,
        "coverage_complete": False,
        "absence_claim": False,
        "status": "not_started",
        "match": None,
        "compressed_sha256_prefix": None,
        "compressed_sha256_full": None,
    }
    counter: _CompressedReader | None = None
    request = urllib.request.Request(url, headers={
        "User-Agent": "IssueIntel/0.2 historical-receipt",
        "Accept-Encoding": "identity",
    })
    try:
        with urllib.request.urlopen(request, timeout=min(limits.read_timeout, limits.max_seconds)) as response:
            headers = getattr(response, "headers", {})
            stats["advertised_compressed_bytes"] = headers.get("Content-Length")
            counter = _CompressedReader(response, limits.max_compressed_bytes, deadline)
            with gzip.GzipFile(fileobj=counter, mode="rb") as decompressed:
                while True:
                    if time.monotonic() >= deadline:
                        raise _LimitReached("time_cap")
                    if stats["records_read"] >= limits.max_records:
                        raise _LimitReached("record_cap")
                    remaining = limits.max_uncompressed_bytes - stats["uncompressed_bytes_read"]
                    if remaining <= 0:
                        raise _LimitReached("uncompressed_byte_cap")
                    line = decompressed.readline(min(limits.max_line_bytes, remaining))
                    stats["uncompressed_bytes_read"] += len(line)
                    if not line:
                        stats["archive_scanned_to_end"] = True
                        stats["coverage_complete"] = stats["malformed_records"] == 0
                        stats["status"] = "no_match_in_scanned_hour"
                        break
                    if not line.endswith(b"\n") and len(line) == min(limits.max_line_bytes, remaining):
                        # The parser never receives an oversized/partial JSON line.
                        raise _LimitReached("line_byte_cap" if limits.max_line_bytes <= remaining
                                            else "uncompressed_byte_cap")
                    stats["records_read"] += 1
                    try:
                        event = json.loads(line.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        stats["malformed_records"] += 1
                        continue
                    if not isinstance(event, Mapping):
                        stats["malformed_records"] += 1
                        continue
                    observer = EVENT_OBSERVER.get()
                    if observer is not None:
                        observer(event, line, stats["records_read"], url, stats["archive_hour"])
                    if predicate(event):
                        stats["status"] = "matched_event"
                        stats["match"] = {
                            "event": dict(event),
                            "raw_event_line": line.decode("utf-8"),
                            "raw_line_sha256": hashlib.sha256(line).hexdigest(),
                            "event_sha256": hashlib.sha256(_canonical(event)).hexdigest(),
                            "source_url": url,
                            "archive_hour": stats["archive_hour"],
                            "line_number": stats["records_read"],
                        }
                        break
    except _LimitReached as error:
        stats["status"] = str(error)
    except urllib.error.HTTPError as error:
        stats["status"] = "archive_unavailable"
        stats["http_status"] = error.code
    except (urllib.error.URLError, TimeoutError, OSError, EOFError, zlib.error) as error:
        stats["status"] = "archive_read_failed"
        stats["error_type"] = type(error).__name__
    finally:
        if counter is not None:
            stats["compressed_bytes_read"] = counter.count
            stats["compressed_sha256_prefix"] = counter.digest.hexdigest()
            if counter.eof:
                stats["compressed_sha256_full"] = counter.digest.hexdigest()
        stats["elapsed_seconds"] = round(time.monotonic() - started, 3)
    stats["coverage_note"] = (
        "A matching event is independent positive evidence; incomplete/missing hourly coverage is not negative evidence."
    )
    return stats


def _repository(repository: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Expected repository owner/name")
    return repository


def _save_match(root: str | Path | None, result: dict[str, Any]) -> None:
    if root is None or not result.get("match"):
        return
    root = Path(root).resolve()
    retained = result["match"]
    content = _canonical(retained) + b"\n"
    digest = hashlib.sha256(content).hexdigest()
    path = inside_root(root, root / "data/annotations/history_receipts" / f"{digest}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("Content-addressed historical receipt has changed")
    else:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".history-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    result["saved_receipt"] = {"path": path.relative_to(root).as_posix(), "sha256": digest}


def find_issue_opened_event(
    repository: str, issue_number: int | str, created_at: str, *,
    expected_problem_statement: str | None = None,
    hour_offsets: tuple[int, ...] = (0,), limits: ArchiveLimits | None = None,
    save_root: str | Path | None = None,
) -> dict[str, Any]:
    """Search at most three explicitly selected nearby hours for the original issue."""
    _repository(repository)
    number = str(issue_number)
    if not number.isdigit() or int(number) <= 0:
        raise ValueError("Expected a positive issue number")
    if not hour_offsets or len(hour_offsets) > 3 or any(offset not in {-1, 0, 1} for offset in hour_offsets):
        raise ValueError("Select one to three offsets from -1, 0, 1; no broad archive crawl")
    if len(set(hour_offsets)) != len(hour_offsets):
        raise ValueError("Hour offsets must be unique")
    created = utc_timestamp(created_at)
    attempts = []

    def predicate(event: Mapping[str, Any]) -> bool:
        payload = event.get("payload", {})
        repo = event.get("repo", {})
        if not isinstance(payload, Mapping) or not isinstance(repo, Mapping):
            return False
        issue = payload.get("issue", {})
        return bool(
            event.get("type") == "IssuesEvent" and event.get("public") is not False
            and str(repo.get("name", "")).lower() == repository.lower()
            and payload.get("action") == "opened" and isinstance(issue, Mapping)
            and str(issue.get("number")) == number and "pull_request" not in issue
            and issue.get("created_at")
            and utc_timestamp(issue["created_at"]) == created
        )

    result: dict[str, Any] = {"status": "no_original_event_found", "match": None,
                             "attempts": attempts, "absence_claim": False, "body_provenance": None}
    for offset in hour_offsets:
        scan = scan_archive_hour(_hour(created) + timedelta(hours=offset), predicate, limits=limits)
        attempts.append({key: value for key, value in scan.items() if key != "match"})
        if not scan["match"]:
            continue
        match = scan["match"]
        issue = match["event"]["payload"]["issue"]
        original = f"{issue.get('title') or ''}\n{issue.get('body') or ''}\n"
        matches_expected = None if expected_problem_statement is None else (
            original.replace("\r\n", "\n").strip()
            == expected_problem_statement.replace("\r\n", "\n").strip()
        )
        result.update({
            "status": "original_text_mismatch" if matches_expected is False else "original_event_found",
            "match": match,
            "original_problem_statement": original,
            "problem_statement_matches": matches_expected,
            "original_body_supported_for_task": matches_expected is True,
        })
        if matches_expected is True:
            result["body_provenance"] = {
                "kind": "issue_creation_event", "available_at": created,
                "source_url": match["source_url"], "payload": match["event"],
                "content_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
                "history_complete": True,
                "scope": "opened event proves this initial version, not completeness of hourly history",
            }
        _save_match(save_root, result)
        break
    return result


def find_revision_push_event(
    repository: str, revision: str, hour: str | datetime, *, before: str | None = None,
    limits: ArchiveLimits | None = None, save_root: str | Path | None = None,
) -> dict[str, Any]:
    """Find positive publication evidence for an exact SHA in one explicit hour."""
    _repository(repository)
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Expected a full lowercase commit SHA")
    before_time = datetime.fromisoformat(utc_timestamp(before).replace("Z", "+00:00")) if before else None

    def predicate(event: Mapping[str, Any]) -> bool:
        payload, repo = event.get("payload", {}), event.get("repo", {})
        if not isinstance(payload, Mapping) or not isinstance(repo, Mapping):
            return False
        if (event.get("type") != "PushEvent" or event.get("public") is False
                or str(repo.get("name", "")).lower() != repository.lower()):
            return False
        commits = payload.get("commits", [])
        shas = {str(commit.get("sha")) for commit in commits if isinstance(commit, Mapping)}
        shas.add(str(payload.get("head")))
        if revision not in shas or not event.get("created_at"):
            return False
        observed = datetime.fromisoformat(utc_timestamp(event["created_at"]).replace("Z", "+00:00"))
        return before_time is None or observed < before_time

    result = scan_archive_hour(hour, predicate, limits=limits)
    result["revision_proof"] = None
    if result["match"]:
        match = result["match"]
        result["revision_proof"] = {
            "repository": repository, "revision": revision, "basis": "gharchive_push_event",
            "source_url": match["source_url"], "payload": match["event"],
            "raw_line_sha256": match["raw_line_sha256"],
            "scope": "exact commit appears in a public push; no ancestry inference",
        }
        _save_match(save_root, result)
    return result


def verify_ancestry_receipt(
    receipt: Mapping[str, Any] | None, base_revision: str, selected_revision: str,
) -> bool:
    """Verify Git object IDs and parent edges, not a declared 'ancestor=True' flag."""
    if not receipt or receipt.get("base_revision") != base_revision or receipt.get(
        "selected_snapshot_revision"
    ) != selected_revision:
        return False
    objects = receipt.get("commit_parent_chain")
    if not isinstance(objects, list) or not 1 <= len(objects) <= 257:
        return False
    if any(not isinstance(obj, Mapping) for obj in objects):
        return False
    if receipt.get("parent_chain_sha256") != hashlib.sha256(_canonical(objects)).hexdigest():
        return False
    if objects[0].get("revision") != base_revision or objects[-1].get("revision") != selected_revision:
        return False
    for index, obj in enumerate(objects):
        revision = obj.get("revision", "")
        encoded = obj.get("raw_commit_base64", "")
        if not re.fullmatch(r"[0-9a-f]{40}", revision) or not isinstance(encoded, str) or len(encoded) > 90_000:
            return False
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            return False
        if len(raw) > 64 * 1024:
            return False
        actual_oid = hashlib.sha1(b"commit " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
        if actual_oid != revision:
            return False
        try:
            parents = [line[7:].decode("ascii") for line in raw.split(b"\n\n", 1)[0].splitlines()
                       if line.startswith(b"parent ")]
        except UnicodeDecodeError:
            return False
        if index + 1 < len(objects) and objects[index + 1].get("revision") not in parents:
            return False
    return True


def create_ancestry_receipt(
    repo_path: str | Path, base_revision: str, selected_revision: str, *, max_depth: int = 64,
) -> dict[str, Any]:
    """Record an actually read first-parent chain with its raw hashed Git objects."""
    from issueintel.repository.acquire import _run_git

    if type(max_depth) is not int or not 1 <= max_depth <= 256:
        raise ValueError("max_depth must be between 1 and 256")
    if any(not re.fullmatch(r"[0-9a-f]{40}", revision) for revision in (base_revision, selected_revision)):
        raise ValueError("Expected exact full lowercase Git SHAs")
    repo_path = Path(repo_path).resolve()
    chain = _run_git(repo_path, ["rev-list", "--first-parent", f"--max-count={max_depth + 1}",
                                base_revision], 30).stdout.decode("ascii").splitlines()
    if selected_revision not in chain:
        raise ValueError("Selected snapshot is not in the bounded first-parent ancestry")
    verified = _run_git(repo_path, ["merge-base", "--is-ancestor", selected_revision, base_revision],
                        30, allow_failure=True)
    if verified.returncode != 0:
        raise ValueError("Git did not verify the selected snapshot as an ancestor")
    chain = chain[:chain.index(selected_revision) + 1]
    objects = []
    for revision in chain:
        size = int(_run_git(repo_path, ["cat-file", "-s", revision], 30).stdout)
        if size > 64 * 1024:
            raise ValueError("Commit metadata exceeds ancestry receipt limit")
        raw = _run_git(repo_path, ["cat-file", "commit", revision], 30).stdout
        objects.append({"revision": revision, "raw_commit_base64": base64.b64encode(raw).decode("ascii")})
    receipt = {
        "schema_version": 1, "base_revision": base_revision,
        "selected_snapshot_revision": selected_revision,
        "mechanism": "git_first_parent_raw_object_chain",
        "local_git_ancestor_check": {"command": "git merge-base --is-ancestor", "returncode": 0},
        "commit_parent_chain": objects,
        "parent_chain_sha256": hashlib.sha256(_canonical(objects)).hexdigest(),
        "note": "Git ancestry does not establish publication time; a separate historical push receipt does.",
    }
    if not verify_ancestry_receipt(receipt, base_revision, selected_revision):
        raise ValueError("Raw Git object/parent-chain integrity verification failed")
    return receipt


def select_historical_snapshot(
    repository: str, base_revision: str, issue_created_at: str, *,
    cache_dir: str | Path | None = None, max_depth: int = 64, max_archive_hours: int = 3,
    limits: ArchiveLimits | None = None, save_root: str | Path | None = None,
    repository_lock=None,
) -> dict[str, Any]:
    """Find a proven pre-issue ancestor; the official SWE base/gold stay unchanged.

    Dates choose search hours only. Every selected revision needs an exact public
    push observed strictly before issue creation and a verified Git parent chain.
    No result claims to be the exhaustive latest historical snapshot.
    """
    from issueintel.repository.acquire import acquire_repository, _run_git

    _repository(repository)
    if type(max_depth) is not int or not 1 <= max_depth <= 256:
        raise ValueError("max_depth must be between 1 and 256")
    if type(max_archive_hours) is not int or not 1 <= max_archive_hours <= 6:
        raise ValueError("Choose one to six explicit candidate hours")
    query_time = datetime.fromisoformat(utc_timestamp(issue_created_at).replace("Z", "+00:00"))
    lock = repository_lock or nullcontext
    with lock():
        acquired = acquire_repository(repository, base_revision, cache_dir)
        # Git mutation/inspection is serialized; archive I/O below is independent.
        _run_git(acquired.path, ["fetch", f"--depth={max_depth}", "--no-tags", "--no-recurse-submodules",
                                "--", "origin", base_revision], 120)
        revisions = _run_git(acquired.path, ["rev-list", "--first-parent", f"--max-count={max_depth}",
                                           base_revision], 30).stdout.decode("ascii").splitlines()
        hints = _run_git(acquired.path, ["log", "--first-parent", f"--max-count={max_depth}",
                                       "--format=%H %cI", base_revision], 30).stdout.decode("ascii").splitlines()
    groups: dict[datetime, list[str]] = {}
    for entry in hints:
        revision, hint = entry.split(" ", 1)
        hint_time = datetime.fromisoformat(utc_timestamp(hint).replace("Z", "+00:00"))
        if hint_time < query_time:
            groups.setdefault(_hour(hint_time), []).append(revision)
    hours = sorted(groups, reverse=True)[:max_archive_hours]
    result: dict[str, Any] = {
        "status": "no_proven_historical_snapshot", "repository": repository,
        "base_revision": base_revision, "selected_snapshot_revision": None,
        "issue_created_at": utc_timestamp(issue_created_at),
        "history_depth_limit": max_depth, "commits_read": len(revisions),
        "committer_dates_are_search_hints_only": True,
        "candidate_hours": [hour.isoformat().replace("+00:00", "Z") for hour in hours],
        "attempts": [], "revision_proof": None, "ancestry_receipt": None,
        "absence_claim": False,
        "selection_scope": "bounded first-parent ancestors and hinted hours; not exhaustive latest snapshot",
    }
    for hour in hours:
        targets = set(groups[hour])

        def predicate(event: Mapping[str, Any]) -> bool:
            payload, repo = event.get("payload", {}), event.get("repo", {})
            if not isinstance(payload, Mapping) or not isinstance(repo, Mapping):
                return False
            if (event.get("type") != "PushEvent" or event.get("public") is False
                    or str(repo.get("name", "")).lower() != repository.lower() or not event.get("created_at")):
                return False
            observed = datetime.fromisoformat(utc_timestamp(event["created_at"]).replace("Z", "+00:00"))
            shas = {str(commit.get("sha")) for commit in payload.get("commits", []) if isinstance(commit, Mapping)}
            shas.add(str(payload.get("head")))
            return observed < query_time and bool(shas & targets)

        scan = scan_archive_hour(hour, predicate, limits=limits)
        result["attempts"].append({key: value for key, value in scan.items() if key != "match"})
        if not scan["match"]:
            continue
        match = scan["match"]
        payload = match["event"]["payload"]
        witnessed = {str(commit.get("sha")) for commit in payload.get("commits", []) if isinstance(commit, Mapping)}
        witnessed.add(str(payload.get("head")))
        selected = min(witnessed & targets, key=revisions.index)
        with lock():
            try:
                ancestry = create_ancestry_receipt(acquired.path, base_revision, selected, max_depth=max_depth)
            except ValueError:
                if repository_lock is None:
                    raise
                # Another case may have changed shared shallow boundaries while
                # we streamed the archive. Restore this bounded chain and replay.
                _run_git(acquired.path, ["fetch", f"--depth={max_depth}", "--no-tags", "--no-recurse-submodules",
                                        "--", "origin", base_revision], 120)
                ancestry = create_ancestry_receipt(acquired.path, base_revision, selected, max_depth=max_depth)
        result.update({
            "status": "proven_historical_ancestor_selected", "selected_snapshot_revision": selected,
            "match": match, "ancestry_receipt": ancestry,
            "revision_proof": {
                "repository": repository, "revision": selected, "basis": "gharchive_push_event",
                "source_url": match["source_url"], "payload": match["event"],
                "raw_line_sha256": match["raw_line_sha256"],
            },
        })
        _save_match(save_root, result)
        break
    return result
