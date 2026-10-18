"""Synthetic planner feedback and executable read-option boundaries."""

import json
import subprocess
from dataclasses import replace
from hashlib import sha256

import pytest

from issueintel.investigation import policy_agent
from issueintel.repository.snapshot import SourceFile, build_snapshot
from issueintel.runtime.io import digest
from test_pipeline import repository


@pytest.fixture
def context(repository):
    directory, _ = repository
    # A real long file makes a line-limit failure distinct from reading past EOF.
    (directory / "parser.py").write_text(
        "def parse_list(items):\n    return [item.strip() for item in items]\n"
        + "\n".join(f"# parser context line {number}" for number in range(3, 221))
        + "\n",
        encoding="utf-8",
    )
    subprocess.check_call(["git", "-C", str(directory), "add", "parser.py"])
    subprocess.check_call(["git", "-C", str(directory), "commit", "-qm", "long synthetic parser"])
    revision = subprocess.check_output(
        ["git", "-C", str(directory), "rev-parse", "HEAD"], text=True
    ).strip()
    snapshot = build_snapshot(directory, revision)
    case = {
        "case_id": "synthetic:tool-feedback",
        "repository": "synthetic/example",
        "revision": revision,
        "problem_statement": "parser.parse_list fails on None input",
        "split": "main_dev",
    }
    return case, snapshot


def _findings(payload):
    evidence_id = payload["evidence"][0]["id"]
    value = {
        "hypotheses": [
            {
                "id": f"H{number}",
                "statement": f"Possible parser mechanism {number}",
                "support_ids": [evidence_id],
                "counter_ids": [],
                "counter_status": "not_found",
                "falsification_test": f"Inspect parser input condition {number}",
            }
            for number in range(1, 4)
        ],
        "abstention": {"label": "uncertain", "reason": "No target code was executed"},
        "ranked_paths": ["parser.py"],
        "checks": [
            {
                "id": "check_1",
                "procedure": "Compare None and an empty list on the same pinned build",
                "assumptions": "Keep the caller and build fixed",
                "outcome_a": "The empty list succeeds",
                "outcome_b": "The empty list raises an exception",
                "effort": "low",
                "evidence_ids": [evidence_id],
                "predictions": [
                    {
                        "hypothesis_id": f"H{number}",
                        "outcome": "A" if number == 1 else "B" if number == 2 else "unknown",
                        "reason": f"Tentative expectation {number}",
                    }
                    for number in range(1, 4)
                ],
            }
        ],
    }
    del value["ranked_paths"]
    value["file_preferences"] = {
        row["id"]: "rank_1" if row["path"] == "parser.py" else "omit"
        for row in payload["candidate_files"]
    }
    return value


@pytest.mark.parametrize("end", [61, 200])
def test_overlong_read_is_rejected_instead_of_silently_truncated(context, end):
    _, snapshot = context
    with pytest.raises(policy_agent.ToolRequestError) as caught:
        policy_agent._read(f"parser.py:1-{end}", snapshot)
    assert caught.value.code == "read_line_limit_exceeded"
    # The exact inclusive boundary remains usable.
    accepted = policy_agent._read("parser.py:1-60", snapshot)
    assert accepted["locator"] == "parser.py:1-60"
    assert len(accepted["text"].splitlines()) == 60


def test_reused_search_choice_id_binds_to_the_current_unused_query(context):
    case, snapshot = context
    payloads = []
    selected = []

    def call(stage, messages, schema, maximum):
        payload = json.loads(messages[1]["content"])
        payloads.append((stage, payload))
        if stage.endswith("findings"):
            return _findings(payload)
        if stage in {"decision_1", "decision_2"}:
            entry = next(entry for entry in payload["catalog"] if entry["id"] == "S1")
            assert entry["query"] not in selected
            selected.append(entry["query"])
            return {"choice_id": "S1", "reason": "Inspect a source-derived query"}
        return {"choice_id": "STOP", "reason": "Summarize available evidence"}

    result = policy_agent.run_policy_agent(case, snapshot, policy_agent.ARMS[0], call)
    first, second = result["actions"][:2]
    assert first["status"] == second["status"] == "ok"
    assert first["action"] == second["action"] == "search_code"
    assert [first["argument"], second["argument"]] == selected
    assert first["argument"] != second["argument"]
    assert first["proposal"]["choice_id"] == second["proposal"]["choice_id"] == "S1"
    by_stage = dict(payloads)
    assert by_stage["decision_2"]["history"][0]["query"] == selected[0]
    assert by_stage["decision_3"]["history"][1]["query"] == selected[1]
    for stage in ("decision_2", "decision_3"):
        already_used = {row["query"] for row in by_stage[stage]["history"] if "query" in row}
        assert not already_used.intersection(entry["query"] for entry in by_stage[stage]["catalog"] if "query" in entry)


@pytest.mark.parametrize("arm", policy_agent.ARMS)
def test_offered_read_options_are_bounded_valid_and_not_repeated(context, arm):
    case, snapshot = context
    selected = []
    seen_payloads = []

    def call(stage, messages, schema, maximum):
        payload = json.loads(messages[1]["content"])
        if stage.endswith("findings"):
            return _findings(payload)
        seen_payloads.append(payload)
        reads = [entry for entry in payload["catalog"] if entry["id"].startswith("R")]
        options = [entry["locator"] for entry in reads]
        assert isinstance(options, list) and len(options) <= 12
        assert len(options) == len(set(options))
        assert not set(options).intersection(selected)
        assert "parser.py:1-200" not in options
        assert schema["properties"]["choice_id"]["enum"] == [entry["id"] for entry in payload["catalog"]]
        assert set(schema["properties"]) == {"choice_id", "reason"}
        for option in options:
            assert isinstance(option, str)
            assert policy_agent._read(option, snapshot)["locator"] == option
        if stage == "decision_1":
            assert options, "Retrieved parser evidence should offer an executable read"
            selected.append(options[0])
            return {"choice_id": reads[0]["id"], "reason": "Inspect offered code range"}
        return {"choice_id": "STOP", "reason": "Summarize available evidence"}

    result = policy_agent.run_policy_agent(case, snapshot, arm, call)
    assert len(seen_payloads) == 2
    assert result["actions"][0]["status"] == "ok"
    assert result["actions"][0]["argument"] == selected[0]
    assert selected[0] not in [entry.get("locator") for entry in seen_payloads[1]["catalog"]]
    assert result["findings"]["ranked_paths"] == ["parser.py"]


def test_catalog_supports_read_then_source_derived_search(context):
    case, snapshot = context
    selected_queries = []

    def call(stage, messages, schema, maximum):
        payload = json.loads(messages[1]["content"])
        if stage.endswith("findings"):
            return _findings(payload)
        if stage == "decision_1":
            entry = next(entry for entry in payload["catalog"] if entry["id"].startswith("R"))
            return {"choice_id": entry["id"], "reason": "Inspect offered code range"}
        if stage == "decision_2":
            entry = next(entry for entry in payload["catalog"] if entry["id"].startswith("S") and entry["id"] != "STOP")
            selected_queries.append(entry["query"])
            return {"choice_id": entry["id"], "reason": "Search for matching context"}
        return {"choice_id": "STOP", "reason": "Summarize available evidence"}

    result = policy_agent.run_policy_agent(case, snapshot, policy_agent.ARMS[0], call)
    assert [row["action"] for row in result["actions"]] == ["read_code", "search_code", "finish"]
    assert [row["status"] for row in result["actions"]] == ["ok", "ok", "finished"]
    assert result["actions"][1]["argument"] == selected_queries[0]


@pytest.mark.parametrize("unknown_id", ["R999", "S999"])
def test_unknown_catalog_id_degrades_without_executing_a_read_or_search(context, monkeypatch, unknown_id):
    case, snapshot = context
    read_calls = []
    original = policy_agent._read
    searches = []
    original_search = policy_agent.retrieve_evidence

    def read(argument, selected_snapshot):
        read_calls.append(argument)
        return original(argument, selected_snapshot)

    monkeypatch.setattr(policy_agent, "_read", read)
    def retrieve(query, *args, **kwargs):
        searches.append(query)
        return original_search(query, *args, **kwargs)
    monkeypatch.setattr(policy_agent, "retrieve_evidence", retrieve)
    calls_before_proposal = []
    stages = []

    def call(stage, messages, schema, maximum):
        stages.append(stage)
        payload = json.loads(messages[1]["content"])
        if stage.endswith("findings"):
            assert len(read_calls) == calls_before_proposal[0]
            return _findings(payload)
        calls_before_proposal.append(len(read_calls))
        assert unknown_id not in schema["properties"]["choice_id"]["enum"]
        return {"choice_id": unknown_id, "reason": "Unknown catalog entry"}

    result = policy_agent.run_policy_agent(case, snapshot, policy_agent.ARMS[0], call)
    assert stages == ["decision_1", "findings"]
    assert result["controller_degraded"] is True
    assert result["actions"][0]["status"] == "degraded_stop"
    assert result["actions"][0]["new_evidence_ids"] == []
    assert searches == [case["problem_statement"]]


@pytest.mark.parametrize("query", ["", "arbitrary model supplied query"])
def test_raw_search_query_field_is_rejected_by_the_catalog_contract(context, query, monkeypatch):
    case, snapshot = context
    payloads = {}
    searches = []
    original = policy_agent.retrieve_evidence

    def retrieve(query, *args, **kwargs):
        searches.append(query)
        return original(query, *args, **kwargs)

    monkeypatch.setattr(policy_agent, "retrieve_evidence", retrieve)

    def call(stage, messages, schema, maximum):
        payload = json.loads(messages[1]["content"])
        payloads[stage] = payload
        if stage.endswith("findings"):
            return _findings(payload)
        if stage == "decision_1":
            return {"choice_id": "STOP", "search_query": query, "reason": "Unexpected free-query field"}
        return {"choice_id": "STOP", "reason": "Summarize available evidence"}

    result = policy_agent.run_policy_agent(case, snapshot, policy_agent.ARMS[0], call)
    assert result["controller_degraded"] is True
    assert result["actions"][0]["status"] == "degraded_stop"
    assert result["actions"][0]["new_evidence_ids"] == []
    assert list(payloads) == ["decision_1", "findings"]
    assert searches == [case["problem_statement"]]


def test_search_options_filter_long_empty_duplicate_and_used_queries(context):
    case, _ = context
    long_term = "z" * 501
    case = {**case, "problem_statement": f"{long_term} parse_list parse_list parser input\n\n"}
    declaration = {"kind": "code", "locator": "parser.py:1-2",
                   "text": "def parse_list(items):\n    return items\n"}
    evidence = [declaration, dict(declaration)]
    options = policy_agent._search_options(case, evidence, set())
    assert options and len(options) <= 24
    assert len(options) == len(set(options))
    assert all(query.strip() and len(query) <= 500 for query in options)
    assert not any("z" * 400 in query for query in options), "An overlong term must be excluded, not clipped"
    assert options.count("parse_list") <= 1
    used = {("search_code", query) for query in options}
    next_options = policy_agent._search_options(case, evidence, used)
    assert not set(options).intersection(next_options)
    assert policy_agent._search_options({**case, "problem_statement": " \n\t "}, [], set()) == []


def test_search_options_are_bounded_with_many_source_keywords(context):
    case, _ = context
    case = {**case, "problem_statement": " ".join(f"keyword_{number}" for number in range(100))}
    options = policy_agent._search_options(case, [], set())
    assert 0 < len(options) <= 24
    assert len(options) == len(set(options))


def test_read_options_skip_locators_beyond_the_argument_bound(context):
    _, snapshot = context
    long_path = "/".join(["long_component"] * 40) + "/parser.py"
    text = "def parse_list(items):\n    return items\n"
    file = SourceFile(long_path, text, sha256(text.encode()).hexdigest())
    snapshot = replace(snapshot, files=(file,))
    snapshot = replace(snapshot, snapshot_sha256=digest({key: value for key, value
                       in snapshot.to_dict().items() if key != "snapshot_sha256"}))
    snapshot.verify()
    assert len(f"{long_path}:1-2") > 500
    assert policy_agent._read_options(snapshot, [{"kind": "code", "locator": f"{long_path}:1-2"}], set()) == []
