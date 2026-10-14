from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from issueintel.annotations.generator import generate_annotation
from issueintel.annotations.models import Annotation
from issueintel.annotations.public_inputs import model_payload, reject_hidden
from issueintel.repository.snapshot import build_snapshot
from issueintel.repository.snapshot import source_lines
from issueintel.repository.graph import build_graph
from issueintel.retrieval import retrieve_evidence
from issueintel.runtime.io import digest, write_json
from .controller import Action, DecisionState, RuleController
from .verifier import verify_hypotheses, validate_critique


PUBLIC_KEYS = {"case_id", "instance_id", "repository", "revision", "problem_statement", "issue_url",
               "issue_created_at", "pull_created_at", "issue_numbers", "split"}
GOLD_KEYS = {"patch", "test_patch", "hints_text", "all_hints_text", "fail_to_pass", "pass_to_pass", "FAIL_TO_PASS", "PASS_TO_PASS", "gold", "gold_localization"}


def public_case(case: dict) -> dict:
    def inspect(value):
        if isinstance(value, dict):
            if {str(key).lower() for key in value} & {key.lower() for key in GOLD_KEYS}:
                raise ValueError("Gold fields are forbidden at the inference boundary")
            for child in value.values():
                inspect(child)
        elif isinstance(value, (tuple, list)):
            for child in value:
                inspect(child)
    inspect(case)
    reject_hidden(case)
    if not isinstance(case.get("problem_statement"), str) or not case["problem_statement"].strip():
        raise ValueError("An issue description is required")
    projected = {key: value for key, value in case.items() if key in PUBLIC_KEYS}
    if "issue_numbers" in case:
        numbers = case["issue_numbers"]
        if not isinstance(numbers, list) or any((type(number) is not int and not (isinstance(number,str) and number.isdigit())) or int(number) <= 0 for number in numbers):
            raise ValueError("issue_numbers must contain positive numeric IDs")
        projected["issue_numbers"] = [int(number) for number in numbers]
    return projected


def investigate(case: dict, local_repo: Path | str, *, complete_json=None, provider="none", model="none",
                controller=None, protocol="benchmark_base", availability=None, top_k=8,
                independent_critic=True, snapshot=None, graph=None, max_output_tokens=2048,
                query_receipt=None, prepared_evidence=None) -> dict:
    case = public_case(case)
    supplied = snapshot is not None
    snapshot = snapshot or build_snapshot(local_repo, case["revision"], cutoff=case.get("issue_created_at") if protocol == "issue_time" else None,
                                         protocol=protocol, availability=availability)
    snapshot.verify()
    if supplied and snapshot.commit_sha != case["revision"]:
        raise ValueError("Supplied snapshot must match the case's exact revision")
    if Path(snapshot.repository).resolve() != Path(local_repo).resolve():
        raise ValueError("Snapshot belongs to another repository")
    if snapshot.protocol != protocol:
        raise ValueError("Snapshot protocol differs from requested protocol")
    if protocol == "issue_time":
        if snapshot.cutoff != case.get("issue_created_at") or not snapshot.availability.get("historical_certified"):
            raise ValueError("Snapshot is not bound to the case's issue-time cutoff")
    query_verified = False
    if query_receipt is not None:
        receipt = snapshot.availability.get("receipt", {})
        urls = query_receipt.get("source_urls", [])
        if query_receipt.get("schema") == "main_dev_original_query_receipt_v1":
            proof_hash = query_receipt.get("strict_dev_proof_sha256")
            lineage_bound = (isinstance(proof_hash, str) and bool(re.fullmatch(r"[a-f0-9]{64}", proof_hash))
                and proof_hash == receipt.get("strict_dev_proof_sha256"))
        else:
            lineage_bound = (bool(query_receipt.get("audit_id"))
                and query_receipt.get("audit_id") == receipt.get("audit_id")
                and bool(query_receipt.get("receipt_bundle_sha256"))
                and query_receipt.get("receipt_bundle_sha256") == receipt.get("receipt_bundle_sha256"))
        query_verified = bool(protocol == "issue_time" and query_receipt.get("verified") is True
            and query_receipt.get("case_id") == case.get("case_id")
            and query_receipt.get("created_at") == case.get("issue_created_at")
            and query_receipt.get("text_sha256") == hashlib.sha256(case["problem_statement"].encode()).hexdigest()
            and lineage_bound
            and isinstance(urls, list) and urls and all(isinstance(url, str) and urlparse(url).scheme == "https" for url in urls))
        if not query_verified:
            raise ValueError("Original-query receipt is not bound to this snapshot/case/text")
    graph = graph or build_graph(snapshot)
    if graph.snapshot_sha256 != snapshot.snapshot_sha256 or graph.commit_sha != snapshot.commit_sha:
        raise ValueError("Graph differs from selected snapshot")
    evidence = (deepcopy(prepared_evidence) if prepared_evidence is not None
                else retrieve_evidence(case["problem_statement"], snapshot, graph, top_k=top_k))
    statement = case["problem_statement"]
    if prepared_evidence is not None:
        validate_prepared_evidence(case, evidence, snapshot)
        evidence = [record for record in evidence if record["kind"] != "issue"]
    evidence.insert(0, {"id": "issue_" + digest(statement)[:20], "kind": "issue", "locator": case.get("issue_url") or case["case_id"],
        "text": statement, "sha256": hashlib.sha256(statement.encode()).hexdigest(), "revision": None,
        "available_at": case.get("issue_created_at"), "provenance": {
            "kind": "verified_original_query" if query_verified else "input",
            "edit_history": "opening_event_matched" if query_verified else "unknown",
            **({"receipt": query_receipt} if query_verified else {})}})
    controller = controller or RuleController()
    trace = [{"stage": "snapshot", "protocol": snapshot.protocol, "sha256": snapshot.snapshot_sha256},
             {"stage": "retrieval", "evidence_ids": [e["id"] for e in evidence]}]
    annotation, verification = None, None
    code_count = sum(e["kind"] == "code" for e in evidence)
    if code_count == 0:
        stop_reason = "insufficient_evidence"
    elif complete_json is None:
        stop_reason = "provider_not_configured"
    else:
        annotation = generate_annotation(case, evidence, complete_json, provider=provider, model=model,max_output_tokens=max_output_tokens).to_dict()
        decision = controller.decide(DecisionState(code_count, len(annotation["hypotheses"])), (Action.VERIFY, Action.STOP))
        trace.append({"stage": "decision", "controller": controller.name, "action": decision.value})
        verification = verify_hypotheses(annotation, evidence, complete_json if independent_critic and decision == Action.VERIFY else None,max_output_tokens=max_output_tokens)
        stop_reason = "bounded_workflow_complete"
    leakage = {"gold_in_inference": False, "snapshot_protocol": snapshot.protocol,
        "strict_issue_time_verified": query_verified and snapshot.protocol == "issue_time" and bool(snapshot.availability.get("historical_certified")),
        "snapshot_issue_time_verified": snapshot.protocol == "issue_time" and bool(snapshot.availability.get("historical_certified")),
        "original_query_verified": query_verified,
        "issue_edit_history": "opening_event_matched" if query_verified else "unknown", "snapshot_hash_verified": True,
        "limitations": ["Base commit is not proof of issue-time availability"] if snapshot.protocol != "issue_time" else
            (["Original issue text edit history may be unavailable"] if not query_verified else
             ["Historical receipt acquisition is a trusted caller boundary; no contamination-free guarantee."])}
    snapshot_descriptor = snapshot.to_dict()
    snapshot_descriptor["files"] = [{key: value for key, value in file.to_dict().items() if key != "text"} for file in snapshot.files]
    evidence_paths = {record["locator"].rsplit(":", 1)[0] for record in evidence if record["kind"] == "code"}
    relevant_nodes = [node for node in graph.nodes if node.path in evidence_paths]
    relevant_ids = {node.id for node in relevant_nodes}
    graph_descriptor = {"snapshot_sha256": graph.snapshot_sha256, "commit_sha": graph.commit_sha,
        "total_nodes": len(graph.nodes), "total_edges": len(graph.edges), "parser_version": graph.parser_version,
        "nodes": [{key: value for key, value in node.to_dict().items() if key != "text"} for node in relevant_nodes],
        "edges": [edge.to_dict() for edge in graph.edges if edge.source in relevant_ids and edge.target in relevant_ids],
        "parse_errors": list(graph.parse_errors), "limitations": list(graph.limitations)}
    pack = {"schema_version": 1, "case": case, "snapshot": snapshot_descriptor, "graph": graph_descriptor,
        "evidence": evidence, "annotation": annotation, "verification": verification,
        "leakage_audit": leakage, "trace": trace, "stop_reason": stop_reason,
        "recommended_steps": [h["falsification_test"] for h in annotation["hypotheses"]] if annotation else ["Provide a traceback, relevant component or reproducible example"],
        "confidence": {"level": "unverified", "probability": None}}
    pack["run_id"] = digest({"case": case, "snapshot": snapshot.snapshot_sha256, "provider": provider, "model": model, "evidence": [e["id"] for e in evidence], "annotation": annotation})[:24]
    return pack


def validate_prepared_evidence(case, evidence, snapshot):
    """Replay cached public excerpts against actual snapshot bytes before reuse."""
    model_payload(case, evidence)
    files = {file.path: file for file in snapshot.files}
    issues = 0
    for record in evidence:
        if hashlib.sha256(record["text"].encode()).hexdigest() != record["sha256"]:
            raise ValueError("Prepared evidence digest differs")
        if record["kind"] == "issue":
            issues += 1
            if (record["text"] != case["problem_statement"] or record["revision"] is not None
                    or record["available_at"] != case.get("issue_created_at")):
                raise ValueError("Prepared issue evidence differs from query")
        elif record["kind"] == "code":
            match = re.fullmatch(r"(.+):(\d+)-(\d+)", record["locator"])
            if not match or match[1] not in files or record["revision"] != snapshot.commit_sha:
                raise ValueError("Prepared code evidence differs from revision")
            start, end = int(match[2]), int(match[3])
            lines = source_lines(files[match[1]].text)
            if not 1 <= start <= end <= len(lines) or "\n".join(lines[start-1:end]) != record["text"]:
                raise ValueError("Prepared code evidence differs from snapshot bytes")
        else:
            raise ValueError("Prepared evidence supports only issue/code records")
    if issues != 1:
        raise ValueError("Prepared evidence needs exactly one query record")


def finish_pack(prepared, annotation, verification, *, model, stage_receipts=None):
    """Shared product/dataset assembly after independently receipted passes.

    This does not certify historical acquisition; the caller must first obtain
    the prepared pack through investigate() and replay its durable receipt.
    """
    case = public_case(prepared["case"])
    parsed = Annotation.from_dict(annotation).validate_evidence(prepared["evidence"])
    if parsed.case_id != case["case_id"] or parsed.source_quality or parsed.judgment:
        raise ValueError("Only public generated annotations may enter an investigation pack")
    if parsed.provenance.kind != "llm" or parsed.provenance.model != model:
        raise ValueError("Generation model must match the recorded public annotation provenance")
    if any(h.correctness != "unverified" for h in parsed.hypotheses):
        raise ValueError("Reference judgments cannot enter a public investigation pack")
    structural = verify_hypotheses(annotation, prepared["evidence"])
    if verification.get("reports") != structural["reports"] or verification.get("independent_human") is not False:
        raise ValueError("Verification does not bind the current public hypotheses")
    if verification.get("kind") == "llm_separate_pass":
        validate_critique({"reviews": verification.get("reviews")}, annotation, prepared["evidence"])
    elif verification.get("kind") != "structural":
        raise ValueError("Unknown verification kind")
    pack = deepcopy(prepared)
    pack.update(annotation=parsed.to_dict(), verification=verification, stop_reason="bounded_workflow_complete",
                recommended_steps=[h.falsification_test for h in parsed.hypotheses])
    pack["trace"] += [{"stage": "generation", "model": model}, {"stage": "verification", "kind": verification["kind"]}]
    if stage_receipts is not None:
        pack["stage_receipts"] = stage_receipts
    pack["run_id"] = digest({"prepared_run_id": prepared["run_id"], "annotation": annotation,
                             "verification": verification, "model": model})[:24]
    return pack


def export_pack(pack: dict, directory: Path | str) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "pack.json", pack)
    lines = ["# IssueIntel investigation", "", f"Case: `{pack['case']['case_id']}`", f"Stop: `{pack['stop_reason']}`", "",
             "## Issue", "", pack["case"]["problem_statement"], "", "## Evidence", ""]
    for record in pack["evidence"]:
        lines.append(f"- `{record['id']}` — `{record['locator']}` ({record['kind']}, SHA256 `{record['sha256']}`)")
    if pack["annotation"]:
        lines += ["", "## Competing hypotheses (automatic, correctness unverified)", ""]
        for h in pack["annotation"]["hypotheses"]:
            lines += [f"### {h['id']}", "", h["statement"], "", f"Support: {', '.join(h['support_ids'])}",
                      f"Counter: {', '.join(h['counter_ids']) or h['counter_status']}", f"Check: {h['falsification_test']}", ""]
    lines += ["", "## Limits", "", *[f"- {item}" for item in pack["leakage_audit"]["limitations"]],
              "- Citation integrity and an LLM critique do not establish causal truth.", "- No independent human validation is claimed."]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory / "report.md"
