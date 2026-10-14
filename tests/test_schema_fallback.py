"""Strict provider validation failures share the one existing repair budget."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
import json
import sqlite3
from urllib.error import HTTPError

import pytest

from issueintel.annotations.generator import generate_annotation
from issueintel.annotations.judge import judge_annotation
from issueintel.annotations.models import AnnotationError
from issueintel.investigation.providers import GroqProvider, ProviderError
from issueintel.runtime.budget import BudgetDenied, BudgetPolicy
from issueintel.runtime.config import RuntimeConfig


@pytest.fixture
def inputs():
    case = {"case_id": "synthetic:example", "repository": "example/repo",
            "revision": "a" * 40, "problem_statement": "Empty input raises ValueError."}
    evidence = [{"id": "code:1", "kind": "code", "locator": "parser.py:1",
                 "text": "def parse(value): return int(value)"}]
    response = {"hypotheses": [
        {"id": f"H{i}", "statement": f"Distinct interpretation {i}", "support_ids": ["code:1"],
         "counter_ids": [], "counter_status": "not_found", "falsification_test": f"Check contract {i}"}
        for i in range(1, 4)], "abstention": {"label": "uncertain", "reason": "Input contract is unknown."}}
    annotation = generate_annotation(case, evidence, lambda *a, **k: deepcopy(response),
                                     provider="fixture", model="synthetic")
    gold = {"case_id": case["case_id"], "patch":
            "diff --git a/parser.py b/parser.py\n--- a/parser.py\n+++ b/parser.py\n"
            "@@ -1 +1 @@\n-def parse(value): return int(value)\n+def parse(value): return int(value or 0)\n"}
    judgments = {"judgments": [{"hypothesis_id": f"H{i}", "label": "supported",
                               "reason": "The supplied change alters the input handling.",
                               "gold_evidence_ids": ["G1"]} for i in range(1, 4)]}
    return case, evidence, response, annotation, gold, judgments


class StrictFailureProvider:
    """No transport: a classified strict failure followed by a fixture object."""
    def __init__(self, fallback, error=None):
        self.fallback, self.error = fallback, error or ProviderError(
            "Synthetic validation failure", status=400, code="json_validate_failed")
        self.calls = []

    def complete_structured(self, messages, *, max_output_tokens, schema):
        self.calls.append(("strict", deepcopy(messages), deepcopy(schema)))
        raise self.error

    def complete_json(self, messages, *, max_output_tokens):
        self.calls.append(("json_object", deepcopy(messages), None))
        if isinstance(self.fallback, Exception):
            raise self.fallback
        return deepcopy(self.fallback)


def run(stage, provider, inputs, **kwargs):
    case, evidence, _, annotation, gold, _ = inputs
    if stage == "generation":
        return generate_annotation(case, evidence, provider, provider="fixture", model="synthetic", **kwargs)
    return judge_annotation(annotation, case, evidence, gold, provider,
                            provider="fixture", model="synthetic", **kwargs)


@pytest.mark.parametrize("stage", ["generation", "judge"])
def test_json_object_fallback_consumes_single_repair_and_keeps_provenance(stage, inputs):
    provider = StrictFailureProvider(inputs[2 if stage == "generation" else 5])
    result = run(stage, provider, inputs)
    assert [call[0] for call in provider.calls] == ["strict", "json_object"]
    repair = json.loads(provider.calls[1][1][-1]["content"])
    assert repair["required_schema"] == provider.calls[0][2]
    assert provider.calls[1][1][:-1] == provider.calls[0][1]
    provenance = result.provenance if stage == "generation" else result.hypotheses[0].correctness_provenance
    assert provenance.repair_count == 1 and provenance.method.endswith(".json_object_fallback")
    if stage == "judge":
        assert result.judgment["repair_count"] == 1
        assert result.judgment["gold_prompt_aliases"]["G1"] == result.hypotheses[0].correctness_evidence_ids[0]
        assert result.judgment["independent_human"] is False
        assert result.judgment["causal_truth"] == "unverified"


@pytest.mark.parametrize("stage", ["generation", "judge"])
@pytest.mark.parametrize("status,code", [(400, "invalid_request_error"), (400, None),
    (401, "json_validate_failed"), (403, "json_validate_failed"), (429, "json_validate_failed"),
    (500, "json_validate_failed")])
def test_other_provider_failures_never_fallback(stage, status, code, inputs):
    provider = StrictFailureProvider({}, ProviderError("Terminal synthetic failure", status=status, code=code))
    with pytest.raises(ProviderError):
        run(stage, provider, inputs)
    assert len(provider.calls) == 1


@pytest.mark.parametrize("stage", ["generation", "judge"])
def test_disabled_repair_never_fallback(stage, inputs):
    provider = StrictFailureProvider({})
    with pytest.raises(ProviderError):
        run(stage, provider, inputs, repair_once=False)
    assert len(provider.calls) == 1


@pytest.mark.parametrize("stage", ["generation", "judge"])
@pytest.mark.parametrize("fallback", [{}, "{invalid-json", ProviderError("Synthetic fallback failed", status=400, code="json_validate_failed")])
def test_invalid_or_failed_fallback_is_terminal_without_third_call(stage, fallback, inputs):
    provider = StrictFailureProvider(fallback)
    with pytest.raises((AnnotationError, ProviderError)):
        run(stage, provider, inputs)
    assert len(provider.calls) == 2


@pytest.mark.parametrize("mutation", ["fake_citation", "no_support", "counter_mismatch", "duplicate_hypothesis", "invalid_wire_id", "extra_field"])
def test_generation_fallback_retains_wire_and_app_validation(mutation, inputs):
    response = deepcopy(inputs[2])
    hypothesis = response["hypotheses"][0]
    if mutation == "fake_citation": hypothesis["support_ids"] = ["invented"]
    if mutation == "no_support": hypothesis["support_ids"] = []
    if mutation == "counter_mismatch": hypothesis["counter_status"] = "found"
    if mutation == "duplicate_hypothesis": response["hypotheses"][1]["id"] = "H1"
    if mutation == "invalid_wire_id": hypothesis["id"] = "H6"
    if mutation == "extra_field": hypothesis["correctness"] = "supported"
    provider = StrictFailureProvider(response)
    with pytest.raises(AnnotationError):
        run("generation", provider, inputs)
    assert len(provider.calls) == 2


@pytest.mark.parametrize("mutation", ["fake_citation", "no_citation", "duplicate_hypothesis", "missing_hypothesis", "extra_field"])
def test_judge_fallback_retains_citations_completeness_and_semantic_validation(mutation, inputs):
    response = deepcopy(inputs[5])
    row = response["judgments"][0]
    if mutation == "fake_citation": row["gold_evidence_ids"] = ["invented"]
    if mutation == "no_citation": row["gold_evidence_ids"] = []
    if mutation == "duplicate_hypothesis": response["judgments"][1]["hypothesis_id"] = "H1"
    if mutation == "missing_hypothesis": response["judgments"].pop()
    if mutation == "extra_field": row["human_reviewed"] = True
    provider = StrictFailureProvider(response)
    with pytest.raises(AnnotationError):
        run("judge", provider, inputs)
    assert len(provider.calls) == 2


@pytest.mark.parametrize("stage", ["generation", "judge"])
def test_app_repair_then_strict_rejection_never_gets_an_extra_fallback(stage, inputs):
    class AppFailureThenStrictFailure(StrictFailureProvider):
        def complete_structured(self, messages, *, max_output_tokens, schema):
            self.calls.append(("strict", deepcopy(messages), deepcopy(schema)))
            if len(self.calls) == 1:
                return {}
            raise self.error
    provider = AppFailureThenStrictFailure({})
    with pytest.raises(ProviderError):
        run(stage, provider, inputs)
    assert [call[0] for call in provider.calls] == ["strict", "strict"]


@pytest.mark.parametrize("stage", ["generation", "judge"])
def test_budget_denial_never_fallback(stage, inputs):
    provider = StrictFailureProvider({}, BudgetDenied("Synthetic budget denied"))
    with pytest.raises(BudgetDenied):
        run(stage, provider, inputs)
    assert len(provider.calls) == 1


def test_mocked_transport_reserves_every_attempt_and_retains_failed_bound(tmp_path, monkeypatch, inputs):
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key")
    price_path = tmp_path / "prices.json"
    price_path.write_text(json.dumps({"checked_at": datetime.now(timezone.utc).date().isoformat(),
        "currency": "USD", "source": "https://console.groq.com/docs/models", "models": {
            "openai/gpt-oss-120b": {"input_per_million": "0.15", "output_per_million": "0.60",
                                  "context_tokens": 131072, "max_output_tokens": 65536}}}))
    config = RuntimeConfig(tmp_path, "openai/gpt-oss-120b", 2048,
                           BudgetPolicy(200000, Decimal("2")), price_path, tmp_path / "ledger.sqlite")
    calls = []
    def transport(request, **kwargs):
        body = json.loads(request.data)
        calls.append(body)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 400, "Synthetic validation failure", {},
                            BytesIO(json.dumps({"error": {"code": "json_validate_failed",
                                "message": "synthetic-key must not be exposed"}}).encode()))
        return BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(inputs[2])}}], "usage": {
                "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}).encode())
    result = run("generation", GroqProvider(config, transport=transport), inputs)
    assert [body["response_format"]["type"] for body in calls] == ["json_schema", "json_object"]
    assert result.provenance.repair_count == 1
    assert config.ledger().status()["calls"] == 2
    with sqlite3.connect(config.ledger_file) as connection:
        rows = connection.execute("SELECT status,charged_tokens,input_bound,output_bound FROM calls").fetchall()
    failed = next(row for row in rows if row[0] == "failed_bound")
    assert failed[1] == failed[2] + failed[3]
    assert next(row for row in rows if row[0] == "settled")[1] == 15
