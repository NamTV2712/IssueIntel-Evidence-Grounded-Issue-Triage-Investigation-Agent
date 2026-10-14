"""Dataset construction orchestration; default scope is development cases."""
from __future__ import annotations

import hashlib
import json
import os
import gzip
from pathlib import Path

from issueintel.annotations.variants import create_abstention_variants
from issueintel.investigation.pipeline import investigate, export_pack
from issueintel.investigation.providers import GroqProvider, ProviderError
from issueintel.repository.acquire import acquire_repository
from issueintel.repository.cache import save_snapshot
from issueintel.repository.graph import build_graph
from issueintel.repository.snapshot import build_snapshot
from issueintel.runtime.budget import BudgetDenied
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import write_json, write_progress
from .localization import extract_gold_localization
from .store import load_dev_cases, load_dev_gold, verify_manifest


def case_directory(root: Path, case_id: str) -> Path:
    return root / "data/annotations/automatic" / hashlib.sha256(case_id.encode()).hexdigest()[:24]


def _completion(provider):
    return provider if callable(provider) else provider.complete_json


def _inventory(target: Path, names: list[str]) -> dict:
    if any(not isinstance(name, str) or not name or Path(name).name != name or name in {".", ".."}
           or "/" in name or "\\" in name or ":" in name for name in names):
        raise ValueError("Artifact inventory accepts local filenames only")
    return {name: hashlib.sha256((target / name).read_bytes()).hexdigest() for name in names}


def _verify_inventory(target: Path, expected: dict):
    if not expected or _inventory(target, list(expected)) != expected:
        raise ValueError("Annotation artifacts are missing or changed; explicit repair required")


def build_case_assets(root: Path, case: dict, gold: dict) -> dict:
    """Build input-side snapshot/graph and evaluator-only gold projection separately."""
    root = Path(root).resolve()
    target = root / "data/derived/cases" / hashlib.sha256(case["case_id"].encode()).hexdigest()[:24]
    manifest_sha = hashlib.sha256((root / "data/manifests/dataset.json").read_bytes()).hexdigest()
    receipt_path = target / "build_receipt.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt["manifest_sha256"] != manifest_sha:
            raise ValueError("Asset receipt belongs to another dataset")
        if receipt.get("case_id") != case["case_id"] or receipt.get("repository") != case["repository"] or receipt.get("revision") != case["revision"]:
            raise ValueError("Asset receipt is bound to another case/repository/revision")
        _verify_inventory(target, receipt["files"])
        from issueintel.repository.cache import load_snapshot
        snapshot = load_snapshot(root / receipt["snapshot_index"], root / "data/derived")
        if snapshot.commit_sha != case["revision"]:
            raise ValueError("Asset snapshot revision does not match case")
        if snapshot.snapshot_sha256 != receipt.get("snapshot_sha256") or snapshot.protocol != "benchmark_base":
            raise ValueError("Asset snapshot digest/protocol does not match receipt")
        graph = build_graph(snapshot)
        return {"snapshot": snapshot, "graph": graph, "receipt": receipt,
                "localization": json.loads((target / "localization.json").read_text(encoding="utf-8"))}
    repository = acquire_repository(case["repository"], case["revision"], root / "data/repos")
    snapshot = build_snapshot(repository.path, case["revision"])
    graph = build_graph(snapshot)
    index = save_snapshot(snapshot, root / "data/derived")
    localization = extract_gold_localization(gold["patch"], snapshot, graph).to_dict()
    write_json(target / "localization.json", localization)
    # Graph text comes from shared blobs; do not duplicate it into a dataset index.
    graph_record = graph.to_dict()
    graph_record["nodes"] = [{key: value for key, value in node.to_dict().items() if key != "text"} for node in graph.nodes]
    target.mkdir(parents=True, exist_ok=True)
    (target / "graph.json.gz").write_bytes(gzip.compress(json.dumps(graph_record, sort_keys=True, separators=(",", ":")).encode("utf-8"), mtime=0))
    receipt = {"schema_version": 1, "case_id": case["case_id"], "repository": case["repository"],
        "revision": snapshot.commit_sha, "manifest_sha256": manifest_sha,
        "snapshot_index": index.relative_to(root).as_posix(), "snapshot_sha256": snapshot.snapshot_sha256,
        "protocol": "benchmark_base", "strict_issue_time_verified": False,
        "files": _inventory(target, ["localization.json", "graph.json.gz"]),
        "leakage_audit": {"gold_used_for_graph": False, "gold_used_for_snapshot": False,
            "gold_localization_scope": "evaluator_only", "issue_text_edit_history": "unknown"}}
    write_json(receipt_path, receipt)
    return {"snapshot": snapshot, "graph": graph, "receipt": receipt, "localization": localization}


def build_assets(root: Path | str, *, limit=1, all_splits=False) -> dict:
    if type(limit) is not int or not 1 <= limit <= 800:
        raise ValueError("Asset limit must be between 1 and 800")
    root = Path(root).resolve()
    verify_manifest(root)
    # Dataset preparation is distinct from running predictions/metrics on holdout.
    from issueintel.runtime.io import read_jsonl
    cases = read_jsonl(root / "data/benchmark/cases.jsonl") if all_splits else load_dev_cases(root)
    cases = sorted(cases, key=lambda case: (case["split"] == "temporal_candidate", case["case_id"]))
    gold = {row["case_id"]: row for row in read_jsonl(root / "data/benchmark/gold.jsonl")}
    completed, failures = [], []
    for case in cases[:limit]:
        try:
            assets = build_case_assets(root, case, gold[case["case_id"]])
            completed.append({"case_id": case["case_id"], "snapshot_sha256": assets["snapshot"].snapshot_sha256})
        except (ValueError, RuntimeError, OSError) as error:
            failures.append({"case_id": case["case_id"], "error": str(error)})
        write_progress(root / "artifacts/build_assets_progress.json", {"requested": min(limit,len(cases)),
            "completed": len(completed), "failed": len(failures), "latest_case": case["case_id"],
            "failures": failures, "mode": "dataset_construction", "evaluation_performed": False})
    return {"completed": completed, "failures": failures, "mode": "dataset_construction", "evaluation_performed": False}


def annotate_dev(root: Path | str, *, limit=1, case_id=None, judge=False) -> dict:
    if type(limit) is not int or not 1 <= limit <= 400:
        raise ValueError("Annotation limit must be between 1 and 400")
    root = Path(root).resolve()
    config = RuntimeConfig.load(root)
    if not os.environ.get("GROQ_API_KEY", "").strip():
        raise BudgetDenied("Missing local GROQ_API_KEY")
    config.price()  # Preflight before repository/network acquisition.
    provider = GroqProvider(config)
    cases = load_dev_cases(root)
    if case_id:
        cases = [case for case in cases if case["case_id"] == case_id]
        if not cases:
            raise ValueError("case_id must be an existing main_dev case")
    gold = load_dev_gold(root)
    manifest_sha = hashlib.sha256((root / "data/manifests/dataset.json").read_bytes()).hexdigest()
    completed, skipped, failures = [], [], []
    for case in cases:
        target = case_directory(root, case["case_id"])
        if (target / "receipt.json").exists():
            receipt = json.loads((target / "receipt.json").read_text(encoding="utf-8"))
            if receipt["manifest_sha256"] != manifest_sha:
                raise ValueError("Existing annotation belongs to another dataset")
            if receipt.get("case_id") != case["case_id"] or receipt.get("split") != "main_dev":
                raise ValueError("Existing annotation belongs to another case/split")
            _verify_inventory(target, receipt.get("files", {}))
            if judge and not receipt.get("judgment_completed"):
                from issueintel.annotations.judge import judge_annotation
                saved = json.loads((target / "generation_inputs.json").read_text(encoding="utf-8"))
                original = json.loads((target / "annotation.json").read_text(encoding="utf-8"))
                try:
                    annotation = judge_annotation(original, case, saved["evidence"], gold[case["case_id"]], _completion(provider),
                        provider=provider.name, model=provider.model,max_output_tokens=config.max_output_tokens).to_dict()
                except BudgetDenied as error:
                    return {"completed": completed, "skipped": len(skipped), "failures": failures,
                            "stop_reason": str(error), "budget": config.ledger().status()}
                except ProviderError as error:
                    if error.status in {401,403,429}:
                        return {"completed":completed,"skipped":len(skipped),"failures":failures,
                            "stop_reason":str(error),"provider_status":error.status,"retry_after":error.retry_after,"budget":config.ledger().status()}
                    failures.append({"case_id":case["case_id"],"stage":"judge","error":str(error)})
                    if len(completed) + len(failures) >= limit:
                        break
                    continue
                except (ValueError, RuntimeError, OSError) as error:
                    failures.append({"case_id": case["case_id"], "stage": "judge", "error": str(error)})
                    if len(completed) + len(failures) >= limit:
                        break
                    continue
                write_json(target / "annotation.json", annotation)
                receipt["judgment_completed"] = True
                receipt["files"] = _inventory(target, list(receipt["files"]))
                write_json(target / "receipt.json", receipt)
                completed.append(case["case_id"])
                if len(completed) >= limit:
                    break
                continue
            skipped.append(case["case_id"])
            continue
        if len(completed) + len(failures) >= limit:
            break
        try:
            assets = build_case_assets(root, case, gold[case["case_id"]])
            snapshot, graph = assets["snapshot"], assets["graph"]
            localization = assets["localization"]
            # Localization never enters investigate(). Same snapshot+graph feed both sides.
            pack = investigate(case, snapshot.repository, complete_json=_completion(provider),
                provider=provider.name, model=provider.model, snapshot=snapshot, graph=graph,max_output_tokens=config.max_output_tokens)
            if pack["annotation"] is None:
                raise ValueError(f"No grounded annotation: {pack['stop_reason']}")
            annotation = pack["annotation"]
            variants = create_abstention_variants(case, pack["evidence"], critical_ids=[e["id"] for e in pack["evidence"] if e["kind"] == "code"])
            write_json(target / "annotation.json", annotation)
            write_json(target / "localization.json", localization)
            write_json(target / "variants.json", variants)
            write_json(target / "generation_inputs.json", {"case": case, "evidence": pack["evidence"]})
            export_pack(pack, root / "artifacts/investigations" / pack["run_id"])
            write_json(target / "receipt.json", {"schema_version": 1, "case_id": case["case_id"], "split": "main_dev",
                "manifest_sha256": manifest_sha, "snapshot_index": assets["receipt"]["snapshot_index"],
                "snapshot_sha256": snapshot.snapshot_sha256, "run_id": pack["run_id"], "provider": provider.name,
                "model": provider.model, "kind": "automatic_dataset_construction", "human_reviewed": False,
                "temporal_protocol": "benchmark_base", "strict_issue_time_verified": False,
                "judgment_completed": False, "files": _inventory(target, ["annotation.json", "localization.json", "variants.json", "generation_inputs.json"])})
            completed.append(case["case_id"])
            if judge:
                from issueintel.annotations.judge import judge_annotation
                judged = judge_annotation(annotation, case, pack["evidence"], gold[case["case_id"]], _completion(provider),
                    provider=provider.name, model=provider.model,max_output_tokens=config.max_output_tokens).to_dict()
                write_json(target / "annotation.json", judged)
                receipt = json.loads((target / "receipt.json").read_text(encoding="utf-8"))
                receipt["judgment_completed"] = True
                receipt["files"] = _inventory(target, list(receipt["files"]))
                write_json(target / "receipt.json", receipt)
        except BudgetDenied as error:
            return {"completed": completed, "skipped": len(skipped), "failures": failures,
                    "stop_reason": str(error), "budget": config.ledger().status()}
        except ProviderError as error:
            if error.status in {401,403,429}:
                return {"completed":completed,"skipped":len(skipped),"failures":failures,
                    "stop_reason":str(error),"provider_status":error.status,"retry_after":error.retry_after,"budget":config.ledger().status()}
            failures.append({"case_id":case["case_id"],"error":str(error)})
        except (ValueError, RuntimeError, OSError) as error:
            failures.append({"case_id": case["case_id"], "error": str(error)})
            write_json(root / "artifacts/failures" / (hashlib.sha256(case["case_id"].encode()).hexdigest()[:24] + ".json"), failures[-1])
        write_progress(root / "artifacts/annotation_progress.json", {"completed_this_run":len(completed),"skipped":len(skipped),
            "failures":failures,"latest_case":case["case_id"],"budget":config.ledger().status(),
            "provider_rate_limits":getattr(provider,"last_rate_limits",{}),"mode":"main_dev_annotation"})
    return {"completed": completed, "skipped": len(skipped), "failures": failures, "budget": config.ledger().status()}
