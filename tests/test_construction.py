"""Frozen automatic label construction, not heldout model evaluation."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import pytest

from issueintel.benchmarks import construction
from issueintel.runtime.budget import BudgetDenied
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import digest
from test_workflow import GOLD_SENTINEL, imported_fixture
from test_asof_assets import proof_for


@pytest.fixture
def fixture(imported_fixture, monkeypatch):
    fixture = imported_fixture
    source = fixture.root / "registered_source"
    # These are real source hashes, but each synthetic test owns its copy.
    shutil.copytree(construction._SOURCE_ROOT, source, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(construction, "_SOURCE_ROOT", source)
    monkeypatch.setattr(construction, "GroqProvider", lambda config: fixture.provider)
    return fixture


def register(fixture, **options):
    return construction.register_annotation_recipe(fixture.root, protocol=options.pop("protocol", "benchmark_base"),
        splits=["main_dev"], **options)


def directory(fixture, record):
    return fixture.root / "data/annotations/dataset" / record["recipe_sha256"] / hashlib.sha256(fixture.case["case_id"].encode()).hexdigest()[:24]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_registration_binds_sources_configuration_and_explicit_cohort_without_model_calls(fixture):
    record = register(fixture)
    assert fixture.provider.calls == [] and fixture.acquisitions == []
    recipe = record["recipe"]
    assert recipe["evaluation_performed"] is False
    assert recipe["cases"][0]["case_id"] == fixture.case["case_id"]
    assert recipe["strict_claim"] == "diagnostic_non_strict"
    assert recipe["schema_source_sha256"] and all(recipe["prompt_sources"].values())
    assert "src/issueintel/benchmarks/construction.py" in recipe["source_hashes"]
    assert not any("interfaces/" in path or ".env" in path or "inventory.py" in path for path in recipe["source_hashes"])
    assert register(fixture) == record
    assert construction.load_annotation_recipe(fixture.root, record["recipe_sha256"]) == record


@pytest.mark.parametrize("change", ["source", "config", "registration"])
def test_changed_recipe_denies_resume_before_calls(fixture, monkeypatch, change):
    record = register(fixture)
    if change == "source":
        path = construction._SOURCE_ROOT / "annotations/generator.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n# changed recipe\n", encoding="utf-8")
    elif change == "config":
        monkeypatch.setattr(RuntimeConfig, "load", lambda root: replace(fixture.config, max_output_tokens=1024))
    else:
        path = fixture.root / "data/annotations/recipes" / (record["recipe_sha256"] + ".json")
        row = read(path)
        row["registered_at"] = "2000-01-01T00:00:00Z"
        path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError):
        construction.annotate_dataset(fixture.root, record["recipe_sha256"])
    assert fixture.provider.calls == []


def test_generation_critique_judge_variants_are_separate_immutable_stages_and_gold_isolated(fixture, monkeypatch):
    record = register(fixture)
    original = construction.investigate
    supplied = []
    def investigate(case, repo, **kwargs):
        supplied.append(case)
        assert GOLD_SENTINEL not in json.dumps(case)
        assert GOLD_SENTINEL not in json.dumps(kwargs["graph"].to_dict())
        assert not {"patch", "test_patch", "gold", "gold_localization"} & case.keys()
        return original(case, repo, **kwargs)
    monkeypatch.setattr(construction, "investigate", investigate)
    result = construction.annotate_dataset(fixture.root, record["recipe_sha256"])
    assert result["completed"] == [fixture.case["case_id"]] and not result["failures"]
    assert result["evaluation_performed"] is False and len(supplied) == 1
    assert len(fixture.provider.calls) == 3
    assert GOLD_SENTINEL not in json.dumps(fixture.provider.calls[:2])
    assert GOLD_SENTINEL in json.dumps(fixture.provider.calls[2])
    target = directory(fixture, record)
    generated = read(target / "annotation.generated.json")
    judged = read(target / "annotation.judged.json")
    assert all(h["correctness"] == "unverified" for h in generated["hypotheses"])
    assert [h["correctness"] for h in judged["hypotheses"]] == ["supported", "refuted", "uncertain"]
    assert judged["judgment"]["independent_human"] is False
    assert read(target / "verification.json")["kind"] == "llm_separate_pass"
    variants = read(target / "variants.json")
    assert variants[0]["case"]["split"] == fixture.case["split"]
    assert variants[0]["annotation"]["parent_case_id"] == fixture.case["case_id"]
    assert variants[0]["annotation"]["validation_status"] == "unreviewed"
    assert read(target / "answerability.json")["source_ordinal_is_abstention"] is False
    assert read(target / "answerability.json")["source_quality_scope"] == "upstream_query_only"
    assert variants[0]["annotation"]["source_quality_scope"] == "upstream_query_only"
    receipts_before = {path.name: path.read_bytes() for path in target.iterdir()}
    fixture.provider.calls.clear()
    again = construction.annotate_dataset(fixture.root, record["recipe_sha256"])
    assert again["skipped"] == [fixture.case["case_id"]] and fixture.provider.calls == []
    assert receipts_before == {path.name: path.read_bytes() for path in target.iterdir()}
    assert not (fixture.root / "data/runtime/evaluation_authorizations").exists()


@pytest.mark.parametrize("failed_stage", ["critique", "judge"])
def test_failed_later_stage_resumes_without_regeneration(fixture, failed_stage):
    record = register(fixture)
    original = fixture.provider.complete_json
    def deny(messages, max_output_tokens):
        payload = json.loads(messages[1]["content"])
        is_stage = ("gold_evidence" in payload) if failed_stage == "judge" else ("case" not in payload)
        if is_stage:
            raise BudgetDenied("Synthetic later-stage budget stop")
        return original(messages, max_output_tokens)
    fixture.provider.complete_json = deny
    first = construction.annotate_dataset(fixture.root, record["recipe_sha256"])
    assert "later-stage" in first["stop_reason"]
    target = directory(fixture, record)
    generated = (target / "annotation.generated.json").read_bytes()
    inputs = (target / "generation_inputs.json").read_bytes()
    receipt = (target / "generation.receipt.json").read_bytes()
    assert not (target / (failed_stage + ".receipt.json")).exists()
    fixture.provider.complete_json = original
    fixture.provider.calls.clear()
    resumed = construction.annotate_dataset(fixture.root, record["recipe_sha256"])
    assert resumed["completed"] == [fixture.case["case_id"]]
    assert len(fixture.provider.calls) == (2 if failed_stage == "critique" else 1)
    assert (target / "annotation.generated.json").read_bytes() == generated
    assert (target / "generation_inputs.json").read_bytes() == inputs
    assert (target / "generation.receipt.json").read_bytes() == receipt


@pytest.mark.parametrize("corrupt", ["generated", "judged", "receipt", "orphan"])
def test_tampered_and_unreceipted_artifacts_never_trigger_regeneration(fixture, corrupt):
    record = register(fixture)
    target = directory(fixture, record)
    if corrupt == "orphan":
        target.mkdir(parents=True)
        (target / "annotation.generated.json").write_text("{}", encoding="utf-8")
    else:
        construction.annotate_dataset(fixture.root, record["recipe_sha256"])
        name = {"generated": "annotation.generated.json", "judged": "annotation.judged.json", "receipt": "judge.receipt.json"}[corrupt]
        (target / name).write_text("{}", encoding="utf-8")
    fixture.provider.calls.clear()
    result = construction.annotate_dataset(fixture.root, record["recipe_sha256"])
    assert result["failures"] and not result["completed"]
    assert fixture.provider.calls == []


def test_strict_unknown_proof_is_pending_without_model_or_repository_calls(fixture, monkeypatch):
    record = register(fixture, protocol="issue_time")
    monkeypatch.delenv("GROQ_API_KEY")
    result = construction.annotate_dataset(fixture.root, record["recipe_sha256"])
    assert len(result["pending"]) == 1 and not result["completed"] and not result["failures"]
    assert fixture.provider.calls == [] and fixture.acquisitions == []


def test_source_change_during_generation_denies_next_critic_dispatch(fixture):
    record = register(fixture)
    original = fixture.provider.complete_json
    def changing(messages, max_output_tokens):
        result = original(messages, max_output_tokens)
        path = construction._SOURCE_ROOT / "annotations/judge.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n# recipe changed while worker ran\n", encoding="utf-8")
        return result
    fixture.provider.complete_json = changing
    result = construction.annotate_dataset(fixture.root, record["recipe_sha256"])
    assert len(fixture.provider.calls) == 1
    assert result["failures"][0]["stage"] == "critique"
    target = directory(fixture, record)
    assert (target / "generation.receipt.json").exists()
    assert not (target / "critique.receipt.json").exists()


def test_strict_constructed_overlay_revalidates_original_query_and_snapshot_on_resume(fixture, monkeypatch):
    report = proof_for(fixture, monkeypatch)
    record = register(fixture, protocol="issue_time")
    result = construction.annotate_dataset(fixture.root, record["recipe_sha256"])
    assert result["completed"] == [fixture.case["case_id"]] and not result["failures"]
    target = directory(fixture, record)
    receipt = read(target / "generation.receipt.json")
    assert receipt["strict_issue_time_verified"] is True and receipt["audit_id"] == report["audit_id"]
    pack = read(target / "pack.generated.json")
    assert pack["leakage_audit"]["original_query_verified"] is True
    assert pack["snapshot"]["protocol"] == "issue_time"
    assert pack["evidence"][0]["provenance"]["kind"] == "verified_original_query"
    # A complete label receipt cannot conceal subsequent historical proof corruption.
    proof_path = fixture.root / report["receipt_bundle"]["path"]
    bundle = read(proof_path)
    bundle["revision_proof"]["payload"]["created_at"] = report["query_time"]
    proof_path.write_text(json.dumps(bundle), encoding="utf-8")
    fixture.provider.calls.clear()
    again = construction.annotate_dataset(fixture.root, record["recipe_sha256"])
    assert again["failures"] and not again["skipped"] and fixture.provider.calls == []


def test_dataset_holdout_labels_are_constructed_without_evaluation_or_pilot_adoption(fixture, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from issueintel.benchmarks import import_benchmark
    from issueintel.runtime.io import read_jsonl
    new_root = tmp_path / "heldout_construction"
    source = new_root / "data/upstream/fixture.jsonl"
    source.parent.mkdir(parents=True)
    shutil.copyfile(fixture.root / "data/upstream/verified_fixture.jsonl", source)
    import_benchmark(new_root, verified_path=source, target_live=0, main_dev_count=0)
    case = read_jsonl(new_root / "data/benchmark/cases.jsonl")[0]
    assert case["split"] == "main_test"
    price = new_root / "fixture_prices.json"
    shutil.copyfile(fixture.config.price_file, price)
    config = replace(fixture.config, root=new_root, price_file=price, ledger_file=new_root / "data/runtime/budget.sqlite")
    monkeypatch.setattr(RuntimeConfig, "load", lambda root: config)
    own_source = new_root / "src/issueintel"
    shutil.copytree(construction._SOURCE_ROOT, own_source)
    monkeypatch.setattr(construction, "_SOURCE_ROOT", own_source)
    f = SimpleNamespace(root=new_root, case=case)
    # A historical pilot directory is deliberately unrelated to this recipe.
    pilot = new_root / "data/annotations/automatic/pilot"
    pilot.mkdir(parents=True)
    (pilot / "annotation.json").write_text('{"historical":"pilot"}', encoding="utf-8")
    record = construction.register_annotation_recipe(new_root, protocol="benchmark_base", splits=["main_test"])
    result = construction.annotate_dataset(new_root, record["recipe_sha256"])
    assert result["completed"] == [case["case_id"]] and not result["failures"]
    assert len(fixture.provider.calls) == 3
    assert (pilot / "annotation.json").read_text(encoding="utf-8") == '{"historical":"pilot"}'
    assert read(directory(f, record) / "generation.receipt.json")["split"] == "main_test"
    assert not (new_root / "data/runtime/evaluation_authorizations").exists()


@pytest.mark.parametrize("changed_file,base_denied", [("benchmarks/temporal.py", False), ("annotations/generator.py", True)])
def test_recipe_pins_only_its_actual_protocol_dependencies(fixture, changed_file, base_denied):
    base = register(fixture)
    strict = register(fixture, protocol="issue_time")
    historical_path = "src/issueintel/benchmarks/temporal.py"
    assert historical_path not in base["recipe"]["source_hashes"]
    assert historical_path in strict["recipe"]["source_hashes"]
    path = construction._SOURCE_ROOT / changed_file
    path.write_text(path.read_text(encoding="utf-8") + "\n# owned synthetic source modification\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source changed"):
        construction.load_annotation_recipe(fixture.root, strict["recipe_sha256"])
    if base_denied:
        with pytest.raises(ValueError, match="source changed"):
            construction.load_annotation_recipe(fixture.root, base["recipe_sha256"])
    else:
        result = construction.annotate_dataset(fixture.root, base["recipe_sha256"])
        assert result["completed"] == [fixture.case["case_id"]] and not result["failures"]
        assert len(fixture.provider.calls) == 3


def test_simultaneous_registration_returns_one_original_immutable_record(fixture, monkeypatch):
    barrier = Barrier(2)
    original_lock = construction._worker_lock
    @contextmanager
    def racing_lock(path, *, wait=False):
        assert path.name == ".registration.lock" and wait is True
        barrier.wait(timeout=10)
        with original_lock(path, wait=wait):
            yield
    monkeypatch.setattr(construction, "_worker_lock", racing_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        left = pool.submit(register, fixture)
        right = pool.submit(register, fixture)
        first, second = left.result(timeout=20), right.result(timeout=20)
    assert first == second
    stored = read(fixture.root / "data/annotations/recipes" / (first["recipe_sha256"] + ".json"))
    assert stored == first
    assert fixture.provider.calls == [] and fixture.acquisitions == []


def test_registration_source_archive_contains_importable_code_configs_and_project_only(fixture):
    (fixture.root / ".env").write_text("GROQ_API_KEY=PRIVATE_ENV_SENTINEL", encoding="utf-8")
    private = fixture.root / "data/raw/issue.txt"
    private.parent.mkdir(parents=True)
    private.write_text("PRIVATE_RAW_ISSUE_SENTINEL", encoding="utf-8")
    (fixture.root / "proposal.md").write_text("Not part of a source archive", encoding="utf-8")
    (fixture.root / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="0.1"\n', encoding="utf-8")
    config = fixture.root / "configs/benchmark.toml"
    config.parent.mkdir(parents=True)
    config.write_text('query="source_query"\n', encoding="utf-8")
    record = register(fixture)
    reference = record["source_archive"]
    assert reference["path"].endswith(reference["inventory_sha256"] + ".zip")
    descriptor = read(fixture.root / reference["descriptor_path"])
    with zipfile.ZipFile(fixture.root / reference["path"]) as handle:
        names = handle.namelist()
        assert "pyproject.toml" in names and "configs/benchmark.toml" in names
        assert "src/issueintel/interfaces/cli.py" in names  # importable, even though outside the source gate
        assert "src/issueintel/__main__.py" in names
        assert not any(name.startswith("data/") or ".env" in name or name.endswith(".md") for name in names)
        assert all(hashlib.sha256(handle.read(name)).hexdigest() == descriptor["files"][name] for name in names)
        content = b"".join(handle.read(name) for name in names)
        assert b"PRIVATE_ENV_SENTINEL" not in content and b"PRIVATE_RAW_ISSUE_SENTINEL" not in content
    assert construction.load_annotation_recipe(fixture.root, record["recipe_sha256"]) == record


@pytest.mark.parametrize("changed", ["archive", "descriptor"])
def test_archive_tamper_denies_recipe_load_and_registration_without_replacing_expected_hash(fixture, changed):
    record = register(fixture)
    reference = record["source_archive"]
    path = fixture.root / reference["path" if changed == "archive" else "descriptor_path"]
    before = (fixture.root / "data/annotations/recipes" / (record["recipe_sha256"] + ".json")).read_bytes()
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        construction.load_annotation_recipe(fixture.root, record["recipe_sha256"])
    with pytest.raises(ValueError, match="integrity"):
        register(fixture)
    assert (fixture.root / "data/annotations/recipes" / (record["recipe_sha256"] + ".json")).read_bytes() == before
    assert fixture.provider.calls == []


def test_legacy_registration_without_archive_is_read_without_rewriting(fixture):
    record = register(fixture)
    legacy = {key: value for key, value in record.items() if key not in {"source_archive", "registration_sha256"}}
    legacy["registration_sha256"] = digest(legacy)
    path = fixture.root / "data/annotations/recipes" / (record["recipe_sha256"] + ".json")
    path.write_text(json.dumps(legacy), encoding="utf-8")
    before = path.read_bytes()
    assert construction.load_annotation_recipe(fixture.root, record["recipe_sha256"]) == legacy
    assert path.read_bytes() == before


def authorize_original(fixture):
    path = fixture.root / "configs/author_decisions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"historical_query_policy": "original_opening_query",
        "historical_query_user_statement": "Use original text, retain a separate parent-linked overlay."}), encoding="utf-8")


def test_original_query_policy_is_explicit_authorized_and_never_valid_on_benchmark_base(fixture):
    with pytest.raises(ValueError, match="author decision"):
        register(fixture, protocol="issue_time", query_policy="original_opening_query")
    authorize_original(fixture)
    with pytest.raises(ValueError, match="only"):
        register(fixture, query_policy="original_opening_query")
    source = register(fixture, protocol="issue_time")
    original = register(fixture, protocol="issue_time", query_policy="original_opening_query")
    assert source["recipe"]["query_policy"] == "source_query"
    assert original["recipe"]["query_policy"] == "original_opening_query"
    assert original["recipe_sha256"] != source["recipe_sha256"]
    assert fixture.provider.calls == []


def test_original_query_constructor_uses_revalidated_asset_overlay_and_preserves_source_case(fixture, monkeypatch):
    # The asset adapter owns private receipt authenticity. This test isolates
    # construction's policy forwarding and public input boundary with an
    # explicitly synthetic adapter result; it does not claim historical proof.
    report = proof_for(fixture, monkeypatch)
    assets = construction.build_asof_assets(fixture.root, fixture.case, fixture.gold)
    original_text = "Original opening: parse_value in parser.py fails for None input; reproduction unknown"
    assets["case"] = dict(assets["case"], problem_statement=original_text)
    assets["query_receipt"] = dict(assets["query_receipt"], text_sha256=hashlib.sha256(original_text.encode()).hexdigest())
    seen = []
    def proof(root, case, *, audit_id=None, query_policy="source_query"):
        seen.append(("proof", query_policy))
        assert case == fixture.case
        return {"report": report}
    def build(root, case, gold, *, query_policy="source_query"):
        seen.append(("assets", query_policy))
        assert case == fixture.case and gold == fixture.gold
        return assets
    monkeypatch.setattr(construction, "verified_asof_receipt", proof)
    monkeypatch.setattr(construction, "build_asof_assets", build)
    authorize_original(fixture)
    source_bytes = (fixture.root / "data/benchmark/cases.jsonl").read_bytes()
    record = register(fixture, protocol="issue_time", query_policy="original_opening_query")
    result = construction.annotate_dataset(fixture.root, record["recipe_sha256"])
    assert result["completed"] == [fixture.case["case_id"]] and not result["failures"]
    assert seen == [("proof", "original_opening_query"), ("assets", "original_opening_query")]
    assert fixture.provider.calls[0]["case"]["problem_statement"] == original_text
    target = directory(fixture, record)
    inputs = read(target / "generation_inputs.json")
    assert inputs["case"]["case_id"] == fixture.case["case_id"] and inputs["case"]["problem_statement"] == original_text
    assert read(target / "generation.receipt.json")["case_sha256"] == digest(fixture.case)
    answerability = read(target / "answerability.json")
    assert answerability["source_quality_scope"] == "upstream_query_only"
    assert answerability["human_validated_original_query_overlay"] is False
    assert (fixture.root / "data/benchmark/cases.jsonl").read_bytes() == source_bytes
