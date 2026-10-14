"""Stage integration on a tiny imported fixture; no HTTP or paid model calls.

The synthetic provider tests actual generation/critique/judge contracts rather
than returning a preassembled investigation pack. Git acquisition alone is
mocked to the fixture's local repository.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from issueintel.benchmarks import import_benchmark
from issueintel.benchmarks import workflow
from issueintel.benchmarks.store import load_dev_cases, load_dev_gold
from issueintel.interfaces.cli import main
from issueintel.investigation.providers import GroqProvider
from issueintel.runtime.budget import BudgetDenied, BudgetPolicy
from issueintel.runtime.config import RuntimeConfig


GOLD_SENTINEL = "GOLD_PRIVATE_SOLVED_MECHANISM"


class SyntheticProvider:
    name = "synthetic-no-network"

    def __init__(self, model):
        self.model = model
        self.calls = []

    def complete_json(self, messages, max_output_tokens):
        payload = json.loads(messages[1]["content"])
        self.calls.append(payload)
        if "gold_evidence" in payload:
            patch_id = next(record["id"] for record in payload["gold_evidence"]
                            if record["kind"] == "gold_patch_hunk")
            labels = ["supported", "refuted", "uncertain"]
            return {"judgments": [
                {"hypothesis_id": hypothesis["id"], "label": labels[number],
                 "reason": f"Synthetic reference-relative assessment {number}",
                 "gold_evidence_ids": [patch_id] if number < 2 else []}
                for number, hypothesis in enumerate(payload["hypotheses"])
            ]}
        if "case" in payload:
            support = next(record["id"] for record in payload["evidence"] if record["kind"] == "code")
            return {"hypotheses": [
                {"id": f"h{number}", "statement": mechanism, "support_ids": [support],
                 "counter_ids": [], "counter_status": "not_found",
                 "falsification_test": f"Check controlled input variant {number}"}
                for number, mechanism in enumerate((
                    "None input lacks the strip method", "Unexpected integer input reaches the parser",
                    "A caller omits the required value normalization",
                ))
            ], "abstention": {"label": "uncertain", "reason": "Synthetic fixture has no executed reproduction"}}
        return {"reviews": [
            {"hypothesis_id": hypothesis["id"], "assessment": "uncertain",
             "reason": "Synthetic independent pass; reproduction is not executed",
             "evidence_ids": hypothesis["support_ids"]}
            for hypothesis in payload["hypotheses"]
        ]}


@pytest.fixture
def imported_fixture(tmp_path, monkeypatch):
    repo = tmp_path / "fixture_repo"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()
    git("init", "-q")
    git("config", "user.email", "synthetic@example.invalid")
    git("config", "user.name", "Synthetic Workflow")
    git("config", "core.autocrlf", "false")
    (repo / "parser.py").write_text("def parse_value(value):\n    return value.strip()\n", encoding="utf-8", newline="\n")
    (repo / "unrelated.py").write_text("def unrelated():\n    return 1\n", encoding="utf-8", newline="\n")
    git("add", ".")
    git("commit", "-qm", "synthetic base")
    revision = git("rev-parse", "HEAD")
    patch = ("diff --git a/parser.py b/parser.py\n--- a/parser.py\n+++ b/parser.py\n"
             "@@ -1,2 +1,2 @@\n def parse_value(value):\n-    return value.strip()\n"
             f"+    return str(value).strip()  # {GOLD_SENTINEL}\n")
    source = tmp_path / "data/upstream/verified_fixture.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps({
        "instance_id": "owner__project-1", "repo": "owner/project", "base_commit": revision,
        "problem_statement": "parse_value in parser.py fails for None input",
        "created_at": "2023-01-01T00:00:00Z", "patch": patch,
        "test_patch": "", "FAIL_TO_PASS": ["tests/test_private_gold.py::test_hidden"],
        "PASS_TO_PASS": [], "hints_text": GOLD_SENTINEL,
    }) + "\n", encoding="utf-8")
    import_benchmark(tmp_path, verified_path=source, target_live=0, main_dev_count=1)
    case = load_dev_cases(tmp_path)[0]
    gold = load_dev_gold(tmp_path)[case["case_id"]]
    price_path = tmp_path / "fixture_prices.json"
    model = "openai/gpt-oss-120b"
    price_path.write_text(json.dumps({
        "checked_at": datetime.now(timezone.utc).date().isoformat(), "currency": "USD",
        "source": "https://console.groq.com/docs/models", "models": {model: {
            "input_per_million": "0.15", "output_per_million": "0.60",
            "context_tokens": 131072, "max_output_tokens": 65536,
        }},
    }), encoding="utf-8")
    config = RuntimeConfig(tmp_path, model, 2048, BudgetPolicy(200000, Decimal("2")),
                           price_path, tmp_path / "data/runtime/synthetic_budget.sqlite")
    provider = SyntheticProvider(model)
    acquisitions = []
    def acquire(reference, requested_revision, cache_dir):
        acquisitions.append((reference, requested_revision, cache_dir))
        assert reference == "owner/project" and requested_revision == revision
        return SimpleNamespace(path=repo, commit_sha=revision)
    monkeypatch.setattr(workflow, "acquire_repository", acquire)
    monkeypatch.setattr(RuntimeConfig, "load", lambda root: config)
    monkeypatch.setattr(workflow, "GroqProvider", lambda unused_config: provider)
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-local-test-key")
    return SimpleNamespace(root=tmp_path, repo=repo, revision=revision, case=case, gold=gold,
                           config=config, provider=provider, acquisitions=acquisitions)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_case_asset_cache_reuses_verified_snapshot_and_never_reacquires(imported_fixture):
    fixture = imported_fixture
    first = workflow.build_case_assets(fixture.root, fixture.case, fixture.gold)
    second = workflow.build_case_assets(fixture.root, fixture.case, fixture.gold)
    assert len(fixture.acquisitions) == 1
    assert first["snapshot"] == second["snapshot"]
    assert first["graph"].to_dict() == second["graph"].to_dict()
    assert first["localization"]["patch_sha256"] == hashlib.sha256(fixture.gold["patch"].encode()).hexdigest()
    assert first["receipt"]["leakage_audit"]["gold_used_for_graph"] is False
    assert first["receipt"]["strict_issue_time_verified"] is False
    assert GOLD_SENTINEL not in json.dumps(first["graph"].to_dict())


@pytest.mark.parametrize("field,value", [
    ("case_id", "other-case"), ("repository", "other/project"),
    ("revision", "0" * 40), ("snapshot_sha256", "0" * 64),
])
def test_asset_receipt_identity_tamper_is_denied(imported_fixture, field, value):
    fixture = imported_fixture
    first = workflow.build_case_assets(fixture.root, fixture.case, fixture.gold)
    directory = fixture.root / "data/derived/cases" / hashlib.sha256(fixture.case["case_id"].encode()).hexdigest()[:24]
    receipt_path = directory / "build_receipt.json"
    receipt = read_json(receipt_path)
    receipt[field] = value
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError):
        workflow.build_case_assets(fixture.root, fixture.case, fixture.gold)
    assert len(fixture.acquisitions) == 1


@pytest.mark.parametrize("operation", ["modify", "delete"])
def test_annotation_inventory_corruption_denies_resume_before_provider(imported_fixture, operation):
    fixture = imported_fixture
    assert workflow.annotate_dev(fixture.root)["completed"] == [fixture.case["case_id"]]
    target = workflow.case_directory(fixture.root, fixture.case["case_id"])
    if operation == "modify":
        (target / "annotation.json").write_text("{}", encoding="utf-8")
    else:
        (target / "generation_inputs.json").unlink()
    fixture.provider.calls.clear()
    with pytest.raises((ValueError, OSError)):
        workflow.annotate_dev(fixture.root, judge=True)
    assert fixture.provider.calls == []


@pytest.mark.parametrize("field,value", [("case_id", "other-case"), ("split", "main_test")])
def test_annotation_receipt_identity_tamper_is_denied(imported_fixture, field, value):
    fixture = imported_fixture
    workflow.annotate_dev(fixture.root)
    target = workflow.case_directory(fixture.root, fixture.case["case_id"])
    receipt = read_json(target / "receipt.json")
    receipt[field] = value
    (target / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    fixture.provider.calls.clear()
    with pytest.raises(ValueError):
        workflow.annotate_dev(fixture.root, judge=True)
    assert fixture.provider.calls == []


def test_annotation_generation_keeps_hidden_gold_outside_product_pipeline(imported_fixture, monkeypatch):
    fixture = imported_fixture
    original = workflow.investigate
    supplied = []
    def traced(case, local_repo, **kwargs):
        supplied.append(case)
        assert not {"patch", "test_patch", "gold", "gold_localization"} & case.keys()
        assert GOLD_SENTINEL not in json.dumps(case)
        assert GOLD_SENTINEL not in json.dumps(kwargs["graph"].to_dict())
        return original(case, local_repo, **kwargs)
    monkeypatch.setattr(workflow, "investigate", traced)
    result = workflow.annotate_dev(fixture.root)
    assert result["completed"] == [fixture.case["case_id"]] and not result["failures"]
    assert len(supplied) == 1 and len(fixture.provider.calls) == 2
    assert GOLD_SENTINEL not in json.dumps(fixture.provider.calls)
    target = workflow.case_directory(fixture.root, fixture.case["case_id"])
    receipt = read_json(target / "receipt.json")
    assert receipt["human_reviewed"] is False and receipt["judgment_completed"] is False
    annotation = read_json(target / "annotation.json")
    assert all(hypothesis["correctness"] == "unverified" for hypothesis in annotation["hypotheses"])
    assert GOLD_SENTINEL not in (target / "generation_inputs.json").read_text(encoding="utf-8")


def test_cli_judge_resumes_only_subsequent_stage_and_is_idempotent(imported_fixture, capsys):
    fixture = imported_fixture
    workflow.annotate_dev(fixture.root)
    target = workflow.case_directory(fixture.root, fixture.case["case_id"])
    initial_inputs = (target / "generation_inputs.json").read_bytes()
    initial_run = read_json(target / "receipt.json")["run_id"]
    fixture.provider.calls.clear()
    assert main(["--root", str(fixture.root), "annotate", "--limit", "1", "--judge"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["completed"] == [fixture.case["case_id"]]
    assert len(fixture.provider.calls) == 1
    assert "gold_evidence" in fixture.provider.calls[0]
    assert GOLD_SENTINEL in json.dumps(fixture.provider.calls[0])
    assert (target / "generation_inputs.json").read_bytes() == initial_inputs
    receipt = read_json(target / "receipt.json")
    assert receipt["judgment_completed"] is True and receipt["run_id"] == initial_run
    assert receipt["human_reviewed"] is False
    annotation = read_json(target / "annotation.json")
    assert [h["correctness"] for h in annotation["hypotheses"]] == ["supported", "refuted", "uncertain"]
    assert annotation["judgment"]["causal_truth"] == "unverified"
    assert annotation["judgment"]["independent_human"] is False
    fixture.provider.calls.clear()
    again = workflow.annotate_dev(fixture.root, judge=True)
    assert again["completed"] == [] and again["skipped"] == 1
    assert fixture.provider.calls == [] and len(fixture.acquisitions) == 1


def test_cached_judge_budget_denial_retains_receipt_and_ledger(imported_fixture, monkeypatch):
    fixture = imported_fixture
    workflow.annotate_dev(fixture.root)
    target = workflow.case_directory(fixture.root, fixture.case["case_id"])
    before_receipt = (target / "receipt.json").read_bytes()
    before_annotation = (target / "annotation.json").read_bytes()
    config = replace(fixture.config, policy=BudgetPolicy(3000, Decimal("2")))
    reservation = config.ledger().reserve(config.price(), 1000, 1000)
    config.ledger().reconcile(reservation, config.price(), None, failed=True)
    monkeypatch.setattr(RuntimeConfig, "load", lambda root: config)
    provider = GroqProvider(config, transport=lambda *args, **kwargs: pytest.fail("Network reached after budget denial"))
    monkeypatch.setattr(workflow, "GroqProvider", lambda config: provider)
    result = workflow.annotate_dev(fixture.root, judge=True)
    assert "Daily total token" in result["stop_reason"]
    assert result["budget"]["daily_tokens_charged_or_reserved"] == 2000
    assert (target / "receipt.json").read_bytes() == before_receipt
    assert (target / "annotation.json").read_bytes() == before_annotation
    assert config.ledger().status()["calls"] == 1


def test_new_annotation_survives_judge_denial_and_resumes_without_regeneration(imported_fixture):
    fixture = imported_fixture
    provider = fixture.provider
    original = provider.complete_json
    def deny_judge(messages, max_output_tokens):
        if "gold_evidence" in json.loads(messages[1]["content"]):
            raise BudgetDenied("Synthetic judge budget exhausted")
        return original(messages, max_output_tokens)
    provider.complete_json = deny_judge
    result = workflow.annotate_dev(fixture.root, judge=True)
    assert "Synthetic judge budget" in result["stop_reason"]
    target = workflow.case_directory(fixture.root, fixture.case["case_id"])
    receipt = read_json(target / "receipt.json")
    assert receipt["judgment_completed"] is False
    inputs = (target / "generation_inputs.json").read_bytes()
    provider.complete_json = original
    provider.calls.clear()
    resumed = workflow.annotate_dev(fixture.root, judge=True)
    assert resumed["completed"] == [fixture.case["case_id"]]
    assert len(provider.calls) == 1 and "gold_evidence" in provider.calls[0]
    assert (target / "generation_inputs.json").read_bytes() == inputs
    assert len(fixture.acquisitions) == 1


def test_missing_key_denies_before_asset_acquisition(imported_fixture, monkeypatch):
    fixture = imported_fixture
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(BudgetDenied, match="GROQ_API_KEY"):
        workflow.annotate_dev(fixture.root)
    assert fixture.acquisitions == [] and fixture.provider.calls == []
