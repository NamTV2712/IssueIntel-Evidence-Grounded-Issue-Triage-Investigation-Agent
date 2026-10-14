"""Explicit parent-linked opening-query construction preserves upstream cases."""
import copy
import hashlib
import json

import pytest

from issueintel.benchmarks import asof, temporal
from issueintel.benchmarks.history import _save_match
from issueintel.benchmarks.main_metadata import append_main_mapping, map_verified_case
from issueintel.benchmarks.trac import audit_issue_receipt, fetch_trac_receipt
from test_asof_assets import proof_for
from test_benchmarks import temporal_case, issue_receipt, push_receipt, cutoff_provenance
from test_trac import html, transport
from test_workflow import imported_fixture


def test_original_query_has_distinct_lineage_and_does_not_promote_source_human_label():
    case = dict(temporal_case(), problem_statement="Later title\nLater solution detail\n")
    issue = issue_receipt(original_text=True)
    legacy = temporal.audit_temporal_case(case, [issue], push_receipt(), cutoff_provenance=cutoff_provenance())
    assert legacy["strict_issue_time_receipt_checks_passed"] is False
    report = temporal.audit_temporal_case(case, [issue], push_receipt(), cutoff_provenance=cutoff_provenance(),
                                          query_policy="original_opening_query")
    assert report["strict_issue_time_receipt_checks_passed"] is True
    assert report["upstream_query_matches_selected"] is False
    assert report["source_human_label_scope"] == "upstream_query_only"
    assert report["source_query_sha256"] == hashlib.sha256(case["problem_statement"].encode()).hexdigest()
    bundle = {"query_policy": "original_opening_query", "issue_receipts": [issue]}
    selected = temporal.reconstruct_query_text(case, bundle)
    assert selected == "Bug\nThe symptom\n"
    assert report["query_text_sha256"] == hashlib.sha256(selected.encode()).hexdigest()
    assert "Later solution" in case["problem_statement"]
    assert "query_policy" not in legacy


def test_original_policy_cannot_bypass_opening_proof_or_live_cutoff():
    missing = temporal.audit_temporal_case(temporal_case(), [issue_receipt()], push_receipt(),
        cutoff_provenance=cutoff_provenance(), query_policy="original_opening_query")
    assert missing["strict_issue_time_receipt_checks_passed"] is False
    with pytest.raises(ValueError, match="does not replay"):
        temporal.reconstruct_query_text(temporal_case(), {"query_policy": "original_opening_query", "issue_receipts": [issue_receipt()]})
    old = issue_receipt(original_text=True, created="2023-01-01T00:00:00Z")
    report = temporal.audit_temporal_case(temporal_case(), [old], push_receipt(),
        cutoff_provenance=cutoff_provenance(), query_policy="original_opening_query")
    assert report["all_issue_dates_after_cutoff"] is False
    assert report["strict_issue_time_receipt_checks_passed"] is False


def test_source_query_reconstruction_preserves_exact_original_bytes():
    case = dict(temporal_case(), problem_statement="  exact source\r\n\n")
    assert temporal.reconstruct_query_text(case, {"issue_receipts": []}) == case["problem_statement"]
    with pytest.raises(ValueError, match="policy"):
        temporal.audit_temporal_case(case, [], query_policy="invented_unlimited_query")


def test_strict_source_receipt_converts_to_new_policy_without_any_network(imported_fixture, monkeypatch):
    f = imported_fixture
    original_report = proof_for(f, monkeypatch)
    original_bytes = (f.root / "data/benchmark/cases.jsonl").read_bytes()
    monkeypatch.setattr(asof, "find_issue_opened_event", lambda *a, **k: pytest.fail("Re-downloaded opening proof"))
    monkeypatch.setattr(asof, "select_historical_snapshot", lambda *a, **k: pytest.fail("Re-downloaded publication proof"))
    result = asof.build_asof_receipt(f.root, f.case, query_policy="original_opening_query")
    assert result["reused"] is True
    assert result["audit"]["audit_id"] != original_report["audit_id"]
    assert result["audit"]["query_policy"] == "original_opening_query"
    assert result["audit"]["strict_issue_time_receipt_checks_passed"] is True
    assert (f.root / "data/benchmark/cases.jsonl").read_bytes() == original_bytes
    rows = [json.loads(line) for line in (f.root / "data/annotations/historical_audit.jsonl").read_text().splitlines()]
    assert rows[0] == original_report


def _pending_source_with_retained_event(f, monkeypatch, *, retain_event=True):
    report = proof_for(f, monkeypatch)
    bundle = json.loads((f.root / report["receipt_bundle"]["path"]).read_text())
    current = copy.deepcopy(bundle["issue_receipts"][0])
    current["body_provenance"] = None
    ref = {"repository": f.case["repository"], "number": "7"}
    pull_url = "https://api.github.com/repos/owner/project/pulls/1"
    pull = {"source_url": pull_url, "payload": {"url": pull_url, "number": 1, "body": "Fixes #7",
        "created_at": "2023-01-01T00:00:00Z", "base": {"repo": {"full_name": "owner/project"}}}}
    mapping = map_verified_case(f.case, pull, [current])
    append_main_mapping(f.root, f.case, mapping, {"pull_receipt": pull, "issue_receipts": [current]})
    pending = temporal.append_historical_audit(f.root, f.case["case_id"], [current], bundle["revision_proof"], issue_references=[ref])
    phase = {"reference": ref, "status": "original_text_mismatch" if retain_event else "no_original_event_found", "attempts": []}
    if retain_event:
        event = copy.deepcopy(bundle["issue_receipts"][0]["body_provenance"]["payload"])
        event["payload"]["issue"]["title"] = "Initial symptom before later edits"
        raw = json.dumps(event) + "\n"
        match = {"event": event, "raw_event_line": raw, "raw_line_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "event_sha256": hashlib.sha256(asof._canonical(event)).hexdigest(),
            "source_url": "https://data.gharchive.org/2022-01-01-0.json.gz"}
        retained = {"match": match}
        _save_match(f.root, retained)
        phase["saved_receipt"] = retained["saved_receipt"]
    asof._save_attempt(f.root, {"case_id": f.case["case_id"], "benchmark_manifest_sha256": pending["benchmark_manifest_sha256"],
        "audit_id": pending["audit_id"], "original_body_phase": [phase], "snapshot_phase": None,
        "pending_reasons": ["original_body_unverified:" + phase["status"]], "evaluation_performed": False})
    return pending


def test_saved_mismatched_opening_event_is_reused_for_explicit_original_overlay(imported_fixture, monkeypatch):
    f = imported_fixture
    _pending_source_with_retained_event(f, monkeypatch)
    monkeypatch.setattr(asof, "find_issue_opened_event", lambda *a, **k: pytest.fail("Re-scanned saved event"))
    monkeypatch.setattr(asof, "select_historical_snapshot", lambda *a, **k: pytest.fail("Re-scanned saved push"))
    result = asof.build_asof_receipt(f.root, f.case, query_policy="original_opening_query")
    assert result["audit"]["strict_issue_time_receipt_checks_passed"] is True
    assert result["audit"]["upstream_query_matches_selected"] is False
    bundle = json.loads((f.root / result["audit"]["receipt_bundle"]["path"]).read_text())
    assert temporal.reconstruct_query_text(f.case, bundle).startswith("Initial symptom")
    assert bundle["upstream_issue_receipts"][0]["payload"]["title"] == f.case["problem_statement"]


def test_prior_no_event_search_stays_pending_when_only_query_policy_changes(imported_fixture, monkeypatch):
    f = imported_fixture
    _pending_source_with_retained_event(f, monkeypatch, retain_event=False)
    monkeypatch.setattr(asof, "find_issue_opened_event", lambda *a, **k: pytest.fail("Implicit repeated no-event scan"))
    result = asof.build_asof_receipt(f.root, f.case, query_policy="original_opening_query")
    assert result["audit"]["strict_issue_time_receipt_checks_passed"] is False
    assert "original_body_unverified:prior_bounded_search_pending" in result["pending_reasons"]


def test_trac_original_query_replays_raw_initial_version_and_has_separate_source_identity(tmp_path, monkeypatch):
    transport(monkeypatch, html(summary="Later edited title"), html(initial=True))
    initial = fetch_trac_receipt(7, version=0, save_root=tmp_path)
    receipt = audit_issue_receipt(tmp_path, initial)
    case = dict(temporal_case(), repository="django/django", split="main_dev", problem_statement="Later SWE query")
    ref = {"source_kind": "django_trac", "repository": "django/django", "number": 7}
    push = push_receipt(available="2022-12-31T23:59:59Z")
    push["repository"] = "django/django"
    push["source_url"] = "https://data.gharchive.org/2022-12-31-23.json.gz"
    push["payload"]["repo"]["name"] = "django/django"
    report = temporal.audit_historical_case(case, [receipt], push, issue_references=[ref], query_policy="original_opening_query")
    assert report["strict_issue_time_receipt_checks_passed"] is True
    assert report["issues"][0]["source_kind"] == "django_trac"
    assert report["upstream_query_matches_selected"] is False
    bundle = {"query_policy": "original_opening_query", "issue_receipts": [receipt], "issue_references": [ref]}
    assert temporal.reconstruct_query_text(case, bundle) == initial["payload"]["problem_statement"]
    receipt["raw_html_text"] = receipt["raw_html_text"].replace("Original", "Invented")
    receipt["response_sha256"] = hashlib.sha256(receipt["raw_html_text"].encode()).hexdigest()
    report = temporal.audit_historical_case(case, [receipt], push, issue_references=[ref], query_policy="original_opening_query")
    assert report["strict_issue_time_receipt_checks_passed"] is False
