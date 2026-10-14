"""Inventory validates construction receipts without executing model evaluation."""
from __future__ import annotations

from dataclasses import replace
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from issueintel.annotations.models import Annotation, AbstentionLabel, Hypothesis, Provenance
from issueintel.benchmarks import import_benchmark
from issueintel.benchmarks import inventory
from issueintel.repository.cache import save_snapshot
from issueintel.repository.snapshot import RepositorySnapshot, SourceFile
from issueintel.runtime.io import digest, read_jsonl, write_json


PRIVATE = "PRIVATE_ISSUE_GOLD_SOURCE_SENTINEL"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def corpus(tmp_path):
    upstream = tmp_path / "data/upstream"
    upstream.mkdir(parents=True)
    def record(number, created="2023-01-01T00:00:00Z"):
        return {"instance_id": f"owner__project-{number}", "repo": "owner/project",
                "base_commit": str(number) * 40, "created_at": created,
                "problem_statement": PRIVATE, "patch": PRIVATE, "test_patch": PRIVATE,
                "FAIL_TO_PASS": [], "PASS_TO_PASS": []}
    verified = upstream / "verified.jsonl"
    verified.write_text("".join(json.dumps(record(n)) + "\n" for n in (1, 2)), encoding="utf-8")
    live = upstream / "live.jsonl"
    live.write_text(json.dumps(record(3, "2025-01-01T00:00:00Z")) + "\n", encoding="utf-8")
    import_benchmark(tmp_path, verified_path=verified, live_paths=[live], target_live=1, main_dev_count=1)
    cases = read_jsonl(tmp_path / "data/benchmark/cases.jsonl")
    manifest_path = tmp_path / "data/manifests/dataset.json"
    return SimpleNamespace(root=tmp_path, cases=cases, manifest_sha=sha(manifest_path),
                           manifest_bytes=manifest_path.read_bytes(),
                           dev=next(c for c in cases if c["split"] == "main_dev"),
                           test=next(c for c in cases if c["split"] == "main_test"),
                           temporal=next(c for c in cases if c["split"] == "temporal_candidate"))


def asset(corpus, case, *, index_digest=False):
    source = SourceFile("module.py", "def f():\n    return 1\n",
                        hashlib.sha256(b"def f():\n    return 1\n").hexdigest())
    snapshot = RepositorySnapshot(str(corpus.root / "bare_cache"), case["revision"], "a" * 40,
                                  (source,), "benchmark_base", None,
                                  {"status": "unknown", "historical_certified": False}, (),
                                  "2020-01-01T00:00:00Z", "")
    payload = snapshot.to_dict()
    payload.pop("snapshot_sha256")
    snapshot = replace(snapshot, snapshot_sha256=digest(payload))
    index = save_snapshot(snapshot, corpus.root / "data/derived")
    target = corpus.root / "data/derived/cases" / hashlib.sha256(case["case_id"].encode()).hexdigest()[:24]
    target.mkdir(parents=True)
    (target / "graph.json.gz").write_bytes(gzip.compress(b'{"nodes":[]}', mtime=0))
    write_json(target / "localization.json", {"files": ["module.py"], "unavailable_files": []})
    receipt = {"case_id": case["case_id"], "repository": case["repository"], "revision": case["revision"],
               "manifest_sha256": corpus.manifest_sha, "snapshot_sha256": snapshot.snapshot_sha256,
               "snapshot_index": index.relative_to(corpus.root).as_posix(), "protocol": "benchmark_base",
               "strict_issue_time_verified": False,
               "files": {n: sha(target / n) for n in ("graph.json.gz", "localization.json")}}
    if index_digest:
        receipt["snapshot_index_sha256"] = sha(index)
    write_json(target / "build_receipt.json", receipt)
    return SimpleNamespace(target=target, index=index, receipt=receipt, source=source)


def annotation(corpus, *, judged=False):
    case = corpus.dev
    target = corpus.root / "data/annotations/automatic" / hashlib.sha256(case["case_id"].encode()).hexdigest()[:24]
    provenance = Provenance("llm", "synthetic_generation", provider="synthetic", model="synthetic")
    value = Annotation(case["case_id"], [
        Hypothesis(f"H{i}", f"Distinct synthetic explanation {i}", ["E1"], [], "not_found", "Check input",
                   correctness="uncertain" if judged else "unverified",
                   correctness_provenance=provenance if judged else None)
        for i in range(1, 4)
    ], AbstentionLabel("uncertain", "No execution", provenance), provenance, ["E1"],
        judgment={"method": "synthetic_judge", "kind": "llm", "independent_human": False} if judged else {})
    write_json(target / "annotation.json", value.to_dict())
    write_json(target / "localization.json", {})
    write_json(target / "variants.json", [])
    write_json(target / "generation_inputs.json", {"case": case, "evidence": [{"id": "E1"}]})
    receipt = {"case_id": case["case_id"], "split": "main_dev", "manifest_sha256": corpus.manifest_sha,
               "kind": "automatic_dataset_construction", "temporal_protocol": "benchmark_base",
               "strict_issue_time_verified": False, "human_reviewed": False, "judgment_completed": judged,
               "provider": "synthetic", "model": "synthetic",
               "files": {n: sha(target / n) for n in (
                   "annotation.json", "localization.json", "variants.json", "generation_inputs.json")}}
    write_json(target / "receipt.json", receipt)
    return SimpleNamespace(target=target, receipt=receipt)


def overlay(corpus, case, status, *, name="temporal_audit", strict=False):
    bundle = corpus.root / "data/annotations/temporal_receipts" / (digest(status) + ".json")
    write_json(bundle, {"private_issue_body": PRIVATE})
    row = {"case_id": case["case_id"], "case_sha256": digest(case),
           "benchmark_manifest_sha256": corpus.manifest_sha, "status": status,
           "strict_issue_time_receipt_checks_passed": strict,
           "receipt_bundle": {"path": bundle.relative_to(corpus.root).as_posix(), "sha256": sha(bundle)}}
    row["audit_id"] = digest(row)
    path = corpus.root / "data/annotations" / (name + ".jsonl")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    return row, path


def test_current_status_is_derived_and_does_not_rewrite_bootstrap(corpus, monkeypatch):
    for case in corpus.cases:
        asset(corpus, case, index_digest=True)
    annotation(corpus, judged=True)
    calls = []
    actual = inventory.verify_manifest
    monkeypatch.setattr(inventory, "verify_manifest", lambda root: (calls.append(root), actual(root))[1])
    result = inventory.verified_inventory(corpus.root)
    assert len(calls) == 1
    assert result["base_assets"]["verified_by_split"] == {"main_dev": 1, "main_test": 1, "temporal_candidate": 1}
    assert result["base_assets"]["snapshot_index_checks"]["index_file_digest_checked"] == 3
    assert result["automatic_dev_annotations"]["generation_completed"] == 1
    assert result["automatic_dev_annotations"]["judgment_completed"] == 1
    assert result["manifest"]["bootstrap_annotation_status"]["llm_hypotheses_generated"] == 0
    assert not result["automatic_dev_annotations"]["human_validation_claimed"]
    assert result["evaluation_authorizations_recorded"] == 0
    assert PRIVATE not in json.dumps(result)
    assert (corpus.root / "data/manifests/dataset.json").read_bytes() == corpus.manifest_bytes


def test_changed_generation_artifact_is_not_counted_complete(corpus):
    saved = annotation(corpus)
    assert inventory.verified_inventory(corpus.root)["automatic_dev_annotations"]["judgment_pending"] == 1
    (saved.target / "annotation.json").write_text(PRIVATE, encoding="utf-8")
    summary = inventory.verified_inventory(corpus.root)["automatic_dev_annotations"]
    assert summary["generation_completed"] == 0
    assert summary["invalid_receipts"] == 1
    assert summary["errors"][0]["reason"] == "artifact_digest_mismatch"
    assert PRIVATE not in json.dumps(summary)


def test_forged_judgment_completion_flag_is_not_a_completed_stage(corpus):
    saved = annotation(corpus)
    saved.receipt["judgment_completed"] = True
    write_json(saved.target / "receipt.json", saved.receipt)
    summary = inventory.verified_inventory(corpus.root)["automatic_dev_annotations"]
    assert summary["judgment_completed"] == 0
    assert summary["invalid_receipts"] == 1
    assert summary["errors"][0]["reason"] == "judgment_stage_artifact_missing"


@pytest.mark.parametrize("field,value,reason", [
    ("commit_sha", "f" * 40, "snapshot_index_identity_mismatch"),
    ("snapshot_sha256", "0" * 64, "snapshot_index_identity_mismatch"),
    ("protocol", "issue_time", "base_snapshot_protocol_mismatch"),
])
def test_index_identity_and_protocol_are_checked_without_blob_loading(corpus, field, value, reason):
    saved = asset(corpus, corpus.dev)
    index = json.loads(gzip.decompress(saved.index.read_bytes()))
    index[field] = value
    saved.index.write_bytes(gzip.compress(json.dumps(index).encode(), mtime=0))
    summary = inventory.verified_inventory(corpus.root)["base_assets"]
    assert summary["verified_receipts"] == 0
    assert summary["errors"][0]["reason"] == reason


def test_deep_scope_detects_blob_corruption_fast_scope_does_not_claim_it(corpus):
    saved = asset(corpus, corpus.dev)
    blob = corpus.root / "data/derived/blobs" / saved.source.sha256[:2] / (saved.source.sha256 + ".txt.gz")
    blob.write_bytes(gzip.compress(b"corrupt", mtime=0))
    fast = inventory.verified_inventory(corpus.root)["base_assets"]
    assert fast["verified_receipts"] == 1
    assert fast["snapshot_index_checks"]["scope"] == "index_metadata_bindings_only"
    assert fast["snapshot_index_checks"]["full_restored_snapshot_checked"] == 0
    deep = inventory.verified_inventory(corpus.root, deep_snapshot_validation=True)["base_assets"]
    assert deep["invalid_receipts"] == 1
    assert deep["verified_receipts"] == 0
    assert PRIVATE not in json.dumps(deep)


def test_inventory_rejects_path_escape_before_reading_external_artifact(corpus):
    saved = asset(corpus, corpus.dev)
    saved.receipt["files"]["../../../../outside.json"] = "a" * 64
    write_json(saved.target / "build_receipt.json", saved.receipt)
    summary = inventory.verified_inventory(corpus.root)["base_assets"]
    assert summary["errors"][0]["reason"] == "unsafe_inventory_path"
    assert summary["verified_receipts"] == 0


def test_holdout_annotation_receipt_is_rejected_without_opening_predictions(corpus, monkeypatch):
    target = corpus.root / "data/annotations/automatic" / hashlib.sha256(corpus.test["case_id"].encode()).hexdigest()[:24]
    write_json(target / "receipt.json", {"case_id": corpus.test["case_id"], "split": "main_test",
                                        "manifest_sha256": corpus.manifest_sha})
    heldout_payload = target / "annotation.json"
    original = Path.open
    def guarded_open(path, *args, **kwargs):
        assert path != heldout_payload, "Inventory must not open held-out predictions"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    summary = inventory.verified_inventory(corpus.root)["automatic_dev_annotations"]
    assert summary["errors"][0]["reason"] == "automatic_annotation_not_main_dev"
    assert summary["generation_completed"] == 0


def test_latest_recorded_audit_replaces_prior_claim_without_becoming_verified_cohort(corpus):
    overlay(corpus, corpus.temporal, "strict_receipt_checks_passed", strict=True)
    overlay(corpus, corpus.temporal, "post_cutoff_dates_confirmed_snapshot_unverified")
    overlay(corpus, corpus.dev, "issue_dates_confirmed_snapshot_unverified", name="historical_audit")
    result = inventory.verified_inventory(corpus.root)
    temporal = result["temporal_audits"]
    assert temporal["events_recorded"] == 2
    assert temporal["latest_cases_recorded"] == temporal["latest_receipt_bindings_verified"] == 1
    assert temporal["recorded_status_counts"] == {"post_cutoff_dates_confirmed_snapshot_unverified": 1}
    assert temporal["recorded_strict_receipt_checks_passed"] == 0
    assert not temporal["provenance_validators_rerun"]
    assert not temporal["usable_temporal_cohort_claimed"]
    assert result["historical_audits"]["latest_cases_recorded"] == 1


def test_invalid_latest_audit_does_not_fall_back_to_old_strict_claim(corpus):
    row, path = overlay(corpus, corpus.temporal, "strict_receipt_checks_passed", strict=True)
    row["status"] = PRIVATE
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    summary = inventory.verified_inventory(corpus.root)["temporal_audits"]
    assert summary["recorded_strict_receipt_checks_passed"] == 0
    assert summary["latest_receipt_bindings_verified"] == 0
    assert summary["errors"][0]["reason"] == "overlay_event_digest_mismatch"
    assert PRIVATE not in json.dumps(summary)


def test_pinned_integrity_failure_is_not_silenced(corpus):
    with (corpus.root / "data/benchmark/cases.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{}\n")
    with pytest.raises(ValueError, match="Manifest integrity failure"):
        inventory.verified_inventory(corpus.root)


from test_workflow import imported_fixture
from test_construction import fixture as construction_fixture


def test_registered_stage_inventory_counts_completion_without_parsing_predictions(construction_fixture, monkeypatch):
    from issueintel.benchmarks import construction
    f = construction_fixture
    monkeypatch.setattr(construction, "GroqProvider", lambda config: f.provider)
    record = construction.register_annotation_recipe(f.root, protocol="benchmark_base", splits=["main_dev"])
    rid = record["recipe_sha256"]
    assert construction.annotate_dataset(f.root, rid)["completed"] == [f.case["case_id"]]
    original = inventory._object
    def metadata_only(path, **kwargs):
        assert path.name not in {"annotation.generated.json", "annotation.judged.json", "pack.generated.json"}
        return original(path, **kwargs)
    monkeypatch.setattr(inventory, "_object", metadata_only)
    summary = inventory.verified_inventory(f.root)["registered_annotations"]
    entry = summary["recipes"][0]
    assert summary["verified_recipe_records"] == 1
    assert entry["completed_cases"] == 1 and entry["stage_counts"]["judge"] == 1
    assert entry["current_code_and_config_resumable"] is True
    assert summary["evaluation_performed"] is False


def test_registered_stage_corruption_is_visible_and_does_not_count_as_complete(construction_fixture, monkeypatch):
    from issueintel.benchmarks import construction
    f = construction_fixture
    monkeypatch.setattr(construction, "GroqProvider", lambda config: f.provider)
    rid = construction.register_annotation_recipe(f.root, protocol="benchmark_base", splits=["main_dev"])["recipe_sha256"]
    construction.annotate_dataset(f.root, rid)
    folder = f.root / "data/annotations/dataset" / rid / hashlib.sha256(f.case["case_id"].encode()).hexdigest()[:24]
    (folder / "annotation.judged.json").write_text("{}", encoding="utf-8")
    entry = inventory.verified_inventory(f.root)["registered_annotations"]["recipes"][0]
    assert entry["completed_cases"] == 0 and entry["invalid_stage_receipts"] == 1
