"""Model prompts contain public content, not local post-issue lineage."""
from copy import deepcopy
import hashlib
import json

import pytest

from issueintel.annotations.generator import generate_annotation, public_payload
from issueintel.annotations.models import AnnotationError
from issueintel.annotations.public_inputs import issue_locator, model_evidence, model_payload, repository_name
from issueintel.investigation.providers import ProviderError
from issueintel.investigation.verifier import verify_hypotheses


FUTURE = "FUTURE_PR_PRIVATE_SENTINEL_99999"
LOCAL = "PRIVATE_LOCAL_PATH_SENTINEL"


@pytest.fixture
def inputs():
    case = {"case_id": "verified:" + FUTURE, "instance_id": FUTURE, "split": "main_test",
        "parent_case_id": FUTURE, "variant_id": FUTURE, "pull_created_at": "2099-01-01T00:00:00Z",
        "repository": "owner/project", "revision": "a" * 40,
        "problem_statement": "parse(value) raises an error for None input.",
        "issue_created_at": "2025-01-01T00:00:00Z"}
    evidence = [{"id": "issue_123", "kind": "issue", "locator": case["case_id"],
        "text": case["problem_statement"], "sha256": hashlib.sha256(case["problem_statement"].encode()).hexdigest(),
        "revision": None, "available_at": case["issue_created_at"],
        "provenance": {"kind": "verified_original_query", "edit_history": "opening_event_matched",
            "receipt": {"case_id": case["case_id"], "split": "main_test", "source_query_sha256": FUTURE,
                "private_path": LOCAL, "nested": [{"instance_id": FUTURE}]}}},
        {"id": "e_code", "kind": "code", "locator": "parser.py:1-2", "text": "def parse(value):\n    return int(value)",
         "sha256": hashlib.sha256(b"def parse(value):\n    return int(value)").hexdigest(), "revision": "a" * 40,
         "available_at": "2024-12-31T00:00:00Z", "provenance": {"repository": LOCAL,
             "protocol": "issue_time", "snapshot_sha256": "b" * 64, "nested": {"case_id": FUTURE}}}]
    response = {"hypotheses": [{"id": f"H{number}", "statement": f"Distinct parser mechanism {number}",
        "support_ids": ["e_code"], "counter_ids": [], "counter_status": "not_found",
        "falsification_test": f"Check input contract {number}"} for number in range(1, 4)],
        "abstention": {"label": "uncertain", "reason": "No reproduction was executed."}}
    return case, evidence, response


def assert_no_metadata(messages):
    encoded = json.dumps(messages)
    assert FUTURE not in encoded and LOCAL not in encoded
    assert "2099-01-01" not in encoded
    supplied = json.loads(messages[1]["content"])
    assert "main_test" not in json.dumps(supplied)
    assert "receipt" not in json.dumps(supplied)


def test_generation_keeps_local_lineage_but_only_sends_bounded_model_projection(inputs):
    case, evidence, response = inputs
    original = deepcopy((case, evidence))
    seen = []
    def provider(messages, max_output_tokens):
        seen.append(deepcopy(messages))
        return deepcopy(response)
    annotation = generate_annotation(case, evidence, provider, provider="synthetic", model="fixture")
    assert annotation.case_id == case["case_id"]
    assert annotation.provenance.method == "public_evidence_hypothesis_generation.v2"
    assert (case, evidence) == original
    assert public_payload(case, evidence)["evidence"][0]["provenance"]["receipt"]["case_id"] == case["case_id"]
    assert_no_metadata(seen[0])
    supplied = json.loads(seen[0][1]["content"])
    assert supplied["evidence"][0]["locator"] == "input://issue"
    assert supplied["evidence"][0]["provenance"] == {"kind": "verified_original_query", "edit_history": "opening_event_matched"}
    assert supplied["evidence"][1]["locator"] == "parser.py:1-2"
    assert "provenance" not in supplied["evidence"][1]
    assert supplied["evidence"][1]["sha256"] == evidence[1]["sha256"]
    assert {record["id"] for record in supplied["evidence"]} == {record["id"] for record in evidence}


@pytest.mark.parametrize("repair", ["schema", "strict_fallback"])
def test_generation_repair_messages_preserve_projection_and_citation_validation(inputs, repair):
    case, evidence, response = inputs
    seen = []
    invalid = deepcopy(response)
    invalid["hypotheses"][0]["support_ids"] = ["fabricated_code_id"]
    class Provider:
        def __call__(self, messages, max_output_tokens):
            seen.append(deepcopy(messages))
            return invalid if len(seen) == 1 else deepcopy(response)
        def complete_structured(self, messages, *, max_output_tokens, schema):
            if repair == "schema":
                return self(messages, max_output_tokens)
            seen.append(deepcopy(messages))
            raise ProviderError("Synthetic strict rejection", status=400, code="json_validate_failed")
        def complete_json(self, messages, *, max_output_tokens):
            seen.append(deepcopy(messages))
            return deepcopy(response)
    result = generate_annotation(case, evidence, Provider(), provider="synthetic", model="fixture")
    assert len(seen) == 2 and result.provenance.repair_count == 1
    assert all(hypothesis.support_ids == ["e_code"] for hypothesis in result.hypotheses)
    for messages in seen:
        assert_no_metadata(messages)


def test_fabricated_code_citations_are_not_mapped_after_projection(inputs):
    case, evidence, response = inputs
    response["hypotheses"][0]["support_ids"] = ["fabricated_code_id"]
    seen = []
    def provider(messages, max_output_tokens):
        seen.append(deepcopy(messages))
        return deepcopy(response)
    with pytest.raises(AnnotationError, match="Unknown cited evidence"):
        generate_annotation(case, evidence, provider)
    assert len(seen) == 2
    for messages in seen:
        assert_no_metadata(messages)


@pytest.mark.parametrize("hidden", ["gold_patch", "source_human", "underspecified_ordinal", "human_rationale"])
@pytest.mark.parametrize("stage", ["generation", "critic"])
def test_nested_restricted_fields_rejected_before_projection_or_dispatch(inputs, hidden, stage):
    case, evidence, response = inputs
    annotation = generate_annotation(case, evidence, lambda *a, **k: deepcopy(response)).to_dict()
    evidence[0]["provenance"]["receipt"]["nested"][0][hidden] = "restricted value"
    seen = []
    with pytest.raises(AnnotationError, match="Hidden benchmark"):
        if stage == "generation":
            generate_annotation(case, evidence, lambda *a, **k: seen.append(True))
        else:
            verify_hypotheses(annotation, evidence, lambda *a, **k: seen.append(True))
    assert seen == []


def test_critic_strips_correctness_provenance_and_all_private_evidence_metadata(inputs):
    case, evidence, response = inputs
    annotation = generate_annotation(case, evidence, lambda *a, **k: deepcopy(response)).to_dict()
    for hypothesis in annotation["hypotheses"]:
        hypothesis["correctness"] = "supported"
        hypothesis["correctness_provenance"] = {"kind": "llm", "private_path": LOCAL, "receipt": {"case_id": FUTURE}}
        hypothesis["correctness_evidence_ids"] = ["gold_private_ref_123"]
    seen = []
    def critic(messages, max_output_tokens):
        seen.append(deepcopy(messages))
        payload = json.loads(messages[1]["content"])
        return {"reviews": [{"hypothesis_id": hypothesis["id"], "assessment": "uncertain",
            "reason": "Unexecuted reproduction", "evidence_ids": hypothesis["support_ids"]}
            for hypothesis in payload["hypotheses"]]}
    result = verify_hypotheses(annotation, evidence, critic)
    assert result["kind"] == "llm_separate_pass"
    assert result["model_input_projection"] == "issueintel.public_model_inputs.v1"
    assert_no_metadata(seen[0])
    supplied = json.loads(seen[0][1]["content"])
    assert all(set(hypothesis) == {"id", "statement", "support_ids", "counter_ids", "counter_status", "falsification_test"}
               for hypothesis in supplied["hypotheses"])
    assert "gold_private_ref_123" not in json.dumps(seen)


def test_critic_rejects_source_human_annotations_before_dropping_metadata(inputs):
    case, evidence, response = inputs
    annotation = generate_annotation(case, evidence, lambda *a, **k: deepcopy(response)).to_dict()
    annotation["source_quality"] = {"underspecified_ordinal": 1, "source": "human"}
    with pytest.raises(AnnotationError, match="Hidden benchmark"):
        verify_hypotheses(annotation, evidence, lambda *a, **k: pytest.fail("dispatched"))


@pytest.mark.parametrize("url", ["https://github.com/owner/project/issues/17", "https://code.djangoproject.com/ticket/34626",
    "https://api.github.com/repos/owner/project/issues/17"])
def test_real_issue_ticket_urls_are_retained(url, inputs):
    case, evidence, _ = inputs
    case["issue_url"] = url
    evidence[0]["locator"] = url
    supplied = model_payload(case, evidence)
    assert supplied["case"]["issue_url"] == url and supplied["evidence"][0]["locator"] == url


@pytest.mark.parametrize("value", ["verified:" + FUTURE, "file:///" + LOCAL, "https://github.com/owner/project/pull/17",
    "http://github.com/owner/project/issues/17", "https://user:secret@github.com/owner/project/issues/17", None])
def test_local_or_non_issue_locator_has_neutral_fallback(value):
    assert issue_locator(value) == "input://issue"


def test_url_query_fragment_metadata_and_opaque_provenance_are_not_model_inputs(inputs):
    _, evidence, _ = inputs
    evidence[0]["locator"] = "https://github.com/owner/project/issues/17?case_id=" + FUTURE + "#" + LOCAL
    evidence[0]["provenance"]["kind"] = FUTURE
    evidence[0]["provenance"]["edit_history"] = {"case_id": FUTURE}
    supplied = model_evidence(evidence)
    assert supplied[0]["locator"] == "https://github.com/owner/project/issues/17"
    assert "provenance" not in supplied[0]
    assert FUTURE not in json.dumps(supplied) and LOCAL not in json.dumps(supplied)


@pytest.mark.parametrize("field,metadata", [("metadata", {"gold_patch": "hidden resolution"}),
    ("metadata", {"nested": [{"source_human": {"label": 1}}]}),
    ("provenance", {"kind": "source_human", "human_rationale": "private notes"}),
    ("provenance", {"kind": "source_human"})])
def test_critic_recursively_checks_entire_annotation_before_projection(inputs, field, metadata):
    case, evidence, response = inputs
    annotation = generate_annotation(case, evidence, lambda *a, **k: deepcopy(response)).to_dict()
    annotation[field] = metadata
    with pytest.raises(AnnotationError, match="Hidden benchmark"):
        verify_hypotheses(annotation, evidence, lambda *a, **k: pytest.fail("dispatched hidden input"))


@pytest.mark.parametrize("path", ["D:\\Users\\" + LOCAL + "\\repo", "/home/" + LOCAL + "/repo", "..\\" + LOCAL, None])
@pytest.mark.parametrize("field", ["repository", "repo"])
def test_local_repository_uses_neutral_model_alias_without_mutating_internal_case(inputs, path, field):
    case, evidence, response = inputs
    case[field] = path
    original = deepcopy(case)
    seen = []
    def provider(messages, max_output_tokens):
        seen.append(deepcopy(messages))
        return deepcopy(response)
    generate_annotation(case, evidence, provider)
    supplied = json.loads(seen[0][1]["content"])
    assert supplied["case"][field] == "local_repository"
    assert case == original
    assert_no_metadata(seen[0])


@pytest.mark.parametrize("value,expected", [("owner/repo", "owner/repo"),
    ("https://github.com/owner/repo.git?case_id=" + FUTURE, "https://github.com/owner/repo"),
    ("https://github.com/owner/repo/", "https://github.com/owner/repo"),
    ("https://user:secret@github.com/owner/repo", "local_repository"),
    ("../repo", "local_repository")])
def test_repository_names_and_canonical_github_urls_remain_usable(value, expected):
    assert repository_name(value) == expected
