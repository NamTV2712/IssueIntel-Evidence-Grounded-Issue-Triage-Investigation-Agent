"""Offline frozen-source continuation without touching workers or model APIs."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from issueintel.benchmarks import construction, reproduction
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import digest
from test_construction import fixture, register, read
from test_workflow import imported_fixture


@pytest.fixture
def registered_fixture(fixture, monkeypatch):
    f = fixture
    config_dir = f.root / "configs"
    config_dir.mkdir()
    price = config_dir / "prices.json"
    price.write_bytes(f.config.price_file.read_bytes())
    (config_dir / "runtime.toml").write_text(
        'provider="groq"\nmodel="openai/gpt-oss-120b"\nmax_output_tokens=2048\n'
        'daily_total_tokens=200000\nmonthly_usd="2"\n'
        'price_file="configs/prices.json"\nledger="data/runtime/synthetic_budget.sqlite"\n', encoding="utf-8")
    config = replace(f.config, price_file=price)
    monkeypatch.setattr(RuntimeConfig, "load", lambda root: config)
    f.record = register(f)
    return f


def prepare(fixture):
    return reproduction.prepare_recipe_runtime(fixture.root, fixture.record["recipe_sha256"])


def test_verified_runtime_is_immutable_and_contains_only_original_source_config_and_project(registered_fixture):
    f = registered_fixture
    (f.root / ".env").write_text("GROQ_API_KEY=PRIVATE_ENV_SENTINEL\n", encoding="utf-8")
    runtime = prepare(f)
    directory = Path(runtime["runtime_directory"])
    marker = read(directory / "runtime.json")
    assert directory.parent == f.root / "artifacts/recipe_runtimes"
    assert directory.name == f.record["source_archive"]["inventory_sha256"]
    assert marker["files"] == read(f.root / f.record["source_archive"]["descriptor_path"])["files"]
    assert marker["environment_secrets_copied"] is False and marker["data_copied"] is False
    assert runtime["ledger_file"] == str(f.config.ledger_file)
    assert not (directory / ".env").exists()
    assert not (directory / "data").exists()
    assert b"PRIVATE_ENV_SENTINEL" not in b"".join(path.read_bytes() for path in directory.rglob("*") if path.is_file())
    before = {path.relative_to(directory).as_posix(): path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    assert prepare(f) == runtime
    assert before == {path.relative_to(directory).as_posix(): path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    assert f.provider.calls == [] and f.acquisitions == []
    assert not (f.root / "data/runtime/synthetic_budget.sqlite").exists()


def test_isolated_offline_check_runs_old_guard_after_legitimate_current_source_change(registered_fixture, monkeypatch):
    f = registered_fixture
    prepare(f)
    original_registration = (f.root / "data/annotations/recipes" / (f.record["recipe_sha256"] + ".json")).read_bytes()
    source = construction._SOURCE_ROOT / "annotations/generator.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n# later product implementation\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source changed"):
        construction.load_annotation_recipe(f.root, f.record["recipe_sha256"])
    poison = f.root / "poison"
    (poison / "issueintel").mkdir(parents=True)
    (poison / "issueintel/__init__.py").write_text('raise RuntimeError("PYTHONPATH poison imported")\n', encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(poison))
    result = reproduction.check_recipe_runtime(f.root, f.record["recipe_sha256"])
    assert result["recipe_guard_passed"] is True and result["model_calls_started"] is False
    assert result["model"] == f.config.model
    assert result["ledger_file"] == str(f.config.ledger_file)
    assert result["root"] == str(f.root)
    assert result["source_directory"].startswith(str(f.root / "artifacts/recipe_runtimes"))
    assert f.provider.calls == [] and f.acquisitions == []
    assert original_registration == (f.root / "data/annotations/recipes" / (f.record["recipe_sha256"] + ".json")).read_bytes()
    assert not (f.root / "data/runtime/synthetic_budget.sqlite").exists()
    assert not (f.root / "data/runtime/evaluation_authorizations").exists()
    assert not list(Path(result["source_directory"]).rglob("*.pyc"))


@pytest.mark.parametrize("target", ["source", "marker", "unexpected", "missing"])
def test_existing_extraction_tampering_is_rejected_without_overwrite(registered_fixture, target):
    f = registered_fixture
    runtime = prepare(f)
    directory = Path(runtime["runtime_directory"])
    if target == "source":
        path = directory / "src/issueintel/interfaces/cli.py"
        path.write_bytes(path.read_bytes() + b"\n# tamper\n")
    elif target == "marker":
        path = directory / "runtime.json"
        path.write_bytes(path.read_bytes() + b"tamper")
    elif target == "unexpected":
        path = directory / "unexpected.json"
        path.write_text("{}", encoding="utf-8")
    else:
        path = directory / "src/issueintel/interfaces/cli.py"
        path.unlink()
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(ValueError, match="Immutable recipe runtime"):
        prepare(f)
    assert (path.read_bytes() if path.exists() else None) == before
    assert f.provider.calls == []


def test_child_rechecks_extraction_before_import_even_if_tampered_after_command_preparation(registered_fixture):
    f = registered_fixture
    command = reproduction.recipe_runtime_command(f.root, f.record["recipe_sha256"], check=True)
    binding = json.loads(command[-3])
    path = Path(binding["runtime_directory"]) / "src/issueintel/interfaces/cli.py"
    path.write_text('raise RuntimeError("SHOULD_NOT_IMPORT_MUTATED_CODE")\n', encoding="utf-8")
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode != 0
    assert "Frozen runtime source contents changed" in result.stderr
    assert "SHOULD_NOT_IMPORT_MUTATED_CODE" not in result.stderr
    assert f.provider.calls == []


@pytest.mark.parametrize("changed", ["registration", "archive", "descriptor", "configuration"])
def test_changed_registration_archive_or_configuration_denies_runtime_preparation(registered_fixture, changed):
    f = registered_fixture
    reference = f.record["source_archive"]
    paths = {"registration": f.root / "data/annotations/recipes" / (f.record["recipe_sha256"] + ".json"),
             "archive": f.root / reference["path"], "descriptor": f.root / reference["descriptor_path"],
             "configuration": f.root / "configs/runtime.toml"}
    path = paths[changed]
    path.write_bytes(path.read_bytes() + b"\n# changed\n")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        prepare(f)
    assert f.provider.calls == []
    assert not list((f.root / "artifacts/recipe_runtimes").glob("*/runtime.json"))


def test_diagnostic_negative_assessment_cannot_resume_old_labels(registered_fixture):
    f = registered_fixture
    payload = {"recipe_sha256": f.record["recipe_sha256"], "registration_sha256": f.record["registration_sha256"],
               "primary_eligible": False, "evaluation_performed": False}
    path = f.root / "data/annotations/recipe_assessments" / f.record["recipe_sha256"] / (digest(payload) + ".json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Diagnostic negative"):
        prepare(f)
    assert f.provider.calls == []


def test_launch_helper_returns_argv_for_exact_registered_recipe_and_never_starts_worker(registered_fixture, monkeypatch):
    f = registered_fixture
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("Preparation must not launch a process"))
    command = reproduction.recipe_runtime_command(f.root, f.record["recipe_sha256"], limit=37)
    assert command[1:4] == ["-I", "-B", "-c"]
    assert command[-2:] == ["annotate", "37"]
    binding = json.loads(command[-3])
    assert binding["root"] == str(f.root)
    assert binding["recipe_sha256"] == f.record["recipe_sha256"]
    assert binding["ledger_file"] == str(f.config.ledger_file)
    assert ".env" not in command[-3]
    assert f.provider.calls == []


def test_completed_stage_bytes_remain_unchanged_by_offline_runtime_check(registered_fixture):
    f = registered_fixture
    # The runtime must not inspect, adopt or rewrite an existing stage directory.
    target = f.root / "data/annotations/dataset" / f.record["recipe_sha256"] / "synthetic_stage"
    target.mkdir(parents=True)
    path = target / "generation.receipt.json"
    path.write_text('{"historical":"immutable_stage_sentinel"}', encoding="utf-8")
    before = path.read_bytes()
    checked = reproduction.check_recipe_runtime(f.root, f.record["recipe_sha256"])
    assert checked["recipe_guard_passed"] is True
    assert path.read_bytes() == before


def test_child_rechecks_diagnostic_assessment_added_after_argv_preparation(registered_fixture):
    f = registered_fixture
    command = reproduction.recipe_runtime_command(f.root, f.record["recipe_sha256"], check=True)
    assessment = {"recipe_sha256": f.record["recipe_sha256"], "registration_sha256": f.record["registration_sha256"],
                  "primary_eligible": False, "evaluation_performed": False}
    path = f.root / "data/annotations/recipe_assessments" / f.record["recipe_sha256"] / (digest(assessment) + ".json")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(assessment), encoding="utf-8")
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode != 0 and "Diagnostic negative assessment" in result.stderr
    assert f.provider.calls == []


def test_runtime_never_traverses_a_link_even_when_linked_bytes_have_correct_hash(registered_fixture):
    import os
    f = registered_fixture
    runtime = prepare(f)
    directory = Path(runtime["runtime_directory"])
    linked = directory / "src/issueintel/interfaces/cli.py"
    original = linked.read_bytes()
    outside_runtime = f.root / "outside_runtime.py"
    outside_runtime.write_bytes(original)
    linked.unlink()
    try:
        os.symlink(outside_runtime, linked)
    except OSError:
        pytest.skip("Windows account cannot create symbolic links")
    with pytest.raises(ValueError, match="links|junctions"):
        prepare(f)
    assert outside_runtime.read_bytes() == original


def test_windows_junction_is_rejected_before_traversal(registered_fixture):
    import os
    if os.name != "nt":
        pytest.skip("Windows junction behavior")
    f = registered_fixture
    runtime = prepare(f)
    directory = Path(runtime["runtime_directory"])
    junction = directory / "src/issueintel/interfaces"
    preserved = f.root / "preserved_interfaces"
    assert junction.resolve().is_relative_to(f.root) and preserved.resolve().is_relative_to(f.root)
    # Move only the verified owned fixture directory; retain matching bytes so
    # rejection must come from junction traversal, rather than digest failure.
    junction.rename(preserved)
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(preserved)],
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        pytest.skip("Windows account cannot create directory junctions")
    before = {path.name: path.read_bytes() for path in preserved.iterdir() if path.is_file()}
    with pytest.raises(ValueError, match="links|junctions"):
        prepare(f)
    assert before == {path.name: path.read_bytes() for path in preserved.iterdir() if path.is_file()}

