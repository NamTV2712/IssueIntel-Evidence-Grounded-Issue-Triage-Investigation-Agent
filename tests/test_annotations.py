"""Synthetic fixtures verify gold isolation and provenance; no model/network use."""
from copy import deepcopy
import csv
import io
import json
import pytest

from issueintel.annotations import Annotation, AnnotationError, create_abstention_variants, export_review, generate_annotation, score_agreement
from issueintel.annotations.judge import build_gold_evidence, judge_annotation


@pytest.fixture
def public_case():
    return {"case_id": "example__repo-1", "repository": "example/repo", "revision": "a" * 40,
            "problem_statement": "Calling parse raises ValueError for an empty value.",
            "issue_created_at": "2025-01-02T00:00:00Z", "split": "dev"}


@pytest.fixture
def evidence(public_case):
    return [
        {"id": "issue:1", "kind": "issue", "locator": "https://github.com/example/repo/issues/1", "text": public_case["problem_statement"], "provenance": {"kind": "issue"}},
        {"id": "code:1", "kind": "code", "locator": "parse.py:1", "text": "def parse(value): return int(value)", "revision": "a" * 40},
        {"id": "code:2", "kind": "code", "locator": "caller.py:1", "text": "parse('')", "revision": "a" * 40},
    ]


@pytest.fixture
def response():
    return {"hypotheses": [
        {"id": "h1", "statement": "The parser fails to validate empty input", "support_ids": ["code:1"], "counter_ids": [], "counter_status": "not_found", "falsification_test": "Check how empty input is documented"},
        {"id": "h2", "statement": "The caller violates an input contract", "support_ids": ["code:2"], "counter_ids": ["issue:1"], "counter_status": "found", "falsification_test": "Check whether empty input is allowed"},
        {"id": "h3", "statement": "An upstream normalization step is missing", "support_ids": ["issue:1"], "counter_ids": [], "counter_status": "not_found", "falsification_test": "Trace inputs before the parser"},
    ], "abstention": {"label": "uncertain", "reason": "The intended empty-input contract is unavailable"}}


def generate(public_case, evidence, response):
    return generate_annotation(public_case, evidence, lambda messages, **kwargs: deepcopy(response), provider="fixture", model="synthetic")


def test_generation_calls_only_public_payload_and_forces_unverified(public_case, evidence, response):
    seen = []
    def provider(messages, max_output_tokens):
        seen.append((messages, max_output_tokens))
        return json.dumps(response)
    annotation = generate_annotation(public_case, evidence, provider, provider="fixture", model="synthetic")
    supplied = json.loads(seen[0][0][1]["content"])
    assert supplied["case"]["problem_statement"] == public_case["problem_statement"]
    assert supplied["evidence"] == evidence
    assert not {"case_id","instance_id","split","pull_created_at"}.intersection(supplied["case"])
    assert seen[0][1] == 2048
    assert all(item.correctness == "unverified" and item.correctness_provenance is None for item in annotation.hypotheses)
    assert annotation.abstention.provenance.kind == "llm"
    assert Annotation.from_dict(annotation.to_dict()).to_dict() == annotation.to_dict()


@pytest.mark.parametrize("hidden", ["patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS", "gold_localization", "source_quality"])
def test_gold_rejected_before_dispatch(public_case, evidence, response, hidden):
    public_case[hidden] = "secret"
    called = []
    with pytest.raises(AnnotationError, match="Hidden benchmark"):
        generate_annotation(public_case, evidence, lambda *args, **kwargs: called.append(True))
    assert not called


def test_nested_gold_in_provenance_rejected(public_case, evidence):
    evidence[0]["provenance"]["gold_patch"] = "secret"
    with pytest.raises(AnnotationError, match="Hidden benchmark"):
        generate_annotation(public_case, evidence, lambda *args, **kwargs: None)


@pytest.mark.parametrize("mutation", ["unknown_id", "duplicate_id", "duplicate_statement", "counter_status", "overlap", "no_support", "self_judgment", "too_few"])
def test_invalid_provider_hypotheses(public_case, evidence, response, mutation):
    if mutation == "unknown_id": response["hypotheses"][0]["support_ids"] = ["not-retrieved"]
    if mutation == "duplicate_id": response["hypotheses"][1]["id"] = "h1"
    if mutation == "duplicate_statement": response["hypotheses"][1]["statement"] = "  " + response["hypotheses"][0]["statement"].upper()
    if mutation == "counter_status": response["hypotheses"][0]["counter_status"] = "found"
    if mutation == "overlap": response["hypotheses"][0].update(counter_ids=["code:1"], counter_status="found")
    if mutation == "no_support": response["hypotheses"][0]["support_ids"] = []
    if mutation == "self_judgment": response["hypotheses"][0]["correctness"] = "supported"
    if mutation == "too_few": response["hypotheses"].pop()
    with pytest.raises(AnnotationError):
        generate(public_case, evidence, response)


def test_duplicate_evidence_and_malformed_json(public_case, evidence):
    with pytest.raises(AnnotationError, match="Duplicate evidence"):
        generate_annotation(public_case, evidence + [evidence[0]], lambda *args, **kwargs: {})
    with pytest.raises(AnnotationError, match="valid JSON"):
        generate_annotation(public_case, evidence, lambda *args, **kwargs: "```bad```")


def test_missing_response_field_is_schema_error(public_case, evidence, response):
    del response["hypotheses"][0]["falsification_test"]
    with pytest.raises(AnnotationError, match="Missing hypothesis"):
        generate(public_case, evidence, response)


def test_upstream_public_issue_numbers_are_safe(public_case, evidence, response):
    public_case["issue_numbers"] = ["1"]
    assert generate(public_case, evidence, response).case_id == public_case["case_id"]


def test_variants_record_synthetic_changes_preserve_parent_and_split(public_case, evidence):
    before = deepcopy((public_case, evidence))
    variants = create_abstention_variants(public_case, evidence, critical_ids=["issue:1"], conflict={"target_id": "code:1", "text": "The parser explicitly returns None for every empty input."})
    assert (public_case, evidence) == before
    assert len(variants) == 2
    assert variants[0]["case"]["problem_statement"] != public_case["problem_statement"]
    assert "issue:1" not in {item["id"] for item in variants[0]["evidence"]}
    for packet in variants:
        assert packet["case"]["split"] == "dev"
        assert packet["annotation"]["split_group"] == public_case["case_id"]
        assert packet["annotation"]["label_provenance"]["kind"] == "synthetic"
        assert packet["annotation"]["validation_status"] == "unreviewed"
    inserted = variants[1]["evidence"][-1]
    assert inserted["available_at"] is None
    assert inserted["provenance"]["kind"] == "synthetic"


def test_removal_also_removes_duplicate_issue_text(public_case, evidence):
    evidence.append({**evidence[0], "id": "duplicate-text"})
    packet = create_abstention_variants(public_case, evidence, critical_ids=["issue:1"])[0]
    assert set(packet["annotation"]["removed_ids"]) == {"issue:1", "duplicate-text"}
    assert all(item["text"] != public_case["problem_statement"] for item in packet["evidence"])


@pytest.mark.parametrize("kwargs", [{"critical_ids": ["missing"]}, {"conflict": {"target_id": "missing", "text": "different"}}, {"conflict": {"target_id": "code:1", "text": "def parse(value): return int(value)"}}])
def test_invalid_variant_requests(public_case, evidence, kwargs):
    with pytest.raises(AnnotationError):
        create_abstention_variants(public_case, evidence, **kwargs)


def test_blind_export_does_not_fill_automatic_labels(public_case, evidence, response, tmp_path):
    annotation = generate(public_case, evidence, response)
    value = export_review([annotation], tmp_path / "blind.csv", reviewer_id="person-a")
    rows = list(csv.DictReader(io.StringIO(value)))
    assert len(rows) == 3
    assert all(not row["correctness"] and not row["abstention"] and not row["reviewer_kind"] for row in rows)
    score = score_agreement(value, value)
    assert score["status"] == "pending_no_independent_pairs"
    assert score["correctness"]["cohen_kappa"] is None
    assert score["pending_rows"] == {"left": 3, "right": 3}


def reviewed_rows(identifier, correctness, abstain="uncertain", case_id="case-1"):
    return [{"case_id": case_id, "hypothesis_id": f"h{i}", "reviewer_id": identifier,
             "reviewer_kind": "human", "correctness": label, "abstention": abstain}
            for i, label in enumerate(correctness)]


def test_agreement_per_hypothesis_but_abstention_per_case():
    left = reviewed_rows("alice", ["supported", "refuted", "uncertain"])
    right = reviewed_rows("bob", ["supported", "refuted", "supported"])
    score = score_agreement(left, right)
    assert score["correctness"]["paired"] == 3
    assert score["correctness"]["agreed"] == 2
    assert score["correctness"]["cohen_kappa"] == pytest.approx(0.5)
    assert score["abstention"]["paired"] == 1
    assert score["abstention"]["cohen_kappa"] is None
    assert score["correctness"]["disagreements"][0]["hypothesis_id"] == "h2"


def test_missing_reviews_reported():
    score = score_agreement(reviewed_rows("alice", ["supported", "refuted"]), reviewed_rows("bob", ["supported"]))
    assert score["correctness"]["left_unpaired"] == 1
    assert score["correctness"]["paired"] == 1
    assert score["correctness"]["kappa_status"] == "undefined_constant_labels"


@pytest.mark.parametrize("error", ["same_person", "agent", "duplicate", "inconsistent_abstention", "mixed_reviewers"])
def test_independent_review_constraints(error):
    left = reviewed_rows("alice", ["supported", "refuted"])
    right = reviewed_rows("bob", ["supported", "refuted"])
    if error == "same_person": right = reviewed_rows("alice", ["supported", "refuted"])
    if error == "agent": right[0]["reviewer_kind"] = "llm"
    if error == "duplicate": right.append(deepcopy(right[0]))
    if error == "inconsistent_abstention": right[1]["abstention"] = "answerable"
    if error == "mixed_reviewers": right[1]["reviewer_id"] = "carol"
    with pytest.raises(AnnotationError):
        score_agreement(left, right)


@pytest.fixture
def hidden_gold(public_case):
    return {"case_id": public_case["case_id"], "patch": "diff --git a/parse.py b/parse.py\n--- a/parse.py\n+++ b/parse.py\n@@ -1 +1,3 @@\n-def parse(value): return int(value)\n+def parse(value):\n+    if value == '': return None\n+    return int(value)\n",
            "test_patch": "", "fail_to_pass": ["test_empty_value"],
            "pass_to_pass": ["test_integer_value"], "hints_text": "Secret resolution discussion must be ignored."}


def make_judgments(hypotheses, refs):
    patch_id = next(item["id"] for item in refs if item["kind"] == "gold_patch_hunk")
    return {"judgments": [{"hypothesis_id": item.id, "label": "supported" if i == 0 else "uncertain",
                           "reason": "The reference adds an empty-value check" if i == 0 else "The reference does not determine this explanation",
                           "gold_evidence_ids": [patch_id] if i == 0 else []}
                          for i, item in enumerate(hypotheses)]}


def test_offline_judge_uses_hidden_hunks_and_keeps_automatic_provenance(public_case, evidence, response, hidden_gold):
    annotation = generate(public_case, evidence, response)
    before = annotation.to_dict()
    seen = []
    refs = build_gold_evidence(hidden_gold, case_id=annotation.case_id)
    def judge(messages, max_output_tokens):
        seen.append(json.loads(messages[1]["content"]))
        return make_judgments(annotation.hypotheses, refs)
    judged = judge_annotation(annotation, public_case, evidence, hidden_gold, judge, provider="fixture", model="synthetic")
    assert annotation.to_dict() == before
    assert seen[0]["gold_evidence"] == refs
    assert "Secret resolution" not in json.dumps(seen)
    assert judged.hypotheses[0].correctness == "supported"
    assert judged.hypotheses[0].correctness_provenance.kind == "llm"
    assert judged.hypotheses[1].correctness == "uncertain"
    assert judged.judgment["causal_truth"] == "unverified"
    assert judged.judgment["independent_human"] is False
    assert Annotation.from_dict(judged.to_dict()).to_dict() == judged.to_dict()
    blind = list(csv.DictReader(io.StringIO(export_review([judged]))))
    assert all(not item["correctness"] for item in blind)
    assert "gold:" not in export_review([judged])


def test_missing_reference_stays_uncertain_without_call(public_case, evidence, response):
    annotation = generate(public_case, evidence, response)
    called = []
    judged = judge_annotation(annotation, public_case, evidence, {"patch": "", "fail_to_pass": ["test_empty"]}, lambda *a, **k: called.append(True))
    assert not called
    assert all(item.correctness == "uncertain" for item in judged.hypotheses)
    assert all(item.correctness_provenance.kind == "automatic" for item in judged.hypotheses)
    assert all(not item.correctness_evidence_ids for item in judged.hypotheses)


@pytest.mark.parametrize("mutation", ["unknown_citation", "no_citation", "tests_only", "duplicate", "incomplete", "human_claim", "forced_label"])
def test_judge_rejects_unverifiable_labels(public_case, evidence, response, hidden_gold, mutation):
    annotation = generate(public_case, evidence, response)
    refs = build_gold_evidence(hidden_gold, case_id=annotation.case_id)
    rows = make_judgments(annotation.hypotheses, refs)
    if mutation == "unknown_citation": rows["judgments"][0]["gold_evidence_ids"] = ["invented-location"]
    if mutation == "no_citation": rows["judgments"][0]["gold_evidence_ids"] = []
    if mutation == "tests_only": rows["judgments"][0]["gold_evidence_ids"] = [next(item["id"] for item in refs if item["kind"] == "gold_test_identifier")]
    if mutation == "duplicate": rows["judgments"].append(deepcopy(rows["judgments"][0]))
    if mutation == "incomplete": rows["judgments"].pop()
    if mutation == "human_claim": rows["judgments"][0]["provenance"] = {"kind": "human"}
    if mutation == "forced_label": rows["judgments"][0]["label"] = "certain_root_cause"
    with pytest.raises(AnnotationError):
        judge_annotation(annotation, public_case, evidence, hidden_gold, lambda *a, **k: rows)


def test_judge_rejects_wrong_case_before_dispatch(public_case, evidence, response, hidden_gold):
    annotation = generate(public_case, evidence, response)
    hidden_gold["case_id"] = "another-case"
    called = []
    with pytest.raises(AnnotationError, match="different case"):
        judge_annotation(annotation, public_case, evidence, hidden_gold, lambda *a, **k: called.append(True))
    assert not called


def test_one_citation_repair_is_fresh_and_records_provenance(public_case, evidence, response):
    invalid = deepcopy(response)
    invalid["hypotheses"][0]["support_ids"] = ["invented-shorthand"]
    calls = []
    def provider(messages, max_output_tokens):
        calls.append(deepcopy(messages))
        return invalid if len(calls) == 1 else deepcopy(response)
    annotation = generate_annotation(public_case, evidence, provider, provider="fixture", model="synthetic")
    assert len(calls) == 2
    repair = json.loads(calls[1][-1]["content"])
    assert repair["allowed_evidence_ids"] == [item["id"] for item in evidence]
    assert "invented-shorthand" in repair["validation_error"]
    assert json.loads(calls[1][-2]["content"]) == invalid
    assert annotation.hypotheses[0].support_ids == ["code:1"]
    assert annotation.provenance.repair_count == 1
    assert "schema_repair" in annotation.provenance.method
    payload = json.loads(calls[1][1]["content"])
    assert payload["case"]["problem_statement"] == public_case["problem_statement"]
    assert payload["evidence"] == evidence
    assert not {"case_id","instance_id","split","pull_created_at"}.intersection(payload["case"])
    assert "gold_evidence" not in json.dumps(calls)


def test_second_bad_schema_stops_no_citation_mapping(public_case, evidence, response):
    response["hypotheses"][0]["support_ids"] = ["invented-shorthand"]
    called = []
    def provider(*args, **kwargs):
        called.append(True)
        return deepcopy(response)
    with pytest.raises(AnnotationError, match="Unknown cited evidence"):
        generate_annotation(public_case, evidence, provider)
    assert len(called) == 2


def test_repair_can_be_disabled(public_case, evidence, response):
    response["hypotheses"][0]["support_ids"] = ["invented-shorthand"]
    called = []
    def provider(*args, **kwargs):
        called.append(True)
        return deepcopy(response)
    with pytest.raises(AnnotationError):
        generate_annotation(public_case, evidence, provider, repair_once=False)
    assert len(called) == 1


def test_provider_errors_never_retry(public_case, evidence):
    called = []
    def provider(*args, **kwargs):
        called.append(True)
        raise RuntimeError("Budget denied")
    with pytest.raises(RuntimeError, match="Budget denied"):
        generate_annotation(public_case, evidence, provider)
    assert len(called) == 1


def test_judge_repairs_one_invalid_gold_citation(public_case, evidence, response, hidden_gold):
    annotation = generate(public_case, evidence, response)
    refs = build_gold_evidence(hidden_gold, case_id=annotation.case_id)
    valid = make_judgments(annotation.hypotheses, refs)
    invalid = deepcopy(valid)
    invalid["judgments"][0]["gold_evidence_ids"] = ["gold:invented-shorthand"]
    calls = []
    def provider(messages, max_output_tokens):
        calls.append(deepcopy(messages))
        return invalid if len(calls) == 1 else valid
    judged = judge_annotation(annotation, public_case, evidence, hidden_gold, provider, provider="fixture", model="synthetic")
    assert len(calls) == 2
    repair = json.loads(calls[1][-1]["content"])
    assert repair["allowed_gold_evidence_ids"] == [item["id"] for item in refs]
    assert repair["allowed_public_evidence_ids"] == [item["id"] for item in evidence]
    assert json.loads(calls[1][-2]["content"]) == invalid
    assert judged.hypotheses[0].correctness_evidence_ids == valid["judgments"][0]["gold_evidence_ids"]
    assert judged.hypotheses[0].correctness_provenance.repair_count == 1
    assert judged.judgment["repair_count"] == 1
    assert judged.judgment["causal_truth"] == "unverified"


def test_second_invalid_judge_response_stops_without_mapping(public_case, evidence, response, hidden_gold):
    annotation = generate(public_case, evidence, response)
    rows = make_judgments(annotation.hypotheses, build_gold_evidence(hidden_gold))
    rows["judgments"][0]["gold_evidence_ids"] = ["gold:invented-shorthand"]
    calls = []
    def provider(*args, **kwargs):
        calls.append(True)
        return deepcopy(rows)
    with pytest.raises(AnnotationError, match="unknown gold locations"):
        judge_annotation(annotation, public_case, evidence, hidden_gold, provider)
    assert len(calls) == 2
    assert annotation.hypotheses[0].correctness == "unverified"


def test_judge_repair_can_be_disabled(public_case, evidence, response, hidden_gold):
    annotation = generate(public_case, evidence, response)
    calls = []
    def provider(*args, **kwargs):
        calls.append(True)
        return {"judgments": []}
    with pytest.raises(AnnotationError, match="every hypothesis"):
        judge_annotation(annotation, public_case, evidence, hidden_gold, provider, repair_once=False)
    assert len(calls) == 1


def test_judge_provider_budget_error_never_retries(public_case, evidence, response, hidden_gold):
    annotation = generate(public_case, evidence, response)
    calls = []
    def provider(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("Budget denied")
    with pytest.raises(RuntimeError, match="Budget denied"):
        judge_annotation(annotation, public_case, evidence, hidden_gold, provider)
    assert len(calls) == 1
