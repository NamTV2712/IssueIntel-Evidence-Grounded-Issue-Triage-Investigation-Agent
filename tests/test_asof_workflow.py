"""Batch limits and pending/strict branches avoid unintended network or inference."""
import json
import hashlib

import pytest

from test_workflow import imported_fixture
from test_asof_assets import proof_for
from issueintel.benchmarks import asof_workflow
from issueintel.benchmarks.temporal import append_historical_audit


def test_batch_reuses_only_revalidated_strict_receipts(imported_fixture, monkeypatch):
    f = imported_fixture
    proof_for(f, monkeypatch)
    monkeypatch.setattr(asof_workflow, "build_asof_receipt", lambda *a, **k: pytest.fail("Reacquired completed proof"))
    result = asof_workflow.prepare_asof(f.root, limit=1)
    assert result["attempted"] == 0 and not result["failures"]
    assert result["skipped"][0]["reason"] == "verified_historical_assets_reused"
    assert result["evaluation_performed"] is False


def test_pending_attempt_is_preserved_until_explicit_retry(imported_fixture, monkeypatch):
    f = imported_fixture
    cid = f.case["case_id"]
    strict = proof_for(f, monkeypatch)
    bundle = json.loads((f.root / strict["receipt_bundle"]["path"]).read_text(encoding="utf-8"))
    row = append_historical_audit(f.root, cid, bundle["issue_receipts"], None, issue_references=bundle["issue_references"])
    folder = f.root / "data/annotations"
    content = (json.dumps({"audit_id": row["audit_id"], "case_id": cid,
        "benchmark_manifest_sha256": row["benchmark_manifest_sha256"], "pending_reasons": ["archive_missing"]}) + "\n").encode()
    attempt = folder / "asof_attempts" / (hashlib.sha256(content).hexdigest() + ".json")
    attempt.parent.mkdir(exist_ok=True)
    attempt.write_bytes(content)
    calls = []
    def pending(*args, **kwargs):
        calls.append(kwargs)
        return {"case_id": cid, "status": row["status"], "pending_reasons": ["archive_missing"], "audit": row}
    monkeypatch.setattr(asof_workflow, "build_asof_receipt", pending)
    result = asof_workflow.prepare_asof(f.root, limit=1)
    assert not calls and result["pending"][0]["reused_pending_attempt"] is True
    retried = asof_workflow.prepare_asof(f.root, limit=1, retry_pending=True)
    assert len(calls) == 1 and retried["attempted"] == 1
    assert retried["completed"] == [] and retried["evaluation_performed"] is False


def test_heldout_metadata_scope_does_not_implicitly_open_test(imported_fixture):
    with pytest.raises(ValueError, match="explicit all_splits"):
        asof_workflow.prepare_asof(imported_fixture.root, split="main_test")
    assert not (imported_fixture.root / "data/runtime/evaluation_authorizations").exists()


def test_inventory_path_traversal_denied_before_file_read(imported_fixture):
    from issueintel.benchmarks.workflow import _inventory
    with pytest.raises(ValueError, match="local filenames"):
        _inventory(imported_fixture.root, ["../.env"])
