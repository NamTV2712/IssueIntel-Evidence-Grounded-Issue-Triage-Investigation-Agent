import json
from pathlib import Path

import pytest

from issueintel.evaluation import open_test_split
from issueintel.benchmarks import releases


@pytest.fixture
def verified_release(tmp_path, monkeypatch):
    cases = [{"case_id": "dev", "split": "main_dev", "problem_statement": "development"},
             {"case_id": "test", "split": "main_test", "problem_statement": "opening query"},
             {"case_id": "live", "split": "temporal_candidate", "problem_statement": "later issue"}]
    gold = tmp_path / "data/benchmark/gold.jsonl"
    gold.parent.mkdir(parents=True)
    gold.write_text("\n".join(json.dumps({"case_id": row["case_id"], "patch": "restricted"}) for row in cases))
    result = {"inference_cases": cases, "label_paths": {row["case_id"]: {"judged": "private"} for row in cases},
        "manifest": {"splits": ["main_dev", "main_test", "temporal_candidate"], "cases": cases,
            "source": {"files": {"gold": {"path": gold.relative_to(tmp_path).as_posix()}},
                       "manifest": {"sha256": "a" * 64}}, "recipe": {"recipe_sha256": "b" * 64}}}
    calls = []

    def verify(root, sha):
        calls.append((Path(root), sha))
        return result

    monkeypatch.setattr(releases, "verify_dataset_release", verify)
    return result, calls


def test_release_access_requires_fresh_authorization_before_any_release_read(tmp_path, verified_release):
    _, calls = verified_release
    for authorization in ("", "đóng băng", "dataset construction"):
        with pytest.raises(PermissionError):
            with open_test_split(tmp_path, split="temporal_candidate", authorization=authorization,
                                 purpose="registered evaluation", release_sha256="c" * 64):
                pytest.fail("unauthorized access")
    assert calls == []
    assert not (tmp_path / "data/runtime/evaluation_authorizations").exists()


def test_release_capability_limits_cases_gold_labels_logs_scope_and_expires(tmp_path, verified_release):
    _, calls = verified_release
    with open_test_split(tmp_path, split="temporal_candidate", authorization="evaluate held-out test",
                         purpose="synthetic test only", release_sha256="c" * 64) as capability:
        assert [row["case_id"] for row in capability.cases()] == ["live"]
        assert "patch" not in capability.cases()[0]
        assert set(capability.gold()) == {"live"}
        assert set(capability.labels()) == {"live"}
    assert len(calls) == 5
    logs = list((tmp_path / "data/runtime/evaluation_authorizations").glob("*.json"))
    assert len(logs) == 1
    record = json.loads(logs[0].read_text())
    assert record["release_sha256"] == "c" * 64 and record["selected_case_count"] == 1
    assert record["authorization_scope"] == "release_eligible_complete_cases_only"
    with pytest.raises(PermissionError, match="expired"):
        capability.cases()


def test_active_capability_revalidates_release_before_each_access(tmp_path, verified_release, monkeypatch):
    with open_test_split(tmp_path, split="main_test", authorization="evaluate held-out test",
                         purpose="synthetic test only", release_sha256="c" * 64) as capability:
        def altered(*args):
            raise ValueError("changed historical proof")
        monkeypatch.setattr(releases, "verify_dataset_release", altered)
        for operation in (capability.cases, capability.gold, capability.labels):
            with pytest.raises(ValueError, match="changed historical proof"):
                operation()


def test_empty_or_absent_release_split_cannot_grant_access(tmp_path, verified_release):
    result, _ = verified_release
    result["manifest"]["cases"] = [row for row in result["manifest"]["cases"] if row["split"] != "main_test"]
    with pytest.raises(PermissionError, match="no eligible"):
        with open_test_split(tmp_path, split="main_test", authorization="evaluate held-out test",
                             purpose="synthetic test only", release_sha256="c" * 64):
            pytest.fail("empty cohort")
    assert not (tmp_path / "data/runtime/evaluation_authorizations").exists()


def test_original_temporal_bootstrap_gate_stays_closed_without_release(tmp_path, monkeypatch):
    from issueintel.benchmarks import store
    monkeypatch.setattr(store, "verify_manifest", lambda root: {"temporal_validation": {"strict_issue_time_validated": False}})
    with pytest.raises(PermissionError, match="not passed issue-time audit"):
        with open_test_split(tmp_path, split="temporal_candidate", authorization="evaluate held-out test",
                             purpose="synthetic test only"):
            pytest.fail("bootstrap cannot become temporal test")
