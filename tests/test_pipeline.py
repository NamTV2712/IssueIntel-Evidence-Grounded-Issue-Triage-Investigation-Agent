import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from issueintel.investigation.pipeline import investigate, public_case
from issueintel.investigation.controller import Action, DecisionState, JevController
from issueintel.repository.cache import save_snapshot, load_snapshot
from issueintel.repository.snapshot import build_snapshot
from issueintel.repository.graph import build_graph
from issueintel.retrieval import retrieve_evidence
from issueintel.evaluation import localization_metrics


@pytest.fixture
def repository(tmp_path):
    def git(*args):
        return subprocess.check_output(["git","-C",str(tmp_path),*args]).decode().strip()
    git("init", "-q")
    git("config","user.email","test@example.invalid")
    git("config","user.name","Synthetic Test")
    (tmp_path / "parser.py").write_text("def parse_list(items):\n    return [item.strip() for item in items]\n")
    (tmp_path / "unrelated.py").write_text("def unrelated():\n    return 4\n")
    git("add","."); git("commit","-qm","synthetic fixture")
    return tmp_path, git("rev-parse","HEAD")


def case(repository):
    return {"case_id":"fixture", "repository":"synthetic/fixture", "revision":repository[1],
            "problem_statement":"parser.parse_list fails on None input", "split":"main_dev"}


def test_offline_pipeline_is_usable_and_does_not_claim_llm(repository):
    pack = investigate(case(repository), repository[0])
    assert pack["stop_reason"] == "provider_not_configured"
    assert pack["annotation"] is None
    assert pack["leakage_audit"]["strict_issue_time_verified"] is False
    assert any(e["kind"] == "code" for e in pack["evidence"])
    assert all("text" not in file for file in pack["snapshot"]["files"])


def test_public_boundary_denies_gold_before_provider(repository):
    with pytest.raises(ValueError, match="Gold"):
        investigate({**case(repository), "patch":"secret solution"}, repository[0], complete_json=lambda *a,**k: pytest.fail("called"))


def test_upstream_numeric_string_ids_and_recursive_boundary():
    assert public_case({"problem_statement":"Example", "issue_numbers":["12",13]})["issue_numbers"] == [12,13]
    with pytest.raises(ValueError, match="Gold"):
        public_case({"problem_statement":"Example", "issue_numbers":[{"patch":"hidden"}]})


def test_shared_pipeline_two_separate_public_passes(repository):
    calls = []
    def complete(messages, max_output_tokens):
        payload = json.loads(messages[1]["content"])
        calls.append(payload)
        if "case" in payload:
            eid = next(e["id"] for e in payload["evidence"] if e["kind"] == "code")
            return {"hypotheses":[{"id":f"h{i}","statement":f"Distinct mechanism {i}","support_ids":[eid],"counter_ids":[],"counter_status":"not_found","falsification_test":f"Check input {i}"} for i in range(3)], "abstention":{"label":"uncertain","reason":"Not reproduced"}}
        return {"reviews":[{"hypothesis_id":h["id"],"assessment":"uncertain","reason":"Not executed","evidence_ids":h["support_ids"]} for h in payload["hypotheses"]]}
    pack = investigate(case(repository), repository[0], complete_json=complete, provider="synthetic",model="fixture")
    assert len(calls) == 2
    assert pack["verification"]["kind"] == "llm_separate_pass"
    assert all(h["correctness"] == "unverified" for h in pack["annotation"]["hypotheses"])
    assert not any("patch" in payload for payload in calls)


def test_cache_roundtrip_and_corruption_detection(repository, tmp_path):
    snapshot = build_snapshot(repository[0], repository[1])
    cache = tmp_path / "cache"
    index = save_snapshot(snapshot, cache)
    assert load_snapshot(index, cache) == snapshot
    file = next((cache / "blobs").glob("*/*.gz"))
    file.write_bytes(b"corrupt")
    with pytest.raises((ValueError, OSError)):
        load_snapshot(index, cache)


def test_retrieval_revision_binding(repository):
    snapshot = build_snapshot(repository[0], repository[1])
    graph = build_graph(snapshot)
    evidence = retrieve_evidence("parse_list", snapshot, graph)
    assert evidence[0]["locator"].startswith("parser.py:")
    assert evidence[0]["provenance"]["score_is_probability"] is False


def test_supplied_snapshot_cannot_bypass_case_revision_or_protocol(repository):
    snapshot = build_snapshot(repository[0], repository[1])
    with pytest.raises(ValueError, match="exact revision"):
        investigate({**case(repository), "revision": "0" * 40}, repository[0], snapshot=snapshot)
    with pytest.raises(ValueError, match="protocol"):
        investigate(case(repository), repository[0], snapshot=snapshot, protocol="issue_time")


def test_jev_invalid_remote_action_falls_back_without_execution():
    controller = JevController(lambda state:{"action":"delete_repository"})
    assert controller.decide(DecisionState(4,3), (Action.VERIFY,Action.STOP)) == Action.VERIFY


def test_all_file_denominator_and_metric_distinction():
    result = localization_metrics(["a.py", "a.py", "noise.py"], ["a.py","missing.py"], k=2)
    assert result["recall_at_k"] == 0.5
    assert result["all_files_at_k"] == 0
    assert result["mrr"] == 1
    assert localization_metrics([],[]) ["recall_at_k"] is None
