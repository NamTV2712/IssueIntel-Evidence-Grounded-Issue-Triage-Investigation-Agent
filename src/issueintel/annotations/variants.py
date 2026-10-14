"""Explicit controlled manipulations; synthetic labels are not natural truth."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from collections.abc import Mapping, Sequence

from .generator import public_payload
from .models import AnnotationError, evidence_ids, id_list, require_text


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def create_abstention_variants(
    case: Mapping,
    evidence: Sequence[Mapping],
    *,
    critical_ids: Sequence[str] = (),
    conflict: Mapping | None = None,
) -> list[dict]:
    """Return variant packets without mutating their parent.

    ``critical_ids`` must be selected/justified by the annotator. ``conflict`` is
    an explicit {target_id, text} diagnostic contradiction; the code cannot
    establish that arbitrary replacement text truly contradicts the target.
    Synthetic expected abstention remains pending independent validation.
    """
    payload = public_payload(case, evidence)
    parent_id = payload["case"].get("case_id") or payload["case"].get("instance_id")
    known = set(evidence_ids(evidence))
    removed = id_list(list(critical_ids), "critical_ids")
    if set(removed) - known:
        raise AnnotationError("Unknown critical evidence IDs")
    parent_hash = _digest(payload)
    variants = []

    def packet(operation: str, variant_case: dict, variant_evidence: list, changes: dict) -> dict:
        variant_id = f"{parent_id}::{operation}::{_digest(changes)[:12]}"
        variant_case["case_id"] = variant_id
        variant_case["parent_case_id"] = parent_id
        variant_case["variant_id"] = variant_id
        return {
            "case": variant_case, "evidence": variant_evidence,
            "annotation": {
                "schema_version": "issueintel.variant.v1", "variant_id": variant_id,
                "parent_case_id": parent_id, "parent_sha256": parent_hash,
                "split_group": parent_id, "operation": operation, **changes,
                "expected_abstention": "abstain", "validation_status": "unreviewed",
                "label_provenance": {"kind": "synthetic", "method": operation},
                "limitation": "Controlled target; remaining evidence may still permit an answer. Requires review before a natural answerability claim.",
            },
        }

    if removed:
        variant_case = deepcopy(payload["case"])
        variant_evidence = [deepcopy(item) for item in payload["evidence"] if item["id"] not in removed]
        removed_texts = {item["text"] for item in payload["evidence"] if item["id"] in removed}
        duplicate_ids = [item["id"] for item in variant_evidence if item.get("text") in removed_texts]
        variant_evidence = [item for item in variant_evidence if item["id"] not in duplicate_ids]
        redactions = []
        issue_text = payload["case"]["problem_statement"]
        removed_issue_text = any(
            item["id"] in removed and (
                str(item.get("kind", "")).lower() in {"issue", "issue_text", "problem_statement"}
                or item.get("text") == issue_text
            ) for item in payload["evidence"]
        )
        if removed_issue_text:
            variant_case["problem_statement"] = "[Issue description removed in this controlled evidence-removal variant.]"
            redactions.append("case.problem_statement")
        variants.append(packet("remove_critical_evidence", variant_case, variant_evidence, {
            "removed_ids": sorted(set(removed + duplicate_ids)), "redacted_fields": redactions,
        }))

    if conflict is not None:
        if not isinstance(conflict, Mapping) or set(conflict) - {"target_id", "text"}:
            raise AnnotationError("conflict requires only target_id and text")
        target = require_text(conflict.get("target_id"), "conflict.target_id")
        text = require_text(conflict.get("text"), "conflict.text")
        if target not in known:
            raise AnnotationError("Unknown conflict target ID")
        if text == next(item["text"] for item in evidence if item["id"] == target):
            raise AnnotationError("Conflict text must differ from its target")
        synthetic_id = "synthetic:conflict:" + _digest({"target": target, "text": text})[:16]
        if synthetic_id in known:
            raise AnnotationError("Synthetic ID collides with existing evidence")
        inserted = {
            "id": synthetic_id, "kind": "synthetic_conflict", "locator": f"synthetic://{synthetic_id}",
            "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "revision": case.get("revision") or case.get("base_commit"), "available_at": None,
            "provenance": {"kind": "synthetic", "method": "inject_conflicting_evidence", "target_id": target},
        }
        variants.append(packet("inject_conflicting_evidence", deepcopy(payload["case"]),
                               deepcopy(payload["evidence"]) + [inserted], {
                                   "injected_ids": [synthetic_id], "target_ids": [target],
                                   "historical_status": "synthetic_not_historical",
                               }))
    return variants
