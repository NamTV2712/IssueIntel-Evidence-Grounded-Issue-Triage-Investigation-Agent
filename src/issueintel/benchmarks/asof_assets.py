"""Build a separate historical layer from revalidated, immutable receipts.

The upstream base/gold and the original asset receipts remain unchanged. An
older public ancestor may be selected, but its relation to the upstream base
must be proved by the historical auditor before repository acquisition.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from issueintel.repository.acquire import acquire_repository
from issueintel.repository.cache import load_snapshot, save_snapshot
from issueintel.repository.graph import build_graph
from issueintel.repository.snapshot import build_snapshot
from issueintel.runtime.io import digest, read_jsonl, write_json
from .localization import contextless_insertion_paths, extract_gold_localization
from .sources import inside_root
from .store import verify_manifest
from .workflow import _inventory, _verify_inventory, build_case_assets


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _project_gold_localization(patch, snapshot, graph, base):
    """Keep file targets while refusing unproved cross-revision coordinates."""
    original = base["localization"]
    historical_files = {file.path: file.sha256 for file in snapshot.files}
    original_files = {file.path: file.sha256 for file in base["snapshot"].files}
    try:
        localization = extract_gold_localization(patch, snapshot, graph).to_dict()
        for path in contextless_insertion_paths(patch):
            if path in historical_files and historical_files[path] != original_files.get(path):
                raise ValueError("Contextless insertion anchor cannot be transferred to changed historical content")
        localization["line_projection_status"] = "exact_old_hunks_in_selected_snapshot"
        return localization
    except ValueError as error:
        if not any(marker in str(error) for marker in ("no unique exact hunk alignment", "old-side content differs",
                                                       "Contextless insertion anchor")):
            raise
        return {"files": original["files"], "changed_lines": {}, "insertion_anchors": {},
            "symbols": [], "unavailable_files": [name for name in original["files"] if name not in historical_files],
            "patch_sha256": original["patch_sha256"], "hunk_alignments": [],
            "line_projection_status": "unresolved_historical_content_changed",
            "limitations": list(original["limitations"]) + [
                "Original base line/symbol coordinates cannot be assigned to the historical snapshot without exact alignment."]}


def verified_asof_receipt(root: Path | str, case: dict, *, audit_id: str | None = None,
                          query_policy: str = "source_query") -> dict:
    """Recompute the audit rather than trusting a saved ``strict: true`` flag."""
    root = Path(root).resolve()
    if query_policy not in {"source_query", "original_opening_query"}:
        raise ValueError("Unknown historical query policy")
    manifest_path = root / "data/manifests/dataset.json"
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    overlay_name = "temporal_audit.jsonl" if case["split"] == "temporal_candidate" else "historical_audit.jsonl"
    overlay = root / "data/annotations" / overlay_name
    rows = read_jsonl(overlay) if overlay.exists() else []
    matches = [row for row in rows if row.get("case_id") == case["case_id"]
               and (audit_id is None or row.get("audit_id") == audit_id)
               and row.get("query_policy", "source_query") == query_policy
               and row.get("strict_issue_time_receipt_checks_passed") is True]
    if not matches:
        raise ValueError("No verified historical receipt for this case; base assets remain non-strict")
    report = matches[-1]
    payload = {key: value for key, value in report.items() if key != "audit_id"}
    if digest(payload) != report.get("audit_id"):
        raise ValueError("Historical audit digest mismatch")
    if report.get("benchmark_manifest_sha256") != manifest_sha or report.get("case_sha256") != digest(case):
        raise ValueError("Historical audit belongs to another dataset/case")
    descriptor = report["receipt_bundle"]
    bundle_path = inside_root(root, root / descriptor["path"])
    if not bundle_path.is_relative_to(root / "data/annotations"):
        raise ValueError("Historical receipt bundle must stay in private annotations")
    raw = bundle_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
        raise ValueError("Historical receipt bundle digest mismatch")
    bundle = json.loads(raw)
    common = {"selected_snapshot_revision": bundle.get("selected_snapshot_revision"),
              "ancestry_receipt": bundle.get("ancestry_receipt")}
    if query_policy != "source_query":
        common["query_policy"] = query_policy
    if case["split"] == "temporal_candidate":
        from .temporal import audit_temporal_case
        checked = audit_temporal_case(case, bundle["issue_receipts"], bundle.get("revision_proof"),
            model_cutoff=report["configured_model_cutoff"], cutoff_provenance=bundle.get("cutoff_provenance"), **common)
    else:
        from .temporal import audit_historical_case
        checked = audit_historical_case(case, bundle["issue_receipts"], bundle.get("revision_proof"),
            issue_references=bundle["issue_references"], **common)
    if any(report.get(key) != value for key, value in checked.items()):
        raise ValueError("Saved historical audit differs from independently recomputed checks")
    if not checked.get("strict_issue_time_receipt_checks_passed"):
        raise ValueError("Historical receipt no longer passes strict checks")
    return {"report": report, "bundle": bundle}


def build_asof_assets(root: Path | str, case: dict, gold: dict, *, audit_id: str | None = None,
                      query_policy: str = "source_query") -> dict:
    """Dataset construction only: no model predictions or evaluation metrics."""
    root = Path(root).resolve()
    verify_manifest(root)
    proof = verified_asof_receipt(root, case, audit_id=audit_id, query_policy=query_policy)
    report, bundle = proof["report"], proof["bundle"]
    selected = report["selected_snapshot_revision"]
    case_key = hashlib.sha256(case["case_id"].encode()).hexdigest()[:24]
    target = root / "data/derived/asof" / case_key / report["audit_id"]
    selected_text = case["problem_statement"]
    query_variant = None
    if query_policy == "original_opening_query":
        from .temporal import reconstruct_query_text
        selected_text = reconstruct_query_text(case, bundle)
        selected_sha = hashlib.sha256(selected_text.encode()).hexdigest()
        if report.get("query_text_sha256") != selected_sha:
            raise ValueError("Opening query digest differs from revalidated historical report")
        query_variant = {"schema_version": 1, "parent_case_id": case["case_id"], "parent_case_sha256": digest(case),
            "query_policy": query_policy, "source_query_sha256": hashlib.sha256(case["problem_statement"].encode()).hexdigest(),
            "query_text_sha256": selected_sha, "problem_statement": selected_text, "created_at": report["query_time"],
            "audit_id": report["audit_id"], "receipt_bundle_sha256": report["receipt_bundle"]["sha256"],
            "split": case["split"], "gold_parent_case_id": case["case_id"],
            "human_validated_query_variant": False, "source_human_quality_scope": "upstream_query_only"}
        query_path = root / "data/annotations/original_queries" / (digest(query_variant) + ".json")
        if query_path.exists() and _read_json(query_path) != query_variant:
            raise ValueError("Original-query overlay content-addressed artifact changed")
        if not query_path.exists():
            write_json(query_path, query_variant)
        query_variant = {"path": query_path.relative_to(root).as_posix(),
                         "sha256": hashlib.sha256(query_path.read_bytes()).hexdigest()}
    runtime_case = dict(case, revision=selected, issue_created_at=report["query_time"], problem_statement=selected_text)
    references = [row["source_url"] for row in report["issues"]]
    if len(references) == 1:
        runtime_case["issue_url"] = references[0].replace("https://api.github.com/repos/", "https://github.com/")
    availability = {"commit_sha": selected, "verified": True,
        "source_id": report["revision_availability"]["source_url"],
        "available_at": report["revision_availability"]["available_at"],
        "audit_id": report["audit_id"], "receipt_bundle_sha256": report["receipt_bundle"]["sha256"]}
    query_receipt = {"verified": True, "case_id": case["case_id"],
        "text_sha256": hashlib.sha256(selected_text.encode()).hexdigest(),
        "created_at": report["query_time"], "source_urls": references,
        "audit_id": report["audit_id"], "receipt_bundle_sha256": report["receipt_bundle"]["sha256"]}
    if query_policy != "source_query":
        query_receipt.update(query_policy=query_policy, source_query_sha256=hashlib.sha256(case["problem_statement"].encode()).hexdigest(),
                             query_variant=query_variant)
    path = target / "build_receipt.json"
    if path.exists():
        receipt = _read_json(path)
        expected = {"case_id": case["case_id"], "revision": selected,
                    "upstream_base_revision": case["revision"], "audit_id": report["audit_id"],
                    "manifest_sha256": report["benchmark_manifest_sha256"], "protocol": "issue_time",
                    "strict_issue_time_verified": True, "query_receipt": query_receipt}
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise ValueError("Historical asset identity/protocol mismatch")
        if set(receipt.get("files", {})) != {"localization.json", "graph.json.gz"}:
            raise ValueError("Historical asset inventory must contain both registered outputs")
        _verify_inventory(target, receipt["files"])
        index = inside_root(root, root / receipt["snapshot_index"])
        if not index.is_relative_to(root / "data/derived"):
            raise ValueError("Historical snapshot index escapes derived data")
        snapshot = load_snapshot(index, root / "data/derived")
        if (snapshot.snapshot_sha256 != receipt["snapshot_sha256"] or snapshot.commit_sha != selected
                or snapshot.protocol != "issue_time" or snapshot.cutoff != report["query_time"]
                or snapshot.availability.get("receipt") != availability):
            raise ValueError("Historical snapshot digest/availability mismatch")
        graph = build_graph(snapshot)
        localization = _read_json(target / "localization.json")
        base = build_case_assets(root, case, gold)
        expected_localization = _project_gold_localization(gold["patch"], snapshot, graph, base)
        expected_localization.update({"upstream_base_revision": case["revision"],
            "selected_snapshot_revision": selected, "upstream_localization_sha256": digest(base["localization"])})
        if digest(localization) != digest(expected_localization):
            raise ValueError("Historical localization no longer passes projection checks; preserve original receipt and repair explicitly")
    else:
        repository = acquire_repository(case["repository"], selected, root / "data/repos")
        snapshot = build_snapshot(repository.path, selected, cutoff=report["query_time"],
                                  protocol="issue_time", availability=availability)
        graph = build_graph(snapshot)
        index = save_snapshot(snapshot, root / "data/derived")
        base = build_case_assets(root, case, gold)
        localization = _project_gold_localization(gold["patch"], snapshot, graph, base)
        localization["upstream_base_revision"] = case["revision"]
        localization["selected_snapshot_revision"] = selected
        localization["upstream_localization_sha256"] = digest(base["localization"])
        write_json(target / "localization.json", localization)
        graph_record = graph.to_dict()
        graph_record["nodes"] = [{key: value for key, value in node.to_dict().items() if key != "text"} for node in graph.nodes]
        target.mkdir(parents=True, exist_ok=True)
        (target / "graph.json.gz").write_bytes(gzip.compress(json.dumps(graph_record, sort_keys=True,
            separators=(",", ":")).encode(), mtime=0))
        receipt = {"schema_version": 1, "case_id": case["case_id"], "repository": case["repository"],
            "revision": selected, "upstream_base_revision": case["revision"], "audit_id": report["audit_id"],
            "manifest_sha256": report["benchmark_manifest_sha256"], "protocol": "issue_time",
            "snapshot_index": index.relative_to(root).as_posix(), "snapshot_sha256": snapshot.snapshot_sha256,
            "strict_issue_time_verified": True, "query_receipt": query_receipt,
            "files": _inventory(target, ["localization.json", "graph.json.gz"]),
            "mode": "dataset_construction", "evaluation_performed": False,
            "leakage_audit": {"gold_used_for_snapshot": False, "gold_used_for_graph": False,
                "query_original_text_verified": True, "historical_code_available_before_issue": True,
                "gold_localization_scope": "evaluator_only", "contamination_free_claim": False}}
        write_json(path, receipt)
    return {"case": runtime_case, "snapshot": snapshot, "graph": graph, "localization": localization,
            "receipt": receipt, "query_receipt": query_receipt, "availability": availability, "query_variant": query_variant}
