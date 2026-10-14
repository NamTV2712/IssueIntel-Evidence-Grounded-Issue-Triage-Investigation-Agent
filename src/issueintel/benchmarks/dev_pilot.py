"""Registered DEV-only paired-model diagnostic; never opens holdout or gold.

Generation arms share public inputs. Critic arms share the same previously
generated hypothesis set, allowing a paired assessment comparison.
"""
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from io import BytesIO
import gzip
import hashlib
import json
import re
import sqlite3
import time
import zipfile

from issueintel.annotations.generator import generate_annotation
from issueintel.annotations.models import Annotation, AnnotationError
from issueintel.annotations.public_inputs import model_payload, model_hypotheses
from issueintel.investigation.verifier import verify_hypotheses
from issueintel.investigation.providers import GroqProvider, ProviderError
from issueintel.repository.snapshot import source_lines
from issueintel.repository.cache import load_snapshot
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.budget import BudgetDenied
from issueintel.runtime.io import digest, write_json, write_progress
from issueintel.runtime.routing import route, SMALL, LARGE
from .sources import inside_root, sha256_file
from .strict_dev import verify_main_dev_cohort, load_pool, _checked, _json, _lock
from .temporal import reconstruct_query_text

SCHEMA = "main_dev_paired_pilot_v1"
LEGACY = "01f34481f1db064a535db6f11d344dff0e8c6e0d8e506de1d7936ea7d6ef91e4"
CONFIGS = ("runtime.toml", "author_decisions.json", "strict_dev_authorization.json", "strict_dev_prices.json")


def fingerprint(root):
    return {p.relative_to(root).as_posix(): sha256_file(p)
            for p in sorted((root / "src/issueintel").rglob("*.py"))}


def config_hashes(root):
    return {name: sha256_file(root / "configs" / name) for name in CONFIGS}


def _publish(root, value, folder):
    identifier = digest(value)
    path = inside_root(root, root / "data/development/pilots" / folder / (identifier + ".json"))
    if path.exists() and _json(path) != value:
        raise ValueError("Immutable pilot object changed")
    if not path.exists():
        write_json(path, value)
    return {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}


def validate_evidence(inputs, case, proof, snapshot):
    query = reconstruct_query_text(case, proof)
    saved = inputs["case"]
    if (saved["case_id"] != case["case_id"] or saved["split"] != "main_dev"
            or saved["repository"] != case["repository"] or saved["revision"] != snapshot.commit_sha
            or saved["problem_statement"] != query or saved["issue_created_at"] != snapshot.cutoff):
        raise ValueError("Legacy DEV input differs from accepted query/snapshot")
    model_payload(saved, inputs["evidence"])  # Reject hidden fields before reuse.
    files = {f.path: f for f in snapshot.files}
    issues = 0
    for e in inputs["evidence"]:
        if hashlib.sha256(e["text"].encode()).hexdigest() != e["sha256"]:
            raise ValueError("Legacy evidence digest changed")
        if e["kind"] == "issue":
            issues += 1
            if e["text"] != query or e["available_at"] != snapshot.cutoff:
                raise ValueError("Legacy issue evidence does not bind opening query")
        elif e["kind"] == "code":
            match = re.fullmatch(r"(.+):(\d+)-(\d+)", e["locator"])
            if not match or match[1] not in files or e["revision"] != snapshot.commit_sha:
                raise ValueError("Legacy code evidence does not bind selected snapshot")
            start, end = int(match[2]), int(match[3])
            lines = source_lines(files[match[1]].text)
            if not 1 <= start <= end <= len(lines) or "\n".join(lines[start-1:end]) != e["text"]:
                raise ValueError("Legacy excerpt differs from actual selected code")
        else:
            raise ValueError("Pilot supports only issue/code evidence")
    if issues != 1:
        raise ValueError("Pilot needs one opening-query evidence record")


def _legacy_inputs(root, member, case):
    folder = root / "data/annotations/dataset" / LEGACY / hashlib.sha256(case["case_id"].encode()).hexdigest()[:24]
    receipt_path = folder / "generation.receipt.json"
    if not receipt_path.exists():
        return None
    receipt = _json(receipt_path)
    if (receipt.get("case_id") != case["case_id"] or receipt.get("split") != "main_dev"
            or receipt.get("case_sha256") != digest(case) or receipt.get("recipe_sha256") != LEGACY
            or receipt.get("protocol") != "issue_time" or receipt.get("gold_in_inference") is not False
            or digest({k: v for k, v in receipt.items() if k != "receipt_sha256"}) != receipt.get("receipt_sha256")):
        raise ValueError("Legacy generation receipt identity/isolation invalid")
    # Read only these two allowed DEV files, not pack/localization/judged/human.
    references = {}
    for name in ("generation_inputs.json", "annotation.generated.json"):
        path = inside_root(root, folder / name)
        if sha256_file(path) != receipt["files"][name]:
            raise ValueError("Legacy allowed DEV artifact hash changed")
        references[name] = {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}
    inputs = _json(folder / "generation_inputs.json")
    if digest(inputs) != receipt["input_sha256"]:
        raise ValueError("Legacy generation input digest mismatch")
    eligibility = _json(_checked(root, member["receipt"]))
    proof = _json(_checked(root, eligibility["proof"]))
    asset = _json(_checked(root, eligibility["snapshot"]))
    snapshot = load_snapshot(_checked(root, asset["index"]), root / "data/derived")
    validate_evidence(inputs, case, proof, snapshot)
    annotation = _json(folder / "annotation.generated.json")
    Annotation.from_dict(annotation).validate_evidence(inputs["evidence"])
    model_hypotheses(annotation["hypotheses"])
    references["generation.receipt.json"] = {"path": receipt_path.relative_to(root).as_posix(), "sha256": sha256_file(receipt_path)}
    return {"case_id": case["case_id"], "repository": case["repository"], "files": references,
            "eligibility_receipt": member["receipt"], "input_sha256": digest(inputs),
            "prior_model": annotation["provenance"]["model"], "historical_recipe": LEGACY}


def register_pilot(root, cohort_id, *, count=30):
    if not 30 <= count <= 50:
        raise ValueError("Pilot requires 30–50 previously understood accepted DEV cases")
    root = Path(root).resolve()
    cohort = verify_main_dev_cohort(root, cohort_id)
    authority = load_pool(root, cohort["pool_sha256"])
    cases = {r["case"]["case_id"]: r["case"] for r in authority["candidates"]}
    by_repo, exclusions = defaultdict(list), []
    for member in cohort["members"]:
        try:
            item = _legacy_inputs(root, member, cases[member["case_id"]])
            if item:
                by_repo[item["repository"]].append(item)
        except (ValueError, OSError, KeyError) as error:
            exclusions.append({"case_id": member["case_id"], "reason": str(error)[:180]})
    selected = []
    for values in by_repo.values():
        values.sort(key=lambda r: r["case_id"])
    while len(selected) < count and any(by_repo.values()):
        for repository in sorted(by_repo):
            if by_repo[repository] and len(selected) < count:
                selected.append(by_repo[repository].pop(0))
    if len(selected) != count:
        raise ValueError("Insufficient hash-valid previously understood DEV cases")
    spec = {"schema": SCHEMA, "cohort_sha256": cohort_id, "namespace": "main-dev-strict-v1",
        "pool_sha256": cohort["pool_sha256"], "frozen_dev_count": cohort["accepted_count"],
        "selection_rule": "round_robin_repository_then_case_id_among_valid_prior_DEV_generation_inputs",
        "cases": selected, "input_reuse_exclusions": exclusions, "source_hashes": fingerprint(root),
        "configuration": config_hashes(root), "models": [SMALL, LARGE], "effort": "high", "max_output_tokens": 16384,
        "stages": ["generation", "critique"], "generation_comparison": "paired public inputs",
        "critic_comparison": "paired identical previously generated hypotheses; no reference gold",
        "acceptance": {"validity_min": 0.95, "generation_abstention_agreement_min": 0.85,
                       "critic_assessment_agreement_min": 0.80, "citation_integrity_required": 1.0},
        "scope_limits": ["citation existence is not semantic truth", "model agreement is not human IAA",
                         "no holdout, Live annotations, gold or human rationale", "diagnostic outputs are not bulk labels"],
        "evaluation_authorized": False}
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for relative in sorted(spec["source_hashes"]):
            info = zipfile.ZipInfo(relative, date_time=(2026, 10, 13, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, (root / relative).read_bytes())
        for name in CONFIGS:
            info = zipfile.ZipInfo("configs/" + name, date_time=(2026, 10, 13, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, (root / "configs" / name).read_bytes())
    content = archive.getvalue()
    with zipfile.ZipFile(BytesIO(content)) as frozen:
        for relative, expected in spec["source_hashes"].items():
            if hashlib.sha256(frozen.read(relative)).hexdigest() != expected:
                raise ValueError("Source changed during pilot registration")
    path = root / "data/development/pilots/sources" / (hashlib.sha256(content).hexdigest() + ".zip")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != content:
        raise ValueError("Pilot source archive conflict")
    if not path.exists():
        path.write_bytes(content)
    spec["source_archive"] = {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}
    return _publish(root, spec, "registrations")


class _RecordedProvider:
    def __init__(self, root, spec, model, stage):
        self.root, self.spec, self.calls = root, spec, []
        selected = route(stage, frozen_dev_count=spec["frozen_dev_count"], pilot_model=model)
        config = replace(RuntimeConfig.routed(root, selected), max_output_tokens=spec["max_output_tokens"])
        price = config.price()
        if type(config.max_output_tokens) is not int or not 0 < config.max_output_tokens <= price.max_output_tokens:
            raise BudgetDenied("Registered pilot output bound exceeds dated model limits")
        self.real = GroqProvider(config, call_context=asdict(selected))

    def _call(self, method, *args, **kwargs):
        if fingerprint(self.root) != self.spec["source_hashes"] or config_hashes(self.root) != self.spec["configuration"]:
            raise ValueError("Registered pilot code/config changed before paid dispatch")
        started = time.monotonic()
        try:
            return getattr(self.real, method)(*args, **kwargs)
        finally:
            identifier = self.real.last_reservation
            if identifier:
                self.calls.append({"reservation_id": identifier, "usage": self.real.last_usage,
                                   "latency_seconds": time.monotonic()-started})

    def complete_json(self, *args, **kwargs):
        return self._call("complete_json", *args, **kwargs)

    def complete_structured(self, *args, **kwargs):
        return self._call("complete_structured", *args, **kwargs)

    def __call__(self, *args, **kwargs):
        return self.complete_json(*args, **kwargs)


def _stage(root, pilot_id, spec, item, model, stage):
    key = digest([pilot_id, item["case_id"], model, stage])
    directory = root / "data/development/pilots/outputs" / pilot_id / key
    receipt_path = directory / "receipt.json"
    identity = {"pilot_sha256": pilot_id, "case_id": item["case_id"], "model": model, "stage": stage,
                "input_sha256": item["input_sha256"], "namespace": spec["namespace"]}
    with _lock(root, "pilot_stage:" + key):
        if receipt_path.exists():
            saved = _json(receipt_path)
            if any(saved.get(k) != v for k, v in identity.items()):
                raise ValueError("Pilot stage identity changed")
            if digest({k: v for k, v in saved.items() if k != "receipt_sha256"}) != saved["receipt_sha256"]:
                raise ValueError("Pilot stage receipt digest changed")
            if saved.get("output"):
                _checked(root, saved["output"])
            return saved, True
        if (directory / "output.json").exists():
            raise ValueError("Unreceipted pilot output retained; explicit recovery required before another paid call")
        provider = _RecordedProvider(root, spec, model, stage)
        inputs = _json(_checked(root, item["files"]["generation_inputs.json"]))
        old_annotation = _json(_checked(root, item["files"]["annotation.generated.json"]))
        started = time.monotonic()
        try:
            if stage == "generation":
                output = generate_annotation(inputs["case"], inputs["evidence"], provider,
                    provider="groq", model=model, max_output_tokens=spec["max_output_tokens"]).to_dict()
            else:
                output = verify_hypotheses(old_annotation, inputs["evidence"], provider, max_output_tokens=spec["max_output_tokens"])
            directory.mkdir(parents=True, exist_ok=True)
            write_json(directory / "output.json", output)
            output_ref = {"path": (directory / "output.json").relative_to(root).as_posix(),
                          "sha256": sha256_file(directory / "output.json")}
            result = {**identity, "status": "valid", "output": output_ref, "calls": provider.calls}
        except (ProviderError, AnnotationError, ValueError, BudgetDenied) as error:
            result = {**identity, "status": "failed", "output": None, "calls": provider.calls,
                "error_type": type(error).__name__, "error": str(error)[:200],
                "provider_status": getattr(error, "status", None), "retry_after": getattr(error, "retry_after", None)}
        result.update(elapsed_seconds=time.monotonic()-started, human_validated=False, diagnostic_only=True)
        result["receipt_sha256"] = digest(result)
        write_json(receipt_path, result)
        return result, False


def summarize(root, pilot_id, spec, results):
    pairs = defaultdict(dict)
    for r in results:
        pairs[(r["case_id"], r["stage"])][r["model"]] = r
    stages = {}
    for stage in spec["stages"]:
        by_model = {}
        agree = count = 0
        overlap = []
        for model in spec["models"]:
            rows = [r for r in results if r["stage"] == stage and r["model"] == model]
            calls = [c for r in rows for c in r["calls"]]
            usages = [c["usage"] for c in calls if c["usage"]]
            cost = 0
            with sqlite3.connect(root / "data/runtime/budget.sqlite") as db:
                for call in calls:
                    row = db.execute("SELECT charged_micro_usd FROM calls WHERE id=?", (call["reservation_id"],)).fetchone()
                    if row:
                        cost += row[0]
            by_model[model] = {"cases": len(rows), "valid": sum(r["status"] == "valid" for r in rows),
                "calls": len(calls), "extra_calls": sum(max(0, len(r["calls"])-1) for r in rows),
                "input_tokens": sum(u["prompt_tokens"] for u in usages), "output_tokens": sum(u["completion_tokens"] for u in usages),
                "missing_usage_calls": len(calls)-len(usages), "charged_or_reserved_micro_usd": cost,
                "latency_seconds": sum(c["latency_seconds"] for c in calls)}
        for (case_id, s), pair in pairs.items():
            if s != stage or set(pair) != set(spec["models"]) or any(r["status"] != "valid" for r in pair.values()):
                continue
            small, large = [_json(_checked(root, pair[m]["output"])) for m in spec["models"]]
            if stage == "generation":
                count += 1
                agree += small["abstention"]["label"] == large["abstention"]["label"]
                refs = [{x for h in a["hypotheses"] for x in h["support_ids"] + h["counter_ids"]} for a in (small, large)]
                overlap.append(len(refs[0] & refs[1]) / len(refs[0] | refs[1]))
            else:
                labels = [{r["hypothesis_id"]: r["assessment"] for r in a["reviews"]} for a in (small, large)]
                count += len(labels[0])
                agree += sum(labels[0][k] == labels[1][k] for k in labels[0])
        agreement = agree/count if count else None
        complete = all(v["cases"] == len(spec["cases"]) for v in by_model.values())
        threshold = spec["acceptance"]["generation_abstention_agreement_min" if stage == "generation" else "critic_assessment_agreement_min"]
        pass_policy = bool(complete and all(v["valid"] / v["cases"] >= spec["acceptance"]["validity_min"] for v in by_model.values())
                           and agreement is not None and agreement >= threshold)
        stages[stage] = {"models": by_model, "complete": complete, "agreement": agreement,
            "agreement_kind": "abstention_label" if stage == "generation" else "same_hypothesis_critic_assessment",
            "mean_citation_set_jaccard": sum(overlap)/len(overlap) if overlap else None,
            "structural_citation_integrity": "validated on every valid output; semantic correctness unverified",
            "small_model_operational_gate_passed": pass_policy,
            "semantic_equivalence_proven": False}
    return {"pilot_sha256": pilot_id, "cohort_sha256": spec["cohort_sha256"], "stages": stages,
            "completed_stages": len(results), "expected_stages": len(spec["cases"]) * 4,
            "evaluation_authorized": False, "human_validation": False, "scope_limits": spec["scope_limits"]}


def run_pilot(root, pilot_id, *, limit=None):
    root = Path(root).resolve()
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("Require a positive pilot case limit")
    if not re.fullmatch(r"[a-f0-9]{64}", pilot_id):
        raise ValueError("Require full pilot SHA256")
    spec = _json(root / "data/development/pilots/registrations" / (pilot_id + ".json"))
    if digest(spec) != pilot_id or spec["schema"] != SCHEMA or spec["evaluation_authorized"] is not False:
        raise ValueError("Invalid DEV pilot registration")
    if fingerprint(root) != spec["source_hashes"] or config_hashes(root) != spec["configuration"]:
        raise ValueError("Pilot code/config changed; register a new version, preserve outputs")
    _checked(root, spec["source_archive"])
    with zipfile.ZipFile(_checked(root, spec["source_archive"])) as archive:
        for relative, expected in spec["source_hashes"].items():
            if hashlib.sha256(archive.read(relative)).hexdigest() != expected:
                raise ValueError("Pilot frozen code does not match registration")
    cohort = verify_main_dev_cohort(root, spec["cohort_sha256"])
    if any(item["case_id"] not in cohort["ordered_case_ids"] for item in spec["cases"]):
        raise ValueError("Pilot includes nonaccepted or non-DEV case")
    results, reused, done = [], 0, 0
    with _lock(root, "dev_pilot_worker"):
        for item in spec["cases"]:
            if limit is not None and done >= limit:
                break
            for stage in spec["stages"]:
                for model in spec["models"]:
                    result, hit = _stage(root, pilot_id, spec, item, model, stage)
                    results.append(result)
                    reused += hit
                    write_progress(root / "artifacts/dev_pilot" / (pilot_id + ".progress.json"),
                        {"results": results, "receipt_reused": reused, "evaluation_authorized": False})
                    if result.get("provider_status") in {401, 403, 429} or result.get("error_type") == "BudgetDenied":
                        report = summarize(root, pilot_id, spec, results)
                        report["stop_reason"] = "provider_access_or_rate_limit"
                        write_progress(root / "artifacts/dev_pilot" / (pilot_id + ".report.json"), report)
                        return report
            done += 1
    report = summarize(root, pilot_id, spec, results)
    report["receipt_reused"] = reused
    write_progress(root / "artifacts/dev_pilot" / (pilot_id + ".report.json"), report)
    return report
