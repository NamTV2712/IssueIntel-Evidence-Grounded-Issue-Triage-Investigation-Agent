import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from issueintel.benchmarks import dev_pilot as pilot
from issueintel.runtime.io import digest, write_json
from issueintel.runtime.routing import SMALL


def input_fixture():
    case = {"case_id": "verified:example-1", "split": "main_dev", "repository": "owner/project",
            "revision": "a"*40, "problem_statement": "source query", "issue_created_at": "2025-01-01T00:00:00Z"}
    statement = "initial query"
    def ev(kind, text, locator, revision):
        import hashlib
        return {"id": kind, "kind": kind, "text": text, "locator": locator, "revision": revision,
                "sha256": hashlib.sha256(text.encode()).hexdigest(), "available_at": case["issue_created_at"]}
    inputs = {"case": dict(case, problem_statement=statement), "evidence": [
        ev("issue", statement, "https://github.com/owner/project/issues/1", None),
        ev("code", "return 1", "a.py:1-1", "a"*40)]}
    snapshot = SimpleNamespace(commit_sha="a"*40, cutoff=case["issue_created_at"],
                               files=[SimpleNamespace(path="a.py", text="return 1\n")])
    return case, inputs, snapshot


def test_public_evidence_replay_needs_exact_opening_and_code(monkeypatch):
    case, inputs, snapshot = input_fixture()
    monkeypatch.setattr(pilot, "reconstruct_query_text", lambda *a: "initial query")
    pilot.validate_evidence(inputs, case, {}, snapshot)
    bad = copy.deepcopy(inputs)
    bad["evidence"][1]["text"] = "return 2"
    import hashlib
    bad["evidence"][1]["sha256"] = hashlib.sha256(b"return 2").hexdigest()
    with pytest.raises(ValueError, match="differs from actual"):
        pilot.validate_evidence(bad, case, {}, snapshot)


@pytest.mark.parametrize("field", ["human_rationale", "patch", "source_quality"])
def test_hidden_data_rejected_before_reuse(monkeypatch, field):
    case, inputs, snapshot = input_fixture()
    monkeypatch.setattr(pilot, "reconstruct_query_text", lambda *a: "initial query")
    inputs["case"][field] = "private sentinel"
    with pytest.raises(ValueError, match="Hidden"):
        pilot.validate_evidence(inputs, case, {}, snapshot)


def test_cached_stage_does_not_construct_provider_and_tamper_fails(tmp_path, monkeypatch):
    item = {"case_id": "verified:one", "input_sha256": "input"}
    spec = {"namespace": "main-dev-strict-v1"}
    pilot_id = "a"*64
    key = digest([pilot_id, item["case_id"], SMALL, "generation"])
    directory = tmp_path / "data/development/pilots/outputs" / pilot_id / key
    write_json(directory / "output.json", {"valid": True})
    output = directory / "output.json"
    receipt = {"pilot_sha256": pilot_id, "case_id": item["case_id"], "model": SMALL, "stage": "generation",
        "input_sha256": "input", "namespace": spec["namespace"], "status": "valid", "calls": [],
        "output": {"path": output.relative_to(tmp_path).as_posix(), "sha256": pilot.sha256_file(output)}}
    receipt["receipt_sha256"] = digest(receipt)
    write_json(directory / "receipt.json", receipt)
    monkeypatch.setattr(pilot, "_RecordedProvider", lambda *a: pytest.fail("paid stage repeated"))
    result, hit = pilot._stage(tmp_path, pilot_id, spec, item, SMALL, "generation")
    assert hit and result["status"] == "valid"
    output.write_text("{}")
    with pytest.raises(ValueError, match="hash_mismatch"):
        pilot._stage(tmp_path, pilot_id, spec, item, SMALL, "generation")


def test_orphan_output_does_not_trigger_paid_regeneration(tmp_path, monkeypatch):
    item = {"case_id": "verified:one", "input_sha256": "input"}
    identifier = "a"*64
    directory = tmp_path / "data/development/pilots/outputs" / identifier / digest([identifier,item["case_id"],SMALL,"generation"])
    write_json(directory / "output.json", {"already_paid": True})
    monkeypatch.setattr(pilot, "_RecordedProvider", lambda *a: pytest.fail("paid stage repeated"))
    with pytest.raises(ValueError, match="Unreceipted"):
        pilot._stage(tmp_path, identifier, {"namespace":"main-dev-strict-v1"}, item, SMALL, "generation")


def test_critic_agreement_uses_same_ids_not_unrelated_generated_hypotheses(tmp_path):
    import sqlite3
    root = tmp_path / "data/runtime"
    root.mkdir(parents=True)
    with sqlite3.connect(root / "budget.sqlite") as db:
        db.execute("CREATE TABLE calls(id TEXT,charged_micro_usd INTEGER)")
    results = []
    models = [SMALL, pilot.LARGE]
    for model in models:
        ref = pilot._publish(tmp_path, {"reviews":[{"hypothesis_id":"h1","assessment":"uncertain"}]}, "test")
        results.append({"case_id":"verified:one","stage":"critique","model":model,"status":"valid","calls":[],"output":ref})
    spec = {"stages":["critique"],"models":models,"cases":[{}],"cohort_sha256":"c",
            "acceptance":{"critic_assessment_agreement_min":0.8,"validity_min":0.95},"scope_limits":[]}
    report = pilot.summarize(tmp_path, "a"*64, spec, results)
    assert report["stages"]["critique"]["agreement"] == 1
    assert not report["stages"]["critique"]["semantic_equivalence_proven"]
