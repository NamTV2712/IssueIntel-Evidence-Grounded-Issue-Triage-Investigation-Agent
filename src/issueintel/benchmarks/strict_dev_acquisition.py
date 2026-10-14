"""Resumable, public-only proof stages for strict DEV; no legacy gold loader."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

from issueintel.runtime.io import digest, read_jsonl, write_json
from .asof import _opening_receipt
from .history import ArchiveLimits, find_issue_opened_event, select_historical_snapshot
from .main_metadata import verify_main_mapping_record
from .temporal import fetch_issue_receipt, issue_reference_url
from .sources import inside_root


def acquire_proof(root, candidate, *, depth=256, hours=3):
    from .strict_dev import _json, _publish, _checked, _lock, opening_versions
    if not 1 <= depth <= 256 or not 1 <= hours <= 6:
        raise ValueError("Invalid bounded historical search")
    case = candidate["case"]
    directory = inside_root(root, root / "data/development/acquisition" / digest(case))
    directory.mkdir(parents=True, exist_ok=True)
    limits = ArchiveLimits(max_compressed_bytes=128 * 1024 * 1024,
                           max_uncompressed_bytes=1024 * 1024 * 1024, max_seconds=60)
    metadata_path = directory / "metadata.json"
    if metadata_path.exists():
        meta = _json(metadata_path)
        if meta["case_sha256"] != digest(case):
            raise ValueError("Metadata stage identity changed")
        metadata = _json(_checked(root, meta["output"]))
    else:
        refs, receipts = [], []
        if candidate["origin"] == "bootstrap_main_dev":
            path = root / "data/annotations/main_metadata.jsonl"
            mappings = [r for r in read_jsonl(path) if r["case_id"] == case["case_id"]]
            if not mappings:
                return None
            row = mappings[-1]
            bundle = verify_main_mapping_record(root, case, row)
            relation = row.get("original_query_candidate")
            selected = [relation] if relation else row.get("selected_issues", [])
            refs = [{k: ref[k] for k in ("repository", "number", "source_kind") if k in ref} for ref in selected]
            if refs and refs[0].get("source_kind") == "django_trac":
                from .trac import audit_issue_receipt
                numbers = {str(ref["number"]) for ref in refs}
                receipts = [audit_issue_receipt(root, pair["initial_receipt"]) for pair in bundle["trac_pairs"]
                            if str(pair["initial_receipt"]["payload"]["ticket_number"]) in numbers]
            else:
                urls = {issue_reference_url(ref) for ref in refs}
                receipts = [copy.deepcopy(r) for r in bundle["issue_receipts"] if r["source_url"] in urls]
        else:
            raise ValueError("Proof acquisition accepts original Main Dev only")
        output = _publish(root, root / "data/development/metadata", {"issue_references": refs, "issue_receipts": receipts})
        write_json(metadata_path, {"case_sha256": digest(case), "output": output})
        metadata = _json(_checked(root, output))
    refs, receipts = metadata["issue_references"], copy.deepcopy(metadata["issue_receipts"])
    if not refs or len(receipts) != len(refs):
        return None
    if any("pull_request" in r.get("payload", {}) for r in receipts):
        return None
    dates = [r["payload"].get("created_at") for r in receipts]
    if not dates[0] or len(set(dates)) != 1:
        return None
    opening_path = directory / "opening.json"
    if opening_path.exists():
        saved = _json(opening_path)
        if saved["case_sha256"] != digest(case):
            raise ValueError("Opening stage binding changed")
        receipts = _json(_checked(root, saved["output"]))["issue_receipts"]
    else:
        for i, (ref, receipt) in enumerate(zip(refs, receipts)):
            if ref.get("source_kind") == "django_trac":
                continue
            query_path = directory / ("opening_attempt_" + digest([ref, dates[i], min(hours, 3)]) + ".json")
            if query_path.exists():
                result = _json(_checked(root, _json(query_path)["output"]))
            else:
                result = find_issue_opened_event(ref["repository"], ref["number"], dates[i],
                    expected_problem_statement=None, hour_offsets=(0, 1, -1)[:min(hours, 3)], limits=limits, save_root=root)
                output = _publish(root, root / "data/development/lookups", result)
                write_json(query_path, {"output": output})
            if not result.get("match"):
                return None
            receipts[i] = _opening_receipt(receipt, result["match"])
        if not opening_versions({"issue_receipts": receipts}):
            return {"query_policy": "original_opening_query", "issue_receipts": receipts, "issue_references": refs,
                    "revision_proof": None, "selected_snapshot_revision": None, "ancestry_receipt": None}
        output = _publish(root, root / "data/development/opening", {"issue_receipts": receipts})
        write_json(opening_path, {"case_sha256": digest(case), "output": output})
    snapshot_path = directory / "snapshot_selection.json"
    if snapshot_path.exists():
        selection = _json(_checked(root, _json(snapshot_path)["output"]))
    else:
        attempt_path = directory / (f"selection_depth{depth}_hours{hours}.json")
        if attempt_path.exists():
            selection = _json(_checked(root, _json(attempt_path)["output"]))
        else:
            selection = select_historical_snapshot(case["repository"], case["revision"], dates[0],
                cache_dir=root / "data/repos", max_depth=depth, max_archive_hours=hours, limits=limits, save_root=root,
                repository_lock=lambda: _lock(root, "repo:" + case["repository"].lower()))
            output = _publish(root, root / "data/development/selections", selection)
            write_json(attempt_path, {"output": output})
        if selection.get("revision_proof"):
            output = _publish(root, root / "data/development/selections", selection)
            write_json(snapshot_path, {"output": output})
    return {"query_policy": "original_opening_query", "issue_receipts": receipts, "issue_references": refs,
            "revision_proof": selection.get("revision_proof"),
            "selected_snapshot_revision": selection.get("selected_snapshot_revision"),
            "ancestry_receipt": selection.get("ancestry_receipt")}
