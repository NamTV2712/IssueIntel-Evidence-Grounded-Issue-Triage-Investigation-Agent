"""Shared, bounded construction of historical receipts for Main and Live.

This prepares metadata/assets only. It neither predicts hypotheses nor evaluates
held-out cases. Pending coverage is recorded explicitly and never promoted from
commit timestamps, current issue bodies, or PR dates.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .history import ArchiveLimits, find_issue_opened_event, select_historical_snapshot
from .main_metadata import resolve_main_case, append_main_mapping, verify_main_mapping_record
from .sources import inside_root, sha256_file
from .store import verify_manifest
from .temporal import (append_historical_audit, append_temporal_audit, fetch_issue_receipt,
                       issue_reference_url, _atomic_bytes, _source_event_bound)

MODEL_CUTOFF_PROVENANCE = {
    "model_id": "openai/gpt-oss-120b", "conservative_cutoff": "2024-07-01T00:00:00Z",
    "source_url": "https://cdn.openai.com/pdf/419b6906-9da6-406c-a19d-1bb078ac7637/oai_gpt-oss_model_card.pdf",
    "source_location": "page 5, section 2.4", "reported_knowledge_cutoff": "June 2024",
    "boundary_rule": "first UTC day after the entire reported cutoff month",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _latest(path: Path, case_id: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return next((entry for entry in reversed(entries) if entry["case_id"] == case_id), None)


def _bundle(root: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = record["receipt_bundle"]
    path = inside_root(root, root / descriptor["path"])
    if not path.is_relative_to(root / "data/annotations"):
        raise ValueError("Historical metadata receipt escapes private annotations")
    if sha256_file(path) != descriptor["sha256"]:
        raise ValueError("Historical metadata receipt has changed")
    return json.loads(path.read_text(encoding="utf-8"))


def _prior_attempt(root: Path, report: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not report:
        return None
    found = []
    for path in (root / "data/annotations/asof_attempts").glob("*.json"):
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != path.stem:
            raise ValueError("Historical attempt content-addressed digest changed")
        row = json.loads(raw)
        if row.get("audit_id") == report["audit_id"]:
            if row.get("case_id") != report["case_id"] or row.get("benchmark_manifest_sha256") != report["benchmark_manifest_sha256"]:
                raise ValueError("Historical attempt belongs to another source case")
            found.append(row)
    if len(found) > 1:
        raise ValueError("Ambiguous prior historical attempt for audit")
    return found[0] if found else None


def _retained_opening_match(root: Path, attempt: Mapping[str, Any] | None, ref: Mapping[str, Any]) -> tuple[dict | None, bool]:
    """Reuse an actual retained event, including a former current-body mismatch."""
    if not attempt:
        return None, False
    matches = [phase for phase in attempt.get("original_body_phase", [])
               if issue_reference_url(phase["reference"]) == issue_reference_url(ref)]
    if len(matches) > 1:
        raise ValueError("Ambiguous retained opening-event phase")
    if not matches:
        return None, False
    descriptor = matches[0].get("saved_receipt")
    if not descriptor:
        return None, True
    path = inside_root(root, root / descriptor["path"])
    if not path.is_relative_to(root / "data/annotations/history_receipts") or sha256_file(path) != descriptor["sha256"]:
        raise ValueError("Retained opening event receipt binding changed")
    match = json.loads(path.read_text(encoding="utf-8"))
    event = match.get("event", {})
    if not isinstance(event, Mapping) or not _source_event_bound(match, event, ref["repository"], "IssuesEvent"):
        raise ValueError("Retained opening event does not bind its source")
    return match, True


def _opening_receipt(current: Mapping[str, Any], match: Mapping[str, Any]) -> dict[str, Any]:
    """Select archived opening fields while retaining current API lineage separately."""
    event = match["event"]
    payload = event["payload"]["issue"]
    text = f"{payload.get('title') or ''}\n{payload.get('body') or ''}\n"
    original = copy.deepcopy(dict(current))
    original["payload"] = copy.deepcopy(payload)
    original["payload_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
    original["body_provenance"] = {"kind": "issue_creation_event", "available_at": payload["created_at"],
        "source_url": match["source_url"], "payload": copy.deepcopy(event),
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(), "history_complete": True,
        "scope": "opened event proves initial version, not complete archive history"}
    for key in ("raw_event_line", "raw_line_sha256", "event_sha256"):
        if key in match:
            original["body_provenance"][key] = match[key]
    return original


def _save_attempt(root: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    content = _canonical(value) + b"\n"
    digest = hashlib.sha256(content).hexdigest()
    path = inside_root(root, root / "data/annotations/asof_attempts" / f"{digest}.json")
    if path.exists() and path.read_bytes() != content:
        raise ValueError("Content-addressed as-of attempt changed")
    if not path.exists():
        _atomic_bytes(root, path, content)
    return {"path": path.relative_to(root).as_posix(), "sha256": digest}


def build_asof_receipt(
    root: str | Path, case: str | Mapping[str, Any], *,
    max_depth: int = 64, max_archive_hours: int = 3, limits: ArchiveLimits | None = None,
    query_policy: str = "source_query", retry_history: bool = False,
) -> dict[str, Any]:
    """Prepare one original source case with strict proofs or an explicit pending audit.

    Archive budgets apply per phase: at most max_archive_hours original-body hours
    in total and that many ancestry-search hours. A completed strict audit is
    reused only after validating its recorded receipt and original case bindings.
    """
    if query_policy not in {"source_query", "original_opening_query"} or type(retry_history) is not bool:
        raise ValueError("Require an explicit historical query policy and retry choice")
    if type(max_archive_hours) is not int or not 1 <= max_archive_hours <= 3:
        raise ValueError("As-of preparation permits one to three hours per phase")
    if type(max_depth) is not int or not 1 <= max_depth <= 256:
        raise ValueError("Bounded ancestry depth must be between 1 and 256")
    root = Path(root).resolve()
    manifest = verify_manifest(root)
    manifest_sha = sha256_file(root / "data/manifests/dataset.json")
    cases = [json.loads(line) for line in (root / manifest["files"]["cases"]["path"]).read_text(
        encoding="utf-8").splitlines()]
    case_id = case if isinstance(case, str) else case["case_id"]
    original = next((item for item in cases if item["case_id"] == case_id), None)
    if original is None or (not isinstance(case, str) and _canonical(case) != _canonical(original)):
        raise ValueError("As-of builder requires the original pinned case")
    case = original
    main = case["split"] in {"main_dev", "main_test"}
    if not main and case["split"] != "temporal_candidate":
        raise ValueError("Unsupported source split")
    overlay = root / "data/annotations" / ("historical_audit.jsonl" if main else "temporal_audit.jsonl")
    previous = _latest(overlay, case_id)
    previous_bundle = None
    if previous:
        content = {key: value for key, value in previous.items() if key != "audit_id"}
        if hashlib.sha256(_canonical(content)).hexdigest() != previous.get("audit_id"):
            raise ValueError("Historical audit event digest changed")
        if previous.get("case_sha256") != hashlib.sha256(_canonical(case)).hexdigest():
            raise ValueError("Historical audit refers to another original case")
        previous_bundle = _bundle(root, previous)
        if previous.get("benchmark_manifest_sha256") != manifest_sha:
            raise ValueError("Historical audit references another original benchmark manifest")
    if previous and previous.get("strict_issue_time_receipt_checks_passed"):
        bundle = previous_bundle
        from .temporal import audit_historical_case, audit_temporal_case
        arguments = dict(selected_snapshot_revision=bundle.get("selected_snapshot_revision"),
                         ancestry_receipt=bundle.get("ancestry_receipt"),
                         query_policy=bundle.get("query_policy", "source_query"))
        if main:
            replay = audit_historical_case(case, bundle["issue_receipts"], bundle.get("revision_proof"),
                issue_references=bundle.get("issue_references"), **arguments)
        else:
            replay = audit_temporal_case(case, bundle["issue_receipts"], bundle.get("revision_proof"),
                cutoff_provenance=bundle.get("cutoff_provenance"), **arguments)
        if not replay["strict_issue_time_receipt_checks_passed"]:
            raise ValueError("Recorded strict historical audit does not replay")
        for key, value in replay.items():
            if previous.get(key) != value:
                raise ValueError("Recorded historical selection differs from its source receipts")
        if arguments["query_policy"] != query_policy:
            convert = dict(selected_snapshot_revision=bundle.get("selected_snapshot_revision"),
                ancestry_receipt=bundle.get("ancestry_receipt"), query_policy=query_policy,
                upstream_issue_receipts=bundle.get("upstream_issue_receipts", bundle["issue_receipts"]))
            if main:
                previous = append_historical_audit(root, case_id, bundle["issue_receipts"], bundle.get("revision_proof"),
                    issue_references=bundle.get("issue_references"), **convert)
            else:
                previous = append_temporal_audit(root, case_id, bundle["issue_receipts"], bundle.get("revision_proof"),
                    cutoff_provenance=bundle.get("cutoff_provenance"), **convert)
            if not previous["strict_issue_time_receipt_checks_passed"]:
                # Opening mode can differ from the source; converting back must
                # not silently treat that derived query as an unchanged source.
                return {"case_id": case_id, "status": previous["status"], "audit": previous,
                    "reused": True, "pending_reasons": ["source_query_differs_from_opening_version"], "evaluation_performed": False}
        return {"case_id": case_id, "status": "strict_receipt_checks_passed", "audit": previous,
                "reused": True, "pending_reasons": [], "evaluation_performed": False}
    limits = limits or ArchiveLimits(max_compressed_bytes=128 * 1024 * 1024,
                                     max_uncompressed_bytes=1024 * 1024 * 1024)
    pending, receipts, references = [], [], []
    prior_attempt = _prior_attempt(root, previous)
    if main:
        mapping = _latest(root / "data/annotations/main_metadata.jsonl", case_id)
        if mapping is None:
            report, bundle = resolve_main_case(case)
            mapping = append_main_mapping(root, case, report, bundle)
        else:
            bundle = verify_main_mapping_record(root, case, mapping)
        if case["repository"].lower() == "django/django" and mapping.get("schema_version", 1) < 3:
            from .main_metadata import resolve_trac_main_case
            report, bundle = resolve_trac_main_case(root, case, pull_receipt=bundle["pull_receipt"])
            mapping = append_main_mapping(root, case, report, bundle)
        if query_policy == "original_opening_query" and mapping.get("original_query_candidate"):
            relation = mapping["original_query_candidate"]
            references = [{"repository": relation["repository"], "number": relation["number"],
                **({"source_kind": "django_trac"} if relation.get("source_kind") == "django_trac" else {})}]
        elif mapping["status"] in {"exact_issue_match", "exact_multi_issue_match"}:
            references = [{"repository": item["repository"], "number": item["number"],
                **({"source_kind": "django_trac"} if item.get("source_kind") == "django_trac" else {})}
                for item in mapping["selected_issues"]]
        else:
            pending.append("main_issue_identity_unresolved:" + mapping["status"])
        if references and references[0].get("source_kind") == "django_trac":
            from .trac import audit_issue_receipt
            selected = {str(ref["number"]) for ref in references}
            receipts = [audit_issue_receipt(root, pair["initial_receipt"]) for pair in bundle["trac_pairs"]
                        if str(pair["initial_receipt"]["payload"]["ticket_number"]) in selected]
        else:
            selected_urls = {issue_reference_url(ref) for ref in references}
            receipts = [copy.deepcopy(receipt) for receipt in bundle["issue_receipts"] if receipt["source_url"] in selected_urls]
    else:
        references = [{"repository": case["repository"], "number": number} for number in case["issue_numbers"]]
        if previous:
            receipts = copy.deepcopy(previous_bundle["issue_receipts"])
        else:
            receipts = [fetch_issue_receipt(ref["repository"], ref["number"]) for ref in references]
    receipt_by_url = {receipt["source_url"]: receipt for receipt in receipts}
    ordered = [receipt_by_url[issue_reference_url(ref)] for ref in references if issue_reference_url(ref) in receipt_by_url]
    upstream_receipts = copy.deepcopy(ordered)
    dates = [receipt["payload"].get("created_at") for receipt in ordered]
    if len(ordered) != len(references) or not ordered or any(not date for date in dates):
        pending.append("missing_independent_issue_creation_metadata")
    if len(set(dates)) > 1:
        pending.append("composite_query_contains_issue_text_created_after_earliest_query_time")
    body_attempts = []
    hours_left = max_archive_hours
    if not pending:
        for index, (ref, receipt) in enumerate(zip(references, ordered)):
            if ref.get("source_kind") == "django_trac":
                body_attempts.append({"reference": ref, "status": "official_initial_Trac_version_replayed",
                                      "attempts": [], "raw_html": receipt["raw_html"]})
                continue
            cached = receipt.get("body_provenance", {}).get("payload") if isinstance(receipt.get("body_provenance"), Mapping) else None
            if cached and receipt["body_provenance"].get("kind") == "issue_creation_event":
                match = {"event": cached, **{key: receipt["body_provenance"][key] for key in
                    ("source_url", "raw_event_line", "raw_line_sha256", "event_sha256") if key in receipt["body_provenance"]}}
                if query_policy == "original_opening_query":
                    ordered[index] = _opening_receipt(receipt, match)
                body_attempts.append({"reference": ref, "status": "retained_original_event_reused", "attempts": []})
                continue
            retained, searched_before = _retained_opening_match(root, prior_attempt, ref)
            if retained and query_policy == "original_opening_query":
                ordered[index] = _opening_receipt(receipt, retained)
                body_attempts.append({"reference": ref, "status": "retained_original_event_reused", "attempts": []})
                continue
            if searched_before and not retry_history:
                pending.append("original_body_unverified:prior_bounded_search_pending")
                body_attempts.append({"reference": ref, "status": "prior_bounded_search_pending", "attempts": []})
                continue
            if hours_left <= 0:
                pending.append("original_body_hour_budget_exhausted")
                break
            payload = receipt["payload"]
            expected = f"{payload.get('title') or ''}\n{payload.get('body') or ''}\n"
            result = find_issue_opened_event(ref["repository"], ref["number"], payload["created_at"],
                expected_problem_statement=expected if query_policy == "source_query" else None,
                hour_offsets=(0, 1, -1)[:hours_left], limits=limits, save_root=root)
            hours_left -= len(result["attempts"])
            body_attempts.append({"reference": ref, "status": result["status"],
                "attempts": result["attempts"], "saved_receipt": result.get("saved_receipt"),
                "problem_statement_matches": result.get("problem_statement_matches")})
            if query_policy == "original_opening_query" and result.get("match"):
                ordered[index] = _opening_receipt(receipt, result["match"])
            elif result.get("body_provenance"):
                receipt["body_provenance"] = result["body_provenance"]
            else:
                pending.append("original_body_unverified:" + result["status"])
    selection = None
    if not pending:
        if previous_bundle and previous_bundle.get("revision_proof"):
            selection = {key: previous_bundle.get(key) for key in ("selected_snapshot_revision", "ancestry_receipt", "revision_proof")}
        elif prior_attempt and prior_attempt.get("snapshot_phase") and not retry_history:
            selection = prior_attempt["snapshot_phase"]
        else:
            selection = select_historical_snapshot(case["repository"], case["revision"], dates[0],
                cache_dir=root / "data/repos", max_depth=max_depth,
                max_archive_hours=max_archive_hours, limits=limits, save_root=root)
        if not selection.get("revision_proof"):
            pending.append("bounded_ancestry_publication_unverified")
    kwargs = dict(selected_snapshot_revision=selection.get("selected_snapshot_revision") if selection else None,
                  ancestry_receipt=selection.get("ancestry_receipt") if selection else None,
                  query_policy=query_policy, upstream_issue_receipts=upstream_receipts)
    proof = selection.get("revision_proof") if selection else None
    if main:
        audit = append_historical_audit(root, case_id, ordered, proof, issue_references=references, **kwargs)
    else:
        audit = append_temporal_audit(root, case_id, ordered, proof,
                                     cutoff_provenance=MODEL_CUTOFF_PROVENANCE, **kwargs)
    if not audit["strict_issue_time_receipt_checks_passed"] and not pending:
        pending.append("strict_receipt_predicates_not_satisfied")
    attempt = {"case_id": case_id, "benchmark_manifest_sha256": manifest_sha,
               "original_body_phase": body_attempts, "snapshot_phase": selection,
               "pending_reasons": pending, "audit_id": audit["audit_id"], "evaluation_performed": False}
    if query_policy != "source_query":
        attempt["query_policy"] = query_policy
    descriptor = _save_attempt(root, attempt)
    return {"case_id": case_id, "status": audit["status"], "audit": audit, "reused": False,
            "pending_reasons": pending, "attempt_receipt": descriptor, "evaluation_performed": False}
