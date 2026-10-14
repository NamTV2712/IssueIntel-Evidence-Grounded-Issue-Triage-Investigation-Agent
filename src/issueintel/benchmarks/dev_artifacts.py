"""Main Dev deterministic artifacts and public context, separate from gold.

Eligibility receipts and the frozen cohort define membership. Nothing here
opens a holdout capability, source-human rationale or a model provider.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import gzip
import hashlib
import json

from issueintel.investigation.pipeline import investigate, validate_prepared_evidence
from issueintel.repository.cache import load_snapshot
from issueintel.repository.graph import build_graph, CodeGraph, GraphNode, GraphEdge
from issueintel.repository.snapshot import source_lines
from issueintel.runtime.io import digest, write_json, write_progress
from .strict_dev import verify_main_dev_cohort, load_pool, _checked, _json, _lock
from .temporal import reconstruct_query_text

SCHEMA = "main_dev_deterministic_artifacts_v1"


def cohort_context(root, cohort_id):
    root = Path(root).resolve()
    cohort = verify_main_dev_cohort(root, cohort_id)
    authority = load_pool(root, cohort["pool_sha256"])
    cases = {item["case"]["case_id"]: item["case"] for item in authority["candidates"]}
    return root, cohort, authority, cases


def artifact_directory(root, cohort_id, case_id):
    return Path(root) / "data/development/artifacts" / cohort_id / digest(case_id)


def accepted_snapshot(root, member, case):
    record = _json(_checked(root, member["receipt"]))
    if record["case_id"] != case["case_id"] or record.get("accepted") is not True or case["split"] != "main_dev":
        raise ValueError("Only accepted original Main Dev may prepare artifacts")
    proof = _json(_checked(root, record["proof"]))
    asset = _json(_checked(root, record["snapshot"]))
    snapshot = load_snapshot(_checked(root, asset["index"]), root / "data/derived")
    query = reconstruct_query_text(case, proof)
    if snapshot.snapshot_sha256 != record["snapshot_sha256"] or hashlib.sha256(query.encode()).hexdigest() != record["query_sha256"]:
        raise ValueError("Accepted query/snapshot binding changed")
    runtime_case = dict(case, revision=snapshot.commit_sha, issue_created_at=snapshot.cutoff, problem_statement=query)
    query_receipt = {"schema": "main_dev_original_query_receipt_v1", "verified": True,
        "case_id": case["case_id"], "created_at": snapshot.cutoff, "text_sha256": record["query_sha256"],
        "strict_dev_proof_sha256": record["proof"]["sha256"],
        "source_urls": [item["source_url"] for item in proof["issue_receipts"]]}
    return record, snapshot, runtime_case, query_receipt


def load_artifact_receipt(root, cohort_id, member, case, *, restricted=False):
    path = artifact_directory(root, cohort_id, case["case_id"]) / "receipt.json"
    saved = _json(path)
    record = _json(_checked(root, member["receipt"]))
    if (saved.get("schema") != SCHEMA or saved.get("cohort_sha256") != cohort_id
            or saved.get("case_id") != case["case_id"] or saved.get("eligibility_receipt") != member["receipt"]
            or saved.get("snapshot_sha256") != record["snapshot_sha256"] or saved.get("query_sha256") != record["query_sha256"]
            or saved.get("gold_used_for_graph_or_retrieval") is not False or saved.get("evaluation_authorized") is not False
            or saved.get("llm_calls") != 0):
        raise ValueError("Deterministic artifact identity/isolation changed")
    names = saved["files"] if restricted else ("generation_inputs.json", "graph.json.gz")
    for name in names:
        _checked(root, saved["files"][name])
    return saved


def load_graph(root, reference, snapshot):
    """Rehydrate text-free graph from the selected blobs, with bounded decoding."""
    path = _checked(root, reference)
    with gzip.open(path, "rb") as stream:
        content = stream.read(128 * 1024 * 1024 + 1)
    if len(content) > 128 * 1024 * 1024:
        raise ValueError("Graph exceeds decoded bound")
    row = json.loads(content)
    if row["snapshot_sha256"] != snapshot.snapshot_sha256 or row["commit_sha"] != snapshot.commit_sha:
        raise ValueError("Graph differs from accepted snapshot")
    files = {item.path: item for item in snapshot.files}
    lines = {path: source_lines(file.text) for path, file in files.items()}
    nodes = []
    for item in row["nodes"]:
        file = files[item["path"]]
        if item["kind"] == "file":
            text = file.text
        else:
            start, end = item["start_line"], item["end_line"]
            if not 1 <= start <= end <= len(lines[file.path]):
                raise ValueError("Graph coordinates differ from selected blob")
            text = "\n".join(lines[file.path][start-1:end])
        if hashlib.sha256(text.encode()).hexdigest() != item["sha256"]:
            raise ValueError("Graph node differs from selected blob bytes")
        nodes.append(GraphNode(**dict(item, text=text)))
    ids = {node.id for node in nodes}
    if len(ids) != len(nodes) or any(edge["source"] not in ids or edge["target"] is not None and edge["target"] not in ids for edge in row["edges"]):
        raise ValueError("Invalid graph node/edge identity")
    return CodeGraph(row["snapshot_sha256"], row["commit_sha"], tuple(nodes),
                     tuple(GraphEdge(**item) for item in row["edges"]), tuple(row["parse_errors"]),
                     row["parser_version"], tuple(row["limitations"]))


def public_context(root, cohort_id, member, case):
    """Replay only public artifacts before calling the common application pipeline."""
    saved = load_artifact_receipt(root, cohort_id, member, case)
    record, snapshot, runtime_case, query_receipt = accepted_snapshot(root, member, case)
    inputs = _json(_checked(root, saved["files"]["generation_inputs.json"]))
    if inputs["case"] != runtime_case:
        raise ValueError("Prepared public case differs from accepted opening/snapshot")
    validate_prepared_evidence(runtime_case, inputs["evidence"], snapshot)
    graph = load_graph(root, saved["files"]["graph.json.gz"], snapshot)
    pack = investigate(runtime_case, snapshot.repository, snapshot=snapshot, graph=graph,
        protocol="issue_time", query_receipt=query_receipt, prepared_evidence=inputs["evidence"])
    if not pack["leakage_audit"]["strict_issue_time_verified"]:
        raise ValueError("Public context must retain strict provenance")
    return pack, saved


def _restricted_rows(root, authority, cohort, cases):
    import pyarrow.parquet as parquet
    rows = parquet.read_table(_checked(root, authority["sources"][0]), columns=["instance_id", "patch"],
        filters=[("instance_id", "in", [cases[cid]["instance_id"] for cid in cohort["ordered_case_ids"]])]).to_pylist()
    selected = {"verified:" + row["instance_id"]: row for row in rows}
    if len(rows) != len(selected) or set(selected) != set(cohort["ordered_case_ids"]):
        raise ValueError("Restricted source selection must match accepted Main Dev exactly")
    return selected


def _build(root, cohort_id, authority, member, case, gold):
    from .asof_assets import _project_gold_localization
    target = artifact_directory(root, cohort_id, case["case_id"])
    with _lock(root, "dev_deterministic_artifacts:" + case["case_id"]):
        if (target / "receipt.json").exists():
            load_artifact_receipt(root, cohort_id, member, case, restricted=True)
            return {"case_id": case["case_id"], "status": "reused"}
        record, snapshot, runtime_case, query_receipt = accepted_snapshot(root, member, case)
        graph = build_graph(snapshot)
        pack = investigate(runtime_case, snapshot.repository, snapshot=snapshot, graph=graph,
                           protocol="issue_time", query_receipt=query_receipt)
        # Restricted reference processing begins only after public retrieval.
        base_folder = root / "data/derived/cases" / hashlib.sha256(case["case_id"].encode()).hexdigest()[:24]
        base_receipt = _json(base_folder / "build_receipt.json")
        if base_receipt["case_id"] != case["case_id"] or base_receipt["revision"] != case["revision"]:
            raise ValueError("Original base asset identity changed")
        loc_path = base_folder / "localization.json"
        if hashlib.sha256(loc_path.read_bytes()).hexdigest() != base_receipt["files"]["localization.json"]:
            raise ValueError("Original base localization changed")
        base_snapshot = load_snapshot(root / base_receipt["snapshot_index"], root / "data/derived")
        if base_snapshot.snapshot_sha256 != base_receipt["snapshot_sha256"]:
            raise ValueError("Original base snapshot changed")
        localization = _project_gold_localization(gold["patch"], snapshot, graph,
            {"localization": _json(loc_path), "snapshot": base_snapshot})
        graph_row = graph.to_dict()
        graph_row["nodes"] = [{k: v for k, v in node.to_dict().items() if k != "text"} for node in graph.nodes]
        write_json(target / "generation_inputs.json", {"case": runtime_case, "evidence": pack["evidence"]})
        write_json(target / "localization.restricted.json", localization)
        (target / "graph.json.gz").write_bytes(gzip.compress(json.dumps(graph_row, sort_keys=True, separators=(",", ":")).encode(), mtime=0))
        names = ("generation_inputs.json", "localization.restricted.json", "graph.json.gz")
        receipt = {"schema": SCHEMA, "cohort_sha256": cohort_id, "case_id": case["case_id"],
            "eligibility_receipt": member["receipt"], "snapshot_sha256": snapshot.snapshot_sha256,
            "query_sha256": record["query_sha256"], "graph_parser": graph.parser_version,
            "gold_source_sha256": authority["sources"][0]["sha256"], "gold_patch_sha256": hashlib.sha256(gold["patch"].encode()).hexdigest(),
            "files": {name: {"path": (target / name).relative_to(root).as_posix(),
                              "sha256": hashlib.sha256((target / name).read_bytes()).hexdigest()} for name in names},
            "gold_used_for_graph_or_retrieval": False, "reference_localization_scope": "restricted_dev_dataset_construction",
            "localization_projection": localization["line_projection_status"], "evaluation_authorized": False,
            "code_evidence_count": len(pack["evidence"])-1, "llm_calls": 0}
        write_json(target / "receipt.json", receipt)
        return {"case_id": case["case_id"], "status": "built", "localization_projection": receipt["localization_projection"]}


def prepare_dev_artifacts(root, cohort_id, *, limit=None, concurrency=2):
    if type(concurrency) is not int or not 1 <= concurrency <= 2 or limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("Use 1–2 workers and a positive optional limit")
    root, cohort, authority, cases = cohort_context(root, cohort_id)
    gold = _restricted_rows(root, authority, cohort, cases)
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as workers:
        pending = {workers.submit(_build, root, cohort_id, authority, member, cases[member["case_id"]], gold[member["case_id"]]): member
                   for member in cohort["members"][:limit]}
        for future in as_completed(pending):
            member = pending[future]
            try:
                results.append(future.result())
            except (ValueError, OSError, KeyError) as error:
                results.append({"case_id": member["case_id"], "status": "failed", "error": str(error)[:240]})
            report = {"cohort_sha256": cohort_id, "results": results,
                "counts": dict(Counter(item["status"] for item in results)), "gold_selected_dev_rows": len(gold),
                "llm_calls": 0, "evaluation_authorized": False}
            write_progress(root / "artifacts/strict_dev/DEV_ARTIFACT_PREPARATION.json", report)
    return report
