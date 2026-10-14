"""Immutable, integrity-checked dataset snapshots, separate from evaluation.

Releases inspect construction receipts and hashes, never heldout hypothesis
contents. Historical registrations are checked against their archived source,
not against today's implementation or runtime configuration.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import tomllib
from urllib.parse import urlparse

from issueintel.repository.cache import load_snapshot, _read_gzip_bounded
from issueintel.repository.snapshot import source_lines
from issueintel.runtime.io import digest, read_jsonl
from . import construction
from .asof_assets import verified_asof_receipt
from .store import verify_manifest
from .models import utc_timestamp
from .temporal import audit_historical_case, audit_temporal_case, reconstruct_query_text


_STAGES = ("generation", "critique", "judge", "variants")
_SPLITS = {"main_dev", "main_test", "temporal_candidate"}
_SCHEMA = "issueintel.dataset_release.v1"


def _declarations() -> dict:
    return {"schema_version": _SCHEMA, "mode": "dataset_construction", "evaluation_performed": False,
        "dataset_name": "IssueIntel-Bench", "protocol": "issue_time", "query_policy": "original_opening_query",
        "label_scope": {"hypotheses": "automatic_reference_relative", "independent_human_validation": False,
            "synthetic_variants": "controlled_unreviewed", "source_human_quality": "upstream_query_only",
            "human_inter_annotator_agreement": None, "causal_truth": "unverified"},
        "localization_denominator": "all_upstream_gold_patch_files_including_unavailable",
        "verification_scope": "source/archive/stage hashes, restored snapshot/blob integrity, graph source bindings and replayed historical proof; annotation quality unverified",
        "contamination_free_claim": False, "all_source_cases_complete_claim": False}


def _identifier(value: str, kind: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{kind} must be a canonical SHA256")
    return value


def _path(root: Path, relative: str, prefix: str) -> Path:
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise ValueError("Release artifact path is not canonical")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != relative:
        raise ValueError("Release artifact path escapes its private directory")
    resolved = construction._safe(root, root / path)
    if not resolved.is_relative_to(root / prefix):
        raise ValueError("Release artifact is outside its private directory")
    return resolved


def _sha(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Release metadata must be a JSON object")
    return value


def _file(root: Path, path: Path) -> dict:
    path = construction._safe(root, path)
    return {"path": path.relative_to(root).as_posix(), "sha256": _sha(path), "bytes": path.stat().st_size}


def _check_file(root: Path, descriptor: dict, prefix: str) -> Path:
    path = _path(root, descriptor["path"], prefix)
    if _file(root, path) != descriptor:
        raise ValueError("Dataset release artifact integrity failure")
    return path


def _assessments(root: Path, identifier: str, registration: str) -> list[dict]:
    directory = construction._safe(root, root / "data/annotations/recipe_assessments" / identifier)
    result = []
    for path in sorted(directory.glob("*.json")):
        assessment = _object(construction._safe(root, path))
        if (digest(assessment) != path.stem or assessment.get("recipe_sha256") != identifier
                or assessment.get("registration_sha256") != registration
                or type(assessment.get("primary_eligible")) is not bool
                or assessment.get("evaluation_performed") is not False):
            raise ValueError("Recipe assessment integrity/binding failure")
        if assessment["primary_eligible"] is False:
            raise ValueError("Diagnostic recipe assessment excludes this recipe from a primary release")
        result.append(_file(root, path))
    return result


def _registration(root: Path, identifier: str, manifest_sha: str, cases: dict) -> tuple[dict, dict]:
    path = _path(root, f"data/annotations/recipes/{identifier}.json", "data/annotations/recipes")
    record = _object(path)
    recipe = record.get("recipe")
    if (not isinstance(recipe, dict) or record.get("recipe_sha256") != identifier or digest(recipe) != identifier
            or digest({key: value for key, value in record.items() if key != "registration_sha256"}) != record.get("registration_sha256")
            or recipe.get("schema_version") != "issueintel.annotation_recipe.v1"
            or recipe.get("mode") != "dataset_construction" or recipe.get("evaluation_performed") is not False
            or recipe.get("registered_before_generation") is not True
            or recipe.get("pilot_adoption") != "forbidden" or recipe.get("dataset_manifest_sha256") != manifest_sha
            or recipe.get("protocol") != "issue_time" or recipe.get("query_policy") != "original_opening_query"
            or recipe.get("human_reviewed") is not False or recipe.get("causal_truth") != "unverified"
            or recipe.get("options", {}).get("judge") is not True
            or recipe.get("options", {}).get("independent_critic") is not True):
        raise ValueError("Primary release requires a valid strict/original-opening four-stage registration")
    sources = recipe.get("source_hashes")
    if not isinstance(sources, dict) or not sources or digest(sources) != recipe.get("source_inventory_sha256"):
        raise ValueError("Recipe source inventory digest mismatch")
    for name, sha in sources.items():
        if not name.startswith("src/issueintel/") or not name.endswith(".py"):
            raise ValueError("Recipe source inventory contains an invalid member")
        _identifier(sha, "Source digest")
    reference = record.get("source_archive")
    if not isinstance(reference, dict):
        raise ValueError("Primary release requires the pre-generation source/configuration archive")
    construction._verify_source_archive(root, reference, recipe)
    archive_descriptor = _object(_path(root, reference["descriptor_path"], "data/annotations/recipe_sources"))
    archived = archive_descriptor["files"]
    if any(archived.get(name) != sha for name, sha in recipe["configuration"]["files"].items()):
        raise ValueError("Recipe configuration was not captured in its pre-generation archive")
    frozen_decision = json.loads(_archive_member(root, reference, "configs/author_decisions.json"))
    if (frozen_decision.get("historical_query_policy") != "original_opening_query"
            or not isinstance(frozen_decision.get("historical_query_user_statement"), str)
            or not frozen_decision["historical_query_user_statement"].strip()):
        raise ValueError("Archived recipe lacks the original-opening author decision")
    descriptors = recipe.get("cases")
    if not isinstance(descriptors, list) or not descriptors:
        raise ValueError("Recipe has no registered cases")
    seen = set()
    for descriptor in descriptors:
        case = cases.get(descriptor.get("case_id"))
        if (case is None or case["case_id"] in seen or case["split"] not in _SPLITS
                or descriptor != {"case_id": case["case_id"], "split": case["split"], "case_sha256": digest(case)}
                or case["split"] not in recipe["splits"]):
            raise ValueError("Recipe case/split binding mismatch")
        seen.add(case["case_id"])
    assessments = _assessments(root, identifier, record["registration_sha256"])
    temporal_policy = None
    if "temporal_candidate" in recipe["splits"]:
        if "configs/benchmark.toml" not in recipe["configuration"]["files"]:
            raise ValueError("Temporal release lacks an archived benchmark cutoff configuration")
        benchmark = tomllib.loads(_archive_member(root, reference, "configs/benchmark.toml").decode("utf-8"))
        cutoff = utc_timestamp(benchmark.get("model_cutoff"))
        source_url = benchmark.get("cutoff_source")
        model = recipe["configuration"]["runtime"]["model"]
        if not isinstance(model, str) or not model or not isinstance(source_url, str) or urlparse(source_url).scheme != "https":
            raise ValueError("Temporal release archived model/cutoff source is invalid")
        temporal_policy = {"model_id": model, "conservative_cutoff": cutoff, "source_url": source_url}
    descriptor = {"recipe_sha256": identifier, "registration_sha256": record["registration_sha256"],
        "registration": _file(root, path), "source_archive": reference,
        "source_inventory_sha256": recipe["source_inventory_sha256"],
        "configuration_sha256": digest(recipe["configuration"]), "assessments": assessments,
        "temporal_policy": temporal_policy}
    return record, descriptor


def _archive_member(root: Path, reference: dict, name: str) -> bytes:
    import zipfile
    with zipfile.ZipFile(_path(root, reference["path"], "data/annotations/recipe_sources")) as archive:
        return archive.read(name)


def _stages(root: Path, recipe: dict, case: dict) -> tuple[Path, dict, list[str]]:
    identifier = digest(recipe)
    key = hashlib.sha256(case["case_id"].encode()).hexdigest()[:24]
    target = _path(root, f"data/annotations/dataset/{identifier}/{key}", f"data/annotations/dataset/{identifier}")
    for name in _STAGES:
        for filename in construction._STAGE_FILES[name] | {name + ".receipt.json"}:
            construction._safe(root, target / filename)
    found = {"generation": construction._stage(target, recipe, case, "generation")}
    dependency = found["generation"]["receipt_sha256"] if found["generation"] else None
    for name in _STAGES[1:]:
        found[name] = construction._stage(target, recipe, case, name, dependency=dependency)
        if found[name] is not None and found["generation"] is None:
            raise ValueError("Construction stage exists without its immutable generation")
    for name in _STAGES:
        if found[name] is None and any((target / filename).exists() for filename in construction._STAGE_FILES[name]):
            raise ValueError("Unreceipted construction artifact cannot enter a release")
    generation = found["generation"]
    if generation is not None and (generation.get("protocol") != "issue_time"
            or generation.get("query_policy") != "original_opening_query"
            or generation.get("strict_issue_time_verified") is not True or generation.get("gold_in_inference") is not False):
        raise ValueError("Generation is not a strict original-opening construction")
    if generation is not None:
        _identifier(generation.get("audit_id"), "Generation audit identity")
    return target, found, [name for name in _STAGES if found[name] is None]


def _cutoff_binding(case: dict, report: dict, policy: dict | None, *, strict: bool) -> None:
    if case["split"] != "temporal_candidate":
        return
    if not policy or utc_timestamp(report.get("configured_model_cutoff")) != policy["conservative_cutoff"]:
        raise ValueError("Temporal audit cutoff differs from the archived recipe configuration")
    provenance = report.get("cutoff_provenance")
    if (strict or provenance is not None) and (not isinstance(provenance, dict)
            or any(provenance.get(key) != value for key, value in policy.items())):
        raise ValueError("Temporal audit model/cutoff provenance differs from the archived recipe")


def _opening_versions(bundle: dict) -> dict:
    """An opened event can be emitted after its nested issue was edited.

    Initial-version eligibility uses payload version timestamps, with no
    arbitrary event-lag cap. Later ``updated_at`` is uncertainty about the body,
    not evidence that an edit definitely occurred.
    """
    records = []
    verified = True
    for receipt in bundle["issue_receipts"]:
        provenance = receipt.get("body_provenance") or {}
        event = provenance.get("payload") or {}
        if event.get("type") != "IssuesEvent":
            records.append({"source_url": receipt["source_url"], "basis": "replayed_initial_version_adapter",
                            "original_version_verified": True})
            continue
        issue = event["payload"]["issue"]
        created = utc_timestamp(issue.get("created_at"), optional=True)
        updated = utc_timestamp(issue.get("updated_at"), optional=True)
        emitted = utc_timestamp(event.get("created_at"), optional=True)
        fetched = utc_timestamp(provenance.get("fetched_at") or receipt.get("fetched_at"), optional=True)
        instant = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
        chronological = bool(created and emitted and instant(created) <= instant(emitted)
            and (not updated or instant(created) <= instant(updated) <= instant(emitted))
            and (not fetched or instant(emitted) <= instant(fetched)))
        initial_version = bool(chronological and updated and instant(updated) == instant(created))
        records.append({"source_url": receipt["source_url"], "basis": "opened_event_nested_issue_version_timestamps",
            "issue_created_at": created, "nested_issue_updated_at": updated, "event_emitted_at": emitted,
            "source_fetched_at": fetched, "chronology_valid": chronological,
            "original_version_verified": initial_version})
        verified = verified and initial_version
    return {"original_version_verified": verified, "records": records,
        "assumption": "opened payload with unchanged nested version timestamp represents its initial text; timestamps do not prove absence of unrecorded edits"}


def _proof(root: Path, case: dict, audit_id: str | None, temporal_policy: dict | None) -> tuple[dict, dict]:
    overlay = "temporal_audit.jsonl" if case["split"] == "temporal_candidate" else "historical_audit.jsonl"
    construction._safe(root, root / "data/annotations" / overlay)
    proof = verified_asof_receipt(root, case, audit_id=audit_id, query_policy="original_opening_query")
    report = proof["report"]
    _cutoff_binding(case, report, temporal_policy, strict=True)
    descriptor = {"audit_id": report["audit_id"], "query_policy": "original_opening_query",
        "receipt_bundle": _file(root, _path(root, report["receipt_bundle"]["path"], "data/annotations")),
        "query_time": report["query_time"], "selected_snapshot_revision": report["selected_snapshot_revision"],
        "query_text_sha256": report["query_text_sha256"], "opening_text_version": _opening_versions(proof["bundle"])}
    return proof, descriptor


def _source_audit(root: Path, case: dict, temporal_policy: dict | None, *, audit_id=None) -> dict | None:
    """Replay negative or incomplete audit metadata without claiming eligibility."""
    name = "temporal_audit.jsonl" if case["split"] == "temporal_candidate" else "historical_audit.jsonl"
    path = _path(root, "data/annotations/" + name, "data/annotations")
    rows = read_jsonl(path) if path.exists() else []
    matches = [row for row in rows if row.get("case_id") == case["case_id"]
               and (audit_id is None or row.get("audit_id") == audit_id)]
    if not matches:
        if audit_id is not None:
            raise ValueError("Dataset release source-exclusion audit is missing")
        return None
    report = matches[-1]
    if (digest({key: value for key, value in report.items() if key != "audit_id"}) != report.get("audit_id")
            or report.get("case_sha256") != digest(case)
            or report.get("benchmark_manifest_sha256") != _sha(root / "data/manifests/dataset.json")):
        raise ValueError("Dataset release source-exclusion audit binding/digest failure")
    bundle_path = _path(root, report["receipt_bundle"]["path"], "data/annotations")
    bundle_file = _file(root, bundle_path)
    if bundle_file["sha256"] != report["receipt_bundle"]["sha256"]:
        raise ValueError("Dataset release source-exclusion bundle digest failure")
    bundle = _object(bundle_path)
    arguments = {"selected_snapshot_revision": bundle.get("selected_snapshot_revision"),
        "ancestry_receipt": bundle.get("ancestry_receipt"), "query_policy": bundle.get("query_policy", "source_query")}
    if case["split"] == "temporal_candidate":
        checked = audit_temporal_case(case, bundle["issue_receipts"], bundle.get("revision_proof"),
            model_cutoff=report["configured_model_cutoff"], cutoff_provenance=bundle.get("cutoff_provenance"), **arguments)
    else:
        checked = audit_historical_case(case, bundle["issue_receipts"], bundle.get("revision_proof"),
            issue_references=bundle["issue_references"], **arguments)
    # Original date-only audits predate the generic historical adapter. Keep
    # their original bytes/ID while replaying all their recorded checks. These
    # explicitly additive fields are derived from the current pinned case;
    # omitted legacy fields never authorize strict release inclusion.
    legacy_added = {"post_model_cutoff_required", "missing_issue_references", "upstream_base_revision",
        "selected_snapshot_revision", "uses_historical_ancestor", "snapshot_ancestry_verified", "validation_role"}
    compared = {key: value for key, value in checked.items() if key not in legacy_added or key in report}
    if isinstance(report.get("issues"), list) and len(report["issues"]) == len(checked["issues"]):
        compared["issues"] = [{key: value for key, value in current.items()
                               if key != "repository" or key in recorded}
                              for recorded, current in zip(report["issues"], checked["issues"])]
    if any(report.get(key) != value for key, value in compared.items()):
        raise ValueError("Dataset release source-exclusion audit does not replay")
    _cutoff_binding(case, report, temporal_policy, strict=checked["strict_issue_time_receipt_checks_passed"])
    return {"audit_id": report["audit_id"], "receipt_bundle": bundle_file,
        "query_policy": arguments["query_policy"], "status": checked["status"],
        "strict_issue_time_receipt_checks_passed": checked["strict_issue_time_receipt_checks_passed"]}


def _snapshot_and_graph(root: Path, asset: dict, report: dict, graph_path: Path) -> dict:
    """Restore the expected snapshot before accepting any current index hash.

    Graph spans are checked against those verified source bytes. This validates
    saved graph bindings, not a claim that static edges prove runtime causality.
    No annotation, gold localization or prediction contents are parsed here.
    """
    index_path = _path(root, asset["snapshot_index"], "data/derived/snapshots")
    index = json.loads(_read_gzip_bounded(index_path, 64_000_000))
    for descriptor in index["files"]:
        sha = _identifier(descriptor["sha256"], "Snapshot source digest")
        _path(root, f"data/derived/blobs/{sha[:2]}/{sha}.txt.gz", "data/derived/blobs")
    snapshot = load_snapshot(index_path, root / "data/derived")
    availability = {"commit_sha": report["selected_snapshot_revision"], "verified": True,
        "source_id": report["revision_availability"]["source_url"],
        "available_at": report["revision_availability"]["available_at"],
        "audit_id": report["audit_id"], "receipt_bundle_sha256": report["receipt_bundle"]["sha256"]}
    if (snapshot.snapshot_sha256 != asset["snapshot_sha256"]
            or index_path.name != snapshot.snapshot_sha256 + ".json.gz"
            or snapshot.commit_sha != report["selected_snapshot_revision"]
            or snapshot.protocol != "issue_time" or snapshot.cutoff != report["query_time"]
            or snapshot.availability.get("receipt") != availability):
        raise ValueError("Dataset release restored snapshot differs from its recorded historical asset")
    graph = json.loads(_read_gzip_bounded(graph_path, 64_000_000))
    if graph.get("snapshot_sha256") != snapshot.snapshot_sha256 or graph.get("commit_sha") != snapshot.commit_sha:
        raise ValueError("Dataset release graph is bound to a different snapshot/revision")
    files = {file.path: file for file in snapshot.files}
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("Dataset release graph nodes are invalid")
    identities, file_nodes = set(), set()
    for node in nodes:
        identifier = node.get("id")
        source = files.get(node.get("path"))
        start, end = node.get("start_line"), node.get("end_line")
        kind = node.get("kind")
        if (not isinstance(identifier, str) or identifier in identities or source is None
                or type(start) is not int or type(end) is not int or not 1 <= start <= end
                or kind not in {"file", "class", "function"} or "text" in node):
            raise ValueError("Dataset release graph node identity/span is invalid")
        identities.add(identifier)
        lines = source_lines(source.text)
        if kind == "file":
            if identifier != "file:" + source.path or start != 1 or end != max(1, len(lines)):
                raise ValueError("Dataset release graph file span differs from its verified source")
            text = source.text
            file_nodes.add(source.path)
        else:
            if end > len(lines) or identifier != f"symbol:{source.path}:{node['qualname']}:{start}":
                raise ValueError("Dataset release graph symbol span differs from its verified source")
            text = "\n".join(lines[start - 1:end])
        if node.get("sha256") != hashlib.sha256(text.encode()).hexdigest():
            raise ValueError("Dataset release graph node source digest mismatch")
    if file_nodes != set(files):
        raise ValueError("Dataset release graph omits verified snapshot file nodes")
    edges = graph.get("edges")
    if not isinstance(edges, list) or any(edge.get("source") not in identities
            or (edge.get("target") is not None and edge["target"] not in identities) for edge in edges):
        raise ValueError("Dataset release graph edge endpoint is not in its verified nodes")
    return {"scope": "intrinsic_snapshot_and_blob_digests_plus_graph_source_bindings",
        "source_file_count": len(snapshot.files), "graph_node_count": len(nodes),
        "source_inventory_sha256": digest([{key: value for key, value in file.to_dict().items() if key != "text"}
                                            for file in snapshot.files])}


def _assets(root: Path, case: dict, generation: dict, proof: dict) -> tuple[dict, dict]:
    report = proof["report"]
    key = hashlib.sha256(case["case_id"].encode()).hexdigest()[:24]
    directory = f"data/derived/asof/{key}/{report['audit_id']}"
    path = _path(root, directory + "/build_receipt.json", "data/derived/asof")
    asset = _object(path)
    selected = report["selected_snapshot_revision"]
    query_text = reconstruct_query_text(case, proof["bundle"])
    query_sha = hashlib.sha256(query_text.encode()).hexdigest()
    if query_sha != report["query_text_sha256"]:
        raise ValueError("Opening query differs from its freshly verified historical proof")
    variant = {"schema_version": 1, "parent_case_id": case["case_id"], "parent_case_sha256": digest(case),
        "query_policy": "original_opening_query", "source_query_sha256": hashlib.sha256(case["problem_statement"].encode()).hexdigest(),
        "query_text_sha256": query_sha, "problem_statement": query_text, "created_at": report["query_time"],
        "audit_id": report["audit_id"], "receipt_bundle_sha256": report["receipt_bundle"]["sha256"],
        "split": case["split"], "gold_parent_case_id": case["case_id"],
        "human_validated_query_variant": False, "source_human_quality_scope": "upstream_query_only"}
    query_path = _path(root, "data/annotations/original_queries/" + digest(variant) + ".json", "data/annotations/original_queries")
    if _object(query_path) != variant:
        raise ValueError("Opening query overlay identity/parent/content mismatch")
    query_file = _file(root, query_path)
    query_reference = {key: query_file[key] for key in ("path", "sha256")}
    expected_query = {"verified": True, "case_id": case["case_id"], "text_sha256": query_sha,
        "created_at": report["query_time"], "source_urls": [row["source_url"] for row in report["issues"]],
        "audit_id": report["audit_id"], "receipt_bundle_sha256": report["receipt_bundle"]["sha256"],
        "query_policy": "original_opening_query", "source_query_sha256": variant["source_query_sha256"],
        "query_variant": query_reference}
    expected = {"case_id": case["case_id"], "repository": case["repository"], "revision": selected,
        "upstream_base_revision": case["revision"], "audit_id": report["audit_id"],
        "manifest_sha256": report["benchmark_manifest_sha256"], "protocol": "issue_time",
        "strict_issue_time_verified": True, "query_receipt": expected_query,
        "mode": "dataset_construction", "evaluation_performed": False}
    if (any(asset.get(key) != value for key, value in expected.items())
            or digest(asset) != generation.get("asset_receipt_sha256")
            or generation.get("selected_snapshot_revision") != selected
            or generation.get("upstream_base_revision") != case["revision"]
            or generation.get("snapshot_sha256") != asset.get("snapshot_sha256")):
        raise ValueError("Generation historical asset/query binding mismatch")
    _identifier(asset["snapshot_sha256"], "Snapshot digest")
    if set(asset.get("files", {})) != {"localization.json", "graph.json.gz"}:
        raise ValueError("Historical asset artifact inventory mismatch")
    files = []
    for name, expected_sha in sorted(asset["files"].items()):
        reference = _file(root, _path(root, directory + "/" + name, "data/derived/asof"))
        if reference["sha256"] != expected_sha:
            raise ValueError("Historical asset file integrity failure")
        files.append(reference)
    integrity = _snapshot_and_graph(root, asset, report, _path(root, directory + "/graph.json.gz", "data/derived/asof"))
    index = _file(root, _path(root, asset["snapshot_index"], "data/derived"))
    descriptor = {"build_receipt": _file(root, path), "receipt_sha256": digest(asset),
        "snapshot_sha256": asset["snapshot_sha256"], "snapshot_index": index, "snapshot_graph_validation": integrity,
        "selected_snapshot_revision": selected, "upstream_base_revision": case["revision"], "files": files,
        "query_variant": {"file": query_file, **{key: variant[key] for key in (
            "parent_case_id", "parent_case_sha256", "source_query_sha256", "query_text_sha256", "created_at", "split",
            "gold_parent_case_id", "human_validated_query_variant", "source_human_quality_scope")}}}
    runtime_case = dict(case, revision=selected, issue_created_at=report["query_time"], problem_statement=query_text)
    # PR date and source-task identity remain release lineage, not query evidence.
    runtime_case.pop("pull_created_at", None)
    references = expected_query["source_urls"]
    if len(references) == 1:
        runtime_case["issue_url"] = references[0].replace("https://api.github.com/repos/", "https://github.com/")
    return descriptor, runtime_case


def _entry(root: Path, recipe: dict, case: dict, target: Path, stages: dict, proof: dict, proof_descriptor: dict) -> tuple[dict, dict, dict]:
    if stages["generation"].get("audit_id") != proof_descriptor["audit_id"]:
        raise ValueError("Dataset release generation audit identity changed")
    assets, inference = _assets(root, case, stages["generation"], proof)
    records = {}
    labels = {}
    for name, receipt in stages.items():
        records[name] = {"file": _file(root, target / (name + ".receipt.json")),
            "receipt_sha256": receipt["receipt_sha256"], "identity": construction._stage_identity(recipe, case, name),
            "dependency_sha256": receipt["dependency_sha256"],
            "artifacts": [_file(root, target / filename) for filename in sorted(receipt["files"])]}
        for filename in receipt["files"]:
            labels[filename] = str(target / filename)
    return ({"case_id": case["case_id"], "split": case["split"], "original_case_sha256": digest(case),
        "original_split_sha256": digest({"case_id": case["case_id"], "split": case["split"]}),
        "proof": proof_descriptor, "assets": assets, "stage_receipts": records}, inference, labels)


def _context(root: Path, identifier: str) -> tuple[dict, dict, dict, dict]:
    manifest = verify_manifest(root)
    manifest_path = root / "data/manifests/dataset.json"
    cases = {row["case_id"]: row for row in read_jsonl(_path(root, manifest["files"]["cases"]["path"], "data/benchmark"))}
    if len(cases) != manifest["counts"]["total"]:
        raise ValueError("Source case count/identity mismatch")
    record, registration = _registration(root, identifier, _sha(manifest_path), cases)
    source = {"manifest": _file(root, manifest_path), "files": manifest["files"], "counts": manifest["counts"],
        "split_binding_sha256": digest([{ "case_id": case["case_id"], "split": case["split"]}
                                      for case in sorted(cases.values(), key=lambda row: row["case_id"])])}
    return record, registration, cases, source


def register_dataset_release(root: Path | str, recipe_sha256: str, splits=None) -> dict:
    """Snapshot complete, eligible constructions without exposing label contents.

    Incomplete cases remain explicit exclusions. A release may contain fewer
    than the imported cohort; its counts never imply all source tasks complete.
    """
    root = Path(root).resolve()
    identifier = _identifier(recipe_sha256, "recipe_sha256")
    record, registration, cases, source = _context(root, identifier)
    recipe = record["recipe"]
    selected_splits = recipe["splits"] if splits is None else splits
    if (not isinstance(selected_splits, (list, tuple)) or not selected_splits
            or len(selected_splits) != len(set(selected_splits)) or any(split not in recipe["splits"] for split in selected_splits)):
        raise ValueError("Release splits must be unique registered splits")
    included, exclusions = [], []
    for descriptor in recipe["cases"]:
        case = cases[descriptor["case_id"]]
        if case["split"] not in selected_splits:
            continue
        target, stages, missing = _stages(root, recipe, case)
        audit_id = stages["generation"].get("audit_id") if stages["generation"] else None
        try:
            proof, proof_descriptor = _proof(root, case, audit_id, registration["temporal_policy"])
        except ValueError as error:
            if stages["generation"] is not None or not str(error).startswith("No verified historical receipt"):
                raise
            source_audit = _source_audit(root, case, registration["temporal_policy"])
            status = source_audit["status"] if source_audit else None
            known_excluded = status == "excluded_issue_not_after_cutoff"
            exclusions.append({**descriptor, "source_eligibility": "known_ineligible" if known_excluded else "unverified",
                "source_reason": status if status and status != "strict_receipt_checks_passed" else "strict_opening_query_receipt_missing",
                "annotation_technically_complete": not missing, "missing_stages": missing,
                "proof": None, "source_audit": source_audit})
            continue
        opening_verified = proof_descriptor["opening_text_version"]["original_version_verified"]
        if not opening_verified or missing:
            diagnostic = None
            if not missing:
                diagnostic, _, _ = _entry(root, recipe, case, target, stages, proof, proof_descriptor)
            exclusions.append({**descriptor, "source_eligibility": "verified",
                "source_reason": None if opening_verified else "opening_text_version_unverified",
                "annotation_technically_complete": not missing, "missing_stages": missing,
                "proof": proof_descriptor, "source_audit": None, "diagnostic_construction": diagnostic})
            if not opening_verified:
                exclusions[-1]["source_eligibility"] = "unverified"
            continue
        entry, _, _ = _entry(root, recipe, case, target, stages, proof, proof_descriptor)
        included.append(entry)
    release = {**_declarations(), "source": source, "recipe": registration, "splits": sorted(selected_splits),
        "cases": included, "exclusions": exclusions,
        "counts": {"registered_selected": len(included) + len(exclusions), "included": len(included),
            "excluded": len(exclusions), "included_by_split": dict(sorted(Counter(row["split"] for row in included).items())),
            "exclusions_by_source_eligibility": dict(sorted(Counter(row["source_eligibility"] for row in exclusions).items()))}}
    release_sha = digest(release)
    path = _path(root, f"data/benchmark/releases/{release_sha}.json", "data/benchmark/releases")
    construction._write_once(path, release)
    # Recheck registration and selected receipts after serialization, without
    # adopting completion that a running worker adds to an excluded case.
    verify_dataset_release(root, release_sha)
    return {"release_sha256": release_sha, "path": str(path), "release": release}


def verify_dataset_release(root: Path | str, release_sha256: str) -> dict:
    """Return validated inference queries separately from restricted label paths.

    This is not an evaluation capability. Callers evaluating heldout cases still
    require ``open_test_split`` and an explicit evaluation authorization.
    """
    root = Path(root).resolve()
    identifier = _identifier(release_sha256, "release_sha256")
    path = _path(root, f"data/benchmark/releases/{identifier}.json", "data/benchmark/releases")
    release = _object(path)
    if digest(release) != identifier or release.get("schema_version") != _SCHEMA:
        raise ValueError("Dataset release manifest integrity/identity failure")
    if any(release.get(key) != value for key, value in _declarations().items()):
        raise ValueError("Dataset release policy/label-scope declaration mismatch")
    recipe_id = _identifier(release["recipe"]["recipe_sha256"], "recipe_sha256")
    record, registration, cases, source = _context(root, recipe_id)
    if release["source"] != source:
        raise ValueError("Dataset release pinned source/manifest binding changed")
    # Preserve all assessment hashes bound at registration. A subsequently
    # negative assessment also vetoes primary eligibility via _context().
    expected_registration = dict(registration, assessments=release["recipe"]["assessments"])
    if release["recipe"] != expected_registration:
        raise ValueError("Dataset release recipe registration/archive binding changed")
    for descriptor in release["recipe"]["assessments"]:
        _check_file(root, descriptor, f"data/annotations/recipe_assessments/{recipe_id}")
    recipe = record["recipe"]
    splits = release["splits"]
    if not splits or splits != sorted(set(splits)) or any(split not in recipe["splits"] for split in splits):
        raise ValueError("Dataset release split selection changed")
    expected_ids = {row["case_id"] for row in recipe["cases"] if row["split"] in splits}
    seen, inference_cases, label_paths = set(), [], {}
    for entry in release["cases"]:
        case = cases[entry["case_id"]]
        if case["case_id"] in seen or case["case_id"] not in expected_ids:
            raise ValueError("Dataset release case identity is duplicated or unregistered")
        seen.add(case["case_id"])
        target, stages, missing = _stages(root, recipe, case)
        if missing:
            raise ValueError("Dataset release construction stage is now missing")
        proof, proof_descriptor = _proof(root, case, entry["proof"]["audit_id"], registration["temporal_policy"])
        if not proof_descriptor["opening_text_version"]["original_version_verified"]:
            raise ValueError("Dataset release opening text version is not established")
        if stages["generation"].get("audit_id") != proof_descriptor["audit_id"]:
            raise ValueError("Dataset release generation audit identity changed")
        actual, inference, labels = _entry(root, recipe, case, target, stages, proof, proof_descriptor)
        if actual != entry:
            raise ValueError("Dataset release stage/query/asset identity changed")
        inference_cases.append(inference)
        label_paths[case["case_id"]] = labels
    for exclusion in release["exclusions"]:
        case = cases[exclusion["case_id"]]
        if (case["case_id"] in seen or case["case_id"] not in expected_ids
                or exclusion["case_sha256"] != digest(case) or exclusion["split"] != case["split"]):
            raise ValueError("Dataset release exclusion case/split binding changed")
        seen.add(case["case_id"])
        if exclusion["proof"] is not None:
            proof, actual = _proof(root, case, exclusion["proof"]["audit_id"], registration["temporal_policy"])
            if actual != exclusion["proof"]:
                raise ValueError("Dataset release excluded-case proof changed")
            if exclusion.get("diagnostic_construction") is not None:
                target, stages, missing = _stages(root, recipe, case)
                if missing:
                    raise ValueError("Dataset release diagnostic construction is missing stages")
                diagnostic, _, _ = _entry(root, recipe, case, target, stages, proof, actual)
                if diagnostic != exclusion["diagnostic_construction"]:
                    raise ValueError("Dataset release diagnostic construction identity changed")
        if exclusion.get("source_audit") is not None:
            actual = _source_audit(root, case, registration["temporal_policy"], audit_id=exclusion["source_audit"]["audit_id"])
            if actual != exclusion["source_audit"]:
                raise ValueError("Dataset release source-exclusion audit changed")
    if seen != expected_ids:
        raise ValueError("Dataset release omitted registered cases without exclusions")
    expected_counts = {"registered_selected": len(expected_ids), "included": len(inference_cases),
        "excluded": len(release["exclusions"]),
        "included_by_split": dict(sorted(Counter(row["split"] for row in release["cases"]).items())),
        "exclusions_by_source_eligibility": dict(sorted(Counter(row["source_eligibility"] for row in release["exclusions"]).items()))}
    if release["counts"] != expected_counts or release.get("evaluation_performed") is not False:
        raise ValueError("Dataset release count/evaluation declaration mismatch")
    return {"release_sha256": identifier, "manifest": release, "inference_cases": inference_cases,
        "label_paths": label_paths, "evaluation_capability_granted": False, "evaluation_performed": False}
