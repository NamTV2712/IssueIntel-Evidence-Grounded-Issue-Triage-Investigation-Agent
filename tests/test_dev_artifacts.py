"""Public replay, DEV-only filtering and paid-stage resume; no real API/DSN."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import pytest

from test_pipeline import repository, case
from issueintel.benchmarks import dev_artifacts as assets, dev_generation as generation
from issueintel.investigation.pipeline import investigate, finish_pack
from issueintel.investigation.providers import ProviderError
from issueintel.repository.snapshot import build_snapshot
from issueintel.repository.graph import build_graph
from issueintel.runtime.io import digest, write_json
from issueintel.runtime.budget import BudgetDenied
from issueintel.runtime.routing import SMALL, LARGE
from issueintel.annotations.generator import generate_annotation
from issueintel.investigation.verifier import verify_hypotheses


def strict_pack(repository):
    public = dict(case(repository), issue_created_at="2025-01-02T00:00:00Z")
    proof_hash = "a" * 64
    snapshot = build_snapshot(repository[0], repository[1], cutoff=public["issue_created_at"], protocol="issue_time",
        availability={"commit_sha": repository[1], "verified": True, "source_id": "https://example.invalid/push",
                      "available_at": "2025-01-01T00:00:00Z", "strict_dev_proof_sha256": proof_hash})
    query = {"schema": "main_dev_original_query_receipt_v1", "verified": True, "case_id": public["case_id"],
             "created_at": public["issue_created_at"], "text_sha256": hashlib.sha256(public["problem_statement"].encode()).hexdigest(),
             "strict_dev_proof_sha256": proof_hash, "source_urls": ["https://example.invalid/opened"]}
    return public, snapshot, query


def test_versioned_query_receipt_cannot_reuse_another_proof(repository):
    public, snapshot, query = strict_pack(repository)
    pack = investigate(public, repository[0], snapshot=snapshot, protocol="issue_time", query_receipt=query)
    assert pack["leakage_audit"]["strict_issue_time_verified"]
    with pytest.raises(ValueError, match="Original-query"):
        investigate(public, repository[0], snapshot=snapshot, protocol="issue_time",
                    query_receipt=dict(query, strict_dev_proof_sha256="b" * 64))
    with pytest.raises(ValueError, match="Original-query"):
        investigate(dict(public, problem_statement="changed query"), repository[0], snapshot=snapshot,
                    protocol="issue_time", query_receipt=query)


def test_cached_evidence_replays_actual_bytes_even_when_rehashed(repository):
    public, snapshot, query = strict_pack(repository)
    pack = investigate(public, repository[0], snapshot=snapshot, protocol="issue_time", query_receipt=query)
    replay = investigate(public, repository[0], snapshot=snapshot, protocol="issue_time", query_receipt=query,
                         prepared_evidence=pack["evidence"])
    assert replay == pack
    changed = copy.deepcopy(pack["evidence"])
    code = next(e for e in changed if e["kind"] == "code")
    code["text"] = "invented code"
    code["sha256"] = hashlib.sha256(code["text"].encode()).hexdigest()
    with pytest.raises(ValueError, match="snapshot bytes"):
        investigate(public, repository[0], snapshot=snapshot, protocol="issue_time", query_receipt=query, prepared_evidence=changed)


def test_graph_rehydration_checks_node_bytes(repository, tmp_path):
    snapshot = build_snapshot(repository[0], repository[1])
    graph = build_graph(snapshot)
    row = graph.to_dict()
    row["nodes"] = [{key: value for key, value in node.to_dict().items() if key != "text"} for node in graph.nodes]
    path = tmp_path / "data/graph.gz"
    path.parent.mkdir()
    path.write_bytes(gzip.compress(json.dumps(row).encode()))
    ref = generation._reference(tmp_path, path)
    assert assets.load_graph(tmp_path, ref, snapshot) == graph
    row["nodes"][0]["sha256"] = "0" * 64
    path.write_bytes(gzip.compress(json.dumps(row).encode()))
    with pytest.raises(ValueError, match="node differs"):
        assets.load_graph(tmp_path, generation._reference(tmp_path, path), snapshot)


def test_reference_source_is_filtered_to_accepted_dev_only(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as parquet
    source = tmp_path / "data/source.parquet"
    source.parent.mkdir()
    parquet.write_table(pa.Table.from_pylist([
        {"instance_id": "dev-1", "patch": "dev patch", "hints_text": "not used"},
        {"instance_id": "test-1", "patch": "HOLDOUT SENTINEL", "hints_text": "never used"}]), source)
    authority = {"sources": [generation._reference(tmp_path, source)]}
    cohort = {"ordered_case_ids": ["verified:dev-1"]}
    selected = assets._restricted_rows(tmp_path, authority, cohort, {"verified:dev-1": {"instance_id": "dev-1"}})
    assert selected == {"verified:dev-1": {"instance_id": "dev-1", "patch": "dev patch"}}


def output(public, evidence):
    eid = next(e["id"] for e in evidence if e["kind"] == "code")
    return {"hypotheses": [{"id": f"h{i}", "statement": f"Distinct mechanism {i}", "support_ids": [eid],
            "counter_ids": [], "counter_status": "not_found", "falsification_test": f"Check {i}"} for i in range(3)],
            "abstention": {"label": "uncertain", "reason": "Not executed"}}


def stage_fixture(repository):
    pack = investigate(case(repository), repository[0])
    spec = {"stage_routes": {stage: {"route": {"model": SMALL}, "max_output_tokens": 2048}
                            for stage in ("generation", "critique")}}
    member = {"case_id": pack["case"]["case_id"], "receipt": {"path": "eligibility.json", "sha256": "a"*64}}
    return pack, spec, member


def test_valid_paid_stage_reused_and_corruption_never_regenerated(repository, tmp_path, monkeypatch):
    pack, spec, member = stage_fixture(repository)
    calls = []
    class Fake:
        def __init__(self, *args):
            self.calls = []
        def __call__(self, messages, **kwargs):
            calls.append(messages)
            return output(pack["case"], pack["evidence"])
    monkeypatch.setattr(generation, "_current_code", lambda *args: None)
    monkeypatch.setattr(generation, "_Provider", Fake)
    recipe = "c" * 64
    saved, reused = generation._stage(tmp_path, recipe, spec, member, pack, "generation")
    assert saved["status"] == "valid" and not reused and len(calls) == 1
    again, reused = generation._stage(tmp_path, recipe, spec, member, pack, "generation")
    assert reused and again == saved and len(calls) == 1
    (tmp_path / saved["output"]["path"]).write_text("{}")
    with pytest.raises(ValueError, match="hash_mismatch"):
        generation._stage(tmp_path, recipe, spec, member, pack, "generation")
    assert len(calls) == 1


def test_orphan_paid_intent_never_dispatches(repository, tmp_path, monkeypatch):
    pack, spec, member = stage_fixture(repository)
    recipe = "c"*64
    directory = tmp_path / "data/development/artifact_recipes/outputs" / recipe / digest(member["case_id"]) / "generation"
    write_json(directory / "started.json", {"paid_attempt": "possibly in flight"})
    monkeypatch.setattr(generation, "_Provider", lambda *args: pytest.fail("Must not repeat a possibly paid call"))
    with pytest.raises(ValueError, match="Unreceipted"):
        generation._stage(tmp_path, recipe, spec, member, pack, "generation")


def test_case_resume_does_not_overwrite_corrupt_report(repository, tmp_path, monkeypatch):
    pack, spec, member = stage_fixture(repository)
    spec.update(cohort_sha256="a"*64)
    directory = assets.artifact_directory(tmp_path, spec["cohort_sha256"], member["case_id"])
    write_json(directory / "receipt.json", {"fixture": True})
    monkeypatch.setattr(generation, "public_context", lambda *args: (pack, {"fixture": True}))
    monkeypatch.setattr(generation, "_current_code", lambda *args: None)
    calls = []
    class Fake:
        def __init__(self, *args): self.calls = []
        def __call__(self, messages, **kwargs):
            data = json.loads(messages[1]["content"])
            calls.append(data)
            if "case" in data:
                return output(pack["case"], pack["evidence"])
            return {"reviews": [{"hypothesis_id": h["id"], "assessment": "uncertain", "reason": "Not reproduced",
                                 "evidence_ids": h["support_ids"]} for h in data["hypotheses"]]}
    monkeypatch.setattr(generation, "_Provider", Fake)
    recipe = "b"*64
    result = generation._case(tmp_path, recipe, spec, member, pack["case"])
    assert result["status"] == "complete" and len(calls) == 2
    again = generation._case(tmp_path, recipe, spec, member, pack["case"])
    assert again["receipt_reuses"] == 2 and len(calls) == 2
    report = tmp_path / "data/development/artifact_recipes/outputs" / recipe / digest(member["case_id"]) / "investigation/report.md"
    report.write_text("tampered")
    with pytest.raises(ValueError, match="hash_mismatch"):
        generation._case(tmp_path, recipe, spec, member, pack["case"])
    assert report.read_text() == "tampered" and len(calls) == 2


def test_pending_deterministic_does_not_construct_provider(repository, tmp_path, monkeypatch):
    pack, spec, member = stage_fixture(repository)
    spec["cohort_sha256"] = "a"*64
    monkeypatch.setattr(generation, "_Provider", lambda *a: pytest.fail("pending cannot call"))
    assert generation._case(tmp_path, "b"*64, spec, member, pack["case"])["status"] == "pending_deterministic"


def test_configuration_drift_denies_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(generation.dev_pilot, "fingerprint", lambda root: {"file.py": "changed"})
    monkeypatch.setattr(generation, "_configs", lambda root: {})
    with pytest.raises(ValueError, match="code/config changed"):
        generation._current_code(tmp_path, {"source_hashes": {"file.py": "original"}, "configuration": {}})


@pytest.fixture
def paired_policy(repository, tmp_path):
    import sqlite3
    import zipfile
    pack = investigate(case(repository), repository[0])
    cases = [f"verified:fixture-{i}" for i in range(30)]
    cohort = {"ordered_case_ids": cases, "accepted_count": 30}
    archive = tmp_path / "data/pilot/source.zip"
    archive.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("fixture.py", b"synthetic source")
    pilot = {"schema": generation.dev_pilot.SCHEMA, "cohort_sha256": digest(cohort),
        "namespace": "main-dev-strict-v1", "evaluation_authorized": False, "max_output_tokens": 16384,
        "models": [SMALL, LARGE], "stages": ["generation", "critique"], "cases": [],
        "source_hashes": {"fixture.py": hashlib.sha256(b"synthetic source").hexdigest()}, "configuration": {},
        "source_archive": generation._reference(tmp_path, archive), "scope_limits": ["synthetic fixture"],
        "acceptance": {"validity_min": 0.95, "generation_abstention_agreement_min": 0.85, "critic_assessment_agreement_min": 0.8}}
    generated = {}
    for identifier in cases:
        public = dict(pack["case"], case_id=identifier, split="main_dev")
        inputs = {"case": public, "evidence": pack["evidence"]}
        annotation = generate_annotation(public, pack["evidence"], lambda *args, **kwargs: output(public, pack["evidence"]),
                                         provider="synthetic", model="fixture").to_dict()
        folder = tmp_path / "data/pilot/inputs" / identifier.replace(":", "_")
        write_json(folder / "inputs.json", inputs)
        write_json(folder / "annotation.json", annotation)
        pilot["cases"].append({"case_id": identifier, "input_sha256": digest(inputs), "files": {
            "generation_inputs.json": generation._reference(tmp_path, folder / "inputs.json"),
            "annotation.generated.json": generation._reference(tmp_path, folder / "annotation.json")}})
        generated[identifier] = annotation
    pilot_id = digest(pilot)
    write_json(tmp_path / "data/development/pilots/registrations" / (pilot_id + ".json"), pilot)
    def critic(messages, **kwargs):
        data = json.loads(messages[1]["content"])
        return {"reviews": [{"hypothesis_id": h["id"], "assessment": "uncertain", "reason": "Synthetic review",
                             "evidence_ids": h["support_ids"]} for h in data["hypotheses"]]}
    for i, item in enumerate(pilot["cases"]):
        for stage in pilot["stages"]:
            for model in pilot["models"]:
                directory = tmp_path / "data/development/pilots/outputs" / pilot_id / digest([pilot_id, item["case_id"], model, stage])
                valid = model == LARGE or i < (24 if stage == "generation" else 19)
                reference = None
                if valid:
                    value = generated[item["case_id"]] if stage == "generation" else verify_hypotheses(generated[item["case_id"]], pack["evidence"], critic)
                    write_json(directory / "output.json", value)
                    reference = generation._reference(tmp_path, directory / "output.json")
                receipt = {"pilot_sha256": pilot_id, "case_id": item["case_id"], "stage": stage, "model": model,
                    "input_sha256": item["input_sha256"], "namespace": pilot["namespace"], "status": "valid" if valid else "failed",
                    "output": reference, "calls": [], "diagnostic_only": True, "human_validated": False}
                receipt["receipt_sha256"] = digest(receipt)
                write_json(directory / "receipt.json", receipt)
    path = tmp_path / "data/runtime/budget.sqlite"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE calls(id TEXT, charged_micro_usd INTEGER)")
    policy = {"schema": "main_dev_stage_policy_v1", "cohort_sha256": digest(cohort), "pilot_sha256": pilot_id,
        "scope": "accepted_original_Main_Dev_only", "evaluation_authorized": False, "human_validated": False,
        "stage_models": {stage: {"model": SMALL, "reasoning_effort": "high", "max_output_tokens": 16384,
                                 "strategy": "small_then_large_on_structured_failure", "fallback_model": LARGE,
                                 "reason": "Measured structured-output failure"} for stage in ("generation", "critique")}}
    policy["stage_models"].update(judge={"enabled_for_bulk": False}, simple_semantic={"enabled_for_bulk": False})
    return cohort, policy


def test_default_small_and_bounded_fallback_replay_pilot_not_boolean(tmp_path, paired_policy):
    cohort, policy = paired_policy
    routes, report, refs = generation.replay_policy(tmp_path, cohort, policy)
    assert routes["generation"]["route"]["model"] == SMALL
    assert routes["generation"]["route"]["escalation_reason"] is None
    assert routes["generation"]["fallback_route"]["model"] == LARGE
    assert routes["generation"]["fallback_route"]["escalation_reason"] == "invalid_structured_output"
    assert routes["generation"]["pilot_validity"][SMALL] == 0.8
    assert len(refs["receipts"]) == 120 and report["expected_stages"] == 120
    policy["stage_models"]["generation"]["model"] = LARGE
    with pytest.raises(BudgetDenied, match="must start with 20B"):
        generation.replay_policy(tmp_path, cohort, policy)


def test_missing_pilot_receipt_denies_bulk_policy(tmp_path, paired_policy):
    cohort, policy = paired_policy
    folder = tmp_path / "data/development/pilots/outputs" / policy["pilot_sha256"]
    next(folder.glob("*/receipt.json")).unlink()
    with pytest.raises(FileNotFoundError):
        generation.replay_policy(tmp_path, cohort, policy)


def test_untested_stage_does_not_inherit_generation_pilot(tmp_path, paired_policy):
    cohort, policy = paired_policy
    policy["stage_models"]["judge"]["enabled_for_bulk"] = True
    with pytest.raises(BudgetDenied, match="Untested"):
        generation.replay_policy(tmp_path, cohort, policy)


@pytest.mark.parametrize("status,expected", [(400, "valid"), (429, "failed")])
def test_large_requires_current_case_structured_failure(repository, tmp_path, monkeypatch, status, expected):
    pack, spec, member = stage_fixture(repository)
    spec["stage_routes"]["generation"]["fallback_route"] = {"model": LARGE}
    models = []
    class Fake:
        def __init__(self, root, spec, stage, journal, route):
            self.calls, self.model = [], route["model"]
        def __call__(self, *args, **kwargs):
            models.append(self.model)
            if self.model == SMALL:
                raise ProviderError("Synthetic provider failure", status=status, code="json_validate_failed" if status == 400 else None)
            return output(pack["case"], pack["evidence"])
    monkeypatch.setattr(generation, "_Provider", Fake)
    monkeypatch.setattr(generation, "_current_code", lambda *a: None)
    saved, _ = generation._stage(tmp_path, "a"*64, spec, member, pack, "generation")
    assert saved["status"] == expected
    assert models == ([SMALL, LARGE] if status == 400 else [SMALL])
    assert saved["attempts"][0]["structured_failure"] == (status == 400)
    generation._stage(tmp_path, "a"*64, spec, member, pack, "generation")
    assert len(models) == (2 if status == 400 else 1)


def test_completed_model_attempt_survives_missing_combined_stage_receipt(repository, tmp_path, monkeypatch):
    pack, spec, member = stage_fixture(repository)
    calls = []
    class Fake:
        def __init__(self, *args): self.calls = []
        def __call__(self, *args, **kwargs):
            calls.append(1)
            return output(pack["case"], pack["evidence"])
    monkeypatch.setattr(generation, "_Provider", Fake)
    monkeypatch.setattr(generation, "_current_code", lambda *a: None)
    recipe = "a"*64
    saved, _ = generation._stage(tmp_path, recipe, spec, member, pack, "generation")
    stage_path = tmp_path / "data/development/artifact_recipes/outputs" / recipe / digest(member["case_id"]) / "generation/receipt.json"
    stage_path.unlink()
    replay, _ = generation._stage(tmp_path, recipe, spec, member, pack, "generation")
    assert replay == saved and len(calls) == 1
