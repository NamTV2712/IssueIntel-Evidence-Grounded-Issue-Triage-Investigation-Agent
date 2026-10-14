"""Synthetic import tests: real gold never reaches the investigator projection."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from issueintel.benchmarks import UpstreamRecord, import_benchmark
from issueintel.benchmarks.sources import SourceFile, _download, inside_root
from issueintel.benchmarks.temporal import (
    append_temporal_audit, audit_temporal_case, import_temporal_metadata,
)


def upstream(instance: str, *, repo: str = "example/project", revision: str = "a" * 40,
             created: str = "2023-01-01T00:00:00Z", **changes) -> dict:
    return {
        "instance_id": instance, "repo": repo, "base_commit": revision,
        "problem_statement": f"Synthetic failure for {instance}", "created_at": created,
        "patch": "GOLD PATCH SECRET", "test_patch": "GOLD TEST SECRET",
        "FAIL_TO_PASS": '["tests/test_bug.py::test_gold"]', "PASS_TO_PASS": ["test_existing"],
        "hints_text": "SOLUTION HINT SECRET", "all_hints_text": "OTHER GOLD SECRET",
        "eval_script": "REFERENCE EVALUATION SECRET", "difficulty": "15 min - 1 hour",
        **changes,
    }


def write_rows(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def annotation_zip(path: Path, rows: list[dict]) -> Path:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=[
        "instance_id", "underspecified", "underspecified_notes", "filter_out", "difficulty"
    ])
    writer.writeheader()
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ensembled_annotations_public.csv", stream.getvalue())
        archive.writestr("__MACOSX/._ensembled_annotations_public.csv", "ignored")
    return path


def test_normalizes_legacy_and_current_rows_without_inventing_issue_time():
    record = UpstreamRecord.from_mapping(
        upstream("example__project-12", created=datetime(2024, 3, 1, 12)),
        source="verified", source_revision="source-sha",
    )
    assert record.pull_created_at == "2024-03-01T12:00:00Z"
    assert record.issue_created_at is None
    assert record.issue_url is None
    assert record.gold["fail_to_pass"] == ["tests/test_bug.py::test_gold"]
    assert record.gold["pass_to_pass"] == ["test_existing"]
    public = record.public_projection("main_dev")
    assert "SECRET" not in json.dumps(public)
    assert not {"patch", "test_patch", "hints_text", "eval_script", "gold"} & public.keys()
    assert record.gold_projection()["patch"] == "GOLD PATCH SECRET"


def test_issue_url_uses_actual_issue_numbers_not_the_pr_number():
    record = UpstreamRecord.from_mapping(
        upstream("example__project-12", issue_numbers=["7"]),
        source="live", source_revision="source-sha",
    )
    assert record.issue_url == "https://github.com/example/project/issues/7"
    assert record.issue_created_at is None
    multiple = UpstreamRecord.from_mapping(
        upstream("example__project-12", issue_numbers=["7", "8"]),
        source="live", source_revision="source-sha",
    )
    assert multiple.issue_url is None


@pytest.mark.parametrize("changes", [
    {"repo": "../../escape"}, {"base_commit": "main"},
    {"created_at": ""}, {"FAIL_TO_PASS": "not-json"},
    {"issue_numbers": ["not-an-issue"]},
])
def test_rejects_invalid_source_identity_and_dates(changes):
    with pytest.raises(ValueError):
        UpstreamRecord.from_mapping(
            upstream("example__project-12", **changes), source="verified", source_revision="sha"
        )


def test_import_separates_gold_and_joins_human_ordinal_not_abstention(tmp_path):
    verified = write_rows(tmp_path / "verified.jsonl", [
        upstream("example__project-1", revision="a" * 40),
        upstream("example__project-2", revision="b" * 40),
    ])
    annotations = annotation_zip(tmp_path / "annotations.zip", [
        {"instance_id": "example__project-1", "underspecified": "0.0",
         "underspecified_notes": "GOLD ROOT CAUSE SECRET", "filter_out": "False", "difficulty": "easy"},
        {"instance_id": "example__project-2", "underspecified": "1.0",
         "underspecified_notes": "GOLD ROOT CAUSE SECRET", "filter_out": "False", "difficulty": "easy"},
    ])
    manifest = import_benchmark(
        tmp_path, verified_path=verified, annotations_path=annotations, target_live=0, main_dev_count=1
    )
    public = (tmp_path / "data/benchmark/cases.jsonl").read_text(encoding="utf-8")
    assert "SECRET" not in public
    gold = read_rows(tmp_path / "data/benchmark/gold.jsonl")
    assert all(record["patch"] == "GOLD PATCH SECRET" for record in gold)
    human = read_rows(tmp_path / "data/annotations/source_human.jsonl")
    assert [row["underspecified_ordinal"] for row in human] == [0, 1]
    assert all(row["abstention_label"] is None for row in human)
    assert "SECRET" not in json.dumps(human)
    assert manifest["annotation_status"]["issueintel_human_reviewed"] == 0
    assert manifest["temporal_validation"]["strict_issue_time_validated"] is False
    assert manifest["access_policy"]["evaluation_performed"] is False
    sources = json.loads((tmp_path / "data/manifests/sources.json").read_text())
    assert sources["sources"][0]["revision_validated"] is False
    assert sources["sources"][0]["files"][0]["provenance"] == "user_supplied"
    for descriptor in manifest["files"].values():
        content = (tmp_path / descriptor["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == descriptor["sha256"]


def test_grouped_split_never_shares_base_or_parent_and_is_input_order_invariant(tmp_path):
    rows = [
        upstream("example__project-1", revision="a" * 40),
        upstream("example__project-2", revision="a" * 40),
        upstream("example__project-3", revision="b" * 40, parent_instance_id="example__project-2"),
        upstream("example__project-4", revision="c" * 40),
        upstream("example__project-5", revision="d" * 40),
    ]
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_path = write_rows(first / "input.jsonl", rows)
    second_path = write_rows(second / "input.jsonl", list(reversed(rows)))
    import_benchmark(first, verified_path=first_path, target_live=0, main_dev_count=3)
    import_benchmark(second, verified_path=second_path, target_live=0, main_dev_count=3)
    first_cases = read_rows(first / "data/benchmark/cases.jsonl")
    second_cases = read_rows(second / "data/benchmark/cases.jsonl")
    assert first_cases == second_cases
    splits = {row["instance_id"]: row["split"] for row in first_cases}
    assert splits["example__project-1"] == splits["example__project-2"] == splits["example__project-3"]
    assert sum(row["split"] == "main_dev" for row in first_cases) == 3


def test_selects_latest_nonduplicate_pr_candidates_without_issue_time_claim(tmp_path):
    main = upstream("example__project-1", created="2025-08-01T00:00:00Z")
    verified = write_rows(tmp_path / "verified.jsonl", [main])
    live = write_rows(tmp_path / "live.jsonl", [
        main,
        upstream("example__project-old", created="2024-06-30T23:59:59Z"),
        upstream("example__project-2", created="2025-01-01T00:00:00Z"),
        upstream("example__project-3", created="2025-07-01T00:00:00Z"),
        upstream("example__project-4", created="2025-07-01T00:00:00Z"),
    ])
    manifest = import_benchmark(tmp_path, verified_path=verified, live_paths=[live], target_live=2)
    selected = [row for row in read_rows(tmp_path / "data/benchmark/cases.jsonl")
                if row["split"] == "temporal_candidate"]
    assert [row["instance_id"] for row in selected] == ["example__project-3", "example__project-4"]
    assert all(row["issue_created_at"] is None for row in selected)
    assert manifest["selection"]["date_field"] == "pull_created_at"
    assert manifest["selection"]["contamination_free_claim"] is False
    assert manifest["selection"]["issue_time_validated"] is False
    assert manifest["selection"]["eligible"] == 3


def test_shortfall_is_visible_instead_of_backfilling_old_tasks(tmp_path):
    verified = write_rows(tmp_path / "verified.jsonl", [upstream("example__project-1")])
    live = write_rows(tmp_path / "live.jsonl", [upstream("example__project-2", created="2024-01-01T00:00:00Z")])
    manifest = import_benchmark(tmp_path, verified_path=verified, live_paths=[live], target_live=300)
    assert manifest["counts"]["temporal_candidate"] == 0
    assert manifest["selection"]["shortfall"] == 300


def test_duplicate_instances_rejected_before_any_manifest_is_written(tmp_path):
    row = upstream("example__project-1")
    verified = write_rows(tmp_path / "verified.jsonl", [row, row])
    with pytest.raises(ValueError, match="Duplicate upstream"):
        import_benchmark(tmp_path, verified_path=verified, target_live=0)
    assert not (tmp_path / "data/manifests/dataset.json").exists()


def test_conflicting_live_duplicates_are_excluded_with_audit_and_no_arbitrary_gold(tmp_path):
    verified = write_rows(tmp_path / "verified.jsonl", [upstream("example__project-1")])
    live = write_rows(tmp_path / "live.jsonl", [
        upstream("example__project-2", created="2025-01-01T00:00:00Z", hints_text="variant A"),
        upstream("example__project-2", created="2025-01-01T00:00:00Z", hints_text="variant B"),
        upstream("example__project-3", created="2025-01-01T00:00:00Z"),
    ])
    manifest = import_benchmark(tmp_path, verified_path=verified, live_paths=[live], target_live=2)
    temporal = [case for case in read_rows(tmp_path / "data/benchmark/cases.jsonl")
                if case["split"] == "temporal_candidate"]
    assert [case["instance_id"] for case in temporal] == ["example__project-3"]
    assert manifest["normalization"]["live"]["raw_rows"] == 3
    assert manifest["normalization"]["live"]["usable_unique_instances"] == 1
    assert manifest["normalization"]["live"]["duplicate_decisions"][0]["policy"] == "excluded_conflicting_id"
    assert manifest["selection"]["shortfall"] == 1


def test_identical_import_is_idempotent_and_modified_cohort_never_overwrites(tmp_path):
    verified = write_rows(tmp_path / "verified.jsonl", [upstream("example__project-1")])
    first = import_benchmark(tmp_path, verified_path=verified, target_live=0)
    assert import_benchmark(tmp_path, verified_path=verified, target_live=0) == first
    saved = (tmp_path / "data/benchmark/cases.jsonl").read_bytes()
    write_rows(verified, [upstream("example__project-2")])
    with pytest.raises(FileExistsError):
        import_benchmark(tmp_path, verified_path=verified, target_live=0)
    assert (tmp_path / "data/benchmark/cases.jsonl").read_bytes() == saved


def test_pinned_download_verifies_bytes_and_rejects_corruption(tmp_path, monkeypatch):
    payload = b"source fixture"
    source = SourceFile("verified", "fixture", "revision", "fixture.bin",
                        hashlib.sha256(payload).hexdigest(), len(payload), "https://example.invalid/fixture")
    calls = []

    def fake_open(request, timeout):
        calls.append(request.full_url)
        return io.BytesIO(payload)

    monkeypatch.setattr("issueintel.benchmarks.sources.urllib.request.urlopen", fake_open)
    destination = _download(tmp_path, source)
    assert destination.read_bytes() == payload
    assert _download(tmp_path, source) == destination
    assert len(calls) == 1
    destination.write_bytes(b"corruption")
    with pytest.raises(ValueError, match="changed"):
        _download(tmp_path, source)
    assert len(calls) == 1


def test_failed_download_does_not_publish_or_leave_partial_source(tmp_path, monkeypatch):
    source = SourceFile("verified", "fixture", "revision", "fixture.bin", "0" * 64, 3,
                        "https://example.invalid/fixture")
    monkeypatch.setattr("issueintel.benchmarks.sources.urllib.request.urlopen",
                        lambda *args, **kwargs: io.BytesIO(b"bad"))
    with pytest.raises(ValueError, match="checksum"):
        _download(tmp_path, source)
    assert not (tmp_path / "data/upstream/verified/revision/fixture.bin").exists()
    assert not list(tmp_path.rglob(".download-*"))


def test_path_containment_rejects_writes_outside_root(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        inside_root(tmp_path, tmp_path / ".." / "outside")


def temporal_case(*, numbers=None, statement="Bug\nThe symptom\n"):
    return {
        "case_id": "live:example__project-9", "instance_id": "example__project-9",
        "repository": "example/project", "revision": "a" * 40,
        "split": "temporal_candidate", "problem_statement": statement,
        "issue_numbers": numbers or ["7"], "issue_created_at": None,
        "pull_created_at": "2025-08-01T00:00:00Z", "issue_url": None,
    }


def issue_receipt(*, number=7, created="2025-01-01T00:00:00Z", original_text=False):
    text = "Bug\nThe symptom\n"
    receipt = {
        "source_url": f"https://api.github.com/repos/example/project/issues/{number}",
        "fetched_at": "2026-10-12T00:00:00Z",
        "payload": {"number": number, "url": f"https://api.github.com/repos/example/project/issues/{number}",
                    "created_at": created, "updated_at": "2025-08-01T00:00:00Z",
                    "title": "Bug", "body": "The symptom"},
    }
    if original_text:
        receipt["body_provenance"] = {
            "kind": "issue_creation_event", "available_at": created,
            "source_url": "https://data.gharchive.org/2025-01-01-0.json.gz",
            "content_sha256": hashlib.sha256(text.encode()).hexdigest(), "history_complete": True,
            "payload": {"type": "IssuesEvent", "repo": {"name": "example/project"},
                        "created_at": created, "payload": {"action": "opened", "issue": dict(receipt["payload"])}},
        }
    return receipt


def push_receipt(*, revision="a" * 40, available="2024-12-31T23:59:59Z"):
    return {
        "repository": "example/project", "revision": revision,
        "basis": "gharchive_push_event", "source_url": "https://data.gharchive.org/2024-12-31-23.json.gz",
        "payload": {"type": "PushEvent", "repo": {"name": "example/project"},
                    "created_at": available, "payload": {"head": revision, "commits": [{"sha": revision}]}},
    }


def cutoff_provenance():
    return {"model_id": "test-model", "source_url": "https://example.org/model-card",
            "conservative_cutoff": "2024-07-01T00:00:00Z"}


def test_issue_receipts_confirm_post_cutoff_dates_but_not_original_text_or_snapshot():
    report = audit_temporal_case(temporal_case(), [issue_receipt()])
    assert report["all_issue_dates_after_cutoff"] is True
    assert report["status"] == "post_cutoff_dates_confirmed_snapshot_unverified"
    assert report["original_query_text_supported"] is False
    assert report["strict_issue_time_receipt_checks_passed"] is False
    assert report["contamination_free_claim"] is False


def test_pr_date_cannot_rescue_an_issue_created_before_model_cutoff():
    report = audit_temporal_case(temporal_case(), [issue_receipt(created="2019-01-01T00:00:00Z")])
    assert report["status"] == "excluded_issue_not_after_cutoff"
    assert report["all_issue_dates_after_cutoff"] is False


def test_strict_revision_boundary_and_exact_identity():
    body = issue_receipt(original_text=True)
    valid = audit_temporal_case(temporal_case(), [body], push_receipt(), cutoff_provenance=cutoff_provenance())
    assert valid["strict_issue_time_receipt_checks_passed"] is True
    equal = audit_temporal_case(temporal_case(), [body], push_receipt(available="2025-01-01T00:00:00Z"),
                                cutoff_provenance=cutoff_provenance())
    assert equal["revision_availability"]["available_before_issue"] is False
    assert equal["strict_issue_time_receipt_checks_passed"] is False
    wrong = audit_temporal_case(temporal_case(), [body], push_receipt(revision="b" * 40),
                                cutoff_provenance=cutoff_provenance())
    assert wrong["revision_availability"]["exact_revision"] is False
    assert wrong["strict_issue_time_receipt_checks_passed"] is False


def test_commit_dates_alone_and_current_edited_body_are_weak_proofs():
    proof = {"repository": "example/project", "revision": "a" * 40,
             "basis": "github_commit_api", "available_at": "2020-01-01T00:00:00Z"}
    report = audit_temporal_case(temporal_case(), [issue_receipt(original_text=True)], proof,
                                cutoff_provenance=cutoff_provenance())
    assert report["revision_availability"]["independent_availability"] is False
    assert "do not prove" in report["revision_availability"]["reason"]
    changed = issue_receipt(original_text=True)
    changed["payload"]["body"] = "Later solution text"
    report = audit_temporal_case(temporal_case(), [changed], push_receipt(),
                                cutoff_provenance=cutoff_provenance())
    assert report["benchmark_statement_matches_receipt_text"] is False
    assert report["original_query_text_supported"] is False


def test_body_timestamp_declaration_without_independent_event_is_not_proof():
    receipt = issue_receipt(original_text=True)
    receipt["body_provenance"].pop("payload")
    report = audit_temporal_case(temporal_case(), [receipt], push_receipt(),
                                cutoff_provenance=cutoff_provenance())
    assert report["original_query_text_supported"] is False
    assert report["strict_issue_time_receipt_checks_passed"] is False


def test_multiple_issue_case_requires_every_date_and_no_future_issue_body():
    case = temporal_case(numbers=["7", "8"], statement="Bug\nThe symptom\nBug\nThe symptom\n")
    incomplete = audit_temporal_case(case, [issue_receipt(original_text=True)])
    assert incomplete["missing_issue_numbers"] == ["8"]
    later = issue_receipt(number=8, created="2025-01-02T00:00:00Z", original_text=True)
    report = audit_temporal_case(case, [issue_receipt(original_text=True), later], push_receipt(),
                                cutoff_provenance=cutoff_provenance())
    assert report["all_issue_dates_after_cutoff"] is True
    assert report["all_linked_issue_text_existed_at_query_time"] is False
    assert report["strict_issue_time_receipt_checks_passed"] is False


def test_a_pr_masquerading_as_issue_and_duplicate_receipts_fail_identity():
    receipt = issue_receipt()
    receipt["payload"]["pull_request"] = {"url": "https://api.github.com/repos/example/project/pulls/7"}
    report = audit_temporal_case(temporal_case(), [receipt])
    assert report["all_issue_dates_after_cutoff"] is False
    duplicated = audit_temporal_case(temporal_case(), [issue_receipt(), issue_receipt()])
    assert duplicated["duplicate_issue_receipts"] == ["7"]
    assert duplicated["all_issue_dates_after_cutoff"] is False


def test_temporal_overlay_is_idempotent_append_only_and_preserves_pinned_manifests(tmp_path):
    verified = write_rows(tmp_path / "main.jsonl", [upstream("example__project-1")])
    live = write_rows(tmp_path / "live.jsonl", [upstream(
        "example__project-9", issue_numbers=["7"], created="2025-08-01T00:00:00Z",
        problem_statement="Bug\nThe symptom\n",
    )])
    import_benchmark(tmp_path, verified_path=verified, live_paths=[live], target_live=1)
    manifest_path = tmp_path / "data/manifests/dataset.json"
    source_path = tmp_path / "data/manifests/sources.json"
    before = manifest_path.read_bytes(), source_path.read_bytes()
    report = append_temporal_audit(tmp_path, "live:example__project-9", [issue_receipt()])
    assert append_temporal_audit(tmp_path, "live:example__project-9", [issue_receipt()]) == report
    overlay = tmp_path / "data/annotations/temporal_audit.jsonl"
    first_line = overlay.read_bytes()
    assert len(read_rows(overlay)) == 1
    updated = issue_receipt(original_text=True)
    append_temporal_audit(tmp_path, "live:example__project-9", [updated], push_receipt(),
                          cutoff_provenance=cutoff_provenance())
    assert len(read_rows(overlay)) == 2
    assert overlay.read_bytes().startswith(first_line)
    assert (manifest_path.read_bytes(), source_path.read_bytes()) == before
    assert not (tmp_path / "data/annotations/temporal_audit.lock").exists()
    input_path = tmp_path / "receipts.json"
    input_path.write_text(json.dumps([issue_receipt()]))
    assert import_temporal_metadata(tmp_path, input_path)["audited_cases"] == 1


def test_temporal_audit_does_not_silence_changed_pinned_data(tmp_path):
    verified = write_rows(tmp_path / "main.jsonl", [upstream("example__project-1")])
    live = write_rows(tmp_path / "live.jsonl", [upstream("example__project-9", issue_numbers=["7"],
                                                       created="2025-08-01T00:00:00Z")])
    import_benchmark(tmp_path, verified_path=verified, live_paths=[live], target_live=1)
    (tmp_path / "data/benchmark/cases.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="Pinned benchmark file changed"):
        append_temporal_audit(tmp_path, "live:example__project-9", [issue_receipt()])


def test_historical_snapshot_override_requires_verified_raw_ancestry_and_its_own_push():
    import base64

    ancestor = b"tree " + b"0" * 40 + b"\nauthor Test <test@example.invalid> 1 +0000\ncommitter Test <test@example.invalid> 1 +0000\n\nold\n"
    selected = hashlib.sha1(b"commit " + str(len(ancestor)).encode() + b"\0" + ancestor).hexdigest()
    base = b"tree " + b"0" * 40 + b"\nparent " + selected.encode() + b"\nauthor Test <test@example.invalid> 2 +0000\ncommitter Test <test@example.invalid> 2 +0000\n\nnew\n"
    base_revision = hashlib.sha1(b"commit " + str(len(base)).encode() + b"\0" + base).hexdigest()
    chain = [{"revision": base_revision, "raw_commit_base64": base64.b64encode(base).decode()},
             {"revision": selected, "raw_commit_base64": base64.b64encode(ancestor).decode()}]
    ancestry = {"base_revision": base_revision, "selected_snapshot_revision": selected,
                "commit_parent_chain": chain,
                "parent_chain_sha256": hashlib.sha256(json.dumps(chain, ensure_ascii=False,
                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    case = temporal_case()
    case["revision"] = base_revision
    proof = push_receipt(revision=selected)
    valid = audit_temporal_case(case, [issue_receipt(original_text=True)], proof,
                                cutoff_provenance=cutoff_provenance(),
                                selected_snapshot_revision=selected, ancestry_receipt=ancestry)
    assert valid["strict_issue_time_receipt_checks_passed"] is True
    assert valid["uses_historical_ancestor"] is True
    assert valid["upstream_base_revision"] == base_revision
    assert valid["selected_snapshot_revision"] == selected
    missing = audit_temporal_case(case, [issue_receipt(original_text=True)], proof,
                                  cutoff_provenance=cutoff_provenance(), selected_snapshot_revision=selected)
    assert missing["snapshot_ancestry_verified"] is False
    assert missing["strict_issue_time_receipt_checks_passed"] is False
    wrong_push = audit_temporal_case(case, [issue_receipt(original_text=True)], push_receipt(revision=base_revision),
                                     cutoff_provenance=cutoff_provenance(), selected_snapshot_revision=selected,
                                     ancestry_receipt=ancestry)
    assert wrong_push["revision_availability"]["exact_revision"] is False
    assert wrong_push["strict_issue_time_receipt_checks_passed"] is False
