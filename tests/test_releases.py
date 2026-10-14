"""Dataset releases replay provenance without evaluating or reading labels."""
from dataclasses import replace
import gzip
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from issueintel.benchmarks import construction, releases
from issueintel.benchmarks.temporal import append_historical_audit
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import digest
from test_construction import fixture, authorize_original, register, directory, read
from test_workflow import imported_fixture, GOLD_SENTINEL
from test_asof_assets import proof_for


@pytest.fixture
def strict_fixture(fixture, monkeypatch):
    """Reuse the real constructor integration fixture and its synthetic Git."""
    authorize_original(fixture)
    config_dir = fixture.root / "configs"
    config_dir.mkdir(exist_ok=True)
    price = config_dir / "prices.json"
    shutil.copyfile(fixture.config.price_file, price)
    config = replace(fixture.config, price_file=price)
    monkeypatch.setattr(RuntimeConfig, "load", lambda root: config)
    source = proof_for(fixture, monkeypatch)
    bundle = read(fixture.root / source["receipt_bundle"]["path"])
    report = append_historical_audit(fixture.root, fixture.case["case_id"], bundle["issue_receipts"],
        bundle["revision_proof"], issue_references=bundle["issue_references"],
        query_policy="original_opening_query")
    assert report["strict_issue_time_receipt_checks_passed"] is True
    fixture.report = report
    fixture.record = register(fixture, protocol="issue_time", query_policy="original_opening_query")
    return fixture


def complete(fixture):
    result = construction.annotate_dataset(fixture.root, fixture.record["recipe_sha256"])
    assert result["completed"] == [fixture.case["case_id"]] and not result["failures"]
    fixture.provider.calls.clear()
    fixture.acquisitions.clear()
    return directory(fixture, fixture.record)


def release(fixture):
    return releases.register_dataset_release(fixture.root, fixture.record["recipe_sha256"])


def test_release_keeps_source_immutable_and_separates_inference_queries_from_labels(strict_fixture, monkeypatch):
    f = strict_fixture
    complete(f)
    source_paths = [f.root / "data/manifests/dataset.json", f.root / "data/benchmark/cases.jsonl",
                    f.root / "data/benchmark/gold.jsonl"]
    before = [path.read_bytes() for path in source_paths]
    original_reader = releases._object
    def metadata_only(path):
        assert not path.name.startswith("annotation.")
        assert path.name not in {"generation_inputs.json", "pack.generated.json", "variants.json", "answerability.json", "localization.json"}
        return original_reader(path)
    monkeypatch.setattr(releases, "_object", metadata_only)
    registered = release(f)
    manifest = registered["release"]
    assert manifest["counts"] == {"registered_selected": 1, "included": 1, "excluded": 0,
                                  "included_by_split": {"main_dev": 1}, "exclusions_by_source_eligibility": {}}
    assert manifest["localization_denominator"] == "all_upstream_gold_patch_files_including_unavailable"
    assert manifest["label_scope"]["independent_human_validation"] is False
    assert manifest["label_scope"]["source_human_quality"] == "upstream_query_only"
    assert manifest["all_source_cases_complete_claim"] is False
    assert registered["release_sha256"] == digest(manifest) == Path(registered["path"]).stem
    assert GOLD_SENTINEL not in json.dumps(manifest)
    verified = releases.verify_dataset_release(f.root, registered["release_sha256"])
    query = verified["inference_cases"][0]
    assert query["problem_statement"] == f.case["problem_statement"] + "\n\n"
    assert query["issue_created_at"] == f.report["query_time"]
    assert "pull_created_at" not in query and "gold" not in query
    assert GOLD_SENTINEL not in json.dumps(query)
    assert "annotation.judged.json" in verified["label_paths"][f.case["case_id"]]
    assert verified["evaluation_capability_granted"] is False
    assert manifest["cases"][0]["assets"]["snapshot_graph_validation"]["source_file_count"] == 2
    assert not (f.root / "data/runtime/evaluation_authorizations").exists()
    assert f.provider.calls == [] and f.acquisitions == []
    assert before == [path.read_bytes() for path in source_paths]
    assert release(f) == registered  # deterministic immutable snapshot


def test_release_is_valid_after_legitimate_current_source_and_configuration_changes(strict_fixture):
    f = strict_fixture
    complete(f)
    registered = release(f)
    source = construction._SOURCE_ROOT / "annotations/generator.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n# later implementation\n", encoding="utf-8")
    (f.root / "configs/author_decisions.json").write_text('{"new_decision":"later"}', encoding="utf-8")
    with pytest.raises(ValueError):
        construction.load_annotation_recipe(f.root, f.record["recipe_sha256"])
    verified = releases.verify_dataset_release(f.root, registered["release_sha256"])
    assert len(verified["inference_cases"]) == 1
    assert release(f) == registered
    assert f.provider.calls == []


@pytest.mark.parametrize("tamper", ["stage_receipt", "stage_artifact", "query", "audit_bundle", "audit_event",
                                  "asset_receipt", "snapshot_index", "registration", "archive", "archive_descriptor", "release"])
def test_release_detects_tampering_before_returning_inference_cases(strict_fixture, tamper):
    f = strict_fixture
    target = complete(f)
    registered = release(f)
    entry = registered["release"]["cases"][0]
    paths = {
        "stage_receipt": target / "judge.receipt.json",
        "stage_artifact": target / "annotation.judged.json",
        "query": f.root / entry["assets"]["query_variant"]["file"]["path"],
        "audit_bundle": f.root / f.report["receipt_bundle"]["path"],
        "audit_event": f.root / "data/annotations/historical_audit.jsonl",
        "asset_receipt": f.root / entry["assets"]["build_receipt"]["path"],
        "snapshot_index": f.root / entry["assets"]["snapshot_index"]["path"],
        "registration": f.root / registered["release"]["recipe"]["registration"]["path"],
        "archive": f.root / f.record["source_archive"]["path"],
        "archive_descriptor": f.root / f.record["source_archive"]["descriptor_path"],
        "release": Path(registered["path"]),
    }
    path = paths[tamper]
    if tamper == "audit_event":
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        events[-1]["selected_snapshot_revision"] = "0" * 40
        path.write_text("".join(json.dumps(row) + "\n" for row in events), encoding="utf-8")
    else:
        path.write_bytes(path.read_bytes() + b"TAMPERED")
    with pytest.raises((ValueError, OSError, json.JSONDecodeError)):
        releases.verify_dataset_release(f.root, registered["release_sha256"])
    assert f.provider.calls == [] and f.acquisitions == []


def assessment(fixture, *, eligible):
    payload = {"schema_version": 1, "recipe_sha256": fixture.record["recipe_sha256"],
        "registration_sha256": fixture.record["registration_sha256"], "primary_eligible": eligible,
        "evaluation_performed": False, "reason_code": "synthetic_source_review"}
    path = fixture.root / "data/annotations/recipe_assessments" / fixture.record["recipe_sha256"] / (digest(payload) + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_diagnostic_assessment_excludes_recipe_and_cannot_be_ignored_after_release(strict_fixture):
    f = strict_fixture
    complete(f)
    registered = release(f)
    assessment(f, eligible=False)
    with pytest.raises(ValueError, match="Diagnostic"):
        release(f)
    with pytest.raises(ValueError, match="Diagnostic"):
        releases.verify_dataset_release(f.root, registered["release_sha256"])


def test_assessment_is_content_addressed_and_bound_to_original_registration(strict_fixture):
    f = strict_fixture
    complete(f)
    path = assessment(f, eligible=True)
    registered = release(f)
    assert registered["release"]["recipe"]["assessments"][0]["path"] == path.relative_to(f.root).as_posix()
    path.write_text('{"primary_eligible":true}', encoding="utf-8")
    with pytest.raises(ValueError, match="assessment"):
        releases.verify_dataset_release(f.root, registered["release_sha256"])


def test_incomplete_construction_is_explicit_exclusion_and_later_completion_does_not_rewrite_release(strict_fixture):
    f = strict_fixture
    registered = release(f)
    manifest = registered["release"]
    assert manifest["counts"]["included"] == 0 and manifest["counts"]["excluded"] == 1
    excluded = manifest["exclusions"][0]
    assert excluded["source_eligibility"] == "verified" and excluded["annotation_technically_complete"] is False
    assert excluded["missing_stages"] == ["generation", "critique", "judge", "variants"]
    original = Path(registered["path"]).read_bytes()
    complete(f)
    verified = releases.verify_dataset_release(f.root, registered["release_sha256"])
    assert verified["inference_cases"] == []
    assert Path(registered["path"]).read_bytes() == original
    new = release(f)
    assert new["release_sha256"] != registered["release_sha256"] and new["release"]["counts"]["included"] == 1


def test_benchmark_base_cannot_be_primary_even_when_all_annotation_stages_complete(fixture):
    record = register(fixture)
    result = construction.annotate_dataset(fixture.root, record["recipe_sha256"])
    assert result["completed"] == [fixture.case["case_id"]]
    fixture.provider.calls.clear()
    with pytest.raises(ValueError, match="strict/original-opening"):
        releases.register_dataset_release(fixture.root, record["recipe_sha256"])
    assert fixture.provider.calls == []


def test_release_requires_the_actual_generation_audit_instead_of_a_newer_valid_one(strict_fixture):
    f = strict_fixture
    target = complete(f)
    generation = read(target / "generation.receipt.json")
    generation["audit_id"] = "0" * 64
    generation["receipt_sha256"] = digest({key: value for key, value in generation.items() if key != "receipt_sha256"})
    (target / "generation.receipt.json").write_text(json.dumps(generation), encoding="utf-8")
    # Every dependent stage still has the old generation digest and is denied;
    # a latest valid audit is never silently substituted for this wrong ID.
    with pytest.raises(ValueError, match="dependency|receipt"):
        release(f)
    assert f.provider.calls == []


def test_rehashed_release_cannot_claim_human_validation_or_filter_the_gold_denominator(strict_fixture):
    f = strict_fixture
    complete(f)
    registered = release(f)
    forged = registered["release"]
    forged["label_scope"]["independent_human_validation"] = True
    forged["localization_denominator"] = "only_files_in_snapshot"
    identifier = digest(forged)
    path = f.root / "data/benchmark/releases" / (identifier + ".json")
    path.write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(ValueError, match="policy/label-scope"):
        releases.verify_dataset_release(f.root, identifier)


def test_holdout_release_is_dataset_construction_and_opens_no_evaluation_capability(fixture, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from issueintel.benchmarks import import_benchmark
    from issueintel.runtime.io import read_jsonl
    new_root = tmp_path / "holdout_release"
    source = new_root / "data/upstream/fixture.jsonl"
    source.parent.mkdir(parents=True)
    shutil.copyfile(fixture.root / "data/upstream/verified_fixture.jsonl", source)
    import_benchmark(new_root, verified_path=source, target_live=0, main_dev_count=0)
    case = read_jsonl(new_root / "data/benchmark/cases.jsonl")[0]
    gold = read_jsonl(new_root / "data/benchmark/gold.jsonl")[0]
    assert case["split"] == "main_test"
    config_dir = new_root / "configs"
    config_dir.mkdir()
    price = config_dir / "prices.json"
    shutil.copyfile(fixture.config.price_file, price)
    config = replace(fixture.config, root=new_root, price_file=price, ledger_file=new_root / "data/runtime/budget.sqlite")
    monkeypatch.setattr(RuntimeConfig, "load", lambda root: config)
    own_source = new_root / "src/issueintel"
    shutil.copytree(construction._SOURCE_ROOT, own_source)
    monkeypatch.setattr(construction, "_SOURCE_ROOT", own_source)
    f = SimpleNamespace(root=new_root, repo=fixture.repo, revision=fixture.revision, case=case, gold=gold,
                        provider=fixture.provider, acquisitions=fixture.acquisitions)
    authorize_original(f)
    first = proof_for(f, monkeypatch)
    bundle = read(new_root / first["receipt_bundle"]["path"])
    f.report = append_historical_audit(new_root, case["case_id"], bundle["issue_receipts"], bundle["revision_proof"],
        issue_references=bundle["issue_references"], query_policy="original_opening_query")
    f.record = construction.register_annotation_recipe(new_root, protocol="issue_time", splits=["main_test"],
                                                     query_policy="original_opening_query")
    complete(f)
    registered = release(f)
    verified = releases.verify_dataset_release(new_root, registered["release_sha256"])
    assert verified["inference_cases"][0]["split"] == "main_test"
    assert verified["evaluation_capability_granted"] is False
    assert not (new_root / "data/runtime/evaluation_authorizations").exists()
    assert f.provider.calls == [] and f.acquisitions == []


@pytest.mark.parametrize("changed", ["index", "blob"])
def test_first_release_cannot_adopt_a_corrupt_snapshot_or_blob_as_its_expected_hash(strict_fixture, changed):
    f = strict_fixture
    target = complete(f)
    generation = read(target / "generation.receipt.json")
    assets_dir = f.root / "data/derived/asof" / hashlib.sha256(f.case["case_id"].encode()).hexdigest()[:24] / generation["audit_id"]
    asset = read(assets_dir / "build_receipt.json")
    index_path = f.root / asset["snapshot_index"]
    index = json.loads(gzip.decompress(index_path.read_bytes()))
    if changed == "index":
        index["committer_time"] = "2001-01-01T00:00:00Z"
        index_path.write_bytes(gzip.compress(json.dumps(index).encode()))
    else:
        sha = index["files"][0]["sha256"]
        blob = f.root / "data/derived/blobs" / sha[:2] / (sha + ".txt.gz")
        blob.write_bytes(gzip.compress(b"corrupt source bytes\n"))
    with pytest.raises(ValueError, match="snapshot digest|source blob"):
        release(f)
    assert not list((f.root / "data/benchmark/releases").glob("*.json"))
    assert f.provider.calls == []


def reseal_synthetic_asset_chain(target, asset_path, asset):
    """Deliberately counterfeit a local receipt chain to test semantic checks."""
    asset_path.write_text(json.dumps(asset), encoding="utf-8")
    generation_path = target / "generation.receipt.json"
    generation = read(generation_path)
    generation["asset_receipt_sha256"] = digest(asset)
    generation["receipt_sha256"] = digest({key: value for key, value in generation.items() if key != "receipt_sha256"})
    generation_path.write_text(json.dumps(generation), encoding="utf-8")
    for name in ("critique", "judge", "variants"):
        path = target / (name + ".receipt.json")
        stage = read(path)
        stage["dependency_sha256"] = generation["receipt_sha256"]
        stage["receipt_sha256"] = digest({key: value for key, value in stage.items() if key != "receipt_sha256"})
        path.write_text(json.dumps(stage), encoding="utf-8")


@pytest.mark.parametrize("changed", ["revision", "node_span"])
def test_graph_binding_replay_rejects_resealed_metadata_before_first_release(strict_fixture, changed):
    f = strict_fixture
    target = complete(f)
    generation = read(target / "generation.receipt.json")
    assets_dir = f.root / "data/derived/asof" / hashlib.sha256(f.case["case_id"].encode()).hexdigest()[:24] / generation["audit_id"]
    path = assets_dir / "graph.json.gz"
    graph = json.loads(gzip.decompress(path.read_bytes()))
    if changed == "revision":
        graph["commit_sha"] = "0" * 40
    else:
        graph["nodes"][0]["sha256"] = "0" * 64
    path.write_bytes(gzip.compress(json.dumps(graph).encode()))
    asset_path = assets_dir / "build_receipt.json"
    asset = read(asset_path)
    asset["files"]["graph.json.gz"] = hashlib.sha256(path.read_bytes()).hexdigest()
    reseal_synthetic_asset_chain(target, asset_path, asset)
    with pytest.raises(ValueError, match="graph"):
        release(f)
    assert f.provider.calls == []


@pytest.fixture
def live_fixture(fixture, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from issueintel.benchmarks import import_benchmark
    from issueintel.runtime.io import read_jsonl
    from test_benchmarks import upstream, write_rows
    new_root = tmp_path / "live_release"
    source_dir = new_root / "data/upstream"
    source_dir.mkdir(parents=True)
    verified = write_rows(source_dir / "main.jsonl", [upstream("example__project-1")])
    live = write_rows(source_dir / "live.jsonl", [upstream("example__project-9", issue_numbers=["7"],
                      created="2025-08-01T00:00:00Z", problem_statement="Bug\nThe symptom\n")])
    import_benchmark(new_root, verified_path=verified, live_paths=[live], target_live=1)
    case = next(row for row in read_jsonl(new_root / "data/benchmark/cases.jsonl") if row["split"] == "temporal_candidate")
    config_dir = new_root / "configs"
    config_dir.mkdir()
    price = config_dir / "prices.json"
    shutil.copyfile(fixture.config.price_file, price)
    cutoff_source = "https://example.org/model-card"
    (config_dir / "benchmark.toml").write_text('model_cutoff="2024-07-01T00:00:00Z"\ncutoff_source="' + cutoff_source + '"\n', encoding="utf-8")
    config = replace(fixture.config, root=new_root, price_file=price, ledger_file=new_root / "data/runtime/budget.sqlite")
    monkeypatch.setattr(RuntimeConfig, "load", lambda root: config)
    own_source = new_root / "src/issueintel"
    shutil.copytree(construction._SOURCE_ROOT, own_source)
    monkeypatch.setattr(construction, "_SOURCE_ROOT", own_source)
    f = SimpleNamespace(root=new_root, case=case, provider=fixture.provider, acquisitions=fixture.acquisitions,
                        model=config.model, cutoff_source=cutoff_source)
    authorize_original(f)
    f.record = construction.register_annotation_recipe(new_root, protocol="issue_time", splits=["temporal_candidate"],
                                                     query_policy="original_opening_query")
    return f


def test_known_pre_cutoff_live_exclusion_is_replayed_and_kept_separate_from_pending(live_fixture):
    from issueintel.benchmarks.temporal import append_temporal_audit
    from test_benchmarks import issue_receipt
    f = live_fixture
    report = append_temporal_audit(f.root, f.case["case_id"], [issue_receipt(created="2019-01-01T00:00:00Z")])
    assert report["status"] == "excluded_issue_not_after_cutoff"
    registered = release(f)
    excluded = registered["release"]["exclusions"][0]
    assert excluded["source_eligibility"] == "known_ineligible"
    assert excluded["source_reason"] == "excluded_issue_not_after_cutoff"
    assert excluded["source_audit"]["audit_id"] == report["audit_id"]
    assert registered["release"]["counts"]["exclusions_by_source_eligibility"] == {"known_ineligible": 1}
    assert releases.verify_dataset_release(f.root, registered["release_sha256"])["inference_cases"] == []
    bundle = f.root / report["receipt_bundle"]["path"]
    bundle.write_bytes(bundle.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="bundle digest"):
        releases.verify_dataset_release(f.root, registered["release_sha256"])
    assert f.provider.calls == []


def test_legacy_date_audit_replays_additive_schema_without_rewriting_recorded_hashes(live_fixture):
    from issueintel.benchmarks.temporal import append_temporal_audit
    from test_benchmarks import issue_receipt
    f = live_fixture
    report = append_temporal_audit(f.root, f.case["case_id"], [issue_receipt(created="2019-01-01T00:00:00Z")])
    for key in ("post_model_cutoff_required", "missing_issue_references", "upstream_base_revision",
                "selected_snapshot_revision", "uses_historical_ancestor", "snapshot_ancestry_verified", "validation_role"):
        report.pop(key)
    for issue in report["issues"]:
        issue.pop("repository")
    report.pop("audit_id")
    report["audit_id"] = digest(report)
    overlay = f.root / "data/annotations/temporal_audit.jsonl"
    overlay.write_text(json.dumps(report) + "\n", encoding="utf-8")
    before = overlay.read_bytes()
    registered = release(f)
    excluded = registered["release"]["exclusions"][0]
    assert excluded["source_eligibility"] == "known_ineligible"
    assert excluded["source_audit"]["audit_id"] == report["audit_id"]
    assert releases.verify_dataset_release(f.root, registered["release_sha256"])["inference_cases"] == []
    assert overlay.read_bytes() == before


@pytest.mark.parametrize("changed", ["model", "cutoff", "source"])
def test_temporal_strict_proof_must_match_archived_recipe_model_and_cutoff(live_fixture, changed):
    from issueintel.benchmarks.temporal import append_temporal_audit
    from test_benchmarks import issue_receipt, push_receipt
    f = live_fixture
    cutoff = "2023-07-01T00:00:00Z" if changed == "cutoff" else "2024-07-01T00:00:00Z"
    provenance = {"model_id": "other-model" if changed == "model" else f.model,
        "source_url": "https://different.example/model-card" if changed == "source" else f.cutoff_source,
        "conservative_cutoff": cutoff}
    issue = issue_receipt(original_text=True)
    issue["body_provenance"]["payload"]["payload"]["issue"]["updated_at"] = issue["payload"]["created_at"]
    report = append_temporal_audit(f.root, f.case["case_id"], [issue], push_receipt(),
        model_cutoff=cutoff, cutoff_provenance=provenance, query_policy="original_opening_query")
    assert report["strict_issue_time_receipt_checks_passed"] is True  # legacy auditor's weaker contract
    with pytest.raises(ValueError, match="archived recipe"):
        release(f)
    assert f.provider.calls == []


@pytest.mark.parametrize("changed", ["edited_before_emission", "event_before_creation", "updated_after_emission", "event_after_fetch"])
def test_opened_payload_version_or_chronology_risk_is_excluded_with_receipts_preserved(strict_fixture, changed):
    f = strict_fixture
    bundle = read(f.root / f.report["receipt_bundle"]["path"])
    issue = bundle["issue_receipts"][0]
    event = issue["body_provenance"]["payload"]
    nested = event["payload"]["issue"]
    event["created_at"] = "2022-01-01T00:00:10Z"
    if changed == "edited_before_emission":
        nested["updated_at"] = "2022-01-01T00:00:05Z"
    elif changed == "event_before_creation":
        event["created_at"] = "2021-12-31T23:59:59Z"
        issue["body_provenance"]["source_url"] = "https://data.gharchive.org/2021-12-31-23.json.gz"
    elif changed == "updated_after_emission":
        nested["updated_at"] = "2022-01-01T00:00:15Z"
    else:
        issue["fetched_at"] = "2022-01-01T00:00:05Z"
    f.report = append_historical_audit(f.root, f.case["case_id"], [issue], bundle["revision_proof"],
        issue_references=bundle["issue_references"], query_policy="original_opening_query")
    assert f.report["strict_issue_time_receipt_checks_passed"] is True
    target = complete(f)
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    registered = release(f)
    excluded = registered["release"]["exclusions"][0]
    assert excluded["source_reason"] == "opening_text_version_unverified"
    assert excluded["annotation_technically_complete"] is True
    assert excluded["diagnostic_construction"]["stage_receipts"]["judge"]
    assert excluded["proof"]["opening_text_version"]["records"][0]["event_emitted_at"] == event["created_at"]
    assert releases.verify_dataset_release(f.root, registered["release_sha256"])["inference_cases"] == []
    assert before == {path.name: path.read_bytes() for path in target.iterdir()}
    assert f.provider.calls == []
