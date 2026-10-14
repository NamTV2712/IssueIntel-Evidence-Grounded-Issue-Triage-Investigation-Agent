"""Resumable targeted issue-date enrichment, distinct from task inference."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from urllib.error import HTTPError

from issueintel.runtime.io import read_jsonl, write_json
from .store import verify_manifest
from .temporal import append_temporal_audit, fetch_issue_receipt


def enrich_temporal_dates(root: Path | str, *, limit=10) -> dict:
    if type(limit) is not int or not 1 <= limit <= 300:
        raise ValueError("Metadata limit must be between 1 and 300")
    root = Path(root).resolve()
    verify_manifest(root)
    cases = [case for case in read_jsonl(root / "data/benchmark/cases.jsonl") if case["split"] == "temporal_candidate"]
    overlay = root / "data/annotations/temporal_audit.jsonl"
    previous = read_jsonl(overlay) if overlay.exists() else []
    done = {audit["case_id"] for audit in previous if not audit["missing_issue_numbers"]}
    reports, failures = [], []
    for case in cases:
        if case["case_id"] in done:
            continue
        if len(reports) + len(failures) >= limit:
            break
        try:
            receipts = [fetch_issue_receipt(case["repository"], number) for number in case["issue_numbers"]]
            reports.append(append_temporal_audit(root, case["case_id"], receipts,
                cutoff_provenance={"model_id":"openai/gpt-oss-120b", "conservative_cutoff":"2024-07-01T00:00:00Z",
                    "source_url":"https://cdn.openai.com/pdf/419b6906-9da6-406c-a19d-1bb078ac7637/oai_gpt-oss_model_card.pdf"}))
        except HTTPError as error:
            # No raw response or headers containing credentials go into a receipt.
            failures.append({"case_id":case["case_id"], "http_status":error.code})
            if error.code in {403,429}:
                break
        except (ValueError, RuntimeError, OSError) as error:
            failures.append({"case_id":case["case_id"], "error":str(error)})
        write_json(root / "artifacts/temporal_enrichment_progress.json", {"audited":len(reports), "failures":failures,
            "statuses":dict(Counter(report["status"] for report in reports)), "evaluation_performed":False})
    return {"audited":len(reports), "already_audited":len(done), "failures":failures,
            "statuses":dict(Counter(report["status"] for report in reports)), "evaluation_performed":False}
