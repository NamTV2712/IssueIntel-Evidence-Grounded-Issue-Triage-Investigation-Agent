"""Synthetic adversarial checks for the additive opening-version source gate."""
import base64
import copy
import hashlib
import json

import pytest

from test_recovery_sources import history_fixture, seal
from issueintel.benchmarks import creation_attestation as gate
from issueintel.benchmarks.strict_dev import _publish
from issueintel.runtime.io import digest


@pytest.fixture
def attestation(tmp_path, monkeypatch):
    case, current, old_record, proof = history_fixture()
    fresh = copy.deepcopy(old_record)
    fresh.update(schema="independent_initial_edit_attestation_v1", case_id=case["case_id"],
        query=gate.ATTESTATION_QUERY, status="response_received")
    fresh["variables"]["cursor"] = None
    fresh["payload"]["data"]["repository"]["issue"]["includesCreatedEdit"] = True
    seal(fresh)
    docs = []
    doc_records = []
    for role, (url, markers) in gate.DOCUMENTATION.items():
        raw = b"<html>" + b" ".join(markers) + b"</html>"
        value = dict(schema="official_edit_history_documentation_receipt_v1", source_url=url, resolved_url=url,
            role=role, fetched_at="2026-10-13T00:00:00Z", response_sha256=hashlib.sha256(raw).hexdigest(),
            raw_response_base64=base64.b64encode(raw).decode())
        doc_records.append(value)
        docs.append(dict(role=role, source_url=url, receipt=_publish(tmp_path,tmp_path/"data/docs",value)))
    initial = old_record["payload"]["data"]["repository"]["issue"]["userContentEdits"]["nodes"][-1]
    proof_ref = _publish(tmp_path,tmp_path/"data/proofs",proof)
    wave = dict(case_id=case["case_id"], case_sha256=digest(case), proof=proof_ref)
    wave["receipt_sha256"] = digest(wave)
    wave_ref = _publish(tmp_path,tmp_path/"data/waves",wave)
    issue = dict(number=7,issue_id=99,created_at=current["payload"]["created_at"],initial_id=initial["id"],
        initial_body_sha256=hashlib.sha256(initial["diff"].encode()).hexdigest())
    bundle = dict(schema="official_initial_edit_additive_attestation_v1", case_id=case["case_id"], case_sha256=digest(case),
        original_candidate_proof=proof_ref,original_wave_receipt=wave_ref,issues=[issue],official_documentation=docs,
        verdict="This untrusted verdict must not determine acceptance")
    state = dict(root=tmp_path,case=case,proof=proof,fresh=fresh,bundle=bundle,docs=doc_records,records=[fresh])

    def republish():
        issue["official_graphql_records"] = [_publish(tmp_path,tmp_path/"data/fresh",record) for record in state["records"]]
        for entry,value in zip(docs,doc_records):
            entry["receipt"] = _publish(tmp_path,tmp_path/"data/docs",value)
        bundle_ref = _publish(tmp_path,tmp_path/"data/bundles",bundle)
        audit = dict(schema="independent_initial_edit_audit_v1",cases=[dict(case_id=case["case_id"],additive_attestation=bundle_ref,
            verdict="rejected")])
        audit_ref = _publish(tmp_path,tmp_path/"data/audits",audit)
        monkeypatch.setattr(gate,"ATTESTATION_AUDIT",audit_ref)
        state.update(audit_ref=audit_ref,bundle_ref=bundle_ref)
    republish()
    state["republish"] = republish
    return state


def verify(state):
    return gate.verify_creation_attestation(state["root"],state["case"],state["proof"])


def test_offline_guard_replays_raw_creation_data_and_ignores_verdict_flags(attestation):
    before = copy.deepcopy(attestation["proof"])
    result = verify(attestation)
    assert result["issues"][0]["includes_created_edit"] is True
    assert result["issues"][0]["initial_revision_id"] == "initial"
    assert len(result["official_documentation"]) == 4
    assert result["strict_code_time_checks_required_separately"] is True
    assert attestation["proof"] == before
    assert (attestation["root"]/result["attestation"]["path"]).is_file()


@pytest.mark.parametrize("fault",["missing_creation_field","false_creation_field","changed_past_body","changed_initial_id",
    "wrong_issue_id","wrong_number","wrong_url","wrong_repository","wrong_case","wrong_case_hash",
    "wrong_query","wrong_variables","wrong_source_url","raw_tamper","parsed_payload_tamper",
    "deleted_initial","partial_history","empty_history","duplicate_version","title_history_gap","undated"])
def test_false_or_incomplete_official_attestation_never_becomes_opening_proof(attestation,fault):
    record = attestation["fresh"]
    repository = record["payload"]["data"]["repository"]
    item = repository["issue"]
    nodes = item["userContentEdits"]["nodes"]
    if fault == "missing_creation_field":
        item.pop("includesCreatedEdit")
    elif fault == "false_creation_field":
        item["includesCreatedEdit"] = False
    elif fault == "changed_past_body":
        nodes[-1]["diff"] = "Incomplete old version, latest still matches whole body"
    elif fault == "changed_initial_id":
        nodes[-1]["id"] = "other_initial"
    elif fault == "wrong_issue_id":
        item["databaseId"] = 100
    elif fault == "wrong_number":
        item["number"] = 8
    elif fault == "wrong_url":
        item["url"] = item["url"].replace("/issues/","/pull/")
    elif fault == "wrong_repository":
        repository["nameWithOwner"] = "other/repo"
    elif fault == "wrong_case":
        record["case_id"] = "verified:other__repo-8"
    elif fault == "wrong_case_hash":
        record["case_sha256"] = "0"*64
    elif fault == "wrong_query":
        record["query"] = record["query"].replace("includesCreatedEdit","")
    elif fault == "wrong_variables":
        record["variables"]["number"] = 8
    elif fault == "wrong_source_url":
        record["source_url"] = "https://example.invalid/graphql"
    elif fault == "deleted_initial":
        nodes[-1]["deletedAt"] = "2026-01-01T00:00:00Z"
    elif fault == "partial_history":
        item["userContentEdits"]["pageInfo"].update(hasNextPage=True,endCursor="unread_page")
    elif fault == "empty_history":
        item["userContentEdits"].update(nodes=[],totalCount=0)
    elif fault == "duplicate_version":
        nodes[-1]["id"] = nodes[0]["id"]
    elif fault == "title_history_gap":
        item["timelineItems"]["nodes"][0]["currentTitle"] = "Unbound title"
    elif fault == "undated":
        record["fetched_at"] = "2026-10-13T00:00:00"
    if fault == "raw_tamper":
        record["response_sha256"] = "0"*64
    elif fault == "parsed_payload_tamper":
        nodes[-1]["diff"] = "Changed parsed payload, old raw bytes retained"
    else:
        seal(record)
    attestation["republish"]()
    with pytest.raises(ValueError,match="creation_attestation_invalid"):
        verify(attestation)


@pytest.mark.parametrize("fault",["missing_doc","wrong_doc_url","wrong_doc_role","doc_raw_tamper","doc_content_invalid","doc_undated"])
def test_official_documentation_is_dated_bound_and_hash_checked(attestation,fault):
    doc = attestation["docs"][0]
    if fault == "missing_doc":
        attestation["bundle"]["official_documentation"].pop()
    elif fault == "wrong_doc_url":
        doc["source_url"] = "https://example.invalid/creation-proof"
    elif fault == "wrong_doc_role":
        doc["role"] = "unrelated"
    elif fault == "doc_raw_tamper":
        doc["response_sha256"] = "0"*64
    elif fault == "doc_content_invalid":
        raw = b"Unrelated content, no creation-data API descriptor"
        doc.update(raw_response_base64=base64.b64encode(raw).decode(),response_sha256=hashlib.sha256(raw).hexdigest())
    elif fault == "doc_undated":
        doc["fetched_at"] = "2026-10-13"
    attestation["republish"]()
    with pytest.raises(ValueError,match="creation_attestation_invalid"):
        verify(attestation)


def test_raw_file_changes_and_missing_audit_deny_before_acceptance(attestation):
    ref = attestation["bundle"]["issues"][0]["official_graphql_records"][0]
    path = attestation["root"]/ref["path"]
    path.write_bytes(path.read_bytes()+b" ")
    with pytest.raises(ValueError):
        verify(attestation)
    (attestation["root"]/attestation["audit_ref"]["path"]).unlink()
    with pytest.raises(ValueError):
        verify(attestation)


def test_guard_denies_holdout_hidden_fields_and_unindexed_cases(attestation):
    for changed in (dict(attestation["case"],split="main_test"),dict(attestation["case"],split="temporal_candidate"),
            dict(attestation["case"],patch="synthetic forbidden field"),dict(attestation["case"],case_id="verified:other__repo-8")):
        with pytest.raises(ValueError):
            gate.verify_creation_attestation(attestation["root"],changed,attestation["proof"])


def test_complete_multi_page_history_accepts_and_false_later_attestation_denies(attestation):
    first = attestation["fresh"]
    second = copy.deepcopy(first)
    one = first["payload"]["data"]["repository"]["issue"]["userContentEdits"]
    two = second["payload"]["data"]["repository"]["issue"]["userContentEdits"]
    one["nodes"] = one["nodes"][:1]
    one["pageInfo"] = dict(hasNextPage=True,endCursor="next_page")
    two["nodes"] = two["nodes"][-1:]
    two["pageInfo"] = dict(hasNextPage=False,endCursor="last")
    second["variables"]["cursor"] = "next_page"
    seal(first)
    seal(second)
    attestation["records"] = [first,second]
    attestation["republish"]()
    assert verify(attestation)["issues"][0]["includes_created_edit"]
    second["payload"]["data"]["repository"]["issue"]["includesCreatedEdit"] = False
    seal(second)
    attestation["republish"]()
    with pytest.raises(ValueError):
        verify(attestation)


def test_page_cursor_cannot_skip_to_an_unbound_page(attestation):
    first = attestation["fresh"]
    second = copy.deepcopy(first)
    first["payload"]["data"]["repository"]["issue"]["userContentEdits"]["pageInfo"] = dict(hasNextPage=True,endCursor="expected")
    second["variables"]["cursor"] = "different"
    seal(first)
    attestation["records"] = [first,second]
    attestation["republish"]()
    with pytest.raises(ValueError):
        verify(attestation)
