"""Read-only release inventory, with construction status kept apart from evaluation.

The pinned import manifest describes the bootstrap, not subsequent completion.
Receipt file inventories are checked afresh. Historical audit outcomes remain
*recorded* outcomes here: their original provenance validators are not rerun.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from issueintel.annotations.models import Annotation
from issueintel.repository.cache import load_snapshot
from issueintel.repository.snapshot import validate_repo_path
from issueintel.runtime.io import digest, read_jsonl
from .store import verify_manifest


_SHA256 = re.compile(r"[0-9a-f]{64}")
_JSON_LIMIT = 16_000_000
_INDEX_LIMIT = 64_000_000
_AUDIT_STATUSES = {
    "strict_receipt_checks_passed", "excluded_issue_not_after_cutoff",
    "post_cutoff_dates_confirmed_snapshot_unverified",
    "issue_dates_confirmed_snapshot_unverified", "missing_or_invalid_issue_metadata",
    "exact_issue_match", "exact_multi_issue_match", "reference_bound_exceeded",
    "ambiguous_exact_matches", "no_closing_references_in_pr_body",
    "no_exact_current_issue_text_match",
    "unsupported_django_trac_source", "no_closing_references_under_policy",
    "commit_reference_coverage_incomplete",
}


class _Invalid(ValueError):
    """Only fixed reason codes reach the report; never source/provider text."""


def _hash_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _inside(base: Path, relative: str) -> Path:
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or ":" in relative or PurePosixPath(relative).is_absolute()
            or any(part in {".", ".."} for part in relative.split("/"))):
        raise _Invalid("unsafe_inventory_path")
    target = (base / relative).resolve()
    if not target.is_relative_to(base):
        raise _Invalid("inventory_path_outside_allowed_directory")
    return target


def _object(path: Path, *, index=False) -> dict:
    limit = _INDEX_LIMIT if index else _JSON_LIMIT
    if path.stat().st_size > limit:
        raise _Invalid("metadata_size_limit_exceeded")
    if index and path.suffix == ".gz":
        with gzip.open(path, "rb") as handle:
            content = handle.read(limit + 1)
    else:
        content = path.read_bytes()
    if len(content) > limit:
        raise _Invalid("decoded_metadata_size_limit_exceeded")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise _Invalid("metadata_is_not_object")
    return value


def _inventory_files(target: Path, files: object, required: set[str]) -> None:
    if not isinstance(files, dict) or not required.issubset(files):
        raise _Invalid("receipt_inventory_incomplete")
    for name, expected in files.items():
        if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
            raise _Invalid("invalid_artifact_digest")
        if _hash_file(_inside(target, name)) != expected:
            raise _Invalid("artifact_digest_mismatch")


def _snapshot_index(root: Path, receipt: dict, case: dict, *, deep: bool) -> bool:
    expected = receipt.get("snapshot_sha256")
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
        raise _Invalid("invalid_snapshot_digest")
    index = _inside(root, receipt.get("snapshot_index"))
    index_directory = root / "data/derived/snapshots"
    if index.parent != index_directory or index.name not in {expected + ".json", expected + ".json.gz"}:
        raise _Invalid("snapshot_index_path_binding_mismatch")
    value = _object(index, index=True)
    if value.get("snapshot_sha256") != expected or value.get("commit_sha") != case["revision"]:
        raise _Invalid("snapshot_index_identity_mismatch")
    if (value.get("protocol") != "benchmark_base" or value.get("cutoff") is not None
            or value.get("availability", {}).get("historical_certified") is not False):
        raise _Invalid("base_snapshot_protocol_mismatch")
    files = value.get("files")
    if not isinstance(files, list):
        raise _Invalid("snapshot_file_descriptors_missing")
    paths = []
    for descriptor in files:
        if not isinstance(descriptor, dict) or "text" in descriptor:
            raise _Invalid("snapshot_index_descriptor_invalid")
        validate_repo_path(descriptor.get("path", ""))
        paths.append(descriptor["path"])
        sha = descriptor.get("sha256")
        if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
            raise _Invalid("snapshot_source_digest_invalid")
    if len(paths) != len(set(paths)):
        raise _Invalid("snapshot_duplicate_source_paths")
    index_sha = receipt.get("snapshot_index_sha256")
    if index_sha is not None and (not isinstance(index_sha, str) or not _SHA256.fullmatch(index_sha)
                                  or _hash_file(index) != index_sha):
        raise _Invalid("snapshot_index_file_digest_mismatch")
    if deep:
        snapshot = load_snapshot(index, root / "data/derived")
        if snapshot.snapshot_sha256 != expected:
            raise _Invalid("restored_snapshot_digest_mismatch")
    return index_sha is not None


def _failure(receipt_path: Path, root: Path, case_id: str | None, error: Exception) -> dict:
    # JSON/IO/schema error messages can contain private text or external paths.
    reason = str(error) if isinstance(error, _Invalid) else {
        FileNotFoundError: "required_file_missing", json.JSONDecodeError: "invalid_json",
        UnicodeDecodeError: "invalid_text_encoding",
    }.get(type(error), "metadata_or_schema_validation_failed")
    return {"receipt": receipt_path.relative_to(root).as_posix(), "case_id": case_id,
            "reason": reason}


def _receipts(root: Path, cases: dict, manifest_sha: str, *, annotation: bool, deep: bool) -> dict:
    directory = root / ("data/annotations/automatic" if annotation else "data/derived/cases")
    filename = "receipt.json" if annotation else "build_receipt.json"
    summary = {"receipts_found": 0, "verified_receipts": 0, "invalid_receipts": 0,
               "verified_by_split": {}, "errors": [], "missing_case_ids": []}
    if annotation:
        summary.update({"generation_completed": 0, "judgment_completed": 0,
                        "judgment_pending": 0, "human_validation_claimed": False})
    else:
        summary.update({"protocol": "benchmark_base", "strict_issue_time_assets": 0,
                        "snapshot_index_checks": {
                            "metadata_bindings_checked": 0, "index_file_digest_checked": 0,
                            "full_restored_snapshot_checked": 0,
                            "scope": "restored_snapshot_and_blob_hashes" if deep else "index_metadata_bindings_only",
                        }})
    seen, verified_splits = set(), Counter()
    if directory.exists() and not directory.resolve().is_relative_to(root / "data"):
        raise _Invalid("receipt_directory_outside_private_data")
    for path in sorted(directory.glob("*/" + filename)):
        summary["receipts_found"] += 1
        case_id = None
        try:
            path = _inside(root, path.relative_to(root).as_posix())
            receipt_digest = _hash_file(path)
            receipt = _object(path)
            candidate = receipt.get("case_id")
            case = cases.get(candidate) if isinstance(candidate, str) else None
            if case is None:
                raise _Invalid("receipt_case_not_in_pinned_manifest")
            case_id = case["case_id"]
            if path.parent.name != hashlib.sha256(case_id.encode()).hexdigest()[:24]:
                raise _Invalid("receipt_directory_case_binding_mismatch")
            if receipt.get("manifest_sha256") != manifest_sha:
                raise _Invalid("receipt_manifest_binding_mismatch")
            if annotation:
                # Do not read held-out model outputs while inventorying dev annotation.
                if case["split"] != "main_dev" or receipt.get("split") != "main_dev":
                    raise _Invalid("automatic_annotation_not_main_dev")
                if (receipt.get("kind") != "automatic_dataset_construction"
                        or receipt.get("temporal_protocol") != "benchmark_base"
                        or receipt.get("strict_issue_time_verified") is not False
                        or receipt.get("human_reviewed") is not False):
                    raise _Invalid("automatic_annotation_policy_mismatch")
                if type(receipt.get("judgment_completed")) is not bool:
                    raise _Invalid("judgment_stage_flag_invalid")
                _inventory_files(path.parent, receipt.get("files"), {
                    "annotation.json", "localization.json", "variants.json", "generation_inputs.json"})
                annotation_value = Annotation.from_dict(_object(path.parent / "annotation.json"))
                inputs = _object(path.parent / "generation_inputs.json")
                if annotation_value.case_id != case_id or inputs.get("case") != case:
                    raise _Invalid("annotation_input_case_binding_mismatch")
                annotation_value.validate_evidence(inputs.get("evidence", []))
                if (annotation_value.provenance.kind not in {"llm", "automatic"}
                        or annotation_value.provenance.provider != receipt.get("provider")
                        or annotation_value.provenance.model != receipt.get("model")):
                    raise _Invalid("annotation_provider_binding_mismatch")
                if receipt["judgment_completed"]:
                    if (not annotation_value.judgment
                            or any(item.correctness == "unverified" for item in annotation_value.hypotheses)):
                        raise _Invalid("judgment_stage_artifact_missing")
                    if (annotation_value.judgment.get("independent_human") is not False
                            or annotation_value.judgment.get("kind") not in {"llm", "automatic"}):
                        raise _Invalid("automatic_judgment_policy_mismatch")
            else:
                if receipt.get("repository") != case["repository"] or receipt.get("revision") != case["revision"]:
                    raise _Invalid("asset_case_repository_revision_mismatch")
                if receipt.get("protocol") != "benchmark_base" or receipt.get("strict_issue_time_verified") is not False:
                    raise _Invalid("base_asset_policy_mismatch")
                _inventory_files(path.parent, receipt.get("files"), {"graph.json.gz", "localization.json"})
                index_digest_checked = _snapshot_index(root, receipt, case, deep=deep)
            if _hash_file(path) != receipt_digest:
                raise _Invalid("receipt_changed_during_inventory")
            summary["verified_receipts"] += 1
            seen.add(case_id)
            verified_splits[case["split"]] += 1
            if annotation:
                summary["generation_completed"] += 1
                summary["judgment_completed" if receipt["judgment_completed"] else "judgment_pending"] += 1
            else:
                checks = summary["snapshot_index_checks"]
                checks["metadata_bindings_checked"] += 1
                checks["index_file_digest_checked"] += index_digest_checked
                checks["full_restored_snapshot_checked"] += deep
        except (ValueError, TypeError, AttributeError, KeyError, OSError) as error:
            summary["invalid_receipts"] += 1
            summary["errors"].append(_failure(path, root, case_id, error))
    expected = {cid for cid, case in cases.items() if not annotation or case["split"] == "main_dev"}
    summary["missing_case_ids"] = sorted(expected - seen)
    summary["verified_by_split"] = dict(sorted(verified_splits.items()))
    return summary


def _overlay(root: Path, cases: dict, manifest_sha: str, name: str, *, identity="audit_id") -> dict:
    path = root / "data/annotations" / (name + ".jsonl")
    summary = {"events_recorded": 0, "latest_cases_recorded": 0,
               "latest_receipt_bindings_verified": 0, "recorded_status_counts": {},
               "recorded_strict_receipt_checks_passed": 0, "errors": [],
               "provenance_validators_rerun": False, "usable_temporal_cohort_claimed": False}
    if not path.exists():
        return summary
    path = _inside(root / "data/annotations", path.name)
    latest = {}
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            summary["events_recorded"] += 1
            try:
                if len(line.encode("utf-8")) > _JSON_LIMIT:
                    raise _Invalid("overlay_event_size_limit_exceeded")
                event = json.loads(line)
                if not isinstance(event, dict) or event.get("case_id") not in cases:
                    raise _Invalid("overlay_case_not_in_pinned_manifest")
                latest[event["case_id"]] = (number, event)
            except (ValueError, TypeError) as error:
                entry = _failure(path, root, None, error)
                entry["line"] = number
                summary["errors"].append(entry)
    summary["latest_cases_recorded"] = len(latest)
    statuses = Counter()
    for case_id, (number, event) in latest.items():
        try:
            case = cases[case_id]
            permitted_splits = {"temporal_candidate"} if name == "temporal_audit" else {"main_dev", "main_test"}
            if case["split"] not in permitted_splits:
                raise _Invalid("overlay_split_binding_mismatch")
            if event.get("benchmark_manifest_sha256") != manifest_sha or event.get("case_sha256") != digest(case):
                raise _Invalid("overlay_case_manifest_binding_mismatch")
            if event.get(identity) != digest({key: value for key, value in event.items() if key != identity}):
                raise _Invalid("overlay_event_digest_mismatch")
            bundle = event.get("receipt_bundle")
            if not isinstance(bundle, dict):
                raise _Invalid("overlay_receipt_bundle_missing")
            bundle_path = _inside(root, bundle.get("path"))
            if not bundle_path.is_relative_to(root / "data/annotations"):
                raise _Invalid("overlay_bundle_outside_annotations")
            if _hash_file(bundle_path) != bundle.get("sha256"):
                raise _Invalid("overlay_bundle_digest_mismatch")
            summary["latest_receipt_bindings_verified"] += 1
            status = event.get("status")
            statuses[status if isinstance(status, str) and status in _AUDIT_STATUSES else "unrecognized_recorded_status"] += 1
            summary["recorded_strict_receipt_checks_passed"] += event.get("strict_issue_time_receipt_checks_passed") is True
        except (ValueError, TypeError, AttributeError, KeyError, OSError) as error:
            entry = _failure(path, root, case_id, error)
            entry["line"] = number
            summary["errors"].append(entry)
    summary["recorded_status_counts"] = dict(sorted(statuses.items()))
    return summary


def _registered_annotations(root: Path, cases: dict, manifest_sha: str) -> dict:
    """Count receipted stages without inspecting heldout hypothesis contents."""
    from .construction import _stage, load_annotation_recipe
    result = {"recipes_found": 0, "verified_recipe_records": 0, "recipes": [], "errors": [],
        "verification_scope": "registration, stage receipt identities and artifact hashes; no annotation quality claim",
        "human_validation_claimed": False, "evaluation_performed": False}
    for path in sorted((root / "data/annotations/recipes").glob("*.json")):
        result["recipes_found"] += 1
        try:
            record = _object(path)
            recipe = record["recipe"]
            identifier = record["recipe_sha256"]
            if (identifier != path.stem or digest(recipe) != identifier
                    or digest({key: value for key, value in record.items() if key != "registration_sha256"}) != record["registration_sha256"]
                    or recipe["dataset_manifest_sha256"] != manifest_sha
                    or recipe["mode"] != "dataset_construction" or recipe["evaluation_performed"] is not False):
                raise _Invalid("construction_registration_binding_mismatch")
            entry = {"recipe_sha256": identifier, "protocol": recipe["protocol"],
                "query_policy": recipe.get("query_policy", "source_query"), "registered_cases": len(recipe["cases"]),
                "stage_counts": {}, "completed_by_split": {}, "completed_cases": 0, "cases_with_generation": 0,
                "invalid_stage_receipts": 0, "stage_errors": [], "current_code_and_config_resumable": False}
            assessments = []
            for assessment_path in (root / "data/annotations/recipe_assessments" / identifier).glob("*.json"):
                assessment = _object(assessment_path)
                if (digest(assessment) != assessment_path.stem or assessment.get("recipe_sha256") != identifier
                        or assessment.get("registration_sha256") != record["registration_sha256"]):
                    raise _Invalid("recipe_assessment_binding_mismatch")
                assessments.append({"primary_eligible": assessment.get("primary_eligible"),
                                    "reason_code": assessment.get("reason_code")})
            entry["primary_eligible"] = recipe["protocol"] == "issue_time" and not any(
                assessment["primary_eligible"] is False for assessment in assessments)
            entry["assessments"] = assessments
            try:
                load_annotation_recipe(root, identifier)
                entry["current_code_and_config_resumable"] = True
            except (ValueError, RuntimeError, OSError, KeyError, TypeError):
                # Historical registrations remain historical evidence even when
                # current source/config legitimately differs from that run.
                entry["resume_limitation"] = "registration requires its pinned code/config and intact source archive"
            stages = ["generation", "variants"]
            if recipe["options"]["independent_critic"]:
                stages.append("critique")
            if recipe["options"]["judge"]:
                stages.append("judge")
            counters, completed = Counter(), Counter()
            seen = set()
            for descriptor in recipe["cases"]:
                cid = descriptor["case_id"]
                case = cases.get(cid)
                if (case is None or cid in seen or case["split"] != descriptor["split"]
                        or digest(case) != descriptor["case_sha256"]):
                    raise _Invalid("construction_recipe_case_binding_mismatch")
                seen.add(cid)
                target = _inside(root, "data/annotations/dataset/" + identifier + "/" + hashlib.sha256(cid.encode()).hexdigest()[:24])
                try:
                    generation = _stage(target, recipe, case, "generation")
                    dependency = generation["receipt_sha256"] if generation else None
                    found = {"generation": generation}
                    for name in ("critique", "judge", "variants"):
                        found[name] = _stage(target, recipe, case, name, dependency=dependency)
                        if found[name] and not generation:
                            raise _Invalid("construction_later_stage_without_generation")
                    if generation:
                        if (generation.get("protocol") != recipe["protocol"]
                                or generation.get("strict_issue_time_verified") != (recipe["protocol"] == "issue_time")
                                or generation.get("gold_in_inference") is not False):
                            raise _Invalid("construction_generation_policy_mismatch")
                        entry["cases_with_generation"] += 1
                    for name, receipt in found.items():
                        counters[name] += bool(receipt)
                    if all(found[name] for name in stages):
                        entry["completed_cases"] += 1
                        completed[case["split"]] += 1
                except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
                    entry["invalid_stage_receipts"] += 1
                    entry["stage_errors"].append(_failure(target, root, cid, error))
            entry["stage_counts"] = dict(counters)
            entry["completed_by_split"] = dict(completed)
            entry["primary_completed_cases"] = entry["completed_cases"] if entry["primary_eligible"] else 0
            result["verified_recipe_records"] += 1
            result["recipes"].append(entry)
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
            result["errors"].append(_failure(path, root, None, error))
    return result


def verified_inventory(root: Path | str, *, deep_snapshot_validation: bool = False) -> dict:
    """Verify the import once, then inspect construction receipts without model calls.

    Default snapshot checks bind index metadata to receipts. Opt in to the more
    expensive restoration to hash every source blob and full snapshot. Neither
    mode revalidates public historical provenance or performs holdout evaluation.
    """
    if type(deep_snapshot_validation) is not bool:
        raise ValueError("deep_snapshot_validation must be a boolean")
    root = Path(root).resolve()
    manifest = verify_manifest(root)
    manifest_sha = _hash_file(root / "data/manifests/dataset.json")
    cases = {row["case_id"]: row for row in read_jsonl(root / manifest["files"]["cases"]["path"])}
    result = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest": {"integrity": "verified", "sha256": manifest_sha,
                     "counts": manifest["counts"], "bootstrap_status": manifest["status"],
                     "bootstrap_annotation_status": manifest["annotation_status"],
                     "bootstrap_temporal_validation": manifest["temporal_validation"]},
        "base_assets": _receipts(root, cases, manifest_sha, annotation=False, deep=deep_snapshot_validation),
        "automatic_dev_annotations": _receipts(root, cases, manifest_sha, annotation=True, deep=False),
        "registered_annotations": _registered_annotations(root, cases, manifest_sha),
        "temporal_audits": _overlay(root, cases, manifest_sha, "temporal_audit"),
        "historical_audits": _overlay(root, cases, manifest_sha, "historical_audit"),
        "main_issue_mapping": _overlay(root, cases, manifest_sha, "main_metadata", identity="mapping_id"),
        "evaluation_performed_by_inventory": False,
        "evaluation_authorizations_recorded": len(list((root / "data/runtime/evaluation_authorizations").glob("*.json"))),
        "limitations": ["Counts are a read-only observation; concurrent stage writers can expose an integrity mismatch.",
                        "Recorded historical audit success is not a freshly revalidated strict cohort.",
                        "Automatic annotations and source human labels do not establish independent human validation."],
    }
    if _hash_file(root / "data/manifests/dataset.json") != manifest_sha:
        raise _Invalid("manifest_changed_during_inventory")
    return result
