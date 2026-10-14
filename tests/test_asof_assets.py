"""Historical asset integration with synthetic Git/events, no paid calls."""
import hashlib
import json
import subprocess

import pytest

from test_workflow import imported_fixture
from issueintel.benchmarks import asof_assets, workflow
from issueintel.benchmarks.temporal import append_historical_audit
from issueintel.investigation.pipeline import investigate


def proof_for(fixture, monkeypatch):
    created = "2022-01-01T00:00:00Z"
    url = "https://api.github.com/repos/owner/project/issues/7"
    payload = {"number": 7, "url": url, "created_at": created,
               "updated_at": created, "title": fixture.case["problem_statement"], "body": ""}
    text = payload["title"] + "\n\n"
    issue = {"source_url": url, "fetched_at": "2026-01-01T00:00:00Z", "payload": payload,
        "body_provenance": {"kind": "issue_creation_event", "available_at": created,
            "source_url": "https://data.gharchive.org/2022-01-01-0.json.gz", "history_complete": True,
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "payload": {"type": "IssuesEvent", "created_at": created, "repo": {"name": "owner/project"},
                "payload": {"action": "opened", "issue": dict(payload)}}}}
    push = {"repository": "owner/project", "revision": fixture.revision, "basis": "gharchive_push_event",
        "source_url": "https://data.gharchive.org/2021-12-31-23.json.gz",
        "payload": {"type": "PushEvent", "created_at": "2021-12-31T23:00:00Z", "repo": {"name": "owner/project"},
                    "payload": {"head": fixture.revision, "commits": [{"sha": fixture.revision}]}}}
    report = append_historical_audit(fixture.root, fixture.case["case_id"], [issue], push,
                                    issue_references=[{"repository": "owner/project", "number": "7"}])
    assert report["strict_issue_time_receipt_checks_passed"] is True
    monkeypatch.setattr(asof_assets, "acquire_repository", workflow.acquire_repository)
    return report


def test_strict_assets_revalidate_proof_preserve_base_and_share_product_pipeline(imported_fixture, monkeypatch):
    f = imported_fixture
    base = workflow.build_case_assets(f.root, f.case, f.gold)
    base_receipt = json.dumps(base["receipt"], sort_keys=True)
    report = proof_for(f, monkeypatch)
    asset = asof_assets.build_asof_assets(f.root, f.case, f.gold)
    assert asset["snapshot"].protocol == "issue_time"
    assert asset["receipt"]["audit_id"] == report["audit_id"]
    assert asset["localization"]["line_projection_status"] == "exact_old_hunks_in_selected_snapshot"
    pack = investigate(asset["case"], f.repo, snapshot=asset["snapshot"], graph=asset["graph"],
                       protocol="issue_time", query_receipt=asset["query_receipt"])
    assert pack["leakage_audit"]["strict_issue_time_verified"] is True
    assert pack["evidence"][0]["provenance"]["edit_history"] == "opening_event_matched"
    assert pack["annotation"] is None
    resumed = asof_assets.build_asof_assets(f.root, f.case, f.gold)
    assert resumed["snapshot"] == asset["snapshot"]
    assert json.dumps(workflow.build_case_assets(f.root, f.case, f.gold)["receipt"], sort_keys=True) == base_receipt


def test_availability_alone_does_not_claim_original_issue_text(imported_fixture, monkeypatch):
    f = imported_fixture
    proof_for(f, monkeypatch)
    asset = asof_assets.build_asof_assets(f.root, f.case, f.gold)
    pack = investigate(asset["case"], f.repo, snapshot=asset["snapshot"], protocol="issue_time")
    assert pack["leakage_audit"]["snapshot_issue_time_verified"] is True
    assert pack["leakage_audit"]["strict_issue_time_verified"] is False
    wrong = dict(asset["query_receipt"], text_sha256="0" * 64)
    with pytest.raises(ValueError, match="Original-query"):
        investigate(asset["case"], f.repo, snapshot=asset["snapshot"], protocol="issue_time", query_receipt=wrong)


def test_changed_bundle_cannot_reuse_strict_flag(imported_fixture, monkeypatch):
    f = imported_fixture
    report = proof_for(f, monkeypatch)
    path = f.root / report["receipt_bundle"]["path"]
    bundle = json.loads(path.read_text(encoding="utf-8"))
    bundle["revision_proof"]["payload"]["created_at"] = "2022-01-01T00:00:00Z"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="bundle digest"):
        asof_assets.build_asof_assets(f.root, f.case, f.gold)


def test_audit_flag_or_case_identity_tamper_is_rejected(imported_fixture, monkeypatch):
    f = imported_fixture
    proof_for(f, monkeypatch)
    with pytest.raises(ValueError, match="another dataset/case"):
        asof_assets.verified_asof_receipt(f.root, dict(f.case, problem_statement="Different query"))
    path = f.root / "data/annotations/historical_audit.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["selected_snapshot_revision"] = "0" * 40
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="audit digest"):
        asof_assets.verified_asof_receipt(f.root, f.case)


def test_contextless_insertion_never_transfers_base_line_to_changed_ancestor(imported_fixture):
    from issueintel.repository.snapshot import build_snapshot
    from issueintel.repository.graph import build_graph
    from issueintel.benchmarks.localization import extract_gold_localization
    f = imported_fixture
    ancestor = build_snapshot(f.repo, f.revision)
    path = f.repo / "parser.py"
    path.write_text("# later prefix\n" + path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    subprocess.run(["git", "-C", str(f.repo), "add", "parser.py"], check=True)
    subprocess.run(["git", "-C", str(f.repo), "commit", "-qm", "later content"], check=True)
    base = build_snapshot(f.repo, "HEAD")
    patch = "diff --git a/parser.py b/parser.py\n--- a/parser.py\n+++ b/parser.py\n@@ -2,0 +3 @@\n+    value = str(value)\n"
    base_assets = {"snapshot": base, "localization": extract_gold_localization(patch, base).to_dict()}
    result = asof_assets._project_gold_localization(patch, ancestor, build_graph(ancestor), base_assets)
    assert result["files"] == ("parser.py",) and result["insertion_anchors"] == {}
    assert result["line_projection_status"] == "unresolved_historical_content_changed"
    identical = asof_assets._project_gold_localization(patch, base, build_graph(base), base_assets)
    assert identical["line_projection_status"] == "exact_old_hunks_in_selected_snapshot"


def test_original_query_overlay_preserves_pinned_source_and_gold(imported_fixture, monkeypatch):
    f = imported_fixture
    initial = proof_for(f, monkeypatch)
    bundle = json.loads((f.root / initial["receipt_bundle"]["path"]).read_text(encoding="utf-8"))
    receipt = bundle["issue_receipts"][0]
    receipt["payload"]["title"] = "Original parse_value symptom at opening"
    event_issue = receipt["body_provenance"]["payload"]["payload"]["issue"]
    event_issue["title"] = receipt["payload"]["title"]
    opening = receipt["payload"]["title"] + "\n\n"
    receipt["body_provenance"]["content_sha256"] = hashlib.sha256(opening.encode()).hexdigest()
    manifest_before = (f.root / "data/manifests/dataset.json").read_bytes()
    gold_before = (f.root / "data/benchmark/gold.jsonl").read_bytes()
    report = append_historical_audit(f.root, f.case["case_id"], [receipt], bundle["revision_proof"],
        issue_references=bundle["issue_references"], query_policy="original_opening_query")
    assert report["strict_issue_time_receipt_checks_passed"] is True
    assert report["upstream_query_matches_selected"] is False
    assets = asof_assets.build_asof_assets(f.root, f.case, f.gold, query_policy="original_opening_query")
    assert assets["case"]["problem_statement"] == opening
    assert assets["case"]["problem_statement"] != f.case["problem_statement"]
    descriptor = assets["query_variant"]
    variant = json.loads((f.root / descriptor["path"]).read_text(encoding="utf-8"))
    assert variant["parent_case_id"] == f.case["case_id"]
    assert variant["source_human_quality_scope"] == "upstream_query_only"
    assert variant["human_validated_query_variant"] is False
    pack = investigate(assets["case"], f.repo, snapshot=assets["snapshot"], protocol="issue_time",
                       query_receipt=assets["query_receipt"])
    assert pack["leakage_audit"]["strict_issue_time_verified"] is True
    assert pack["evidence"][0]["text"] == opening
    assert (f.root / "data/manifests/dataset.json").read_bytes() == manifest_before
    assert (f.root / "data/benchmark/gold.jsonl").read_bytes() == gold_before
