"""Content-addressed positive event cache; bounded scans never imply absence."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from collections.abc import Mapping

from issueintel.runtime.io import canonical_json, digest, write_json
from .sources import inside_root, sha256_file


@contextmanager
def cache_archive_events(root, repositories):
    from .history import ARCHIVE_CACHE
    context = (Path(root).resolve(), frozenset(r.lower() for r in repositories))
    token = ARCHIVE_CACHE.set(context)
    try:
        yield
    finally:
        ARCHIVE_CACHE.reset(token)


def scan_cached_hour(hour, predicate, limits, context, scanner):
    from .history import EVENT_OBSERVER, archive_hour_url, _hour
    root, repos = context
    url = archive_hour_url(hour)
    key = digest([url, sorted(repos)])
    directory = inside_root(root, root / "data/development/history_cache")
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(directory / (key + ".lock.sqlite3"), timeout=120)
    pointer = directory / (key + ".index.json")
    try:
        connection.execute("BEGIN IMMEDIATE")
        prior = json.loads(pointer.read_text(encoding="utf-8")) if pointer.exists() else {"url": url, "batches": []}
        if prior["url"] != url:
            raise ValueError("Archive cache hour binding changed")
        for reference in prior["batches"]:
            path = inside_root(root, directory / reference["file"])
            if sha256_file(path) != reference["sha256"]:
                raise ValueError("Archive cache bytes changed")
            batch = json.loads(path.read_text(encoding="utf-8"))
            for match in batch["matches"]:
                raw = match["raw_event_line"].encode()
                if (match["source_url"] != url or hashlib.sha256(raw).hexdigest() != match["raw_line_sha256"]
                        or json.loads(raw) != match["event"] or digest(match["event"]) != match["event_sha256"]):
                    raise ValueError("Archive event cache integrity failure")
                if predicate(match["event"]):
                    return {**batch["scan"], "status": "matched_event", "match": match,
                            "cache_hit": True, "elapsed_seconds": 0, "absence_claim": False}
            if batch["scan"].get("coverage_complete") and batch["scan"].get("archive_scanned_to_end"):
                return {**batch["scan"], "status": "no_match_in_scanned_hour", "match": None,
                        "cache_hit": True, "elapsed_seconds": 0, "absence_claim": False}
        captured = []
        def observe(event, line, number, source_url, archive_hour):
            repo, payload = event.get("repo", {}), event.get("payload", {})
            if (not isinstance(repo, Mapping) or not isinstance(payload, Mapping)
                    or str(repo.get("name", "")).lower() not in repos or event.get("public") is False
                    or event.get("type") not in {"IssuesEvent", "PushEvent"}
                    or (event.get("type") == "IssuesEvent" and payload.get("action") != "opened")):
                return
            captured.append({"event": dict(event), "raw_event_line": line.decode("utf-8"),
                "raw_line_sha256": hashlib.sha256(line).hexdigest(), "event_sha256": digest(event),
                "source_url": source_url, "archive_hour": archive_hour, "line_number": number})
        token = EVENT_OBSERVER.set(observe)
        try:
            result = scanner(hour, predicate, limits=limits)
        finally:
            EVENT_OBSERVER.reset(token)
        batch = {"scan": {k: v for k, v in result.items() if k != "match"}, "matches": captured}
        name = digest(batch) + ".batch.json"
        path = directory / name
        if path.exists():
            if json.loads(path.read_text(encoding="utf-8")) != batch:
                raise ValueError("Immutable archive cache conflict")
        else:
            write_json(path, batch)
        ref = {"file": name, "sha256": sha256_file(path)}
        if ref not in prior["batches"]:
            prior["batches"].append(ref)
            write_json(pointer, prior)
        connection.commit()
        return {**result, "cache_hit": False}
    finally:
        connection.close()
