"""Bounded model inputs; local lineage and receipts never become prompt data.

The full case/evidence records remain local. Reject restricted information
before projection so an allowlist cannot silently conceal accidental gold use.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .models import AnnotationError, evidence_ids, id_list, require_text


HIDDEN_FIELDS = {
    "patch", "test_patch", "fail_to_pass", "pass_to_pass", "gold", "gold_patch",
    "gold_localization", "gold_tests", "hidden_tests", "expected_tests", "annotations",
    "annotation", "source_quality", "underspecified", "underspecified_notes",
    "underspecified_ordinal", "source_human", "source_human_notes", "source_human_annotations",
    "human_annotations", "human_rationale", "human_notes", "hints_text", "all_hints_text",
}
MODEL_CASE_FIELDS = {"problem_statement", "repository", "repo", "revision", "base_commit", "issue_url", "issue_created_at"}
MODEL_EVIDENCE_FIELDS = {"id", "kind", "text", "sha256", "revision", "available_at"}
MODEL_HYPOTHESIS_FIELDS = {"id", "statement", "support_ids", "counter_ids", "counter_status", "falsification_test"}
_SEMANTIC_KINDS = {"input", "issue", "verified_original_query", "code", "git_blob", "synthetic"}
_EDIT_HISTORY = {"unknown", "opening_event_matched"}
MODEL_INPUT_PROJECTION_VERSION = "issueintel.public_model_inputs.v1"


def reject_hidden(value: Any) -> None:
    if isinstance(value, Mapping):
        hidden = {str(key).casefold() for key in value} & HIDDEN_FIELDS
        if hidden or value.get("kind") == "source_human":
            raise AnnotationError("Hidden benchmark fields cannot enter public model inputs")
        for item in value.values():
            reject_hidden(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            reject_hidden(item)


def issue_locator(value: Any) -> str:
    """Preserve a canonical HTTPS issue/ticket URL; otherwise use a neutral ID.

    Syntax does not certify that the issue existed at query time. Historical
    receipt validation remains outside this model projection. URL query/fragment
    metadata and credentials are never sent to a model.
    """
    if not isinstance(value, str):
        return "input://issue"
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        path = parsed.path.rstrip("/")
        if (parsed.scheme != "https" or not hostname or parsed.username or parsed.password
                or parsed.port not in {None, 443} or not re.fullmatch(r"[A-Za-z0-9.-]+", hostname)
                or any(segment in {".", ".."} for segment in path.split("/"))
                or not re.fullmatch(r"/[A-Za-z0-9_./-]+/(?:issues|ticket)/[1-9][0-9]*|/ticket/[1-9][0-9]*", path)):
            return "input://issue"
        return urlunsplit(("https", hostname.lower(), path, "", ""))
    except ValueError:
        return "input://issue"


def model_case(case: Mapping) -> dict:
    reject_hidden(case)
    if not isinstance(case, Mapping):
        raise AnnotationError("Public case must be an object")
    require_text(case.get("problem_statement"), "problem_statement")
    result = {key: value for key, value in case.items() if key in MODEL_CASE_FIELDS}
    for key, value in result.items():
        if value is not None and not isinstance(value, str):
            raise AnnotationError(f"Model case {key} must be text or null")
    for key in ("repository", "repo"):
        if key in result:
            result[key] = repository_name(result[key])
    if "issue_url" in result:
        result["issue_url"] = issue_locator(result["issue_url"])
    return result


def repository_name(value: Any) -> str:
    """Remote name/URL or a neutral local alias, never a machine path."""
    if not isinstance(value, str):
        return "local_repository"
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) and all(part not in {".", ".."} for part in value.split("/")):
        return value
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == "https" and parsed.hostname == "github.com"
                and not parsed.username and not parsed.password and parsed.port in {None, 443}):
            name = parsed.path.strip("/")
            name = name[:-4] if name.endswith(".git") else name
            if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name) and all(part not in {".", ".."} for part in name.split("/")):
                return "https://github.com/" + name
    except ValueError:
        pass
    return "local_repository"


def model_evidence(evidence: Sequence[Mapping]) -> list[dict]:
    reject_hidden(evidence)
    evidence_ids(evidence)
    projected = []
    for record in evidence:
        require_text(record.get("text"), "evidence.text")
        item = {key: value for key, value in record.items() if key in MODEL_EVIDENCE_FIELDS}
        for key, value in item.items():
            if value is not None and not isinstance(value, str):
                raise AnnotationError(f"Model evidence {key} must be text or null")
        if record.get("kind") in {"issue", "issue_text", "problem_statement"}:
            item["locator"] = issue_locator(record.get("locator"))
        elif "locator" in record:
            require_text(record["locator"], "evidence.locator")
            item["locator"] = record["locator"]
        provenance = record.get("provenance")
        if isinstance(provenance, Mapping):
            semantic = {}
            if isinstance(provenance.get("kind"), str) and provenance["kind"] in _SEMANTIC_KINDS:
                semantic["kind"] = provenance["kind"]
            if isinstance(provenance.get("edit_history"), str) and provenance["edit_history"] in _EDIT_HISTORY:
                semantic["edit_history"] = provenance["edit_history"]
            if semantic:
                item["provenance"] = semantic
        projected.append(item)
    return projected


def model_payload(case: Mapping, evidence: Sequence[Mapping]) -> dict:
    # Inspect both raw objects before either is discarded by projection.
    reject_hidden(case)
    reject_hidden(evidence)
    return {"case": model_case(case), "evidence": model_evidence(evidence)}


def model_hypotheses(hypotheses: Sequence[Mapping]) -> list[dict]:
    reject_hidden(hypotheses)
    if isinstance(hypotheses, (str, bytes)) or not isinstance(hypotheses, Sequence):
        raise AnnotationError("Hypotheses must be an array")
    projected = []
    for record in hypotheses:
        if not isinstance(record, Mapping):
            raise AnnotationError("Hypothesis must be an object")
        item = {key: value for key, value in record.items() if key in MODEL_HYPOTHESIS_FIELDS}
        for key in ("id", "statement", "falsification_test"):
            require_text(item.get(key), key)
        id_list(item.get("support_ids"), "support_ids")
        id_list(item.get("counter_ids"), "counter_ids")
        if not isinstance(item.get("counter_status"), str) or item["counter_status"] not in {"found", "not_found"}:
            raise AnnotationError("Invalid hypothesis counter status")
        projected.append(item)
    return projected
