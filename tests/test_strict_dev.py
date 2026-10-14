import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from issueintel.benchmarks import strict_dev as dev
from issueintel.benchmarks import asof
from issueintel.runtime import io
from test_benchmarks import temporal_case, issue_receipt, push_receipt
from test_asof_assets import proof_for
from test_workflow import imported_fixture


def pure_fixture():
    case = dict(temporal_case(), case_id="verified:example__project-9", split="main_dev")
    issue = issue_receipt(original_text=True)
    issue["body_provenance"]["payload"]["payload"]["issue"]["updated_at"] = issue["payload"]["created_at"]
    bundle = {"issue_receipts": [issue], "revision_proof": push_receipt(),
              "query_policy": "original_opening_query", "selected_snapshot_revision": case["revision"],
              "issue_references": [{"repository": case["repository"], "number": 7}], "ancestry_receipt": None}
    return case, bundle


def authority_and_candidate(case):
    candidate = {"case": case, "source_case_sha256": dev.digest(case), "exclusion_reasons": [],
                 "tier": "A", "origin": "bootstrap_main_dev"}
    authority = {"reserved_issues": [], "reserved_queries": [], "reserved_groups": []}
    return authority, candidate


def test_initial_query_and_pre_issue_proof_need_no_model():
    case, bundle = pure_fixture()
    report, reasons = dev.check_proof(case, bundle)
    assert reasons == [] and report["strict_issue_time_receipt_checks_passed"]


@pytest.mark.parametrize("field", ["patch", "human_rationale", "source_quality", "test_patch"])
def test_private_field_rejected_before_proof(field):
    case, bundle = pure_fixture()
    case[field] = "forbidden sentinel"
    assert dev.check_proof(case, bundle)[1] == ["public_projection_or_dev_membership_invalid"]


@pytest.mark.parametrize("split", ["main_test", "temporal_candidate", "final_test"])
def test_holdout_live_membership_denied(split):
    case, bundle = pure_fixture()
    case["split"] = split
    assert dev.check_proof(case, bundle)[1]


def test_opening_ambiguity_does_not_pass():
    case, bundle = pure_fixture()
    bundle["issue_receipts"][0]["body_provenance"]["payload"]["payload"]["issue"]["updated_at"] = "2025-01-01T00:00:01Z"
    assert "opening_version_ambiguous" in dev.check_proof(case, bundle)[1]


def test_equal_time_and_git_dates_are_not_pre_issue_proof():
    case, bundle = pure_fixture()
    bundle["revision_proof"] = push_receipt(available="2025-01-01T00:00:00Z")
    assert "snapshot_not_pre_issue" in dev.check_proof(case, bundle)[1]
    bundle["revision_proof"]["basis"] = "git_commit_timestamp"
    assert "independent_code_availability_missing" in dev.check_proof(case, bundle)[1]


def test_tier_a_wins_by_provenance_not_semantic_quality():
    case, _ = pure_fixture()
    _, candidate = authority_and_candidate(case)
    assert dev.priority(candidate, {"strict_issue_time_receipt_checks_passed": True}) == ("A", 100)
    assert dev.priority(candidate)[0] == "B"
    candidate["exclusion_reasons"] = ["reserved_issue_identity"]
    assert dev.priority(candidate)[0] == "C"


def test_early_failure_never_reaches_acquisition_or_model(tmp_path, monkeypatch):
    case, _ = pure_fixture()
    authority, candidate = authority_and_candidate(case)
    candidate["exclusion_reasons"] = ["reserved_issue_identity"]
    monkeypatch.setattr(dev, "_acquire", lambda *a, **kw: pytest.fail("acquisition ran"))
    result = dev.process_candidate(tmp_path, "a" * 64, authority, candidate, acquire=True)
    assert result["status"] == "excluded" and result["accepted"] is False


def test_eligibility_gold_human_isolation_and_idempotent_resume(imported_fixture, monkeypatch):
    f = imported_fixture
    proof_for(f, monkeypatch)
    asof.build_asof_receipt(f.root, f.case, query_policy="original_opening_query")
    authority, candidate = authority_and_candidate(f.case)
    monkeypatch.setattr(dev, "acquire_repository", lambda *a, **kw: SimpleNamespace(path=f.repo))
    original_open = Path.open
    forbidden = ["gold.jsonl", "source_human", "human_rationale", "annotations/dataset", "annotations/automatic"]
    def guarded_open(path, *a, **kw):
        assert not any(x in path.as_posix() for x in forbidden), str(path)
        return original_open(path, *a, **kw)
    monkeypatch.setattr(Path, "open", guarded_open)
    from issueintel.investigation.providers import GroqProvider
    monkeypatch.setattr(GroqProvider, "complete_json", lambda *a, **kw: pytest.fail("model ran"))
    first = dev.process_candidate(f.root, "a" * 64, authority, candidate)
    assert first["accepted"]
    receipt = f.root / first["receipt"]["path"]
    original_bytes = receipt.read_bytes()
    monkeypatch.setattr(dev, "_acquire", lambda *a, **kw: pytest.fail("reacquired proof"))
    monkeypatch.setattr(dev, "build_snapshot", lambda *a, **kw: pytest.fail("rebuilt valid snapshot"))
    second = dev.process_candidate(f.root, "a" * 64, authority, candidate, acquire=True)
    assert second["receipt_reused"] and second["snapshot_reused"]
    assert receipt.read_bytes() == original_bytes


def test_corrupt_proof_is_not_silently_rehashed(imported_fixture, monkeypatch):
    f = imported_fixture
    proof_for(f, monkeypatch)
    asof.build_asof_receipt(f.root, f.case, query_policy="original_opening_query")
    authority, candidate = authority_and_candidate(f.case)
    monkeypatch.setattr(dev, "acquire_repository", lambda *a, **kw: SimpleNamespace(path=f.repo))
    row = dev.process_candidate(f.root, "a" * 64, authority, candidate)
    receipt = json.loads((f.root / row["receipt"]["path"]).read_text())
    proof = f.root / receipt["proof"]["path"]
    proof.write_text("{}")
    with pytest.raises(ValueError, match="hash_mismatch"):
        dev.process_candidate(f.root, "a" * 64, authority, candidate)


def test_windows_progress_lock_keeps_authoritative_receipt(tmp_path, monkeypatch):
    receipt = tmp_path / "receipt.json"
    io.write_json(receipt, {"stage": "valid"})
    saved = receipt.read_bytes()
    replace = io.os.replace
    attempts = []
    def locked(source, target):
        attempts.append(Path(target))
        if Path(target).name == "progress.json":
            raise PermissionError("Windows sharing violation")
        return replace(source, target)
    monkeypatch.setattr(io.os, "replace", locked)
    monkeypatch.setattr(io.time, "sleep", lambda _: None)
    fallback = io.write_progress(tmp_path / "progress.json", {"completed": 1})
    assert fallback and len(attempts) == 6
    assert receipt.read_bytes() == saved
    assert json.loads(Path(fallback).read_text())["completed"] == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([receipt.name, Path(fallback).name])


def test_transaction_lock_is_released_on_interruption(tmp_path):
    with pytest.raises(RuntimeError):
        with dev._lock(tmp_path, "same case"):
            raise RuntimeError("worker crashed")
    with dev._lock(tmp_path, "same case"):
        pass


def test_pool_import_reads_only_public_columns_and_replays_membership(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as parquet
    from issueintel.benchmarks.sources import SourceFile, source_path
    case, _ = pure_fixture()
    cases_path = tmp_path / "data/benchmark/cases.jsonl"
    cases_path.parent.mkdir(parents=True)
    cases_path.write_text(json.dumps(case) + "\n")
    io.write_json(tmp_path / "data/manifests/dataset.json", {"files": {"cases": dev._descriptor(tmp_path, cases_path)}})
    fake_source = tmp_path / "fixture.parquet"
    row = {"repo": case["repository"], "instance_id": case["instance_id"], "base_commit": case["revision"],
           "problem_statement": case["problem_statement"], "created_at": case["pull_created_at"], "issue_numbers": ["10"],
           "patch": "GOLD SENTINEL", "human_rationale": "PRIVATE SENTINEL", "hints_text": "PRIVATE HINT"}
    extra = dict(row, instance_id="not_selected", problem_statement="never import this other task")
    parquet.write_table(pa.Table.from_pylist([row, extra]), fake_source)
    source = SourceFile("verified", "synthetic", "pinned", "fixture.parquet", dev.sha256_file(fake_source),
                        fake_source.stat().st_size, "https://example.invalid/pinned")
    path = source_path(tmp_path, source)
    path.parent.mkdir(parents=True)
    path.write_bytes(fake_source.read_bytes())
    monkeypatch.setattr(dev, "SOURCE_FILES", [source])
    code = tmp_path / "src/issueintel/fixture.py"
    code.parent.mkdir(parents=True)
    code.write_text("# synthetic code\n")
    monkeypatch.setattr(dev, "_code", lambda root: {"fixture.py": dev.sha256_file(code)})
    read = parquet.read_table
    calls = []
    def guarded_read(path, *, columns, filters):
        calls.append(columns)
        assert not {"patch", "human_rationale", "hints_text"} & set(columns)
        assert filters == [("instance_id", "in", [case["instance_id"]])]
        return read(path, columns=columns, filters=filters)
    monkeypatch.setattr(parquet, "read_table", guarded_read)
    original_open = Path.open
    def guarded_open(path, *args, **kwargs):
        assert "gold.jsonl" not in str(path) and "source_human" not in str(path)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded_open)
    ref, authority = dev.register_pool(tmp_path)
    assert len(authority["candidates"]) == 1 and calls
    identifier = Path(ref["path"]).stem
    assert dev.load_pool(tmp_path, identifier) == authority
    altered = copy.deepcopy(authority)
    altered["candidates"][0]["case"]["problem_statement"] = "fabricated"
    altered_ref = dev._publish(tmp_path, tmp_path / "data/development/pools", altered)
    with pytest.raises(ValueError, match="does not replay"):
        dev.load_pool(tmp_path, Path(altered_ref["path"]).stem)


def test_cached_gzip_bytes_are_rehashed_even_if_mtime_is_restored(tmp_path):
    import gzip
    import os
    from issueintel.repository.cache import _read_gzip_bounded
    path = tmp_path / "blob.gz"
    path.write_bytes(gzip.compress(b"first", mtime=0))
    stamp = path.stat()
    assert _read_gzip_bounded(path, 1000) == b"first"
    path.write_bytes(gzip.compress(b"other", mtime=0))
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert _read_gzip_bounded(path, 1000) == b"other"


def test_audit_all400_freezes_true_smaller_count_and_resume_is_immutable(tmp_path, monkeypatch):
    cases = [{"case": {"case_id": f"verified:synthetic-{i:03}", "split": "main_dev"},
              "tier": "A" if i < 2 else "B"} for i in range(400)]
    authority = {"candidates": cases, "purpose": "development_only_never_final_test", "sources": [],
                 "parent_manifest": {}, "code_bindings": {"synthetic": "bound"}}
    pool_id = "a" * 64
    monkeypatch.setattr(dev, "load_pool", lambda *args: authority)
    monkeypatch.setattr(dev, "_code", lambda *args: authority["code_bindings"])
    verified = []
    monkeypatch.setattr(dev, "verify_eligibility_receipt", lambda root, case, ref, identifier: verified.append(case["case_id"]))
    visited = []
    def process(root, identifier, authority, candidate, **kwargs):
        case_id = candidate["case"]["case_id"]
        visited.append(case_id)
        i = int(case_id.rsplit("-", 1)[1])
        return {"case_id": case_id, "accepted": i < 2, "status": "accepted" if i < 2 else "pending",
                "tier": candidate["tier"], "receipt": {}, "reasons": [] if i < 2 else ["missing_proof"]}
    monkeypatch.setattr(dev, "process_candidate", process)
    first = dev.build_strict_dev(tmp_path, pool_id=pool_id, concurrency=4)
    assert first["main_dev_total"] == 400 and len(visited) == 400
    assert first["accepted_strict_dev"] == 2 and first["pending"] == 398
    assert first["cohort_sha256"] and len(verified) == 2
    second = dev.build_strict_dev(tmp_path, pool_id=pool_id, concurrency=4)
    assert second["cohort_sha256"] == first["cohort_sha256"]


@pytest.mark.parametrize("target", [1, 399, 500])
def test_target_expansion_or_partial_scope_is_not_authorized(tmp_path, target):
    with pytest.raises(ValueError, match="exactly 400"):
        dev.build_strict_dev(tmp_path, target=target)


def test_main_test_cannot_be_relabelled_dev(imported_fixture, monkeypatch):
    f = imported_fixture
    original = dict(f.case, split="main_test")
    cases = f.root / "data/benchmark/cases.jsonl"
    cases.write_text(json.dumps(original) + "\n")
    manifest_path = f.root / "data/manifests/dataset.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["cases"] = dev._descriptor(f.root, cases)
    io.write_json(manifest_path, manifest)
    authority, candidate = authority_and_candidate(f.case)
    monkeypatch.setattr(dev, "_acquire", lambda *args, **kw: pytest.fail("forbidden acquisition"))
    with pytest.raises(ValueError, match="relabeling/expansion"):
        dev.process_candidate(f.root, "a" * 64, authority, candidate, acquire=True)
