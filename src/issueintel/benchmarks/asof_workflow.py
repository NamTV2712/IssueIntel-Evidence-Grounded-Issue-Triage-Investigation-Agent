"""Bounded historical preparation across original source cases, without inference."""
from __future__ import annotations

import json
import hashlib
from collections import Counter
from pathlib import Path

from issueintel.runtime.io import digest, read_jsonl, write_json, write_progress
from .asof import build_asof_receipt
from .asof_assets import build_asof_assets, verified_asof_receipt
from .store import load_dev_cases, verify_manifest
from .sources import inside_root


def _replay_previous(root: Path, case: dict, record: dict, manifest_sha: str) -> dict:
    if (digest({key: value for key, value in record.items() if key != "audit_id"}) != record.get("audit_id")
            or record.get("case_sha256") != digest(case)
            or record.get("benchmark_manifest_sha256") != manifest_sha):
        raise ValueError("Historical batch audit identity/hash mismatch")
    descriptor = record["receipt_bundle"]
    path = inside_root(root, root / descriptor["path"])
    if not path.is_relative_to(root / "data/annotations") or hashlib.sha256(path.read_bytes()).hexdigest() != descriptor["sha256"]:
        raise ValueError("Historical batch receipt bundle mismatch")
    bundle = json.loads(path.read_text(encoding="utf-8"))
    arguments = {"selected_snapshot_revision": bundle.get("selected_snapshot_revision"),
                 "ancestry_receipt": bundle.get("ancestry_receipt")}
    if record.get("query_policy", "source_query") != "source_query":
        arguments["query_policy"] = record["query_policy"]
    from .temporal import audit_temporal_case, audit_historical_case
    if case["split"] == "temporal_candidate":
        replay = audit_temporal_case(case, bundle["issue_receipts"], bundle.get("revision_proof"),
            model_cutoff=record["configured_model_cutoff"], cutoff_provenance=bundle.get("cutoff_provenance"), **arguments)
    else:
        replay = audit_historical_case(case, bundle["issue_receipts"], bundle.get("revision_proof"),
            issue_references=bundle["issue_references"], **arguments)
    if (record.get("status") != replay["status"] or record.get("strict_issue_time_receipt_checks_passed") !=
            replay["strict_issue_time_receipt_checks_passed"]):
        raise ValueError("Historical batch recorded outcome does not replay")
    return replay


def prepare_asof(root: Path | str, *, limit: int = 10, all_splits: bool = False,
                 case_id: str | None = None, retry_pending: bool = False,
                 max_archive_hours: int = 3, split: str | None = None,
                 query_policy: str = "source_query") -> dict:
    """Prepare up to limit cases; historical pending attempts require explicit retry.

    ``all_splits`` grants only dataset construction. No model, benchmark metric,
    or evaluation capability is opened by this orchestration.
    """
    if type(limit) is not int or not 1 <= limit <= 800:
        raise ValueError("Historical preparation limit must be between 1 and 800")
    if query_policy not in {"source_query", "original_opening_query"}:
        raise ValueError("Unknown historical query policy")
    root = Path(root).resolve()
    scope = split or ("all_splits" if all_splits else "main_dev")
    if case_id:
        scope += "_" + hashlib.sha256(case_id.encode()).hexdigest()[:12]
    progress_path = root / "artifacts/asof_progress" / (scope + "_" + query_policy + ".json")
    result_path = root / "artifacts/asof_runs" / (scope + "_" + query_policy + ".json")
    verify_manifest(root)
    manifest_sha = hashlib.sha256((root / "data/manifests/dataset.json").read_bytes()).hexdigest()
    cases = read_jsonl(root / "data/benchmark/cases.jsonl") if all_splits else load_dev_cases(root)
    if split:
        if split not in {"main_dev", "main_test", "temporal_candidate"} or (split != "main_dev" and not all_splits):
            raise ValueError("Preparing heldout metadata requires explicit all_splits construction scope")
        cases = [row for row in cases if row["split"] == split]
    if case_id:
        cases = [row for row in cases if row["case_id"] == case_id]
        if not cases:
            raise ValueError("Case is outside the requested construction scope")
    cases = sorted(cases, key=lambda case: (case["split"] == "temporal_candidate", case["case_id"]))
    gold = {row["case_id"]: row for row in read_jsonl(root / "data/benchmark/gold.jsonl")}
    latest, latest_all = {}, {}
    for name in ("historical_audit.jsonl", "temporal_audit.jsonl"):
        path = root / "data/annotations" / name
        if path.exists():
            for row in read_jsonl(path):
                latest_all[row["case_id"]] = row
                if row.get("query_policy", "source_query") == query_policy:
                    latest[row["case_id"]] = row
    attempts = {}
    for path in (root / "data/annotations/asof_attempts").glob("*.json"):
        if hashlib.sha256(path.read_bytes()).hexdigest() != path.stem:
            raise ValueError("Historical attempt content-addressed digest mismatch")
        row = json.loads(path.read_text(encoding="utf-8"))
        attempts[row["audit_id"]] = row
    completed, pending, failures, skipped = [], [], [], []
    attempted = 0
    for case in cases:
        if attempted >= limit:
            break
        previous = latest.get(case["case_id"])
        eligibility_record = previous or latest_all.get(case["case_id"])
        replay = None
        if eligibility_record:
            try:
                replay = _replay_previous(root, case, eligibility_record, manifest_sha)
            except (ValueError, OSError) as error:
                failures.append({"case_id": case["case_id"], "error": str(error)})
                attempted += 1
                continue
        if previous and previous.get("strict_issue_time_receipt_checks_passed"):
            # Do not trust a saved flag even when this stage is already complete.
            try:
                proof = verified_asof_receipt(root, case, query_policy=query_policy)
                assets = build_asof_assets(root, case, gold[case["case_id"]], audit_id=proof["report"]["audit_id"], query_policy=query_policy)
                skipped.append({"case_id": case["case_id"], "reason": "verified_historical_assets_reused",
                                "audit_id": assets["receipt"]["audit_id"]})
                continue
            except (ValueError, RuntimeError, OSError) as error:
                failures.append({"case_id": case["case_id"], "error": str(error)})
                attempted += 1
                continue
        if (case["split"] == "temporal_candidate" and eligibility_record
                and replay.get("all_issue_dates_after_cutoff") is not True):
            skipped.append({"case_id": case["case_id"], "reason": eligibility_record["status"]})
            continue
        if previous and previous.get("audit_id") in attempts and not retry_pending:
            saved = attempts[previous["audit_id"]]
            if saved.get("case_id") != case["case_id"] or saved.get("benchmark_manifest_sha256") != manifest_sha:
                raise ValueError("Historical pending attempt is bound to another case/dataset")
            pending.append({"case_id": case["case_id"], "status": previous["status"],
                            "pending_reasons": saved["pending_reasons"], "reused_pending_attempt": True})
            continue
        attempted += 1
        try:
            result = build_asof_receipt(root, case, max_archive_hours=max_archive_hours, query_policy=query_policy,
                                        retry_history=retry_pending)
            summary = {key: result[key] for key in ("case_id", "status", "pending_reasons")}
            summary["audit_id"] = result["audit"]["audit_id"]
            if result["audit"]["strict_issue_time_receipt_checks_passed"]:
                assets = build_asof_assets(root, case, gold[case["case_id"]], audit_id=summary["audit_id"], query_policy=query_policy)
                summary.update({"snapshot_sha256": assets["snapshot"].snapshot_sha256,
                    "selected_snapshot_revision": assets["snapshot"].commit_sha,
                    "line_projection_status": assets["localization"]["line_projection_status"]})
                completed.append(summary)
            else:
                pending.append(summary)
        except (ValueError, RuntimeError, OSError) as error:
            failures.append({"case_id": case["case_id"], "error": str(error)})
        write_progress(progress_path, {"attempted": attempted,
            "completed": len(completed), "pending": len(pending), "failed": len(failures),
            "latest_case": case["case_id"], "mode": "dataset_construction", "evaluation_performed": False})
    result = {"attempted": attempted, "completed": completed, "pending": pending,
              "failures": failures, "skipped": skipped,
              "pending_reasons": dict(Counter(reason for row in pending for reason in row["pending_reasons"])),
              "query_policy": query_policy, "mode": "dataset_construction", "evaluation_performed": False}
    write_json(result_path, result)
    return result
