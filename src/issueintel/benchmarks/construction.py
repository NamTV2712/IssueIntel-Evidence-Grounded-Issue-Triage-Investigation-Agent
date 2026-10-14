"""Registered, stage-resumable dataset annotation, distinct from evaluation.

Registration precedes new generation. Pilot artifacts are never adopted. The
public investigation pipeline cannot see restricted gold or source-human notes;
only the subsequent offline reference judge receives the gold projection.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import zipfile

from issueintel.annotations.judge import judge_annotation
from issueintel.annotations.models import Annotation
from issueintel.annotations.variants import create_abstention_variants
from issueintel.investigation.pipeline import investigate, public_case
from issueintel.investigation.providers import GroqProvider, ProviderError
from issueintel.investigation.verifier import verify_hypotheses
from issueintel.runtime.budget import BudgetDenied
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import digest, read_jsonl, write_json, write_progress
from .asof_assets import build_asof_assets, verified_asof_receipt
from .store import verify_manifest
from .workflow import build_case_assets


_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_SPLITS = {"main_dev", "main_test", "temporal_candidate"}
_QUERY_POLICIES = {"source_query", "original_opening_query"}
_STAGE_FILES = {
    "generation": {"annotation.generated.json", "generation_inputs.json", "pack.generated.json", "localization.json"},
    "critique": {"verification.json"},
    "judge": {"annotation.judged.json"},
    "variants": {"variants.json", "answerability.json"},
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _safe(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Construction path escapes the project")
    # Resolve detects escaping junctions; reject internal aliases as well.
    current = path
    while current != root:
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("Construction path must not traverse links or junctions")
        current = current.parent
    return resolved


def _source_hashes(protocol: str) -> dict:
    if protocol not in {"benchmark_base", "issue_time"}:
        raise ValueError("Unknown annotation construction protocol")
    files = []
    for package in ("annotations", "investigation", "repository", "retrieval", "runtime"):
        files.extend((_SOURCE_ROOT / package).glob("*.py"))
    # Acquisition-only metadata builders may improve independently. Their
    # output receipts are revalidated by the pinned semantic proof validator;
    # they do not participate in generation, critique or gold judgment.
    files.extend(_SOURCE_ROOT / "benchmarks" / filename for filename in (
        "construction.py", "store.py", "localization.py", "workflow.py", "models.py", "sources.py", "importer.py"))
    if protocol == "issue_time":
        files.extend(_SOURCE_ROOT / "benchmarks" / filename for filename in (
            "asof_assets.py", "temporal.py", "history.py"))
        # A Trac receipt validator, when present, is part of the same strict
        # semantic boundary. It cannot affect the non-strict base constructor.
        files.extend((_SOURCE_ROOT / "benchmarks").glob("trac*.py"))
    if not files or any(not path.is_file() for path in files):
        raise ValueError("Recipe source inventory is unavailable")
    return {"src/issueintel/" + path.relative_to(_SOURCE_ROOT).as_posix(): _sha(path)
            for path in sorted(files)}


def _config_fingerprint(root: Path, config: RuntimeConfig) -> dict:
    paths = set(path for path in (root / "configs").glob("*")
                if path.is_file() and path.suffix in {".json", ".toml"})
    paths.add(config.price_file)
    inventory = {_safe(root, path).relative_to(root).as_posix(): _sha(path) for path in sorted(paths)}
    policy = {key: str(value) if isinstance(value, Decimal) else value for key, value in asdict(config.policy).items()}
    return {"files": inventory, "runtime": {"provider": "groq", "model": config.model,
        "max_output_tokens": config.max_output_tokens, "policy": policy,
        "price_file": _safe(root, config.price_file).relative_to(root).as_posix(),
        "ledger_file": _safe(root, config.ledger_file).relative_to(root).as_posix()}}


def _dataset(root: Path) -> tuple[dict, list[dict]]:
    manifest = verify_manifest(root)
    cases = read_jsonl(_safe(root, root / manifest["files"]["cases"]["path"]))
    ids = [case["case_id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case identity in construction input")
    return manifest, cases


def _recipe_path(root: Path, recipe_sha256: str) -> Path:
    if not isinstance(recipe_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", recipe_sha256):
        raise ValueError("recipe_sha256 must be a canonical SHA256")
    return _safe(root, root / "data/annotations/recipes" / (recipe_sha256 + ".json"))


def _query_policy(root: Path, protocol: str, query_policy: str) -> None:
    if query_policy not in _QUERY_POLICIES or (protocol == "benchmark_base" and query_policy != "source_query"):
        raise ValueError("Original opening query is supported only by strict issue_time construction")
    if query_policy == "original_opening_query":
        try:
            decisions = _json(_safe(root, root / "configs/author_decisions.json"))
        except (OSError, ValueError) as error:
            raise ValueError("Original opening query lacks an author decision") from error
        statement = decisions.get("historical_query_user_statement")
        if decisions.get("historical_query_policy") != query_policy or not isinstance(statement, str) or not statement.strip():
            raise ValueError("Original opening query lacks a matching author decision")


def _archive_sources(root: Path, recipe: dict) -> dict:
    """Capture importable source/configuration; never include .env or dataset text."""
    content = {"src/issueintel/" + path.relative_to(_SOURCE_ROOT).as_posix(): _safe(root, path).read_bytes()
               for path in sorted(_SOURCE_ROOT.rglob("*.py")) if path.is_file()}
    for path in sorted((root / "configs").glob("*")):
        if path.is_file() and path.suffix in {".toml", ".json"}:
            content[_safe(root, path).relative_to(root).as_posix()] = path.read_bytes()
    project = root / "pyproject.toml"
    if project.exists():
        content["pyproject.toml"] = _safe(root, project).read_bytes()
    inventory = {name: hashlib.sha256(data).hexdigest() for name, data in content.items()}
    if any(inventory.get(name) != sha for name, sha in recipe["source_hashes"].items()):
        raise ValueError("Source changed while capturing the recipe archive")
    for name, sha in recipe["configuration"]["files"].items():
        if name.startswith("configs/") and inventory.get(name) != sha:
            raise ValueError("Configuration changed while capturing the recipe archive")
    inventory_sha = digest(inventory)
    directory = _safe(root, root / "data/annotations/recipe_sources")
    archive = _safe(root, directory / (inventory_sha + ".zip"))
    descriptor = _safe(root, directory / (inventory_sha + ".json"))
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
        for name in sorted(content):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            handle.writestr(info, content[name], compresslevel=9)
    encoded = buffer.getvalue()
    sha = hashlib.sha256(encoded).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        if archive.stat().st_size != len(encoded) or _sha(archive) != sha:
            raise ValueError("Existing source archive is changed; explicit repair required")
    else:
        with archive.open("xb") as handle:
            handle.write(encoded)
    archive_record = {"path": archive.relative_to(root).as_posix(), "sha256": sha, "bytes": len(encoded)}
    _write_once(descriptor, {"schema_version": "issueintel.recipe_source_archive.v1",
        "scope": "source_and_configuration_only", "dataset_text_included": False,
        "inventory_sha256": inventory_sha, "files": inventory, "archive": archive_record})
    reference = {**archive_record, "inventory_sha256": inventory_sha,
        "descriptor_path": descriptor.relative_to(root).as_posix(), "descriptor_sha256": _sha(descriptor)}
    _verify_source_archive(root, reference, recipe)
    return reference


def _verify_source_archive(root: Path, reference: dict, recipe: dict) -> None:
    directory = root / "data/annotations/recipe_sources"
    archive = _safe(root, root / reference["path"])
    descriptor = _safe(root, root / reference["descriptor_path"])
    if archive.parent != directory or descriptor.parent != directory:
        raise ValueError("Source archive must stay in its private recipe directory")
    if archive.stat().st_size != reference["bytes"] or _sha(archive) != reference["sha256"] or _sha(descriptor) != reference["descriptor_sha256"]:
        raise ValueError("Source archive/descriptor integrity failure")
    record = _json(descriptor)
    inventory = record["files"]
    if (record.get("schema_version") != "issueintel.recipe_source_archive.v1"
            or record.get("scope") != "source_and_configuration_only"
            or record.get("dataset_text_included") is not False
            or record.get("inventory_sha256") != reference["inventory_sha256"]
            or digest(inventory) != reference["inventory_sha256"]
            or record.get("archive") != {key: reference[key] for key in ("path", "sha256", "bytes")}):
        raise ValueError("Source archive descriptor identity mismatch")
    if any(inventory.get(name) != sha for name, sha in recipe["source_hashes"].items()):
        raise ValueError("Source archive is not bound to the recipe's frozen code")
    if any(inventory.get(name) != sha for name, sha in recipe["configuration"]["files"].items()
           if name.startswith("configs/")):
        raise ValueError("Source archive is not bound to the recipe's frozen configuration")
    with zipfile.ZipFile(archive) as handle:
        names = handle.namelist()
        if len(names) != len(set(names)) or set(names) != set(inventory):
            raise ValueError("Source archive file inventory mismatch")
        for name in names:
            allowed = ((name.startswith("src/issueintel/") and name.endswith(".py"))
                       or (name.startswith("configs/") and Path(name).suffix in {".json", ".toml"})
                       or name == "pyproject.toml")
            if not allowed or ".." in Path(name).parts or "\\" in name or ":" in name:
                raise ValueError("Source archive contains a forbidden path")
            if hashlib.sha256(handle.read(name)).hexdigest() != inventory[name]:
                raise ValueError("Source archive member digest mismatch")


def register_annotation_recipe(root: Path | str, *, protocol: str, splits,
                               case_ids=None, judge=True, independent_critic=True, top_k=8,
                               query_policy="source_query") -> dict:
    """Freeze explicit construction inputs before any new recipe model calls."""
    root = Path(root).resolve()
    if protocol not in {"benchmark_base", "issue_time"}:
        raise ValueError("Unknown annotation construction protocol")
    _query_policy(root, protocol, query_policy)
    if isinstance(splits, (str, bytes)) or not splits or any(split not in _SPLITS for split in splits):
        raise ValueError("Explicit valid construction splits required")
    if len(splits) != len(set(splits)):
        raise ValueError("Duplicate construction split")
    if type(judge) is not bool or type(independent_critic) is not bool or type(top_k) is not int or not 1 <= top_k <= 50:
        raise ValueError("Invalid registered stage/retrieval options")
    _, cases = _dataset(root)
    selected = {case["case_id"]: case for case in cases if case["split"] in splits}
    if case_ids is not None:
        if isinstance(case_ids, (str, bytes)) or len(case_ids) != len(set(case_ids)) or not case_ids:
            raise ValueError("case_ids must be a nonempty unique sequence")
        if any(identifier not in selected for identifier in case_ids):
            raise ValueError("Registered case is outside the selected dataset splits")
        selected = {identifier: selected[identifier] for identifier in case_ids}
    if not selected:
        raise ValueError("Construction recipe cannot have an empty cohort")
    config = RuntimeConfig.load(root)
    config.price()  # Dated bounds, without a secret or paid call.
    sources = _source_hashes(protocol)
    source_key = lambda relative: sources.get("src/issueintel/" + relative)
    recipe = {"schema_version": "issueintel.annotation_recipe.v1", "mode": "dataset_construction",
        "evaluation_performed": False, "registered_before_generation": True,
        "pilot_adoption": "forbidden", "protocol": protocol, "query_policy": query_policy,
        "strict_claim": "per_case_independent_receipts_required" if protocol == "issue_time" else "diagnostic_non_strict",
        "splits": sorted(splits), "cases": [{"case_id": identifier, "split": selected[identifier]["split"],
            "case_sha256": digest(selected[identifier])} for identifier in sorted(selected)],
        "dataset_manifest_sha256": _sha(root / "data/manifests/dataset.json"),
        "source_hashes": sources, "source_inventory_sha256": digest(sources),
        "prompt_sources": {"hypotheses": source_key("annotations/generator.py"),
            "critique": source_key("investigation/verifier.py"), "gold_judge": source_key("annotations/judge.py")},
        "schema_source_sha256": source_key("annotations/structured.py"),
        "configuration": _config_fingerprint(root, config),
        "options": {"judge": judge, "independent_critic": independent_critic, "top_k": top_k,
            "schema_repair_maximum": 1, "variant_policy": "remove_all_retrieved_code_controlled_unreviewed"},
        "human_reviewed": False, "causal_truth": "unverified"}
    identifier = digest(recipe)
    path = _recipe_path(root, identifier)
    lock = _safe(root, root / "data/annotations/recipes" / ".registration.lock")
    with _worker_lock(lock, wait=True):
        if path.exists():
            return load_annotation_recipe(root, identifier)
        # A change while waiting for the short registration critical section
        # cannot be recorded as a frozen current recipe.
        if (_source_hashes(protocol) != sources
                or _config_fingerprint(root, RuntimeConfig.load(root)) != recipe["configuration"]
                or _sha(root / "data/manifests/dataset.json") != recipe["dataset_manifest_sha256"]):
            raise ValueError("Recipe inputs changed during registration")
        target = _safe(root, root / "data/annotations/dataset" / identifier)
        if target.exists() and any(target.iterdir()):
            raise ValueError("Cannot retrospectively register existing generation artifacts")
        source_archive = _archive_sources(root, recipe)
        record = {"recipe_sha256": identifier, "registered_at": datetime.now(timezone.utc).isoformat(),
            "recipe": recipe, "source_archive": source_archive}
        record["registration_sha256"] = digest(record)
        _write_once(path, record)
        return record


def load_annotation_recipe(root: Path | str, recipe_sha256: str) -> dict:
    """Reject changed source, configuration, cohort or registration on resume."""
    root = Path(root).resolve()
    record = _json(_recipe_path(root, recipe_sha256))
    expected_keys = {"recipe_sha256", "registered_at", "recipe", "registration_sha256"}
    # Past registrations had no archive field. Never rewrite those records or
    # retroactively describe a later source capture as pre-generation evidence.
    if not expected_keys <= set(record) or set(record) - expected_keys - {"source_archive"} or digest({key: value for key, value in record.items() if key != "registration_sha256"}) != record.get("registration_sha256"):
        raise ValueError("Annotation recipe registration integrity failure")
    recipe = record["recipe"]
    if record["recipe_sha256"] != recipe_sha256 or digest(recipe) != recipe_sha256:
        raise ValueError("Annotation recipe identity mismatch")
    if recipe.get("schema_version") != "issueintel.annotation_recipe.v1" or recipe.get("mode") != "dataset_construction" or recipe.get("registered_before_generation") is not True or recipe.get("evaluation_performed") is not False:
        raise ValueError("Invalid annotation construction registration")
    _query_policy(root, recipe["protocol"], recipe.get("query_policy", "source_query"))
    if "source_archive" in record:
        _verify_source_archive(root, record["source_archive"], recipe)
    _, cases = _dataset(root)
    if recipe["dataset_manifest_sha256"] != _sha(root / "data/manifests/dataset.json"):
        raise ValueError("Dataset changed after annotation recipe registration")
    sources = _source_hashes(recipe["protocol"])
    if sources != recipe["source_hashes"] or digest(sources) != recipe["source_inventory_sha256"]:
        raise ValueError("Recipe-relevant source changed; register a new recipe instead of resuming")
    config = RuntimeConfig.load(root)
    if _config_fingerprint(root, config) != recipe["configuration"]:
        raise ValueError("Recipe configuration changed; register a new recipe instead of resuming")
    by_id = {case["case_id"]: case for case in cases}
    for descriptor in recipe["cases"]:
        case = by_id.get(descriptor["case_id"])
        if case is None or case["split"] != descriptor["split"] or digest(case) != descriptor["case_sha256"]:
            raise ValueError("Recipe case/split binding changed")
    return record


def _write_once(path: Path, value) -> None:
    """Completed artifacts are immutable; no refresh of an expected hash."""
    if path.exists():
        if _json(path) != value:
            raise ValueError("Construction artifact already exists with different contents")
        return
    write_json(path, value)


@contextmanager
def _worker_lock(path: Path, *, wait=False):
    """OS advisory lock releases on process death; a file alone is not liveness."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except OSError as error:
            raise RuntimeError("Another annotation construction worker holds this recipe") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _stage_identity(recipe: dict, case: dict, stage: str) -> dict:
    return {"recipe_sha256": digest(recipe), "case_id": case["case_id"], "split": case["split"],
        "case_sha256": digest(case), "dataset_manifest_sha256": recipe["dataset_manifest_sha256"],
        "stage": stage, "mode": "dataset_construction", "evaluation_performed": False}


def _stage(target: Path, recipe: dict, case: dict, stage: str, *, dependency=None) -> dict | None:
    path = target / (stage + ".receipt.json")
    if not path.exists():
        return None
    receipt = _json(path)
    if any(receipt.get(key) != value for key, value in _stage_identity(recipe, case, stage).items()):
        raise ValueError("Construction stage identity/recipe mismatch")
    if receipt.get("dependency_sha256") != dependency:
        raise ValueError("Construction stage dependency changed")
    if set(receipt.get("files", {})) != _STAGE_FILES[stage]:
        raise ValueError("Construction stage artifact inventory mismatch")
    if any(_sha(target / name) != sha for name, sha in receipt["files"].items()):
        raise ValueError("Construction stage artifacts are missing or changed")
    expected = digest({key: value for key, value in receipt.items() if key != "receipt_sha256"})
    if expected != receipt.get("receipt_sha256"):
        raise ValueError("Construction stage receipt digest mismatch")
    return receipt


def _save_stage(target: Path, recipe: dict, case: dict, stage: str, outputs: dict, *, dependency=None, extra=None) -> dict:
    if set(outputs) != _STAGE_FILES[stage]:
        raise ValueError("Invalid construction stage outputs")
    for name, value in outputs.items():
        _write_once(target / name, value)
    receipt = {**_stage_identity(recipe, case, stage), "dependency_sha256": dependency,
        "files": {name: _sha(target / name) for name in sorted(outputs)},
        "completed_at": datetime.now(timezone.utc).isoformat(), "human_reviewed": False, **(extra or {})}
    receipt["receipt_sha256"] = digest(receipt)
    _write_once(target / (stage + ".receipt.json"), receipt)
    return receipt


def _completion(provider):
    return provider if callable(provider) else provider.complete_json


class _RegisteredCompletion:
    """Check frozen code/config before every actual dispatch, including repair."""
    def __init__(self, root, recipe, provider):
        self.root, self.recipe, self.provider = root, recipe, provider

    def _check(self):
        if (_sha(self.root / "data/manifests/dataset.json") != self.recipe["dataset_manifest_sha256"]
                or _source_hashes(self.recipe["protocol"]) != self.recipe["source_hashes"]
                or _config_fingerprint(self.root, RuntimeConfig.load(self.root)) != self.recipe["configuration"]):
            raise ValueError("Registered recipe changed before model dispatch")

    def __call__(self, messages, max_output_tokens=2048):
        self._check()
        return _completion(self.provider)(messages, max_output_tokens=max_output_tokens)

    def __getattr__(self, name):
        attribute = getattr(self.provider, name)
        if name in {"complete_structured", "complete_json"} and callable(attribute):
            def admitted(*args, **kwargs):
                self._check()
                return attribute(*args, **kwargs)
            return admitted
        return attribute


def annotate_dataset(root: Path | str, recipe_sha256: str, *, limit=800) -> dict:
    """Construct registered label overlays, including holdout, without evaluation.

    Holdout annotations cannot be used for model/prompt selection. No capability
    is opened, evaluation authorization recorded, metrics computed or predictions
    scored here. Gold is isolated until the separately receipted offline judge.
    """
    if type(limit) is not int or not 1 <= limit <= 800:
        raise ValueError("Construction limit must be between 1 and 800")
    root = Path(root).resolve()
    record = load_annotation_recipe(root, recipe_sha256)
    recipe = record["recipe"]
    query_policy = recipe.get("query_policy", "source_query")
    config = RuntimeConfig.load(root)
    _, cases = _dataset(root)
    by_id = {case["case_id"]: case for case in cases}
    gold = {row["case_id"]: row for row in read_jsonl(root / "data/benchmark/gold.jsonl")}
    human_path = root / "data/annotations/source_human.jsonl"
    source_human = {row["case_id"]: row for row in read_jsonl(human_path)} if human_path.exists() else {}
    base = _safe(root, root / "data/annotations/dataset" / recipe_sha256)
    summary = {"recipe_sha256": recipe_sha256, "mode": "dataset_construction", "evaluation_performed": False,
        "completed": [], "skipped": [], "pending": [], "failures": [], "stages_completed_this_run": {}}
    provider = None

    def guarded_provider():
        nonlocal provider
        if provider is None:
            if not os.environ.get("GROQ_API_KEY", "").strip():
                raise BudgetDenied("Missing local GROQ_API_KEY")
            config.price()
            provider = _RegisteredCompletion(root, recipe, GroqProvider(config))
            if provider.model != recipe["configuration"]["runtime"]["model"]:
                raise ValueError("Provider model differs from the registered recipe")
        return provider

    with _worker_lock(base / ".worker.lock"):
        considered = 0
        for descriptor in recipe["cases"]:
            case = by_id[descriptor["case_id"]]
            target = _safe(root, base / hashlib.sha256(case["case_id"].encode()).hexdigest()[:24])
            stage = "generation"
            try:
                # Check every available completed stage before any new paid call.
                generation = _stage(target, recipe, case, "generation")
                dependency = generation["receipt_sha256"] if generation else None
                for name in ("critique", "judge", "variants"):
                    existing = _stage(target, recipe, case, name, dependency=dependency)
                    if existing is not None and generation is None:
                        raise ValueError("A later stage exists without immutable generation")
                for name in _STAGE_FILES:
                    if not (target / (name + ".receipt.json")).exists() and any((target / file).exists() for file in _STAGE_FILES[name]):
                        raise ValueError("Unreceipted construction artifacts require explicit repair before model calls")
                stages = ["generation", "variants"]
                if recipe["options"]["independent_critic"]:
                    stages.append("critique")
                if recipe["options"]["judge"]:
                    stages.append("judge")
                if recipe["protocol"] == "issue_time":
                    try:
                        verified_asof_receipt(root, case, audit_id=generation.get("audit_id") if generation else None,
                            query_policy=query_policy)
                    except ValueError as error:
                        if generation is None and str(error).startswith("No verified historical receipt"):
                            if considered >= limit:
                                break
                            considered += 1
                            summary["pending"].append({"case_id": case["case_id"], "reason": str(error)})
                            continue
                        raise
                if generation and all(_stage(target, recipe, case, name, dependency=dependency)
                                      for name in stages if name != "generation"):
                    summary["skipped"].append(case["case_id"])
                    continue
                if considered >= limit:
                    break
                considered += 1
                if generation is None:
                    assets = build_asof_assets(root, case, gold[case["case_id"]], query_policy=query_policy) if recipe["protocol"] == "issue_time" else build_case_assets(root, case, gold[case["case_id"]])
                    runtime_case = public_case(assets.get("case", case))
                    active = guarded_provider()
                    pack = investigate(runtime_case, assets["snapshot"].repository,
                        complete_json=_completion(active), provider=active.name, model=active.model,
                        protocol=recipe["protocol"], snapshot=assets["snapshot"], graph=assets["graph"],
                        query_receipt=assets.get("query_receipt"), availability=assets.get("availability"),
                        independent_critic=False, top_k=recipe["options"]["top_k"], max_output_tokens=config.max_output_tokens)
                    if pack["annotation"] is None:
                        raise ValueError("No grounded annotation: " + pack["stop_reason"])
                    strict = recipe["protocol"] == "issue_time"
                    if strict and not pack["leakage_audit"]["strict_issue_time_verified"]:
                        raise ValueError("Historical investigation lacks strict query/snapshot proof")
                    annotation = Annotation.from_dict(pack["annotation"]).validate_evidence(pack["evidence"]).to_dict()
                    generation = _save_stage(target, recipe, case, "generation", {
                        "annotation.generated.json": annotation,
                        "generation_inputs.json": {"case": runtime_case, "evidence": pack["evidence"]},
                        "pack.generated.json": pack, "localization.json": assets["localization"]}, extra={
                            "snapshot_sha256": assets["snapshot"].snapshot_sha256,
                            "asset_receipt_sha256": digest(assets["receipt"]), "input_sha256": digest({"case": runtime_case, "evidence": pack["evidence"]}),
                            "protocol": recipe["protocol"], "strict_issue_time_verified": strict,
                            "query_policy": query_policy,
                            "upstream_base_revision": case["revision"], "selected_snapshot_revision": runtime_case["revision"],
                            "audit_id": assets["receipt"].get("audit_id"),
                            "run_id": pack["run_id"], "gold_in_inference": False})
                    summary["stages_completed_this_run"].setdefault(case["case_id"], []).append("generation")
                    dependency = generation["receipt_sha256"]
                inputs = _json(target / "generation_inputs.json")
                annotation = _json(target / "annotation.generated.json")
                Annotation.from_dict(annotation).validate_evidence(inputs["evidence"])
                if generation.get("input_sha256") != digest(inputs):
                    raise ValueError("Generation input digest differs from its receipt")
                if recipe["options"]["independent_critic"] and not _stage(target, recipe, case, "critique", dependency=dependency):
                    stage = "critique"
                    active = guarded_provider()
                    verification = verify_hypotheses(annotation, inputs["evidence"], _completion(active), max_output_tokens=config.max_output_tokens)
                    _save_stage(target, recipe, case, stage, {"verification.json": verification}, dependency=dependency)
                    summary["stages_completed_this_run"].setdefault(case["case_id"], []).append(stage)
                if recipe["options"]["judge"] and not _stage(target, recipe, case, "judge", dependency=dependency):
                    stage = "judge"
                    active = guarded_provider()
                    judged = judge_annotation(annotation, inputs["case"], inputs["evidence"], gold[case["case_id"]],
                        _completion(active), provider=active.name, model=active.model, max_output_tokens=config.max_output_tokens).to_dict()
                    _save_stage(target, recipe, case, stage, {"annotation.judged.json": judged}, dependency=dependency,
                        extra={"gold_sha256": digest(gold[case["case_id"]]), "causal_truth": "unverified"})
                    summary["stages_completed_this_run"].setdefault(case["case_id"], []).append(stage)
                if not _stage(target, recipe, case, "variants", dependency=dependency):
                    stage = "variants"
                    variants = create_abstention_variants(inputs["case"], inputs["evidence"],
                        critical_ids=[item["id"] for item in inputs["evidence"] if item["kind"] == "code"])
                    for variant in variants:
                        variant["annotation"]["source_quality_scope"] = "upstream_query_only"
                        variant["annotation"]["human_validated_original_query_overlay"] = False
                    answerability = {"case_id": case["case_id"], "split": case["split"],
                        "automatic_recommendation": annotation["abstention"], "source_quality": source_human.get(case["case_id"]),
                        "query_policy": query_policy, "source_quality_scope": "upstream_query_only",
                        "human_validated_original_query_overlay": False,
                        "source_ordinal_is_abstention": False, "human_validated_overlay": False,
                        "controlled_variant_policy": recipe["options"]["variant_policy"],
                        "variant_validation_status": "unreviewed", "causal_truth": "unverified"}
                    _save_stage(target, recipe, case, stage, {"variants.json": variants, "answerability.json": answerability}, dependency=dependency)
                    summary["stages_completed_this_run"].setdefault(case["case_id"], []).append(stage)
                summary["completed"].append(case["case_id"])
            except BudgetDenied as error:
                summary["stop_reason"] = str(error)
                break
            except ProviderError as error:
                if error.status in {401, 403, 429}:
                    summary.update(stop_reason=str(error), provider_status=error.status, retry_after=error.retry_after)
                    break
                summary["failures"].append({"case_id": case["case_id"], "stage": stage, "error": str(error)})
            except (ValueError, RuntimeError, OSError, KeyError) as error:
                summary["failures"].append({"case_id": case["case_id"], "stage": stage, "error": str(error)})
            finally:
                summary["latest_case"] = case["case_id"]
                summary["budget"] = config.ledger().status()
                summary["provider_rate_limits"] = getattr(provider, "last_rate_limits", {})
                write_progress(root / "artifacts/annotation_dataset_progress" / (recipe_sha256 + ".json"), summary)
    return summary
