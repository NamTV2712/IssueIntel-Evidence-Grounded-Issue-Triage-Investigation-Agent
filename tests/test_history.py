"""Bounded historical acquisition tests with tiny synthetic gzip streams."""

from __future__ import annotations

import gzip
import base64
import hashlib
import io
import json
import urllib.error
from pathlib import Path

import pytest

from issueintel.benchmarks.history import (
    ArchiveLimits, archive_hour_url, find_issue_opened_event, find_revision_push_event,
    scan_archive_hour,
    create_ancestry_receipt, verify_ancestry_receipt,
)


def opened(number=7, *, body="Original symptom", repository="example/project"):
    return {"id": "1", "type": "IssuesEvent", "repo": {"name": repository},
            "created_at": "2025-01-01T01:02:03Z", "public": True,
            "payload": {"action": "opened", "issue": {
                "number": number, "title": "Bug", "body": body,
                "url": f"https://api.github.com/repos/{repository}/issues/{number}",
                "created_at": "2025-01-01T01:02:03Z",
            }}}


def push(*, head="a" * 40, before="b" * 40, created="2025-01-01T01:02:02Z"):
    return {"type": "PushEvent", "repo": {"name": "example/project"}, "created_at": created,
            "payload": {"head": head, "before": before, "commits": []}, "public": True}


def archive(monkeypatch, rows, *, raw=None):
    content = raw if raw is not None else b"".join((json.dumps(row) + "\n").encode() for row in rows)
    compressed = gzip.compress(content)
    requests = []

    def opener(request, **kwargs):
        requests.append(request.full_url)
        return io.BytesIO(compressed)

    monkeypatch.setattr("issueintel.benchmarks.history.urllib.request.urlopen", opener)
    return content, compressed, requests


def test_hour_uses_utc_and_official_unpadded_filename():
    assert archive_hour_url("2025-01-01T08:02:03+07:00") == "https://data.gharchive.org/2025-01-01-1.json.gz"
    assert archive_hour_url("2025-01-01-01") == "https://data.gharchive.org/2025-01-01-1.json.gz"
    with pytest.raises(ValueError):
        archive_hour_url("2025-01-01-25")


def test_saves_only_matched_raw_event_and_supports_matching_original_text(tmp_path, monkeypatch):
    first = opened(number=8)
    target = opened()
    content, _, requests = archive(monkeypatch, [first, target, opened(number=9)])
    result = find_issue_opened_event("example/project", 7, "2025-01-01T01:02:03Z",
                                     expected_problem_statement="Bug\nOriginal symptom\n", save_root=tmp_path)
    assert result["status"] == "original_event_found"
    assert result["original_body_supported_for_task"] is True
    assert result["body_provenance"]["payload"] == target
    assert len(requests) == 1
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1
    retained = json.loads(files[0].read_text(encoding="utf-8"))
    assert retained["event"] == target
    assert json.loads(retained["raw_event_line"]) == target
    assert hashlib.sha256(retained["raw_event_line"].encode()).hexdigest() == retained["raw_line_sha256"]
    assert len(files[0].read_bytes()) < len(content) * 3
    assert result["attempts"][0]["archive_scanned_to_end"] is False
    assert result["absence_claim"] is False


def test_changed_or_unspecified_task_text_does_not_receive_original_body_proof(monkeypatch):
    archive(monkeypatch, [opened()])
    changed = find_issue_opened_event("example/project", 7, "2025-01-01T01:02:03Z",
                                      expected_problem_statement="Bug\nLater solution added\n")
    assert changed["status"] == "original_text_mismatch"
    assert changed["body_provenance"] is None
    assert changed["original_body_supported_for_task"] is False
    unspecified = find_issue_opened_event("example/project", 7, "2025-01-01T01:02:03Z")
    assert unspecified["problem_statement_matches"] is None
    assert unspecified["body_provenance"] is None


@pytest.mark.parametrize("limits, expected_status", [
    (ArchiveLimits(max_compressed_bytes=8), "compressed_byte_cap"),
    (ArchiveLimits(max_uncompressed_bytes=8), "uncompressed_byte_cap"),
    (ArchiveLimits(max_line_bytes=8), "line_byte_cap"),
    (ArchiveLimits(max_records=1), "record_cap"),
])
def test_byte_line_record_caps_stop_without_retaining_the_hour(monkeypatch, limits, expected_status):
    archive(monkeypatch, [opened(number=8), opened()])
    result = scan_archive_hour("2025-01-01-1", lambda event: False, limits=limits)
    assert result["status"] == expected_status
    assert result["match"] is None
    assert result["coverage_complete"] is False
    assert result["absence_claim"] is False
    assert result["compressed_bytes_read"] <= limits.max_compressed_bytes
    assert result["uncompressed_bytes_read"] <= limits.max_uncompressed_bytes


def test_time_cap_stops_before_processing(monkeypatch):
    archive(monkeypatch, [opened()])
    values = iter([0.0, 1.0, 2.0])
    monkeypatch.setattr("issueintel.benchmarks.history.time.monotonic", lambda: next(values))
    result = scan_archive_hour("2025-01-01-1", lambda event: True, limits=ArchiveLimits(max_seconds=0.5))
    assert result["status"] == "time_cap"
    assert result["records_read"] == 0


def test_complete_no_match_has_full_digest_but_no_global_absence_claim(monkeypatch):
    _, compressed, _ = archive(monkeypatch, [opened(number=8)])
    result = scan_archive_hour("2025-01-01-1", lambda event: False)
    assert result["archive_scanned_to_end"] is True
    assert result["coverage_complete"] is True
    assert result["compressed_sha256_full"] == hashlib.sha256(compressed).hexdigest()
    assert result["absence_claim"] is False


def test_malformed_records_leave_coverage_incomplete(monkeypatch):
    archive(monkeypatch, [], raw=b"not-json\n[]\n")
    result = scan_archive_hour("2025-01-01-1", lambda event: False)
    assert result["archive_scanned_to_end"] is True
    assert result["malformed_records"] == 2
    assert result["coverage_complete"] is False


def test_missing_archive_is_not_an_absence_proof(monkeypatch):
    def missing(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 404, "missing", {}, None)
    monkeypatch.setattr("issueintel.benchmarks.history.urllib.request.urlopen", missing)
    result = scan_archive_hour("2025-01-01-1", lambda event: False)
    assert result["status"] == "archive_unavailable"
    assert result["http_status"] == 404
    assert result["absence_claim"] is False


def test_issue_identity_date_and_explicit_hour_bounds(monkeypatch):
    wrong_date = opened()
    wrong_date["payload"]["issue"]["created_at"] = "2025-01-02T01:02:03Z"
    archive(monkeypatch, [opened(repository="wrong/project"), opened(number=8), wrong_date])
    result = find_issue_opened_event("example/project", 7, "2025-01-01T01:02:03Z")
    assert result["match"] is None
    with pytest.raises(ValueError, match="broad archive"):
        find_issue_opened_event("example/project", 7, "2025-01-01T01:02:03Z", hour_offsets=(0, 2))


def test_public_push_proof_requires_exact_sha_and_strict_time(monkeypatch):
    target = push()
    archive(monkeypatch, [push(head="c" * 40), target])
    result = find_revision_push_event("example/project", "a" * 40, "2025-01-01-1",
                                      before="2025-01-01T01:02:03Z")
    assert result["revision_proof"]["payload"] == target
    equal = find_revision_push_event("example/project", "a" * 40, "2025-01-01-1",
                                     before="2025-01-01T01:02:02Z")
    assert equal["revision_proof"] is None
    # The 'before' SHA is not treated as publication at this push time.
    before_only = find_revision_push_event("example/project", "b" * 40, "2025-01-01-1")
    assert before_only["revision_proof"] is None


def test_push_commit_list_supports_old_events_without_a_head_match(monkeypatch):
    event = push(head="c" * 40)
    event["payload"]["commits"] = [{"sha": "a" * 40}]
    archive(monkeypatch, [event])
    result = find_revision_push_event("example/project", "a" * 40, "2025-01-01-1")
    assert result["revision_proof"] is not None


def synthetic_ancestry():
    ancestor = b"tree " + b"0" * 40 + b"\nauthor Test <test@example.invalid> 1 +0000\ncommitter Test <test@example.invalid> 1 +0000\n\nold version\n"
    ancestor_sha = hashlib.sha1(b"commit " + str(len(ancestor)).encode() + b"\0" + ancestor).hexdigest()
    base = b"tree " + b"0" * 40 + b"\nparent " + ancestor_sha.encode() + b"\nauthor Test <test@example.invalid> 2 +0000\ncommitter Test <test@example.invalid> 2 +0000\n\nnew version\n"
    base_sha = hashlib.sha1(b"commit " + str(len(base)).encode() + b"\0" + base).hexdigest()
    chain = [{"revision": base_sha, "raw_commit_base64": base64.b64encode(base).decode()},
             {"revision": ancestor_sha, "raw_commit_base64": base64.b64encode(ancestor).decode()}]
    digest = hashlib.sha256(json.dumps(chain, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()
    return {"base_revision": base_sha, "selected_snapshot_revision": ancestor_sha,
            "commit_parent_chain": chain, "parent_chain_sha256": digest}


def test_raw_git_objects_and_parent_links_verify_ancestry_without_trusting_a_flag():
    receipt = synthetic_ancestry()
    base, selected = receipt["base_revision"], receipt["selected_snapshot_revision"]
    assert verify_ancestry_receipt(receipt, base, selected) is True
    assert verify_ancestry_receipt({"base_revision": base, "selected_snapshot_revision": selected,
                                    "ancestor": True}, base, selected) is False
    receipt["commit_parent_chain"][0]["raw_commit_base64"] = base64.b64encode(b"modified commit").decode()
    # Re-hashing metadata cannot make a different Git object have the pinned OID.
    receipt["parent_chain_sha256"] = hashlib.sha256(json.dumps(receipt["commit_parent_chain"],
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert verify_ancestry_receipt(receipt, base, selected) is False


def test_receipt_binds_both_original_base_and_selected_snapshot():
    receipt = synthetic_ancestry()
    assert verify_ancestry_receipt(receipt, "a" * 40, receipt["selected_snapshot_revision"]) is False
    assert verify_ancestry_receipt(receipt, receipt["base_revision"], "b" * 40) is False


def test_create_ancestry_reads_real_git_objects_in_a_disposable_fixture(tmp_path):
    import subprocess
    import shutil

    if shutil.which("git") is None:
        pytest.skip("Git required for the disposable object fixture")
    repo = tmp_path / "fixture.git"
    subprocess.run(["git", "init", "--bare", "--quiet", "--template=", str(repo)], check=True)
    receipt = synthetic_ancestry()
    for entry in reversed(receipt["commit_parent_chain"]):
        raw = base64.b64decode(entry["raw_commit_base64"])
        written = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
                                 input=raw, capture_output=True, check=True).stdout.decode().strip()
        assert written == entry["revision"]
    actual = create_ancestry_receipt(repo, receipt["base_revision"], receipt["selected_snapshot_revision"])
    assert verify_ancestry_receipt(actual, receipt["base_revision"], receipt["selected_snapshot_revision"])
    assert actual["local_git_ancestor_check"]["returncode"] == 0


def test_bad_gzip_is_declared_unknown(monkeypatch):
    monkeypatch.setattr("issueintel.benchmarks.history.urllib.request.urlopen",
                        lambda *args, **kwargs: io.BytesIO(b"not a gzip archive"))
    result = scan_archive_hour("2025-01-01-1", lambda event: True)
    assert result["status"] == "archive_read_failed"
    assert result["coverage_complete"] is False
    assert result["absence_claim"] is False
