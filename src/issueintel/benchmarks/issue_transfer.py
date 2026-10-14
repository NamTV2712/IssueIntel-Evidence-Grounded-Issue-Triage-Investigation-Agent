"""Pure replay of an issue's archived opening across one official transfer.

Issue transfer can change repository, number and database ID. It is distinct
from a repository rename, which preserves the repository identity. This module
requires an official transfer event and a captured permanent API redirect before
connecting those different identities. It never rewrites an original receipt,
fetches a source, or certifies code publication/benchmark membership by itself.

Hashes bind supplied bytes, not their historical authenticity. The caller must
replay immutable acquisition references and the original Main Dev assignment.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from issueintel.repository.acquire import parse_github_reference
from issueintel.runtime.io import digest
from .strict_dev import PUBLIC_KEYS

_LOADED_SOURCE = Path(__file__).read_bytes()

TRANSFER_QUERY = """query($owner:String!,$name:String!,$number:Int!){
 repository(owner:$owner,name:$name){id databaseId nameWithOwner
 issue(number:$number){id databaseId number url createdAt
 timelineItems(itemTypes:[TRANSFERRED_EVENT],first:100){totalCount
 pageInfo{hasNextPage endCursor} nodes{... on TransferredEvent{
 id createdAt fromRepository{id databaseId nameWithOwner}
 issue{id databaseId number url}}}}}}
}"""


class _Invalid(ValueError):
    pass


def _require(condition, reason):
    if not condition:
        raise _Invalid(reason)


def _positive_int(value):
    return type(value) is int and value > 0


def _instant(value):
    _require(isinstance(value, str) and value, "transfer_timestamp_invalid")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(instant.tzinfo is not None and instant.utcoffset() is not None,
             "transfer_timestamp_timezone_missing")
    return instant.astimezone(timezone.utc)


def _raw_json(record, key, maximum):
    encoded = record.get(key)
    _require(isinstance(encoded, str) and len(encoded) <= ((maximum + 2) // 3) * 4,
             "transfer_primary_response_bytes_missing_or_capped")
    raw = base64.b64decode(encoded, validate=True)
    _require(len(raw) <= maximum and hashlib.sha256(raw).hexdigest() == record.get("response_sha256"),
             "transfer_primary_response_hash_mismatch")
    return json.loads(raw)


def _one(bundle, key):
    values = bundle.get(key)
    _require(isinstance(values, list) and len(values) == 1 and isinstance(values[0], dict),
             "transfer_single_source_record_required")
    return values[0]


def verify_transferred_opening(case, bundle):
    """Return ``(checked_metadata, [])`` or ``(None, reasons)`` without I/O.

    ``checked_metadata`` includes the exact archived title/body/query_text; do
    not put those strings in public reports. The destination issue remains the
    original query assignment. The origin's repository and issue IDs are kept
    separately from the destination/code repository identity. One complete
    transfer is currently supported; multiple transfers remain unverified.

    Required bundle fields: source_case_sha256, query_policy, issue_references,
    repository_identity_fact, current_issue_receipts, transfer_records,
    redirect_receipts, opening_matches. The last four each contain one record.
    The caller separately verifies code ancestry and publication before query_time.
    """
    try:
        _require(isinstance(case, dict) and set(case) == PUBLIC_KEYS and case.get("split") == "main_dev",
                 "public_projection_or_dev_membership_invalid")
        _require(isinstance(bundle, dict) and bundle.get("source_case_sha256") == digest(case)
                 and bundle.get("query_policy") == "original_opening_query",
                 "query_source_binding_invalid")
        repository = case["repository"]
        _require(parse_github_reference(repository).repository == repository,
                 "transfer_destination_repository_invalid")
        current = _one(bundle, "current_issue_receipts")
        reference = _one(bundle, "issue_references")
        number = reference.get("number")
        _require(reference.get("repository") == repository and _positive_int(number)
                 and reference.get("source_kind", "github_issues") == "github_issues",
                 "transfer_original_assignment_invalid")
        current_url = f"https://api.github.com/repos/{repository}/issues/{number}"
        current_html = f"https://github.com/{repository}/issues/{number}"
        current_payload = current["payload"]
        _require(isinstance(current_payload, dict) and current.get("source_url") == current_url
                 and current_payload.get("url") == current_url
                 and current_payload.get("html_url") == current_html
                 and _positive_int(current_payload.get("number")) and current_payload.get("number") == number
                 and _positive_int(current_payload.get("id"))
                 and "pull_request" not in current_payload
                 and current.get("payload_sha256") == digest(current_payload),
                 "transfer_current_issue_identity_invalid")
        current_id = current_payload["id"]
        created = _instant(current_payload["created_at"])
        current_fetched = _instant(current["fetched_at"])
        _require(created <= current_fetched, "transfer_current_issue_chronology_invalid")

        fact = bundle["repository_identity_fact"]
        repo_payload = fact["payload"]
        _require(fact.get("requested_repository") == repository
                 and fact.get("source_url") == "https://api.github.com/repos/" + repository
                 and _positive_int(repo_payload.get("id")) and repo_payload.get("private") is False,
                 "transfer_destination_repository_identity_unresolved")
        destination_repo_id = repo_payload["id"]

        transfer = _one(bundle, "transfer_records")
        owner, name = repository.split("/")
        _require(transfer.get("source_url") == "https://api.github.com/graphql"
                 and transfer.get("case_id") == case["case_id"]
                 and transfer.get("case_sha256") == digest(case)
                 and transfer.get("status") == "response_received"
                 and transfer.get("query") == TRANSFER_QUERY
                 and transfer.get("variables") == {"owner": owner, "name": name, "number": number},
                 "transfer_graphql_source_binding_invalid")
        payload = _raw_json(transfer, "raw_response_base64", 1_000_000)
        _require(payload == transfer.get("payload") and not payload.get("errors"),
                 "transfer_graphql_response_binding_invalid")
        observed_repo = payload["data"]["repository"]
        issue = observed_repo["issue"]
        _require(observed_repo.get("nameWithOwner") == repository
                 and _positive_int(observed_repo.get("databaseId")) and observed_repo.get("databaseId") == destination_repo_id
                 and _positive_int(issue.get("databaseId")) and issue.get("databaseId") == current_id
                 and _positive_int(issue.get("number")) and issue.get("number") == number
                 and isinstance(issue.get("id"), str) and bool(issue["id"])
                 and issue.get("url") == current_html and _instant(issue["createdAt"]) == created,
                 "transfer_graphql_current_identity_invalid")
        timeline = issue["timelineItems"]
        _require(type(timeline.get("totalCount")) is int and timeline.get("totalCount") == 1
                 and timeline.get("pageInfo", {}).get("hasNextPage") is False
                 and isinstance(timeline.get("nodes"), list) and len(timeline["nodes"]) == 1,
                 "transfer_history_incomplete_or_multihop")
        event = timeline["nodes"][0]
        _require(isinstance(event.get("id"), str) and bool(event["id"]),
                 "transfer_official_event_identity_missing")
        origin = event["fromRepository"]
        origin_repo_id, origin_repository = origin.get("databaseId"), origin.get("nameWithOwner")
        _require(_positive_int(origin_repo_id) and origin_repo_id != destination_repo_id
                 and isinstance(origin_repository, str)
                 and parse_github_reference(origin_repository).repository == origin_repository,
                 "transfer_origin_repository_invalid_or_rename_only")
        destination = event["issue"]
        _require(_positive_int(destination.get("databaseId")) and destination.get("databaseId") == current_id
                 and _positive_int(destination.get("number")) and destination.get("number") == number
                 and destination.get("url") == current_html and destination.get("id") == issue.get("id"),
                 "transfer_official_event_destination_invalid")
        transfer_time, transfer_fetched = _instant(event["createdAt"]), _instant(transfer["fetched_at"])
        _require(created < transfer_time <= transfer_fetched,
                 "transfer_official_event_chronology_invalid")

        redirect = _one(bundle, "redirect_receipts")
        _require(redirect.get("case_id") == case["case_id"]
                 and redirect.get("kind") == "official_old_issue_redirect"
                 and redirect.get("status") in {"http_access_failure", "response_received"}
                 and type(redirect.get("http_status")) is int and redirect["http_status"] == 301,
                 "transfer_official_permanent_redirect_missing")
        old_url_pattern = re.fullmatch(r"https://api\.github\.com/repos/" + re.escape(origin_repository)
                                      + r"/issues/([1-9][0-9]*)", redirect.get("source_url", ""))
        _require(old_url_pattern is not None, "transfer_redirect_origin_invalid")
        old_number = int(old_url_pattern.group(1))
        raw_key = "raw_error_base64" if "raw_error_base64" in redirect else "raw_response_base64"
        redirect_body = _raw_json(redirect, raw_key, 4096)
        _require(isinstance(redirect_body, dict) and redirect_body.get("url") == current_url
                 and redirect.get("response_headers", {}).get("Location") == current_url,
                 "transfer_redirect_destination_invalid")
        redirect_fetched = _instant(redirect["fetched_at"])
        _require(transfer_time <= redirect_fetched, "transfer_redirect_capture_predates_transfer")

        opening = _one(bundle, "opening_matches")
        _require(isinstance(opening.get("raw_event_line"), str)
                 and len(opening["raw_event_line"]) <= 64 * 1024 * 1024,
                 "transfer_raw_opening_integrity_invalid")
        raw_line = opening["raw_event_line"].encode("utf-8")
        raw_event = json.loads(raw_line)
        _require(len(raw_line) <= 64 * 1024 * 1024 and raw_event == opening.get("event")
                 and hashlib.sha256(raw_line).hexdigest() == opening.get("raw_line_sha256")
                 and digest(raw_event) == opening.get("event_sha256"),
                 "transfer_raw_opening_integrity_invalid")
        archive_url = opening.get("source_url", "")
        archive = re.fullmatch(r"https://data\.gharchive\.org/(\d{4}-\d{2}-\d{2})-(\d|1\d|2[0-3])\.json\.gz",
                              archive_url)
        _require(archive is not None and raw_event.get("type") == "IssuesEvent"
                 and raw_event.get("public") is True,
                 "transfer_raw_opening_source_invalid")
        archive_repo = raw_event["repo"]
        _require(_positive_int(archive_repo.get("id")) and archive_repo.get("id") == origin_repo_id
                 and archive_repo.get("name") == origin_repository
                 and archive_repo.get("private") is not True,
                 "transfer_raw_opening_origin_repository_invalid")
        archived_payload = raw_event["payload"]
        archived_issue = archived_payload["issue"]
        old_url = redirect["source_url"]
        _require(archived_payload.get("action") == "opened" and isinstance(archived_issue, dict)
                 and "pull_request" not in archived_issue
                 and _positive_int(archived_issue.get("number")) and archived_issue.get("number") == old_number
                 and _positive_int(archived_issue.get("id"))
                 and archived_issue.get("url") == old_url
                 and archived_issue.get("html_url") == f"https://github.com/{origin_repository}/issues/{old_number}",
                 "transfer_raw_opening_issue_identity_invalid")
        _require(isinstance(archived_issue.get("title"), str) and bool(archived_issue["title"])
                 and isinstance(archived_issue.get("body"), str),
                 "transfer_complete_opening_text_missing")
        opening_time = _instant(archived_issue["created_at"])
        emitted = _instant(raw_event["created_at"])
        _require(opening_time == created and _instant(archived_issue["updated_at"]) == opening_time
                 and opening_time <= emitted < transfer_time
                 and emitted <= min(current_fetched, transfer_fetched, redirect_fetched),
                 "transfer_initial_version_or_opening_chronology_invalid")
        hour = emitted.replace(minute=0, second=0, microsecond=0)
        _require(archive.group(1) == hour.strftime("%Y-%m-%d") and int(archive.group(2)) == hour.hour,
                 "transfer_archive_hour_unbound")
        if opening.get("archive_hour") is not None:
            marker = opening["archive_hour"]
            marker_hour = (hour if marker == archive.group(1) + "-" + str(hour.hour)
                           else _instant(marker))
            _require(marker_hour == hour, "transfer_archive_hour_unbound")
        query = archived_issue["title"] + "\n" + archived_issue["body"] + "\n"
        return {"query_text": query, "title": archived_issue["title"], "body": archived_issue["body"],
                "query_text_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                "query_time": opening_time.isoformat().replace("+00:00", "Z"),
                "original_created_at": archived_issue["created_at"],
                "origin_repository": origin_repository, "origin_repository_id": origin_repo_id,
                "origin_issue_number": old_number, "origin_issue_id": archived_issue["id"],
                "current_repository": repository, "current_repository_id": destination_repo_id,
                "current_issue_number": number, "current_issue_id": current_id,
                "transfer_event_id": event["id"], "transferred_at": event["createdAt"],
                "transfer_record_fetched_at": transfer["fetched_at"],
                "redirect_record_fetched_at": redirect["fetched_at"],
                "redirect_headers_validation_scope": "captured parsed Location and exact raw JSON destination; no raw wire header bytes",
                "raw_opening_line_sha256": opening["raw_line_sha256"],
                "raw_events_rewritten": False, "current_assignment_rewritten": False,
                "code_publication_verified_by_this_helper": False}, []
    except _Invalid as error:
        return None, [str(error)]
    except (KeyError, ValueError, TypeError, AttributeError, UnicodeError, binascii.Error, OverflowError):
        return None, ["transfer_proof_malformed"]
