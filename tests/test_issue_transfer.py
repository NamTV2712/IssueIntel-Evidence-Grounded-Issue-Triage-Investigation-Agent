import base64
import copy
import hashlib
import json

import pytest

from issueintel.benchmarks.issue_transfer import TRANSFER_QUERY, verify_transferred_opening
from issueintel.runtime.io import digest
from test_strict_dev import pure_fixture


def seal(record, key="raw_response_base64", payload=None):
    raw = json.dumps(record["payload"] if payload is None else payload, ensure_ascii=False).encode("utf-8")
    record[key] = base64.b64encode(raw).decode("ascii")
    record["response_sha256"] = hashlib.sha256(raw).hexdigest()


def opening_seal(match):
    raw = json.dumps(match["event"], ensure_ascii=False) + "\n"
    match.update(raw_event_line=raw, raw_line_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                 event_sha256=digest(match["event"]))


def fixture():
    case, _ = pure_fixture()
    current_repo, old_repo = case["repository"], "origin/project"
    created, transferred, fetched = "2020-01-01T14:10:00Z", "2020-02-01T00:00:00Z", "2026-10-13T00:00:00Z"
    current_url = f"https://api.github.com/repos/{current_repo}/issues/7"
    current_html = f"https://github.com/{current_repo}/issues/7"
    old_url = f"https://api.github.com/repos/{old_repo}/issues/3"
    current_payload = {"id": 200, "number": 7, "url": current_url, "html_url": current_html,
                       "created_at": created, "updated_at": transferred, "title": "Current title", "body": "Current body"}
    current = {"source_url": current_url, "fetched_at": fetched, "payload": current_payload,
               "payload_sha256": digest(current_payload)}
    graph_issue = {"id": "current-node", "databaseId": 200, "number": 7, "url": current_html,
                   "createdAt": created, "timelineItems": {"totalCount": 1,
                    "pageInfo": {"hasNextPage": False}, "nodes": [{"id": "transfer-node", "createdAt": transferred,
                    "fromRepository": {"id": "origin-node", "databaseId": 100, "nameWithOwner": old_repo},
                    "issue": {"id": "current-node", "databaseId": 200, "number": 7, "url": current_html}}]}}
    owner, name = current_repo.split("/")
    graph = {"case_id": case["case_id"], "case_sha256": digest(case), "source_url": "https://api.github.com/graphql",
             "query": TRANSFER_QUERY, "variables": {"owner": owner, "name": name, "number": 7},
             "status": "response_received", "fetched_at": fetched,
             "payload": {"data": {"repository": {"id": "repo-node", "databaseId": 300,
                 "nameWithOwner": current_repo, "issue": graph_issue}}}}
    seal(graph)
    redirect = {"case_id": case["case_id"], "kind": "official_old_issue_redirect", "http_status": 301,
                "status": "http_access_failure", "source_url": old_url, "fetched_at": fetched,
                "response_headers": {"Location": current_url}}
    seal(redirect, "raw_error_base64", {"message": "Moved Permanently", "url": current_url})
    archived_issue = {"id": 101, "number": 3, "url": old_url, "html_url": f"https://github.com/{old_repo}/issues/3",
                      "created_at": created, "updated_at": created, "title": "Initial title  ",
                      "body": "Full initial body\r\nUnicode: Tiếng Việt ✓\n"}
    opened = {"type": "IssuesEvent", "public": True, "created_at": "2020-01-01T14:10:03Z",
              "repo": {"id": 100, "name": old_repo}, "payload": {"action": "opened", "issue": archived_issue}}
    match = {"event": opened, "source_url": "https://data.gharchive.org/2020-01-01-14.json.gz",
             "archive_hour": "2020-01-01T14:00:00Z"}
    opening_seal(match)
    bundle = {"source_case_sha256": digest(case), "query_policy": "original_opening_query",
              "issue_references": [{"repository": current_repo, "number": 7}],
              "repository_identity_fact": {"requested_repository": current_repo,
                  "source_url": "https://api.github.com/repos/" + current_repo,
                  "payload": {"id": 300, "private": False}},
              "current_issue_receipts": [current], "transfer_records": [graph],
              "redirect_receipts": [redirect], "opening_matches": [match]}
    return case, bundle


def test_transfer_recovers_exact_initial_text_with_all_three_identities_separate():
    case, bundle = fixture()
    before = copy.deepcopy(bundle)
    checked, reasons = verify_transferred_opening(case, bundle)
    assert not reasons and checked["query_time"] == "2020-01-01T14:10:00Z"
    assert checked["origin_issue_id"] == 101 and checked["current_issue_id"] == 200
    assert checked["origin_repository_id"] == 100 and checked["current_repository_id"] == 300
    assert checked["query_text"] == "Initial title  \nFull initial body\r\nUnicode: Tiếng Việt ✓\n\n"
    assert checked["query_text_sha256"] == hashlib.sha256(checked["query_text"].encode()).hexdigest()
    assert not checked["code_publication_verified_by_this_helper"]
    assert bundle == before


@pytest.mark.parametrize("fault", [
    "wrong_case", "wrong_variables", "write_query", "graphql_partial", "graphql_errors",
    "wrong_current_id", "wrong_destination_repo", "wrong_from_repo", "rename_only",
    "transfer_before_opening", "transfer_after_fetch", "different_graph_creation", "naive_transfer_time",
    "graph_raw_missing", "graph_raw_tamper", "graph_payload_tamper", "multiple_transfers", "boolean_count",
])
def test_official_lineage_must_match_original_destination_and_complete_primary_record(fault):
    case, bundle = fixture()
    graph = bundle["transfer_records"][0]
    repo = graph["payload"]["data"]["repository"]
    issue = repo["issue"]
    event = issue["timelineItems"]["nodes"][0]
    if fault == "wrong_case": graph["case_sha256"] = "0" * 64
    elif fault == "wrong_variables": graph["variables"]["number"] = 8
    elif fault == "write_query": graph["query"] = "mutation { addComment(input:{}) { clientMutationId } }"
    elif fault == "graphql_partial": issue["timelineItems"]["pageInfo"]["hasNextPage"] = True
    elif fault == "graphql_errors": graph["payload"]["errors"] = [{"message": "Partial response"}]
    elif fault == "wrong_current_id": event["issue"]["databaseId"] = 999
    elif fault == "wrong_destination_repo": repo["databaseId"] = 999
    elif fault == "wrong_from_repo": event["fromRepository"]["databaseId"] = 999
    elif fault == "rename_only": event["fromRepository"]["databaseId"] = 300
    elif fault == "transfer_before_opening": event["createdAt"] = "2019-12-31T00:00:00Z"
    elif fault == "transfer_after_fetch": event["createdAt"] = "2027-01-01T00:00:00Z"
    elif fault == "different_graph_creation": issue["createdAt"] = "2020-01-01T14:10:01Z"
    elif fault == "naive_transfer_time": event["createdAt"] = "2020-02-01T00:00:00"
    elif fault == "multiple_transfers": issue["timelineItems"]["totalCount"] = 2
    elif fault == "boolean_count": issue["timelineItems"]["totalCount"] = True
    seal(graph)
    if fault == "graph_raw_missing": del graph["raw_response_base64"]
    elif fault == "graph_raw_tamper": graph["response_sha256"] = "0" * 64
    elif fault == "graph_payload_tamper": event["fromRepository"]["databaseId"] = 999
    assert verify_transferred_opening(case, bundle)[1]


@pytest.mark.parametrize("fault", ["wrong_status", "wrong_source", "wrong_location", "wrong_body_target",
    "missing_raw_body", "raw_body_tamper", "capture_before_transfer"])
def test_exact_permanent_redirect_is_required_for_the_archived_origin_number(fault):
    case, bundle = fixture()
    redirect = bundle["redirect_receipts"][0]
    if fault == "wrong_status": redirect["http_status"] = 302
    elif fault == "wrong_source": redirect["source_url"] = "https://api.github.com/repos/origin/project/issues/4"
    elif fault == "wrong_location": redirect["response_headers"]["Location"] += "?untrusted=1"
    elif fault == "wrong_body_target": seal(redirect, "raw_error_base64", {"url": "https://evil.invalid/issues/7"})
    elif fault == "missing_raw_body": del redirect["raw_error_base64"]
    elif fault == "raw_body_tamper": redirect["response_sha256"] = "0" * 64
    elif fault == "capture_before_transfer": redirect["fetched_at"] = "2020-01-01T14:10:01Z"
    assert verify_transferred_opening(case, bundle)[1]


@pytest.mark.parametrize("fault", ["edited_opened_copy", "wrong_origin_number", "wrong_origin_repo", "missing_body",
    "pr_payload", "wrong_creation", "emitted_before_opening", "emitted_after_transfer", "naive_creation",
    "private_event", "untrusted_archive_url", "wrong_archive_hour", "raw_line_tamper", "raw_line_missing",
    "zero_global_id", "asserted_identity_without_source", "wrong_archive_marker"])
def test_archive_opening_requires_complete_initial_bytes_and_actual_origin(fault):
    case, bundle = fixture()
    match = bundle["opening_matches"][0]
    event = match["event"]
    issue = event["payload"]["issue"]
    if fault == "edited_opened_copy": issue["updated_at"] = "2020-01-01T14:10:01Z"
    elif fault == "wrong_origin_number": issue["number"] = 4
    elif fault == "wrong_origin_repo": event["repo"]["id"] = 999
    elif fault == "missing_body": del issue["body"]
    elif fault == "pr_payload": issue["pull_request"] = {}
    elif fault == "wrong_creation": issue["created_at"] = issue["updated_at"] = "2020-01-01T14:10:01Z"
    elif fault == "emitted_before_opening": event["created_at"] = "2020-01-01T14:09:59Z"
    elif fault == "emitted_after_transfer": event["created_at"] = "2020-02-01T00:00:00Z"
    elif fault == "naive_creation": issue["created_at"] = "2020-01-01T14:10:00"
    elif fault == "private_event": event["public"] = False
    elif fault == "untrusted_archive_url": match["source_url"] = "https://mirror.invalid/2020-01-01-14.json.gz"
    elif fault == "wrong_archive_hour": match["source_url"] = "https://data.gharchive.org/2020-01-01-15.json.gz"
    elif fault == "wrong_archive_marker": match["archive_hour"] = "2020-01-01T15:00:00Z"
    elif fault == "zero_global_id": issue["id"] = 0
    opening_seal(match)
    if fault == "raw_line_tamper": match["raw_line_sha256"] = "0" * 64
    elif fault == "raw_line_missing": del match["raw_event_line"]
    elif fault == "asserted_identity_without_source": bundle["opening_matches"] = [{"old_issue_id": 101}]
    assert verify_transferred_opening(case, bundle)[1]


@pytest.mark.parametrize("fault", ["holdout", "live", "hidden_field", "wrong_source_hash", "current_pr",
    "current_raw_hash", "wrong_query_policy", "private_code_repo", "wrong_current_reference"])
def test_source_boundary_and_current_assignment_are_never_rewritten(fault):
    case, bundle = fixture()
    if fault == "holdout": case["split"] = "main_test"
    elif fault == "live": case["split"] = "temporal_candidate"
    elif fault == "hidden_field": case["patch"] = "gold sentinel"
    elif fault == "wrong_source_hash": bundle["source_case_sha256"] = "0" * 64
    elif fault == "current_pr": bundle["current_issue_receipts"][0]["payload"]["pull_request"] = {}
    elif fault == "current_raw_hash": bundle["current_issue_receipts"][0]["payload_sha256"] = "0" * 64
    elif fault == "wrong_query_policy": bundle["query_policy"] = "current_query"
    elif fault == "private_code_repo": bundle["repository_identity_fact"]["payload"]["private"] = True
    elif fault == "wrong_current_reference": bundle["issue_references"][0]["number"] = 8
    assert verify_transferred_opening(case, bundle)[1]


def test_equivalent_explicit_utc_offsets_are_compared_as_instants():
    case, bundle = fixture()
    graph = bundle["transfer_records"][0]
    graph["payload"]["data"]["repository"]["issue"]["createdAt"] = "2020-01-01T07:10:00-07:00"
    seal(graph)
    assert not verify_transferred_opening(case, bundle)[1]
