from __future__ import annotations

import hashlib
import json
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
from issueintel.retrieval.focused import expand_for_hypotheses, POLICY as FOCUSED_POLICY
from issueintel.runtime.io import digest, write_json
from .controller import Action, DecisionState, RuleController
from .verifier import verify_hypotheses, validate_critique
from .intake import inspect_issue


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
                query_receipt=None, prepared_evidence=None, refinement_rounds=0, on_stage=None,
                repository_context=None, resumable=False) -> dict:
    case = public_case(case)
    if resumable:
        from .resume import validate_product_case
        validate_product_case(case,protocol)
    context_records=[]
    if repository_context is not None:
        from issueintel.retrieval.repository_context import context_evidence
        reject_hidden(repository_context)
        if prepared_evidence is not None:
            raise ValueError('Frozen prepared evidence cannot acquire current repository context')
        context_records=context_evidence(repository_context,case,protocol=protocol)
    if type(refinement_rounds) is not int or refinement_rounds not in {0,1}:
        raise ValueError('Only zero or one additional retrieval round is supported')
    if on_stage is not None and not callable(on_stage):
        raise ValueError('A checkpoint callback must be callable')
    if refinement_rounds and prepared_evidence is not None:
        raise ValueError('A frozen prepared evidence set cannot be expanded')
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
    intake = inspect_issue(statement)
    if prepared_evidence is not None:
        validate_prepared_evidence(case, evidence, snapshot)
        evidence = [record for record in evidence if record["kind"] != "issue"]
    evidence.extend(context_records)
    evidence.insert(0, {"id": "issue_" + digest(statement)[:20], "kind": "issue", "locator": case.get("issue_url") or case["case_id"],
        "text": statement, "sha256": hashlib.sha256(statement.encode()).hexdigest(), "revision": None,
        "available_at": case.get("issue_created_at"), "provenance": {
            "kind": "verified_original_query" if query_verified else "input",
            "edit_history": "opening_event_matched" if query_verified else "unknown",
            **({"receipt": query_receipt} if query_verified else {})}})
    controller = controller or RuleController()
    trace = [{"stage": "snapshot", "protocol": snapshot.protocol, "sha256": snapshot.snapshot_sha256},
             {"stage": "retrieval", "evidence_ids": [e["id"] for e in evidence]}]
    if repository_context is not None:
        trace.append({'stage':'repository_context','sources':len(context_records),
                      'sha256':repository_context['sha256'],'historical_verified':False})
    annotation, verification = None, None
    refinement = {'policy':FOCUSED_POLICY,'requested_rounds':refinement_rounds,
                  'retrieval_rounds':0,'generation_passes':0,'stop_reason':'not_started'}
    checkpoints = {}
    def checkpoint(stage,payload):
        if on_stage is not None:
            record={'schema':'product_investigation_stage_v1','stage':stage,
                    'case_id':case['case_id'],'query_sha256':hashlib.sha256(statement.encode()).hexdigest(),
                    'snapshot_sha256':snapshot.snapshot_sha256,'commit_sha':snapshot.commit_sha,
                    'provider':provider,'model':model,'refinement_rounds':refinement_rounds,
                    'payload':deepcopy(payload),'evaluation_performed':False,'automatic_retry':False}
            checkpoints[stage] = on_stage(stage,record)
    def assemble(stop_reason, verification):
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
        pack = {"schema_version": 1, "case": case, "intake": intake, "snapshot": snapshot_descriptor, "graph": graph_descriptor,
            "evidence": evidence, "annotation": annotation, "verification": verification,
            "leakage_audit": leakage, "trace": trace, "stop_reason": stop_reason,
            "recommended_steps": [h["falsification_test"] for h in annotation["hypotheses"]] if annotation else ["Provide a traceback, relevant component or reproducible example"],
            "confidence": {"level": "unverified", "probability": None}}
        if repository_context is not None:
            pack['repository_context']=deepcopy(repository_context)
            pack['leakage_audit']['limitations'].append('Current repository history may include later edits; it is product context, not strict historical proof.')
        if refinement_rounds:
            pack['refinement'] = refinement
        if checkpoints:
            pack['stage_checkpoints'] = checkpoints
        pack["run_id"] = digest({"case": case, "snapshot": snapshot.snapshot_sha256, "provider": provider, "model": model, "evidence": [e["id"] for e in evidence], "annotation": annotation,
                                 **({'refinement_policy':FOCUSED_POLICY} if refinement_rounds else {}),
                                 **({'repository_context':repository_context['sha256']} if repository_context is not None else {})})[:24]
        return pack
    code_count = sum(e["kind"] == "code" for e in evidence)
    if code_count == 0:
        stop_reason = "insufficient_evidence"
        refinement['stop_reason'] = 'insufficient_evidence'
    elif complete_json is None:
        stop_reason = "provider_not_configured"
        refinement['stop_reason'] = 'provider_not_configured'
    else:
        checkpoint('prepared_input',{'case':case,'evidence':evidence})
        annotation = generate_annotation(case, evidence, complete_json, provider=provider, model=model,max_output_tokens=max_output_tokens).to_dict()
        refinement['generation_passes'] = 1
        checkpoint('seed_generation',{'annotation':annotation})
        first_action=None
        if refinement_rounds:
            allowed=(Action.RETRIEVE,Action.VERIFY,Action.STOP)
            action=controller.decide(DecisionState(code_count,len(annotation['hypotheses']),
                tuple(intake['missing_information']),rounds_remaining=1),allowed)
            if not isinstance(action,Action) or action not in allowed:
                action=RuleController().decide(DecisionState(code_count,len(annotation['hypotheses'])),allowed)
            first_action=action
            trace.append({'stage':'refinement_decision','controller':controller.name,'action':action.value,
                          'rounds_remaining':1})
            if action==Action.RETRIEVE:
                added,search=expand_for_hypotheses(annotation['hypotheses'],evidence,snapshot,graph)
                refinement.update(retrieval_rounds=1,seed_annotation=deepcopy(annotation),search=search,
                                  stop_reason=search['stop_reason'])
                checkpoint('focused_retrieval',{'search':search,'added_evidence':added})
                trace.append({'stage':'focused_retrieval','policy':FOCUSED_POLICY,
                              'query_count':len(search['queries']),'added_ids':search['added_ids'],
                              'stop_reason':search['stop_reason'],'rounds_remaining':0})
                if added:
                    evidence.extend(added)
                    annotation=generate_annotation(case,evidence,complete_json,provider=provider,model=model,max_output_tokens=max_output_tokens).to_dict()
                    refinement['generation_passes'] = 2
                    checkpoint('refined_generation',{'annotation':annotation})
            else:
                refinement['stop_reason'] = 'controller_skipped_retrieval'
            if action==Action.STOP:
                refinement['stop_reason'] = 'controller_stopped'
        decision = (first_action if first_action in (Action.VERIFY,Action.STOP) else
            controller.decide(DecisionState(sum(e['kind']=='code' for e in evidence),len(annotation['hypotheses']),rounds_remaining=0),(Action.VERIFY,Action.STOP)))
        if not isinstance(decision,Action) or decision not in (Action.VERIFY,Action.STOP):
            decision=Action.STOP
        trace.append({"stage": "decision", "controller": controller.name, "action": decision.value})
        if resumable and independent_critic and decision == Action.VERIFY and on_stage is not None:
            from .resume import resume_policy
            checkpoint('verification_input',{'prepared':assemble('verification_pending',None),
                'policy_sha256':resume_policy(),'max_output_tokens':max_output_tokens})
        verification = verify_hypotheses(annotation, evidence, complete_json if independent_critic and decision == Action.VERIFY else None,max_output_tokens=max_output_tokens)
        checkpoint('verification',{'verification':verification})
        stop_reason = "controller_stopped" if decision==Action.STOP else "bounded_workflow_complete"
    return assemble(stop_reason, verification)


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
    if pack.get('repository_context'):
        context=pack['repository_context']
        lines += ['', '## Current repository context', '',
                  f"Selected sources: {len(context['sources'])}; SHA256 `{context['sha256']}`.",
                  'Bounded local retrieval; text mentions are not confirmed duplicates/fixes. Historical availability is unverified.']
    if pack.get('refinement'):
        refined=pack['refinement']
        search=refined.get('search',{})
        lines += ['', '## Bounded hypothesis-focused retrieval', '',
                  f"Retrieval rounds: {refined['retrieval_rounds']} / 1",
                  f"Generation passes: {refined['generation_passes']}",
                  f"Additional excerpts: {len(search.get('added_ids',[]))}",
                  'Search hits are candidates, not confirmed counter-evidence or proof of a cause.']
    if pack.get('intake'):
        lines += ['', '## Intake (lexical hints, unverified)', '',
                  'Intent: '+pack['intake']['intent'],
                  'Review missing cues: '+(', '.join(pack['intake']['missing_information']) or 'none')]
    if pack.get('followup'):
        link=pack['followup']
        lines += ['', '## Follow-up provenance', '', f"Parent job: `{link['parent_job_id']}`",
                  f"Parent pack SHA256: `{link['parent_pack_sha256']}`",
                  'User context is unverified and does not inherit historical query certification.']
        if link.get('clarification'):
            receipt=link['clarification']
            lines += ['', '### Guided clarification answers', '',
                      f"Question bundle SHA256: `{receipt['questions_sha256']}`; answers SHA256: `{receipt['sha256']}`.",
                      'Answers remain unverified, including unknowns. Generated question wording is excluded from intake cues.',
                      'Code is retrieved anew at the same commit; prior selected repository context is not carried into the new query.',
                      '', '```json', json.dumps(receipt['answers'],ensure_ascii=False,indent=2), '```']
            if link.get('question_job_id'):
                lines += ['', f"Adaptive question job: `{link['question_job_id']}`.",
                          'Model-proposed distinctions are unverified; referenced hypotheses and code stay bound to the source report.']
    if pack.get('resumed_from'):
        link=pack['resumed_from']
        lines += ['', '## Explicit verifier continuation', '',
                  f"Source job: `{link['source_job_id']}`; checkpoint SHA256 `{link['checkpoint_sha256']}`.",
                  'Generation was reused after replay. Only verification continued; this was not an automatic retry.']
    if pack.get('context_selection'):
        link=pack['context_selection']
        lines += ['', '## User-selected repository context', '',
                  f"Parent job: `{link['parent_job_id']}`; pack SHA256 `{link['parent_pack_sha256']}`.",
                  f"Selected context SHA256: `{link['context_sha256']}`.",
                  'Only explicitly selected current sources were included. Historical availability and causal correctness remain unverified.']
    if pack["annotation"]:
        lines += ["", "## Competing hypotheses (automatic, correctness unverified)", ""]
        for h in pack["annotation"]["hypotheses"]:
            lines += [f"### {h['id']}", "", h["statement"], "", f"Support: {', '.join(h['support_ids'])}",
                      f"Counter: {', '.join(h['counter_ids']) or h['counter_status']}", f"Check: {h['falsification_test']}", ""]
    lines += ["", "## Limits", "", *[f"- {item}" for item in pack["leakage_audit"]["limitations"]],
              "- Citation integrity and an LLM critique do not establish causal truth.", "- No independent human validation is claimed."]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory / "report.md"
