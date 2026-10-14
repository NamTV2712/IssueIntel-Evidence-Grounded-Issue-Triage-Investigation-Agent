"""Replay source bindings from synthetic receipts, without network or model calls."""
import copy
import hashlib
import json

import pytest

from issueintel.benchmarks.temporal import audit_historical_case, audit_temporal_case
from test_benchmarks import cutoff_provenance, issue_receipt, push_receipt, temporal_case


def strict_report(issue=None, push=None):
    return audit_temporal_case(temporal_case(), [issue or issue_receipt(original_text=True)],
                               push or push_receipt(), cutoff_provenance=cutoff_provenance())


@pytest.mark.parametrize("private_kind", ["issue", "push"])
def test_explicit_private_event_cannot_prove_public_availability(private_kind):
    issue, push = issue_receipt(original_text=True), push_receipt()
    event = issue["body_provenance"]["payload"] if private_kind == "issue" else push["payload"]
    event["public"] = False
    assert strict_report(issue, push)["strict_issue_time_receipt_checks_passed"] is False


def test_legacy_missing_public_flag_and_explicit_public_both_replay():
    assert strict_report()["strict_issue_time_receipt_checks_passed"] is True
    issue, push = issue_receipt(original_text=True), push_receipt()
    issue["body_provenance"]["payload"]["public"] = True
    push["payload"]["public"] = True
    assert strict_report(issue, push)["strict_issue_time_receipt_checks_passed"] is True


@pytest.mark.parametrize("url", [
    "http://data.gharchive.org/2025-01-01-0.json.gz",
    "https://data.gharchive.org/not-an-archive",
    "https://data.gharchive.org/2025-01-02-0.json.gz",
    "https://data.gharchive.org/2025-01-01-1.json.gz",
    "https://data.gharchive.org/2025-01-01-24.json.gz",
    "https://data.gharchive.org/2025-01-01-0.json.gz?source=other",
    "https://data.gharchive.org/2025-01-01-0.json.gz#fragment",
    "https://user@data.gharchive.org/2025-01-01-0.json.gz",
    "https://data.gharchive.org:443/2025-01-01-0.json.gz",
    "https://data.gharchive.org.example/2025-01-01-0.json.gz",
])
def test_original_body_requires_exact_https_archive_hour(url):
    issue = issue_receipt(original_text=True)
    issue["body_provenance"]["source_url"] = url
    assert strict_report(issue)["original_query_text_supported"] is False


@pytest.mark.parametrize("url", [
    "http://data.gharchive.org/2024-12-31-23.json.gz",
    "https://data.gharchive.org/2024-12-31-22.json.gz",
    "https://data.gharchive.org/2025-01-01-0.json.gz",
    "https://data.gharchive.org/not-an-archive",
])
def test_snapshot_push_requires_exact_https_archive_hour(url):
    push = push_receipt()
    push["source_url"] = url
    report = strict_report(push=push)
    assert report["revision_availability"]["independent_availability"] is False
    assert report["strict_issue_time_receipt_checks_passed"] is False


def attach_raw(proof):
    event = proof["payload"]
    raw = json.dumps(event, ensure_ascii=False) + "\n"
    proof["raw_event_line"] = raw
    proof["raw_line_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    proof["event_sha256"] = hashlib.sha256(json.dumps(event, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("kind", ["issue", "push"])
def test_captured_raw_line_must_match_digest_and_event_payload(kind):
    issue, push = issue_receipt(original_text=True), push_receipt()
    proof = issue["body_provenance"] if kind == "issue" else push
    attach_raw(proof)
    assert strict_report(issue, push)["strict_issue_time_receipt_checks_passed"] is True
    proof["raw_event_line"] += " "
    assert strict_report(issue, push)["strict_issue_time_receipt_checks_passed"] is False
    # A matching hash of unrelated bytes still cannot bind this declared event.
    other_event = copy.deepcopy(proof["payload"])
    other_event["repo"]["name"] = "wrong/project"
    proof["raw_event_line"] = json.dumps(other_event) + "\n"
    proof["raw_line_sha256"] = hashlib.sha256(proof["raw_event_line"].encode()).hexdigest()
    assert strict_report(issue, push)["strict_issue_time_receipt_checks_passed"] is False


def test_declared_event_digest_is_verified_even_without_retained_raw_bytes():
    push = push_receipt()
    push["event_sha256"] = "0" * 64
    assert strict_report(push=push)["strict_issue_time_receipt_checks_passed"] is False
    push.pop("event_sha256")
    # Old records retain this digest without the raw line; its presence alone
    # cannot add a raw-byte verification claim to the replayed report.
    push["raw_line_sha256"] = "0" * 64
    assert strict_report(push=push)["strict_issue_time_receipt_checks_passed"] is True


def test_original_issue_event_cannot_refer_to_pull_request_payload():
    issue = issue_receipt(original_text=True)
    issue["body_provenance"]["payload"]["payload"]["issue"]["pull_request"] = {}
    assert strict_report(issue)["strict_issue_time_receipt_checks_passed"] is False


def test_repo_public_event_api_requires_matching_repository_endpoint_and_basis():
    push = push_receipt()
    push["basis"] = "github_push_event"
    push["source_url"] = "https://api.github.com/repos/example/project/events?page=1"
    assert strict_report(push=push)["strict_issue_time_receipt_checks_passed"] is True
    push["source_url"] = "https://api.github.com/repos/other/project/events"
    assert strict_report(push=push)["strict_issue_time_receipt_checks_passed"] is False
    push["source_url"] = "https://api.github.com/repos/example/project/issues/7"
    assert strict_report(push=push)["strict_issue_time_receipt_checks_passed"] is False
    push["source_url"] = "https://data.gharchive.org/2024-12-31-23.json.gz"
    assert strict_report(push=push)["strict_issue_time_receipt_checks_passed"] is False


def test_main_historical_proof_does_not_require_live_model_cutoff():
    case = dict(temporal_case(), split="main_dev")
    issue = issue_receipt(original_text=True)
    created = "2023-01-01T00:00:00Z"
    issue["payload"]["created_at"] = created
    proof = issue["body_provenance"]
    proof["available_at"] = created
    proof["source_url"] = "https://data.gharchive.org/2023-01-01-0.json.gz"
    proof["payload"]["created_at"] = created
    proof["payload"]["payload"]["issue"]["created_at"] = created
    push = push_receipt(available="2022-12-31T23:59:59Z")
    push["source_url"] = "https://data.gharchive.org/2022-12-31-23.json.gz"
    report = audit_historical_case(case, [issue], push,
        issue_references=[{"repository": "example/project", "number": 7}])
    assert report["strict_issue_time_receipt_checks_passed"] is True
    assert report["post_model_cutoff_required"] is False
    assert report["all_issue_dates_after_cutoff"] is False
    assert report["evaluation_performed"] is False


def test_api_payload_digest_mismatch_invalidates_issue_identity():
    issue = issue_receipt(original_text=True)
    issue["payload_sha256"] = "0" * 64
    report = strict_report(issue)
    assert report["issues"][0]["identity_valid"] is False
    assert report["strict_issue_time_receipt_checks_passed"] is False
