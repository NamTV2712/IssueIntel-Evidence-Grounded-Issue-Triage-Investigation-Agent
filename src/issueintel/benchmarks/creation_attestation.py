"""Offline additive creation-data attestation for stored initial issue revisions.

This gate leaves every previously published proof/report byte unchanged. The
fixed immutable audit selects source receipts; its verdict flags are never
eligibility evidence. Raw official responses must independently replay.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

from issueintel.runtime.io import digest
from .recovery_sources import EDIT_QUERY, initial_edit_snapshot_candidate
from .strict_dev import PUBLIC_KEYS, _checked, _json

_LOADED_SOURCE = Path(__file__).read_bytes()

ATTESTATION_AUDIT = {
    "path": "data/development/recovery/initial_edit_attestations/audits/ea3866f94d7850646f5818a7cb26490d6573e981ff25a38a3454a09a22fecc8f.json",
    "sha256": "0ea252e07deec263ad54c412728498402adc2cd76f6812d0a14fa881b16088e3",
}
ATTESTATION_QUERY = EDIT_QUERY.replace("lastEditedAt title body", "lastEditedAt includesCreatedEdit title body")
DOCUMENTATION = {
    "explicit_creation_data_attestation": (
        "https://docs.github.com/en/graphql/reference/issues", (b"includesCreatedEdit", b"creation data")),
    "diff_field_descriptor_limitation": (
        "https://docs.github.com/en/graphql/reference/users", (b"UserContentEdit", b"diff")),
    "original_content_preservation_policy": (
        "https://github.blog/changelog/2026-07-02-issue-fields-are-now-generally-available/",
        (b"original content", b"userContentEdits")),
    "history_visibility_and_deleted_revision_policy": (
        "https://docs.github.com/en/communities/moderating-comments-and-conversations/tracking-changes-in-a-comment",
        (b"history", b"delete")),
}


def _object(root, ref):
    """Require a checked data-only content-addressed JSON object."""
    path = _checked(root, ref)
    value = _json(path)
    if path.stem != digest(value):
        raise ValueError("attestation_object_name_mismatch")
    return value


def _dated(value):
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.utcoffset() is None:
        raise ValueError("attestation_source_date_missing_timezone")
    return date


def _response(record, bound):
    encoded = record["raw_response_base64"]
    if not isinstance(encoded, str) or len(encoded) > (bound * 4 // 3 + 8):
        raise ValueError("attestation_response_cap")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > bound or hashlib.sha256(raw).hexdigest() != record["response_sha256"]:
        raise ValueError("attestation_raw_hash_mismatch")
    _dated(record["fetched_at"])
    return raw


def _documentation(root, entries):
    if not isinstance(entries, list) or len(entries) != len(DOCUMENTATION):
        raise ValueError("attestation_documentation_missing")
    seen, refs = set(), []
    for item in entries:
        role = item["role"]
        if role not in DOCUMENTATION or role in seen:
            raise ValueError("attestation_documentation_role_invalid")
        seen.add(role)
        source_url, markers = DOCUMENTATION[role]
        receipt = _object(root, item["receipt"])
        if (item["source_url"] != source_url or receipt.get("source_url") != source_url
                or receipt.get("resolved_url") != source_url or receipt.get("role") != role
                or receipt.get("schema") != "official_edit_history_documentation_receipt_v1"):
            raise ValueError("attestation_documentation_source_invalid")
        raw = _response(receipt, 6_000_000)
        if not all(marker in raw for marker in markers):
            raise ValueError("attestation_documentation_content_unverified")
        refs.append(item["receipt"])
    return refs


def _fresh_history(root, case, current, issue):
    refs = issue["official_graphql_records"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 10:
        raise ValueError("attestation_history_missing_or_capped")
    owner, name = case["repository"].split("/")
    number = current["payload"]["number"]
    cursor, seen_cursors, records, declared_total = None, set(), [], None
    for page_number, ref in enumerate(refs):
        record = _object(root, ref)
        expected_variables = {"owner": owner, "name": name, "number": number, "cursor": cursor}
        if (record.get("schema") != "independent_initial_edit_attestation_v1"
                or record.get("case_id") != case["case_id"] or record.get("case_sha256") != digest(case)
                or record.get("source_url") != "https://api.github.com/graphql"
                or record.get("query") != ATTESTATION_QUERY or record.get("variables") != expected_variables
                or record.get("status") != "response_received"):
            raise ValueError("attestation_graphql_source_binding_invalid")
        raw = _response(record, 4_000_000)
        payload = json.loads(raw)
        if payload != record["payload"] or payload.get("errors"):
            raise ValueError("attestation_raw_payload_mismatch")
        repository = payload["data"]["repository"]
        item = repository["issue"]
        if item.get("includesCreatedEdit") is not True:
            raise ValueError("creation_data_not_explicitly_attested")
        if (item["databaseId"] != current["payload"]["id"] or item["number"] != number
                or item["createdAt"] != current["payload"]["created_at"]
                or repository["nameWithOwner"].lower() != case["repository"].lower()
                or item["url"] != "https://github.com/" + case["repository"] + "/issues/" + str(number)):
            raise ValueError("attestation_issue_identity_invalid")
        history = item["userContentEdits"]
        total = history["totalCount"]
        if type(total) is not int or not 1 <= total <= 1000 or declared_total not in (None, total):
            raise ValueError("attestation_history_count_inconsistent")
        declared_total = total
        nodes = history["nodes"]
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 100:
            raise ValueError("attestation_history_empty_or_capped")
        info = history["pageInfo"]
        more = info["hasNextPage"]
        if type(more) is not bool or more != (page_number + 1 < len(refs)):
            raise ValueError("attestation_history_page_gap")
        if more:
            cursor = info["endCursor"]
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024 or cursor in seen_cursors:
                raise ValueError("attestation_history_cursor_invalid")
            seen_cursors.add(cursor)
        # The adapter adds only collector kind metadata in memory. The official
        # payload and its raw bytes/hash remain exactly as received.
        adapted = copy.deepcopy(record)
        adapted["kind"] = "edits"
        records.append(adapted)
    candidate, reasons = initial_edit_snapshot_candidate(case, current, records)
    if reasons or candidate is None:
        raise ValueError("attestation_initial_revision_does_not_replay")
    if (issue["number"] != candidate["number"] or issue["issue_id"] != candidate["issue_id"]
            or issue["created_at"] != candidate["created_at"]
            or issue["initial_id"] != candidate["initial_body_revision_id"]
            or issue["initial_body_sha256"] != hashlib.sha256(candidate["body"].encode()).hexdigest()):
        raise ValueError("attestation_summary_disagrees_with_raw_source")
    return candidate, refs


def verify_creation_attestation(root, case, proof):
    """Replay additive official creation data; raise ValueError on any unknown.

    Caller separately replays original source selection, repository identity,
    ancestry and strict code publication. Returned descriptors must be saved
    with the checkpoint; this function does not modify any receipt or report.
    """
    if set(case) != PUBLIC_KEYS or case.get("split") != "main_dev":
        raise ValueError("creation_attestation_requires_public_main_dev")
    root = Path(root).resolve()
    try:
        if (proof.get("event_schema_version") != "official_initial_edit_snapshot_v1"
                or proof.get("source_case_sha256") != digest(case)
                or proof.get("query_policy") != "original_opening_query"):
            raise ValueError("attestation_original_proof_binding_invalid")
        audit = _object(root, ATTESTATION_AUDIT)
        if audit.get("schema") != "independent_initial_edit_audit_v1":
            raise ValueError("attestation_audit_schema_invalid")
        selected = [row for row in audit["cases"] if row.get("case_id") == case["case_id"]]
        if len(selected) != 1:
            raise ValueError("attestation_case_missing_or_ambiguous")
        bundle_ref = selected[0]["additive_attestation"]
        bundle = _object(root, bundle_ref)
        if (bundle.get("schema") != "official_initial_edit_additive_attestation_v1"
                or bundle.get("case_id") != case["case_id"] or bundle.get("case_sha256") != digest(case)
                or _object(root, bundle["original_candidate_proof"]) != proof):
            raise ValueError("attestation_candidate_proof_changed")
        old_wave = _json(_checked(root, bundle["original_wave_receipt"]))
        if (old_wave.get("case_id") != case["case_id"] or old_wave.get("case_sha256") != digest(case)
                or old_wave.get("proof") != bundle["original_candidate_proof"]
                or old_wave.get("receipt_sha256") != digest({key:value for key,value in old_wave.items() if key != "receipt_sha256"})):
            raise ValueError("attestation_original_wave_binding_invalid")
        documents = _documentation(root, bundle["official_documentation"])
        originals, currents, selection, issues = (proof["initial_edit_records"], proof["current_issue_receipts"],
            proof["issue_references"], bundle["issues"])
        if not issues or not len(issues) == len(currents) == len(originals) == len(selection):
            raise ValueError("attestation_original_selection_changed")
        if len({(ref["repository"], ref["number"]) for ref in selection}) != len(selection):
            raise ValueError("attestation_issue_selection_ambiguous")
        metadata, fresh_refs = [], []
        for current, old_pages, ref, item in zip(currents, originals, selection, issues):
            expected_url = "https://api.github.com/repos/" + case["repository"] + "/issues/" + str(ref["number"])
            if (ref["repository"] != case["repository"] or ref.get("source_kind") == "django_trac"
                    or current["source_url"] != expected_url or digest(current["payload"]) != current["payload_sha256"]):
                raise ValueError("attestation_current_source_identity_invalid")
            original, reasons = initial_edit_snapshot_candidate(case, current, old_pages)
            if reasons or original is None:
                raise ValueError("attestation_preserved_initial_revision_invalid")
            fresh, refs = _fresh_history(root, case, current, item)
            if fresh != original:
                raise ValueError("attestation_initial_body_title_or_version_changed")
            fresh_refs.extend(refs)
            metadata.append({"issue_id":fresh["issue_id"], "number":fresh["number"],
                "includes_created_edit":True, "initial_revision_id":fresh["initial_body_revision_id"],
                "initial_body_sha256":hashlib.sha256(fresh["body"].encode()).hexdigest(),
                "initial_title_sha256":hashlib.sha256(fresh["title"].encode()).hexdigest(),
                "version_time":fresh["initial_revision_edited_at"],
                "retrospective_record_created_at":fresh["initial_revision_record_created_at"]})
        return {"schema":"checked_creation_data_attestation_v1", "case_id":case["case_id"],
            "audit":dict(ATTESTATION_AUDIT), "attestation":bundle_ref,
            "original_candidate_proof":bundle["original_candidate_proof"], "original_wave_receipt":bundle["original_wave_receipt"],
            "official_graphql_records":fresh_refs, "official_documentation":documents, "issues":metadata,
            "strict_code_time_checks_required_separately":True}
    except (OSError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError("creation_attestation_invalid") from exc
