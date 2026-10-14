"""Synthetic cohort contracts: source integrity, isolation, temporal and identity gates."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from issueintel.benchmarks import cohorts, importer
from issueintel.benchmarks.models import UpstreamRecord
from issueintel.benchmarks.sources import LIVE_DATASET, LIVE_REVISION, SourceFile
from issueintel.benchmarks.store import load_dev_cases, open_test_split
from issueintel.benchmarks.temporal import _issue_receipt, audit_temporal_case
from issueintel.runtime.io import digest, read_jsonl, write_json


GOLD = "GOLD_ONLY_SENTINEL_DO_NOT_EXPOSE"


def descriptor(root, path):
    raw = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def save(root, path, value):
    write_json(path, value)
    return descriptor(root, path)


def receipt(url, payload):
    return {"source_url": url, "fetched_at": "2026-10-12T00:00:00Z", "payload": payload,
            "payload_sha256": digest(payload), "response_sha256": digest(payload), "body_version_at_creation_verified": False}


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    upstream = tmp_path / "data/upstream"
    upstream.mkdir(parents=True)

    def row(number, date, issue_number=None):
        return {"instance_id": f"owner__project-{number}", "repo": "owner/project", "base_commit": f"{number:040x}",
            "created_at": date, "problem_statement": f"Issue {number}\nbody{number}\n",
            "issue_numbers": [str(issue_number)] if issue_number else [],
            "patch": GOLD + str(number), "test_patch": GOLD + "TEST", "FAIL_TO_PASS": [GOLD + "FAIL"], "PASS_TO_PASS": []}

    verified = upstream / "verified.jsonl"
    verified.write_text("".join(json.dumps(row(number, "2023-01-01T00:00:00Z")) + "\n" for number in (1, 2)), encoding="utf-8")
    live_rows = [row(10, "2025-03-01T00:00:00Z", 110), row(11, "2025-02-01T00:00:00Z", 111),
                 row(12, "2025-01-01T00:00:00Z", 112), row(13, "2024-12-01T00:00:00Z", 113)]
    live = upstream / "live.jsonl"
    live.write_text("".join(json.dumps(value) + "\n" for value in live_rows), encoding="utf-8")
    pin = SourceFile("live", LIVE_DATASET, LIVE_REVISION, "live.jsonl", hashlib.sha256(live.read_bytes()).hexdigest(),
                     live.stat().st_size, "https://synthetic.invalid/pinned-live.jsonl")
    monkeypatch.setattr(importer, "SOURCE_FILES", (pin,))
    monkeypatch.setattr(cohorts, "SOURCE_FILES", (pin,))
    parent = importer.import_benchmark(tmp_path, verified_path=verified, live_paths=[live], target_live=1, main_dev_count=1)
    parent_paths = [tmp_path / value["path"] for value in parent["files"].values()] + [tmp_path / "data/manifests/dataset.json", live, verified]
    parent_bytes = {path: path.read_bytes() for path in parent_paths}
    live_group = next(value for value in json.loads((tmp_path / "data/manifests/sources.json").read_text())["sources"] if value["source"] == "live")
    selected = [UpstreamRecord.from_mapping(value, source="live", source_revision=LIVE_REVISION) for value in live_rows[1:]]
    registration = {"schema_version": 1, "kind": "live_extension_metadata_preregistration",
        "parent_manifest_sha256": descriptor(tmp_path, tmp_path / "data/manifests/dataset.json")["sha256"],
        "source_manifest_sha256": descriptor(tmp_path, tmp_path / "data/manifests/sources.json")["sha256"],
        "live_source_revision": LIVE_REVISION, "source_files": live_group["files"],
        "source_counts": {"raw": 4, "usable": 4, "not_in_current_cohort_eligible": 3},
        "rule": cohorts.SELECTION_RULE, "cutoff": "2024-07-01T00:00:00Z", "limit": 3,
        "candidate_cases": [{k: v for k, v in value.public_projection("temporal_candidate").items() if k != "issue_url"} for value in selected],
        "metadata_columns_read": ["instance_id", "repo", "base_commit", "problem_statement", "created_at", "issue_numbers"],
        "model_calls": 0, "evaluation_performed": False, "annotation_contents_read": False,
        "source_manifest_mutation": False, "opening_query_and_public_snapshot_status": "not_yet_audited"}
    selection = digest(registration)
    base = tmp_path / "data/annotations/live_extension" / selection
    reg_descriptor = save(tmp_path, base / "registration.json", registration)
    snapshot = {"inputs": {name: {"path": "data/annotations/" + name + ".jsonl", "bytes": 0,
        "sha256": hashlib.sha256(b"").hexdigest(), "events": 0} for name in ("main_metadata", "historical_audit", "temporal_audit")},
        "identities": [], "unknowns_are_not_absence": True}
    known_descriptor = save(tmp_path, base / "known_identity_snapshots" / (digest(snapshot) + ".json"), snapshot)
    reports, failures, case_paths = [], [], []
    for record in selected:
        number = int(record.instance_id.rsplit("-", 1)[1])
        pull_url = f"https://api.github.com/repos/owner/project/pulls/{number}"
        alias = number == 13
        canonical = "newowner/project" if alias else "owner/project"
        pull = {"url": f"https://api.github.com/repos/{canonical}/pulls/{number}", "number": number,
            "created_at": record.pull_created_at, "title": "Fix", "body": f"Fixes #{number + 100}",
            "base": {"repo": {"full_name": canonical, "id": 42, "node_id": "REPO42"}}}
        pull_receipt = receipt(pull_url, pull)
        pull_descriptor = save(tmp_path, base / "receipts" / (digest(pull_receipt) + ".json"), pull_receipt)
        if alias:
            failures.append({"case_id": record.case_id, "error_kind": "ValueError", "error": "Live pull metadata identity differs"})
            continue
        issue_number = number + 100
        issue_url = f"https://api.github.com/repos/owner/project/issues/{issue_number}"
        issue = {"url": issue_url, "number": issue_number, "title": f"Issue {number}", "body": f"body{number}",
            "created_at": "2025-01-31T00:00:00Z" if number == 11 else "2023-01-01T00:00:00Z", "updated_at": "2025-03-01T00:00:00Z"}
        issue_receipt = receipt(issue_url, issue)
        issue_descriptor = save(tmp_path, base / "receipts" / (digest(issue_receipt) + ".json"), issue_receipt)
        parsed = _issue_receipt(issue_receipt, "owner/project", str(issue_number))
        ref = {"repository": "owner/project", "number": issue_number, "bases": ["upstream_issue_numbers", "pr_title_body_closing_reference"],
            "created_at": parsed["created_at"], "issue_url": f"https://github.com/owner/project/issues/{issue_number}",
            "receipt_sha256": digest(issue_receipt), "query_text_sha256": parsed["content_sha256"],
            "opening_text_supported_by_receipt": False, "known_current_case_overlap": []}
        status = "post_cutoff_dates_confirmed_historical_proofs_pending" if number == 11 else "excluded_issue_not_after_cutoff"
        partial = {k: v for k, v in record.public_projection("temporal_candidate").items() if k != "issue_url"}
        report = {"schema_version": 1, "selection_sha256": selection, "parent_manifest_sha256": registration["parent_manifest_sha256"],
            "case_id": record.case_id, "case_sha256": digest(partial), "candidate_rank": number - 9,
            "pull_receipt": pull_descriptor, "pull_created_at_source": record.pull_created_at, "pull_created_at_api": record.pull_created_at,
            "mapping_status": "exact_selected_receipt_text_match", "cutoff": registration["cutoff"],
            "all_actual_issue_dates_after_cutoff": number == 11, "selected_issues": [ref], "valid_candidate_issues": [ref],
            "rejected_references": [], "issue_receipts": [issue_descriptor], "temporal_status": status,
            "strict_issue_time": False, "evaluation_performed": False, "model_calls": 0,
            "metadata_uses_future_PR_relations_for_lineage_only": True}
        path = base / "cases" / (hashlib.sha256(record.case_id.encode()).hexdigest() + ".json")
        save(tmp_path, path, report)
        reports.append({"case_id": record.case_id, "path": path.relative_to(tmp_path).as_posix(),
                        "mapping_status": report["mapping_status"], "temporal_status": status})
        case_paths.append(path)
    summary = {"schema_version": 1, "kind": "live_extension_metadata_acquisition", "selection_sha256": selection,
        "registration": reg_descriptor, "known_identity_snapshot": known_descriptor, "source_counts": registration["source_counts"],
        "selected": 3, "audited": 2, "statuses": {"excluded_issue_not_after_cutoff": 1, "post_cutoff_dates_confirmed_historical_proofs_pending": 1},
        "mapping_statuses": {"exact_selected_receipt_text_match": 2}, "unique_selected_issue_identities": 2,
        "known_current_issue_overlap": [], "within_extension_issue_overlap": [], "failures": failures, "case_reports": reports,
        "strict_issue_time_cases": 0, "model_calls": 0, "evaluation_performed": False, "annotation_contents_read": False,
        "parent_manifest_modified": False, "source_modified": False, "current_recipe_modified": False}
    fixture = SimpleNamespace(root=tmp_path, selection=selection, base=base, selected=selected,
        parent=parent, parent_bytes=parent_bytes, source=live, summary=summary, case_paths=case_paths,
        snapshot=snapshot, registration=registration)
    republish_summary(fixture)
    return fixture


def republish_summary(corpus):
    desc = save(corpus.root, corpus.base / "reports" / (digest(corpus.summary) + ".json"), corpus.summary)
    write_json(corpus.root / "artifacts/live_extension_metadata_report.json", {**corpus.summary, "immutable_report": desc})


def registered(corpus):
    return cohorts.register_extension_cohort(corpus.root, corpus.selection)


def forged_manifest(corpus, result, mutate):
    original = corpus.root / result["manifest_path"]
    manifest = json.loads(original.read_text(encoding="utf-8"))
    mutate(manifest)
    sha = digest(manifest)
    target = corpus.root / "data/benchmark/cohorts" / sha
    for descriptor in manifest["files"].values():
        path = target / descriptor["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((original.parent / descriptor["path"]).read_bytes())
    write_json(target / "manifest.json", manifest)
    return sha


def test_register_explicit_view_preserves_parent_and_separates_gold(corpus):
    result = registered(corpus)
    assert result["counts"] == {"candidate_cases": 3, "eligibility": {
        "post_cutoff_dates_confirmed_historical_proofs_pending": 1, "excluded_issue_not_after_cutoff": 1,
        "pending_repository_identity": 1}, "strict_issue_time_cases": 0, "complete_annotations": 0}
    view = cohorts.load_cohort_view(corpus.root, result["cohort_sha256"])
    cases = view.cases()
    assert [case["case_id"] for case in cases] == [record.case_id for record in corpus.selected]
    assert all(case["split"] == "temporal_candidate" and case["issue_created_at"] is None for case in cases)
    assert cases[0]["issue_url"] == "https://github.com/owner/project/issues/111"
    assert GOLD not in json.dumps(cases)
    gold = view.gold_for_construction()
    assert gold == {record.case_id: record.gold_projection() for record in corpus.selected}
    assert GOLD in json.dumps(gold)
    assert view.eligibility()[corpus.selected[-1].case_id]["current_alias_candidate"]["historical_identity_verified"] is False
    assert all(path.read_bytes() == raw for path, raw in corpus.parent_bytes.items())
    assert len(load_dev_cases(corpus.root)) == 1
    assert not (corpus.root / "data/runtime/evaluation_authorizations").exists()
    with pytest.raises(PermissionError, match="Temporal candidates"):
        with open_test_split(corpus.root, split="temporal_candidate", authorization="evaluate held-out test", purpose="synthetic"):
            pass


def test_registration_idempotent(corpus):
    first = registered(corpus)
    assert registered(corpus) == first


def test_pinned_source_mutation_denies_before_publication(corpus):
    raw = corpus.source.read_bytes()
    corpus.source.write_bytes(raw.replace(b"body11", b"Body11"))
    with pytest.raises(ValueError, match="digest mismatch"):
        registered(corpus)
    assert not (corpus.root / "data/benchmark/cohorts").exists()


@pytest.mark.parametrize("field,value", [("case_sha256", "a" * 64), ("cutoff", "2025-01-01T00:00:00Z"),
    ("all_actual_issue_dates_after_cutoff", False), ("model_calls", 1), ("strict_issue_time", True), ("candidate_rank", 999)])
def test_case_receipt_or_temporal_claim_tampering_denied(corpus, field, value):
    path = corpus.case_paths[0]
    report = json.loads(path.read_text(encoding="utf-8"))
    report[field] = value
    write_json(path, report)
    with pytest.raises(ValueError):
        registered(corpus)


@pytest.mark.parametrize("field,value", [("audited", 1), ("selected", 2), ("model_calls", 1)])
def test_readdressed_acquisition_claim_tampering_denied(corpus, field, value):
    corpus.summary[field] = value
    republish_summary(corpus)
    with pytest.raises(ValueError):
        registered(corpus)


def test_unknown_alias_cannot_be_marked_mapped(corpus):
    corpus.summary["failures"] = []
    republish_summary(corpus)
    with pytest.raises(ValueError, match="coverage"):
        registered(corpus)


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(status="strict_release"),
    lambda m: m.update(query_policy="source_query"),
    lambda m: m.update(model_cutoff="2026-01-01T00:00:00Z"),
    lambda m: m.update(source_dataset="untrusted/source"),
    lambda m: m["quality"].update(issueintel_independent_human_checked=True),
    lambda m: m["access_policy"].update(inference="restricted_gold.jsonl"),
    lambda m: m["access_policy"].update(evaluation_authorized=True),
])
def test_readdressed_manifest_cannot_change_policy_or_quality(corpus, mutate):
    result = registered(corpus)
    sha = forged_manifest(corpus, result, mutate)
    with pytest.raises(ValueError, match="boundary"):
        cohorts.verify_cohort(corpus.root, sha)


def test_loader_rechecks_files_after_view_creation(corpus):
    result = registered(corpus)
    view = cohorts.load_cohort_view(corpus.root, result["cohort_sha256"])
    path = view.namespace / "cases.jsonl"
    path.write_bytes(path.read_bytes().replace(b"body11", b"Body11"))
    with pytest.raises(ValueError, match="projection"):
        view.cases()


def test_appended_parent_overlay_events_do_not_invalidate_prefix(corpus):
    result = registered(corpus)
    path = corpus.root / "data/annotations/temporal_audit.jsonl"
    path.write_text('{"later": "outside recorded empty prefix"}\n', encoding="utf-8")
    assert cohorts.verify_cohort(corpus.root, result["cohort_sha256"])["quality"]["identity_overlap_scope"].startswith("recorded_parent")


def test_unrelated_or_refetched_receipts_do_not_change_frozen_cohort(corpus):
    result = registered(corpus)
    path = next((corpus.base / "receipts").glob("*.json"))
    extra = json.loads(path.read_text(encoding="utf-8"))
    extra["fetched_at"] = "2026-10-12T01:00:00Z"
    save(corpus.root, corpus.base / "receipts" / (digest(extra) + ".json"), extra)
    (corpus.base / "receipts/unrelated.json").write_text("not JSON", encoding="utf-8")
    assert cohorts.verify_cohort(corpus.root, result["cohort_sha256"])["counts"]["candidate_cases"] == 3


def test_duplicate_selected_issue_identity_denied(corpus):
    path = corpus.case_paths[0]
    report = json.loads(path.read_text(encoding="utf-8"))
    report["selected_issues"].append(deepcopy(report["selected_issues"][0]))
    write_json(path, report)
    with pytest.raises(ValueError, match="mapping"):
        registered(corpus)


def test_recorded_overlay_prefix_mutation_denied(corpus):
    # A recorded nonempty prefix need not contain identities; newline-boundary
    # corruption must fail before metadata-event interpretation.
    path = corpus.root / "data/annotations/temporal_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\n")
    corpus.snapshot["inputs"]["temporal_audit"].update(bytes=1, sha256=hashlib.sha256(b"\n").hexdigest())
    known = save(corpus.root, corpus.base / "known_identity_snapshots" / (digest(corpus.snapshot) + ".json"), corpus.snapshot)
    corpus.summary["known_identity_snapshot"] = known
    republish_summary(corpus)
    result = registered(corpus)
    path.write_bytes(b"x")
    with pytest.raises(ValueError, match="prefix"):
        cohorts.verify_cohort(corpus.root, result["cohort_sha256"])


def record_known_identity(corpus, number):
    parent_case = next(value for value in read_jsonl(corpus.root / "data/benchmark/cases.jsonl") if value["split"] == "temporal_candidate")
    url = f"https://api.github.com/repos/owner/project/issues/{number}"
    payload = {"url": url, "number": number, "title": "Known parent issue", "body": "body",
               "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z"}
    issue_receipt = receipt(url, payload)
    bundle = {"issue_receipts": [issue_receipt], "revision_proof": None}
    desc = save(corpus.root, corpus.root / "data/annotations/temporal_receipts" / (digest(bundle) + ".json"), bundle)
    event = {**audit_temporal_case(parent_case, [issue_receipt]),
             "case_sha256": digest(parent_case), "benchmark_manifest_sha256": corpus.registration["parent_manifest_sha256"],
             "receipt_bundle": {"path": desc["path"], "sha256": desc["sha256"]}}
    event["audit_id"] = digest(event)
    path = corpus.root / "data/annotations/temporal_audit.jsonl"
    path.write_bytes((json.dumps(event, sort_keys=True) + "\n").encode())
    corpus.snapshot["inputs"]["temporal_audit"].update(bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(), events=1)
    corpus.snapshot["identities"] = [{"repository": "owner/project", "number": number, "case_ids": [parent_case["case_id"]]}]
    known = save(corpus.root, corpus.base / "known_identity_snapshots" / (digest(corpus.snapshot) + ".json"), corpus.snapshot)
    corpus.summary["known_identity_snapshot"] = known
    republish_summary(corpus)
    return path


def test_existing_history_bundle_without_byte_size_replays_and_append_is_allowed(corpus):
    path = record_known_identity(corpus, 110)
    result = registered(corpus)
    with path.open("ab") as stream:
        stream.write(b'{"not_part_of_recorded_prefix":true}\n')
    assert cohorts.verify_cohort(corpus.root, result["cohort_sha256"])["counts"]["candidate_cases"] == 3


def test_known_parent_issue_overlap_is_denied_from_receipt_replay(corpus):
    record_known_identity(corpus, 111)
    # Case-level cached absence is insufficient when the recorded known receipt
    # itself proves that the selected identity belongs to the parent cohort.
    with pytest.raises(ValueError):
        registered(corpus)


def add_same_text_candidate(corpus, *, declare):
    path = corpus.case_paths[0]
    audit = json.loads(path.read_text(encoding="utf-8"))
    source_receipt = json.loads((corpus.root / audit["issue_receipts"][0]["path"]).read_text(encoding="utf-8"))
    value = deepcopy(source_receipt)
    value["source_url"] = "https://api.github.com/repos/owner/project/issues/999"
    value["payload"].update(url=value["source_url"], number=999)
    value["payload_sha256"] = value["response_sha256"] = digest(value["payload"])
    desc = save(corpus.root, corpus.base / "receipts" / (digest(value) + ".json"), value)
    ref = deepcopy(audit["selected_issues"][0])
    ref.update(number=999, issue_url="https://github.com/owner/project/issues/999", receipt_sha256=digest(value),
               bases=["pr_title_body_closing_reference"])
    audit["issue_receipts"].append(desc)
    audit["valid_candidate_issues"].append(ref)
    if declare:
        pull = json.loads((corpus.root / audit["pull_receipt"]["path"]).read_text(encoding="utf-8"))
        pull["payload"]["body"] += "\nFixes #999"
        pull["payload_sha256"] = pull["response_sha256"] = digest(pull["payload"])
        audit["pull_receipt"] = save(corpus.root, corpus.base / "receipts" / (digest(pull) + ".json"), pull)
    else:
        audit["selected_issues"] = [ref]
    write_json(path, audit)


def test_valid_but_unreferenced_issue_cannot_replace_selected_identity(corpus):
    add_same_text_candidate(corpus, declare=False)
    with pytest.raises(ValueError, match="universe"):
        registered(corpus)


def test_two_valid_identical_query_matches_remain_ambiguous(corpus):
    add_same_text_candidate(corpus, declare=True)
    with pytest.raises(ValueError, match="ambiguous"):
        registered(corpus)


def test_gold_cannot_be_changed_even_with_rewritten_local_file_hash(corpus):
    result = registered(corpus)
    original = corpus.root / result["manifest_path"]
    manifest = json.loads(original.read_text(encoding="utf-8"))
    raw = (original.parent / "restricted_gold.jsonl").read_bytes().replace(GOLD.encode(), b"REFERENCE_REPLACED")
    manifest["files"]["restricted_gold"].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    sha = digest(manifest)
    target = corpus.root / "data/benchmark/cohorts" / sha
    target.mkdir(parents=True)
    for key, value in manifest["files"].items():
        (target / value["path"]).write_bytes(raw if key == "restricted_gold" else (original.parent / value["path"]).read_bytes())
    write_json(target / "manifest.json", manifest)
    with pytest.raises(ValueError, match="projection"):
        cohorts.verify_cohort(corpus.root, sha)


def test_registration_rejects_junction_outside_cohort(tmp_path, corpus):
    # Portable filesystem symlink check; skip only when the host cannot create it.
    result = registered(corpus)
    source = corpus.root / result["manifest_path"]
    target = source.parent / "cases.jsonl"
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Host denies symlink creation")
    with pytest.raises(ValueError, match="escapes"):
        cohorts.verify_cohort(corpus.root, result["cohort_sha256"])
