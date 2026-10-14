"""Registered artifact generation on a frozen original Main Dev cohort only.

Model policy is justified from immutable paired pilot receipts, not a boolean
or a convenient progress file. Gold localization remains outside this module.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from io import BytesIO
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import time
import zipfile

from issueintel.annotations.generator import generate_annotation
from issueintel.annotations.models import Annotation, AnnotationError
from issueintel.annotations.variants import create_abstention_variants
from issueintel.investigation.pipeline import finish_pack, export_pack
from issueintel.investigation.providers import GroqProvider, ProviderError
from issueintel.investigation.verifier import verify_hypotheses, validate_critique
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.budget import BudgetDenied
from issueintel.runtime.routing import route, ModelRoute, SMALL, LARGE
from issueintel.runtime.io import digest, write_json, write_progress
from . import dev_pilot
from .dev_artifacts import cohort_context, artifact_directory, public_context
from .strict_dev import _checked, _json, _lock
from .sources import inside_root, sha256_file

SCHEMA = "main_dev_artifact_recipe_v1"
CONFIGS = (*dev_pilot.CONFIGS, "dev_artifact_policy.json")


def _configs(root):
    return {name: sha256_file(root / "configs" / name) for name in CONFIGS}


def _reference(root, path):
    path = inside_root(root, path)
    return {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}


def replay_policy(root, cohort, policy):
    if (policy.get("schema") != "main_dev_stage_policy_v1" or policy.get("cohort_sha256") != digest(cohort)
            or policy.get("scope") != "accepted_original_Main_Dev_only" or policy.get("evaluation_authorized") is not False
            or policy.get("human_validated") is not False):
        raise ValueError("Stage policy has no matching accepted Main Dev scope")
    pilot_id = policy["pilot_sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", pilot_id):
        raise ValueError("Full pilot SHA256 required")
    path = root / "data/development/pilots/registrations" / (pilot_id + ".json")
    pilot = _json(path)
    if (digest(pilot) != pilot_id or pilot["schema"] != dev_pilot.SCHEMA
            or pilot["cohort_sha256"] != digest(cohort) or pilot.get("evaluation_authorized") is not False
            or not 30 <= len(pilot["cases"]) <= 50 or pilot["stages"] != ["generation", "critique"]
            or pilot["models"] != [SMALL, LARGE]):
        raise ValueError("Invalid registered paired DEV pilot")
    if any(item["case_id"] not in cohort["ordered_case_ids"] for item in pilot["cases"]):
        raise ValueError("Pilot contains a nonaccepted or non-DEV case")
    archive_path = _checked(root, pilot["source_archive"])
    with zipfile.ZipFile(archive_path) as archive:
        for relative, expected in pilot["source_hashes"].items():
            if hashlib.sha256(archive.read(relative)).hexdigest() != expected:
                raise ValueError("Pilot archived source changed")
        for name, expected in pilot["configuration"].items():
            if hashlib.sha256(archive.read("configs/" + name)).hexdigest() != expected:
                raise ValueError("Pilot archived configuration changed")
    results, refs = [], []
    for item in pilot["cases"]:
        inputs = _json(_checked(root, item["files"]["generation_inputs.json"]))
        original = _json(_checked(root, item["files"]["annotation.generated.json"]))
        if (digest(inputs) != item["input_sha256"] or inputs["case"]["case_id"] != item["case_id"]
                or inputs["case"]["split"] != "main_dev"):
            raise ValueError("Pilot input no longer binds the accepted DEV case")
        Annotation.from_dict(original).validate_evidence(inputs["evidence"])
        for stage in pilot["stages"]:
            for model in pilot["models"]:
                key = digest([pilot_id, item["case_id"], model, stage])
                path = root / "data/development/pilots/outputs" / pilot_id / key / "receipt.json"
                saved = _json(path)  # Missing pilot stages deny policy registration.
                identity = {"pilot_sha256": pilot_id, "case_id": item["case_id"], "stage": stage,
                            "model": model, "input_sha256": item["input_sha256"], "namespace": pilot["namespace"]}
                if any(saved.get(k) != v for k, v in identity.items()) or digest({k: v for k, v in saved.items() if k != "receipt_sha256"}) != saved["receipt_sha256"]:
                    raise ValueError("Pilot stage receipt identity changed")
                if saved.get("diagnostic_only") is not True or saved.get("human_validated") is not False or saved["status"] not in {"valid", "failed"}:
                    raise ValueError("Pilot provenance changed")
                if saved.get("output"):
                    output = _json(_checked(root, saved["output"]))
                    if stage == "generation":
                        parsed = Annotation.from_dict(output).validate_evidence(inputs["evidence"])
                        if parsed.case_id != item["case_id"] or parsed.source_quality or parsed.judgment:
                            raise ValueError("Pilot generation contains restricted or other-case annotations")
                    else:
                        structural = verify_hypotheses(original, inputs["evidence"])
                        if output["reports"] != structural["reports"] or output.get("independent_human") is not False:
                            raise ValueError("Pilot critique differs from the registered hypotheses")
                        validate_critique({"reviews": output["reviews"]}, original, inputs["evidence"])
                elif saved["status"] == "valid":
                    raise ValueError("Valid pilot stage is missing its output")
                results.append(saved)
                refs.append(_reference(root, path))
    report = dev_pilot.summarize(root, pilot_id, pilot, results)
    routes = {}
    for stage in ("generation", "critique"):
        selected = policy["stage_models"][stage]
        measured = report["stages"][stage]
        small, large = measured["models"][SMALL], measured["models"][LARGE]
        if not measured["complete"] or large["valid"] / large["cases"] < pilot["acceptance"]["validity_min"]:
            raise BudgetDenied("No valid pilot-backed model for this stage")
        if (selected["model"] != SMALL or selected.get("strategy") != "small_then_large_on_structured_failure"
                or selected.get("fallback_model") != LARGE):
            raise BudgetDenied("Normal investigation must start with 20B; large requires a concrete case failure")
        threshold = pilot["acceptance"]["generation_abstention_agreement_min" if stage == "generation" else "critic_assessment_agreement_min"]
        if measured["agreement"] is None or measured["agreement"] < threshold:
            raise BudgetDenied("Paired agreement gate failed for the fallback strategy")
        selected_route = route(stage, frozen_dev_count=cohort["accepted_count"], pilot_approved=True)
        fallback_route = route(stage, frozen_dev_count=cohort["accepted_count"], pilot_approved=True,
                               escalation_reason="invalid_structured_output")
        if selected["reasoning_effort"] != selected_route.reasoning_effort or selected["max_output_tokens"] != pilot["max_output_tokens"]:
            raise BudgetDenied("Stage settings differ from measured pilot")
        routes[stage] = {"route": asdict(selected_route), "fallback_route": asdict(fallback_route),
                         "strategy": selected["strategy"], "max_output_tokens": selected["max_output_tokens"],
                         "reason": selected["reason"], "pilot_validity": {SMALL: small["valid"] / small["cases"], LARGE: large["valid"] / large["cases"]}}
    if policy["stage_models"]["judge"].get("enabled_for_bulk") is not False or policy["stage_models"]["simple_semantic"].get("enabled_for_bulk") is not False:
        raise BudgetDenied("Untested semantic/judge stages require their own pilot")
    return routes, report, {"registration": _reference(root, root / "data/development/pilots/registrations" / (pilot_id + ".json")),
                            "receipts": refs, "source_archive": pilot["source_archive"]}


def register_dev_recipe(root, cohort_id, *, reuse_from=None):
    root, cohort, authority, cases = cohort_context(root, cohort_id)
    policy = _json(root / "configs/dev_artifact_policy.json")
    routes, report, pilot_refs = replay_policy(root, cohort, policy)
    source_hashes = dev_pilot.fingerprint(root)
    configuration = _configs(root)
    spec = {"schema": SCHEMA, "cohort_sha256": cohort_id, "namespace": "main-dev-strict-v1",
        "pool_sha256": cohort["pool_sha256"], "frozen_dev_count": cohort["accepted_count"],
        "members": cohort["members"], "source_hashes": source_hashes, "configuration": configuration,
        "stage_routes": routes, "policy": policy, "pilot_refs": pilot_refs,
        "pilot_report_sha256": digest(report), "stages": ["generation", "critique", "variants", "pack"],
        "deterministic_schema": "main_dev_deterministic_artifacts_v1", "stage_attempt_limit_per_model": 1,
        "generation_repair_limit": 1, "diagnostic_outputs_adopted": False,
        "human_validated": False, "reference_judge_enabled": False, "evaluation_authorized": False,
        "eligibility_counts": {"main_dev_total": cohort["main_dev_total"], "accepted_strict_dev": cohort["accepted_count"],
            "pending": sum(row["status"] == "pending" for row in cohort["exclusions"]),
            "excluded": sum(row["status"] == "excluded" for row in cohort["exclusions"])}}
    spec["fallback_reuse"] = _reuse_candidates(root, cohort, routes, reuse_from) if reuse_from else []
    # Legacy deterministic outputs retain their original receipt and builder.
    legacy_builder = root / "artifacts/strict_dev/prepare_dev_artifacts.py"
    if legacy_builder.exists():
        spec["legacy_deterministic_builder"] = _reference(root, legacy_builder)
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
        def add(relative, content):
            info = zipfile.ZipInfo(relative, date_time=(2026, 10, 13, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, content)
        for relative in sorted(source_hashes):
            add(relative, (root / relative).read_bytes())
        for name in CONFIGS:
            add("configs/" + name, (root / "configs" / name).read_bytes())
        if spec.get("legacy_deterministic_builder"):
            add("legacy/prepare_dev_artifacts.py", legacy_builder.read_bytes())
    content = archive.getvalue()
    path = root / "data/development/artifact_recipes/sources" / (hashlib.sha256(content).hexdigest() + ".zip")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != content:
        raise ValueError("Frozen artifact source archive conflict")
    if not path.exists():
        path.write_bytes(content)
    spec["source_archive"] = _reference(root, path)
    identifier = digest(spec)
    path = root / "data/development/artifact_recipes/registrations" / (identifier + ".json")
    if path.exists() and _json(path) != spec:
        raise ValueError("Immutable artifact recipe changed")
    if not path.exists():
        write_json(path, spec)
    # Recheck bytes before any subsequent dispatch, including registration race.
    _current_code(root, spec)
    return {"recipe_sha256": identifier, "cohort_sha256": cohort_id, "cases": len(spec["members"]),
            "routes": routes, "evaluation_authorized": False}


def _reuse_candidates(root, cohort, routes, old_id):
    if not re.fullmatch(r"[a-f0-9]{64}", old_id):
        raise ValueError("Require full prior recipe SHA")
    old = _json(root / "data/development/artifact_recipes/registrations" / (old_id + ".json"))
    if digest(old) != old_id or old["cohort_sha256"] != digest(cohort) or old["members"] != cohort["members"] or old.get("evaluation_authorized") is not False:
        raise ValueError("Prior recipe differs from current accepted Main Dev")
    _checked(root, old["source_archive"])
    current = dev_pilot.fingerprint(root)
    dependencies = ("annotations/generator.py", "annotations/models.py", "annotations/public_inputs.py",
                    "annotations/structured.py", "investigation/verifier.py", "investigation/providers.py")
    for relative in dependencies:
        name = "src/issueintel/" + relative
        if old["source_hashes"][name] != current[name]:
            raise ValueError("Prior output has different generation/critique semantics")
    selected = []
    for member in cohort["members"]:
        for stage in ("generation", "critique"):
            path = root / "data/development/artifact_recipes/outputs" / old_id / digest(member["case_id"]) / stage / "receipt.json"
            if not path.exists():
                continue
            saved = _json(path)
            settings = saved["settings"]
            if (saved["status"] == "valid" and saved["case_id"] == member["case_id"]
                    and saved["recipe_sha256"] == old_id and saved["eligibility_receipt"] == member["receipt"]
                    and settings["route"]["model"] == LARGE and settings["route"]["reasoning_effort"] == "high"
                    and settings["max_output_tokens"] == routes[stage]["max_output_tokens"]):
                if digest({k: v for k, v in saved.items() if k != "receipt_sha256"}) != saved["receipt_sha256"]:
                    raise ValueError("Prior stage receipt digest changed")
                _checked(root, saved["output"])
                selected.append({"case_id": member["case_id"], "stage": stage, "input_sha256": saved["input_sha256"],
                                 "receipt": _reference(root, path)})
    return selected


def _current_code(root, spec):
    if dev_pilot.fingerprint(root) != spec["source_hashes"] or _configs(root) != spec["configuration"]:
        raise ValueError("Registered artifact code/config changed; preserve this recipe and register a new version")


class _Provider:
    def __init__(self, root, spec, stage, journal, route_override=None):
        self.root, self.spec, self.calls, self.journal = root, spec, [], journal
        settings = spec["stage_routes"][stage]
        selected = ModelRoute(**(route_override or settings["route"]))
        config = replace(RuntimeConfig.routed(root, selected), max_output_tokens=settings["max_output_tokens"])
        if not 0 < config.max_output_tokens <= config.price().max_output_tokens:
            raise BudgetDenied("Recipe output exceeds dated model bounds")
        self.real = GroqProvider(config, call_context=asdict(selected))

    def _call(self, method, *args, **kwargs):
        _current_code(self.root, self.spec)
        started = time.monotonic()
        try:
            return getattr(self.real, method)(*args, **kwargs)
        finally:
            if self.real.last_reservation:
                self.calls.append({"reservation_id": self.real.last_reservation, "usage": self.real.last_usage,
                                   "latency_seconds": time.monotonic()-started})
                write_json(self.journal, {"calls": self.calls, "completed": False})

    def complete_json(self, *args, **kwargs):
        return self._call("complete_json", *args, **kwargs)

    def complete_structured(self, *args, **kwargs):
        return self._call("complete_structured", *args, **kwargs)

    def __call__(self, *args, **kwargs):
        return self.complete_json(*args, **kwargs)


def _stage(root, recipe_id, spec, member, pack, stage, previous=None):
    directory = root / "data/development/artifact_recipes/outputs" / recipe_id / digest(member["case_id"]) / stage
    receipt_path = directory / "receipt.json"
    settings = spec["stage_routes"][stage]
    inputs = {"case": pack["case"], "evidence": pack["evidence"], "previous": previous}
    identity = {"recipe_sha256": recipe_id, "case_id": member["case_id"], "stage": stage,
        "input_sha256": digest(inputs), "eligibility_receipt": member["receipt"],
        "snapshot_sha256": pack["snapshot"]["snapshot_sha256"], "settings": settings}
    with _lock(root, "dev_artifact_stage:" + digest([recipe_id, member["case_id"], stage])):
        if receipt_path.exists():
            saved = _json(receipt_path)
            if (any(saved.get(k) != v for k, v in identity.items())
                    or digest({k: v for k, v in saved.items() if k != "receipt_sha256"}) != saved["receipt_sha256"]):
                raise ValueError("Artifact stage receipt identity changed")
            if saved.get("output"):
                _checked(root, saved["output"])
            return saved, True
        if any(path.name not in {"primary", "fallback"} for path in directory.glob("*")):
            raise ValueError("Unreceipted paid stage retained; cannot silently repeat it")
        primary = _attempt(root, spec, identity, pack, previous, directory / "primary", settings["route"], "primary")
        attempts = [primary]
        selected = primary
        if primary["status"] == "failed" and primary.get("structured_failure") and settings.get("fallback_route"):
            selected = _attempt(root, spec, identity, pack, previous, directory / "fallback", settings["fallback_route"], "fallback")
            attempts.append(selected)
        saved = {**identity, "status": selected["status"], "output": selected.get("output"),
                 "selected_route": selected["route"], "attempts": attempts,
                 "calls": [call for attempt in attempts for call in attempt["calls"]],
                 "human_validated": False, "evaluation_authorized": False}
        for key in ("error_type", "error", "provider_status", "retry_after"):
            if key in selected:
                saved[key] = selected[key]
        saved["receipt_sha256"] = digest(saved)
        write_json(receipt_path, saved)
        return saved, False


def _attempt(root, spec, identity, pack, previous, directory, selected_route, attempt):
    attempt_identity = {**identity, "route": selected_route, "attempt": attempt}
    path = directory / "receipt.json"
    if path.exists():
        saved = _json(path)
        if any(saved.get(k) != v for k, v in attempt_identity.items()) or digest({k: v for k, v in saved.items() if k != "receipt_sha256"}) != saved["receipt_sha256"]:
            raise ValueError("Paid attempt receipt identity changed")
        if saved.get("output"):
            _checked(root, saved["output"])
        return saved
    if any(directory.glob("*")):
        raise ValueError("Unreceipted paid attempt retained; cannot silently repeat it")
    if attempt == "fallback":
        matches = [row for row in spec.get("fallback_reuse", []) if row["case_id"] == identity["case_id"]
                   and row["stage"] == identity["stage"] and row["input_sha256"] == identity["input_sha256"]]
        if matches:
            old = _json(_checked(root, matches[0]["receipt"]))
            _checked(root, old["output"])
            saved = {**attempt_identity, "status": "valid", "output": old["output"], "calls": old["calls"],
                "reused_from": matches[0]["receipt"], "human_validated": False, "evaluation_authorized": False}
            saved["receipt_sha256"] = digest(saved)
            write_json(path, saved)
            return saved
    _current_code(root, spec)
    write_json(directory / "started.json", attempt_identity)
    provider = None
    try:
        stage = identity["stage"]
        provider = _Provider(root, spec, stage, directory / "calls.json", selected_route)
        output_bound = identity["settings"]["max_output_tokens"]
        if stage == "generation":
            output = generate_annotation(pack["case"], pack["evidence"], provider, provider="groq",
                model=selected_route["model"], max_output_tokens=output_bound).to_dict()
        else:
            output = verify_hypotheses(previous, pack["evidence"], provider, max_output_tokens=output_bound)
        write_json(directory / "output.json", output)
        saved = {**attempt_identity, "status": "valid", "output": _reference(root, directory / "output.json")}
    except (ProviderError, AnnotationError, BudgetDenied, ValueError) as error:
        structured = (provider is not None and not isinstance(error, BudgetDenied) and (
            isinstance(error, (AnnotationError, ValueError)) or isinstance(error, ProviderError) and (
                error.status == 400 and error.code == "json_validate_failed"
                or error.status is None and str(error) == "Provider did not return a complete JSON object")))
        saved = {**attempt_identity, "status": "failed", "output": None, "structured_failure": structured,
            "error_type": type(error).__name__, "error": str(error)[:200], "provider_status": getattr(error, "status", None),
            "retry_after": getattr(error, "retry_after", None)}
    saved.update(calls=provider.calls if provider else [], human_validated=False, evaluation_authorized=False)
    saved["receipt_sha256"] = digest(saved)
    write_json(path, saved)
    return saved


def _case(root, recipe_id, spec, member, case):
    target = root / "data/development/artifact_recipes/outputs" / recipe_id / digest(case["case_id"])
    with _lock(root, "dev_artifact_case:" + digest([recipe_id, case["case_id"]])):
        if not (artifact_directory(root, spec["cohort_sha256"], case["case_id"]) / "receipt.json").exists():
            return {"case_id": case["case_id"], "status": "pending_deterministic", "calls": [], "receipt_reuses": 0}
        pack, deterministic = public_context(root, spec["cohort_sha256"], member, case)
        results, hits, refs = [], 0, {}
        previous = None
        for stage in ("generation", "critique"):
            saved, reused = _stage(root, recipe_id, spec, member, pack, stage, previous)
            results.append(saved)
            hits += reused
            refs[stage] = _reference(root, target / stage / "receipt.json")
            if saved["status"] != "valid":
                return {"case_id": case["case_id"], "status": "failed_" + stage,
                    "calls": [call for row in results for call in row["calls"]], "receipt_reuses": hits,
                    "provider_status": saved.get("provider_status"), "error_type": saved.get("error_type"), "error": saved.get("error")}
            previous = _json(_checked(root, saved["output"]))
        annotation = _json(_checked(root, results[0]["output"]))
        verification = previous
        completed = finish_pack(pack, annotation, verification,
            model=results[0]["selected_route"]["model"], stage_receipts=refs)
        # Remove only cited code excerpts; criticality and natural answerability
        # are not established by this deterministic manipulation.
        code_ids = {e["id"] for e in pack["evidence"] if e["kind"] == "code"}
        selected = sorted({ref for hypothesis in annotation["hypotheses"] for ref in hypothesis["support_ids"]} & code_ids)
        variants = create_abstention_variants(pack["case"], pack["evidence"], critical_ids=selected)
        for variant in variants:
            variant["annotation"]["selection_rule"] = "union_of_generated_code_support_ids; criticality_unverified"
        receipt_path = target / "completion.receipt.json"
        if receipt_path.exists():
            saved = _json(receipt_path)
            if (saved.get("schema") != SCHEMA or saved.get("recipe_sha256") != recipe_id
                    or saved.get("case_id") != case["case_id"] or saved.get("stage_receipts") != refs
                    or saved.get("eligibility_receipt") != member["receipt"]
                    or saved.get("deterministic_input_sha256") != digest(deterministic)
                    or saved.get("evaluation_authorized") is not False or saved.get("human_validated") is not False
                    or digest({k: v for k, v in saved.items() if k != "receipt_sha256"}) != saved["receipt_sha256"]):
                raise ValueError("Completed artifact receipt identity changed")
            for reference in saved["files"]:
                _checked(root, reference)
            if digest(_json(target / "investigation/pack.json")) != digest(completed) or digest(_json(target / "variants.json")) != digest(variants):
                raise ValueError("Completed artifact does not replay current stages")
            return {"case_id": case["case_id"], "status": "complete", "receipt": _reference(root, receipt_path),
                "receipt_reuses": hits, "calls": [call for row in results for call in row["calls"]]}
        write_json(target / "variants.json", variants)
        export_pack(completed, target / "investigation")
        files = [_reference(root, target / "variants.json"), _reference(root, target / "investigation/pack.json"),
                 _reference(root, target / "investigation/report.md")]
        receipt = {"schema": SCHEMA, "recipe_sha256": recipe_id, "case_id": case["case_id"],
            "eligibility_receipt": member["receipt"], "stage_receipts": refs, "files": files,
            "deterministic_input_sha256": digest(deterministic), "human_validated": False,
            "reference_judge_enabled": False, "evaluation_authorized": False}
        receipt["receipt_sha256"] = digest(receipt)
        write_json(receipt_path, receipt)
        return {"case_id": case["case_id"], "status": "complete", "receipt": _reference(root, receipt_path),
            "receipt_reuses": hits, "calls": [call for row in results for call in row["calls"]]}


def run_dev_artifacts(root, recipe_id, *, limit=None, concurrency=2):
    root = Path(root).resolve()
    if not re.fullmatch(r"[a-f0-9]{64}", recipe_id) or type(concurrency) is not int or not 1 <= concurrency <= 2 or limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("Use a full recipe SHA, 1–2 workers and a positive optional limit")
    spec = _json(root / "data/development/artifact_recipes/registrations" / (recipe_id + ".json"))
    if digest(spec) != recipe_id or spec["schema"] != SCHEMA or spec["evaluation_authorized"] is not False:
        raise ValueError("Invalid artifact recipe")
    _current_code(root, spec)
    _checked(root, spec["pilot_refs"]["registration"])
    _checked(root, spec["pilot_refs"]["source_archive"])
    for reference in spec["pilot_refs"]["receipts"]:
        _checked(root, reference)
    if spec.get("legacy_deterministic_builder"):
        reference = spec["legacy_deterministic_builder"]
        if _reference(root, root / reference["path"]) != reference:
            raise ValueError("Legacy deterministic builder hash changed")
    archive = _checked(root, spec["source_archive"])
    with zipfile.ZipFile(archive) as z:
        for relative, expected in spec["source_hashes"].items():
            if hashlib.sha256(z.read(relative)).hexdigest() != expected:
                raise ValueError("Frozen artifact code differs")
        for name, expected in spec["configuration"].items():
            if hashlib.sha256(z.read("configs/" + name)).hexdigest() != expected:
                raise ValueError("Frozen artifact configuration differs")
    root, cohort, authority, cases = cohort_context(root, spec["cohort_sha256"])
    if spec["members"] != cohort["members"] or spec["frozen_dev_count"] != cohort["accepted_count"]:
        raise ValueError("Artifact recipe membership differs from frozen accepted Main Dev")
    results, stopped = [], False
    with _lock(root, "main_dev_artifact_worker"):
        with ThreadPoolExecutor(max_workers=concurrency) as workers:
            # Only a bounded frontier is submitted; stop on provider access/rate
            # limits without queuing the rest of the cohort's paid calls.
            members = iter(spec["members"][:limit])
            pending = {}
            for _ in range(concurrency):
                member = next(members, None)
                if member:
                    pending[workers.submit(_case, root, recipe_id, spec, member, cases[member["case_id"]])] = member
            while pending:
                future = next(as_completed(pending))
                member = pending.pop(future)
                try:
                    result = future.result()
                except (ValueError, OSError, KeyError) as error:
                    calls = []
                    directory = root / "data/development/artifact_recipes/outputs" / recipe_id / digest(member["case_id"])
                    for journal in directory.glob("*/**/calls.json"):
                        calls.extend(_json(journal)["calls"])
                    result = {"case_id": member["case_id"], "status": "runtime_failure", "error": str(error)[:240], "calls": calls, "receipt_reuses": 0}
                results.append(result)
                stopped |= result.get("provider_status") in {401, 403, 429} or result.get("error_type") == "BudgetDenied"
                report = summarize_artifacts(root, recipe_id, spec, results, stopped)
                write_progress(root / "artifacts/main_dev_artifacts" / (recipe_id + ".progress.json"), report)
                if not stopped:
                    member = next(members, None)
                    if member:
                        pending[workers.submit(_case, root, recipe_id, spec, member, cases[member["case_id"]])] = member
    report = summarize_artifacts(root, recipe_id, spec, results, stopped)
    write_progress(root / "artifacts/main_dev_artifacts" / (recipe_id + ".report.json"), report)
    return report


def summarize_artifacts(root, recipe_id, spec, results, stopped):
    calls = {call["reservation_id"]: call for result in results for call in result["calls"]}
    cost = 0
    if calls:
        with sqlite3.connect(root / "data/runtime/budget.sqlite") as db:
            for identifier in calls:
                row = db.execute("SELECT charged_micro_usd FROM calls WHERE id=?", (identifier,)).fetchone()
                if row is None:
                    raise ValueError("Artifact receipt refers to missing durable reservation")
                cost += row[0]
    usages = [call["usage"] for call in calls.values() if call["usage"]]
    return {"recipe_sha256": recipe_id, "cohort_sha256": spec["cohort_sha256"], "main_dev_total": 400,
        "accepted_strict_dev": spec["frozen_dev_count"], "pending_eligibility": spec["eligibility_counts"]["pending"],
        "excluded_eligibility": spec["eligibility_counts"]["excluded"],
        "processed": len(results), "counts": dict(Counter(result["status"] for result in results)),
        "receipt_reuses": sum(result["receipt_reuses"] for result in results), "llm_calls": len(calls),
        "input_tokens": sum(usage["prompt_tokens"] for usage in usages), "output_tokens": sum(usage["completion_tokens"] for usage in usages),
        "missing_usage_calls": len(calls)-len(usages), "charged_or_reserved_micro_usd": cost,
        "provider_stopped": stopped, "human_validated": False, "reference_judge_enabled": False,
        "evaluation_authorized": False, "results": results}
