import copy

import pytest

from issueintel.benchmarks.strict_recovery import (
    _recovery_issue_keys, _transfer_source_binding, check_recovered_proof,
)
from issueintel.runtime.io import digest
from test_issue_transfer import fixture, opening_seal
from test_strict_recovery import chain


def publication_fixture():
    case, bundle = fixture()
    ancestry = chain(3)
    case["revision"] = ancestry["base_revision"]
    bundle["source_case_sha256"] = digest(case)
    bundle["transfer_records"][0]["case_sha256"] = digest(case)
    selected = ancestry["selected_snapshot_revision"]
    event = {
        "type": "PushEvent", "public": True,
        "created_at": "2020-01-01T14:09:59Z",
        "repo": {"id": 300, "name": case["repository"]},
        "payload": {"head": selected},
    }
    push = {"event": event, "source_url": "https://data.gharchive.org/2020-01-01-14.json.gz"}
    opening_seal(push)
    bundle.update(event_schema_version="transferred_initial_issue_archive_v1",
                  selected_snapshot_revision=selected,
                  long_ancestry_receipt=ancestry, push_match=push)
    return case, bundle


def test_origin_opening_and_destination_code_publication_are_replayed_separately():
    case, bundle = publication_fixture()
    original = copy.deepcopy(bundle)
    report, reasons = check_recovered_proof(case, bundle)
    assert reasons == []
    assert report["strict_issue_time_receipt_checks_passed"]
    assert report["query_identity_lineage"]["origin_repository_id"] == 100
    assert report["query_identity_lineage"]["current_repository_id"] == 300
    assert report["query_time"] == "2020-01-01T14:10:00Z"
    assert report["revision_availability"]["available_before_issue"]
    assert not report["raw_events_rewritten"] and not report["source_case_was_rewritten"]
    assert bundle == original


@pytest.mark.parametrize("fault,expected", [
    ("equal_cutoff", "snapshot_not_pre_issue"),
    ("after_cutoff", "snapshot_not_pre_issue"),
    ("origin_repo_publication", "independent_code_availability_missing"),
    ("wrong_revision", "independent_code_availability_missing"),
    ("raw_push_tamper", "independent_code_availability_missing"),
    ("no_push", "independent_code_availability_missing"),
    ("wrong_ancestry", "snapshot_identity_unverified"),
    ("raw_commit_tamper", "snapshot_identity_unverified"),
])
def test_transfer_never_supplies_missing_destination_snapshot_proof(fault, expected):
    case, bundle = publication_fixture()
    push = bundle["push_match"]
    if fault == "equal_cutoff":
        push["event"]["created_at"] = "2020-01-01T14:10:00Z"
    elif fault == "after_cutoff":
        push["event"]["created_at"] = "2020-01-01T14:10:01Z"
    elif fault == "origin_repo_publication":
        push["event"]["repo"] = {"id": 100, "name": "origin/project"}
    elif fault == "wrong_revision":
        push["event"]["payload"]["head"] = "a" * 40
    elif fault == "no_push":
        bundle.pop("push_match")
    elif fault == "wrong_ancestry":
        bundle["long_ancestry_receipt"] = chain(4)
    elif fault == "raw_commit_tamper":
        ancestry = bundle["long_ancestry_receipt"]
        ancestry["commit_parent_chain"][0]["raw_commit_base64"] = "ZmFrZQ=="
        ancestry["parent_chain_sha256"] = digest(ancestry["commit_parent_chain"])
    opening_seal(push)
    if fault == "raw_push_tamper":
        push["raw_line_sha256"] = "0" * 64
    report, reasons = check_recovered_proof(case, bundle)
    assert expected in reasons
    assert not report["strict_issue_time_receipt_checks_passed"]


def test_transfer_publication_uses_aware_instants():
    case, bundle = publication_fixture()
    push = bundle["push_match"]
    push["event"]["created_at"] = "2020-01-01T07:09:59-07:00"
    opening_seal(push)
    assert check_recovered_proof(case, bundle)[1] == []


def test_both_origin_and_assigned_issue_are_checked_against_reserved_membership():
    case, bundle = publication_fixture()
    report, reasons = check_recovered_proof(case, bundle)
    assert not reasons
    assert _recovery_issue_keys(bundle, report) == {
        "github:origin/project#3", f"github:{case['repository'].lower()}#7",
    }


def bind_source_files(tmp_path, case, bundle):
    import hashlib
    import json

    def save(name, value):
        path = tmp_path / "data" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return {"path": path.relative_to(tmp_path).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    graph = save("graph.json", bundle["transfer_records"][0])
    redirect = save("redirect.json", bundle["redirect_receipts"][0])
    capture = save("capture.json", {
        "schema": "original_main_dev_transferred_initial_capture_v1",
        "case_id": case["case_id"], "official_transfer_receipt": graph,
        "official_redirect_receipt": redirect,
        "scan": {"match": bundle["opening_matches"][0]},
    })
    bundle["transfer_source_references"] = [capture, graph, redirect]


def test_transfer_embedded_objects_match_checked_primary_source_bytes(tmp_path):
    case, bundle = publication_fixture()
    bind_source_files(tmp_path, case, bundle)
    assert _transfer_source_binding(tmp_path, case, bundle) == bundle["transfer_source_references"]


@pytest.mark.parametrize("fault", ["opening", "graph", "redirect", "missing_refs"])
def test_valid_file_hashes_cannot_bind_different_embedded_transfer_objects(tmp_path, fault):
    case, bundle = publication_fixture()
    bind_source_files(tmp_path, case, bundle)
    if fault == "opening":
        bundle["opening_matches"][0]["event"]["payload"]["issue"]["body"] += " changed"
        opening_seal(bundle["opening_matches"][0])
    elif fault == "graph":
        bundle["transfer_records"][0]["fetched_at"] = "2026-10-14T00:00:00Z"
    elif fault == "redirect":
        bundle["redirect_receipts"][0]["fetched_at"] = "2026-10-14T00:00:00Z"
    else:
        bundle.pop("transfer_source_references")
    with pytest.raises(ValueError, match="primary source"):
        _transfer_source_binding(tmp_path, case, bundle)
