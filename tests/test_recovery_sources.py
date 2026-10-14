import base64
import copy
import hashlib
import json
import pytest

from test_strict_recovery import identity_fixture, match
from issueintel.benchmarks.recovery_sources import initial_edit_snapshot_candidate, fetch_graphql_record
from issueintel.benchmarks.strict_recovery import check_recovered_proof
from issueintel.runtime.io import digest


def history_fixture():
    case, old = identity_fixture()
    current = old["current_issue_receipts"][0]
    created = current["payload"]["created_at"]
    latest = "2014-10-31T22:15:00Z"
    item = {"databaseId": 99, "number": 7, "url": "https://github.com/" + case["repository"] + "/issues/7",
        "createdAt": created, "lastEditedAt": latest, "title": "Later title", "body": "Later complete body",
        "userContentEdits": {"totalCount": 2, "pageInfo": {"hasNextPage": False}, "nodes": [
            {"id": "current", "createdAt": latest, "editedAt": latest, "deletedAt": None, "diff": "Later complete body"},
            {"id": "initial", "createdAt": latest, "editedAt": created, "deletedAt": None, "diff": "Initial complete body"}]},
        "timelineItems": {"totalCount": 1, "pageInfo": {"hasNextPage": False}, "nodes": [
            {"id": "rename", "createdAt": latest, "previousTitle": "Original title", "currentTitle": "Later title"}]}}
    payload = {"data": {"repository": {"nameWithOwner": case["repository"], "issue": item}}}
    record = {"case_sha256": digest(case), "source_url": "https://api.github.com/graphql", "kind": "edits",
        "status": "response_received", "variables": {"owner": case["repository"].split("/")[0], "name": case["repository"].split("/")[1], "number": 7},
        "payload": payload, "fetched_at": "2026-10-13T00:00:00Z"}
    seal(record)
    bundle = {key: value for key, value in old.items() if key != "opening_matches"}
    bundle.update(event_schema_version="official_initial_edit_snapshot_v1", initial_edit_records=[[record]])
    return case, current, record, bundle


def seal(record):
    raw = json.dumps(record["payload"]).encode()
    record.update(raw_response_base64=base64.b64encode(raw).decode(), response_sha256=hashlib.sha256(raw).hexdigest())


def test_explicit_initial_version_replays_body_title_and_strict_snapshot_time():
    case, current, record, bundle = history_fixture()
    value, reasons = initial_edit_snapshot_candidate(case, current, [record])
    assert not reasons and value["title"] == "Original title" and value["body"] == "Initial complete body"
    assert value["initial_revision_record_created_at"] > value["initial_revision_edited_at"]
    report, reasons = check_recovered_proof(case, bundle)
    assert not reasons and report["strict_issue_time_receipt_checks_passed"]
    assert report["retrospective_capture"] and not report["stored_text_quality_human_verified"]
    late = copy.deepcopy(bundle)
    late["push_match"]["event"]["created_at"] = current["payload"]["created_at"]
    late["push_match"] = match(late["push_match"]["event"])
    assert "snapshot_not_pre_issue" in check_recovered_proof(case, late)[1]


@pytest.mark.parametrize("fault", ["missing_initial", "deleted", "summary", "title_gap", "partial", "wrong_id", "raw_tamper"])
def test_incomplete_history_never_becomes_opening_proof(fault):
    case, current, record, bundle = history_fixture()
    item = record["payload"]["data"]["repository"]["issue"]
    if fault == "missing_initial":
        item["userContentEdits"]["nodes"] = []
        item["userContentEdits"]["totalCount"] = 0
    elif fault == "deleted":
        item["userContentEdits"]["nodes"][-1]["deletedAt"] = "2025-01-01T00:00:00Z"
    elif fault == "summary":
        item["userContentEdits"]["nodes"][0]["diff"] = "A shortened summary"
    elif fault == "title_gap":
        item["timelineItems"]["nodes"][0]["currentTitle"] = "Other title"
    elif fault == "partial":
        item["userContentEdits"]["pageInfo"]["hasNextPage"] = True
    elif fault == "wrong_id":
        item["databaseId"] = 100
    seal(record)
    if fault == "raw_tamper":
        record["response_sha256"] = "0" * 64
    assert initial_edit_snapshot_candidate(case, current, [record])[1]


def test_source_fetch_denies_holdout_and_hidden_fields_before_reading_anything(tmp_path):
    case, _, _, _ = history_fixture()
    for changed in (dict(case, split="main_test"), dict(case, split="temporal_candidate"), dict(case, patch="hidden")):
        with pytest.raises(ValueError):
            fetch_graphql_record(tmp_path, changed, kind="edits", number=7)
