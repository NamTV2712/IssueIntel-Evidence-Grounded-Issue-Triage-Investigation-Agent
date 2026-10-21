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
                repository_context=None, resumable=False, planned_workflow=False,
                planner_policy='bounded_planner_v1', planned_parent=None, planned_continuation=None,
                recoverable=False, recovery_records=None, prior_provider_calls=None,on_recovery_attempt=None,recovery_origin=None,recovery_fork=None) -> dict:
    case = public_case(case)
    recovery_commit=None
    if type(recoverable) is not bool or not recoverable and (recovery_records is not None or prior_provider_calls is not None or on_recovery_attempt is not None or recovery_origin is not None or recovery_fork is not None):
        raise ValueError('Stage recovery must be explicitly selected')
    if recoverable:
        from .stage_recovery import StageTape, saved_commit
        from .resume import validate_product_case
        if recovery_fork is None:validate_product_case(case,protocol)
        else:
            from .fork_origins import validate_case
            validate_case(case,protocol,recovery_fork)
        if ('split' in case or 'instance_id' in case or type(independent_critic) is not bool
                or prepared_evidence is not None or query_receipt is not None or availability is not None
                or (planned_parent is not None or planned_continuation is not None) and recovery_origin is None
                or recovery_origin is not None and (planned_parent is None or planned_continuation is None)
                or recovery_fork is not None and (planned_workflow or recovery_origin is not None or resumable)
                or controller is not None and type(controller) is not RuleController
                or not callable(on_stage) or (complete_json is None)!=(provider=='none')):
            raise ValueError('Stage recovery requires an original product with a rules recipe and durable callback')
        if recovery_records is not None:recovery_commit=saved_commit(recovery_records)
    if type(planned_workflow) is not bool:
        raise ValueError('Planned workflow must be an explicit boolean')
    from .planner import POLICY as LEGACY_PLANNER_POLICY, GAP_POLICY, WAIT_POLICY, VERSIONS
    if (not isinstance(planner_policy,str) or planner_policy not in VERSIONS
            or not planned_workflow and planner_policy!=LEGACY_PLANNER_POLICY):
        raise ValueError('Unsupported Planned workflow policy')
    if ((planned_parent is not None or planned_continuation is not None)
            and (not planned_workflow or planner_policy!=WAIT_POLICY
                 or planned_parent is None or planned_continuation is None)):
        raise ValueError('Planned continuation requires an exact waiting source and v3 policy')
    waiting_policy_hash=None
    if planned_workflow and planner_policy==WAIT_POLICY:
        from .planned_waiting import policy_sha256, validate_plan, build_continuation, continued_context
        if controller is not None and type(controller) is not RuleController:
            raise ValueError('Planned waiting supports the rules controller only')
        waiting_policy_hash=policy_sha256()
        if planned_parent is not None:
            validate_plan(planned_parent)
            receipt=planned_continuation['answers_receipt']
            prepared=build_continuation(planned_parent,
                [{'question_id':row['question_id'],'answer':row['answer']} for row in receipt['answers']],receipt['language'])
            if (case!=prepared['case'] or receipt!=prepared['receipt']
                    or planned_continuation['parent_plan_sha256']!=planned_parent['workflow_plan']['sha256']
                    or planned_continuation['policy_sha256']!=waiting_policy_hash
                    or planned_parent['workflow_plan']['policy_sha256']!=waiting_policy_hash
                    or planned_continuation['cue_text']!=prepared['cue_text']
                    or planned_continuation['answered_fields']!=prepared['answered_fields']
                    or provider!=planned_parent['workflow_plan']['provider']
                    or model!=planned_parent['workflow_plan']['model'] or complete_json is None):
                raise ValueError('Planned continuation query, policy or model differs from its source')
            if repository_context is not None:
                raise ValueError('Planned continuation cannot acquire new repository context')
            repository_context=continued_context(planned_parent,case)
    if controller is not None:
        from .typesafe_controller import TypesafePlannedController
        if isinstance(controller,TypesafePlannedController) and not planned_workflow:
            raise ValueError('Typesafe requires an original Planned product investigation')
    if type(refinement_rounds) is not int or refinement_rounds not in {0,1}:
        raise ValueError('Only zero or one additional retrieval round is supported')
    if planned_workflow:
        from .planner import validate_planned_case
        validate_planned_case(case,protocol,refinement_rounds=refinement_rounds,
                              prepared_evidence=prepared_evidence,resumable=resumable)
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
    if on_stage is not None and not callable(on_stage):
        raise ValueError('A checkpoint callback must be callable')
    if refinement_rounds and prepared_evidence is not None:
        raise ValueError('A frozen prepared evidence set cannot be expanded')
    supplied = snapshot is not None
    snapshot = snapshot or build_snapshot(local_repo, recovery_commit or case["revision"], cutoff=case.get("issue_created_at") if protocol == "issue_time" else None,
                                         protocol=protocol, availability=availability)
    snapshot.verify()
    if supplied and snapshot.commit_sha != (recovery_commit or case["revision"]):
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
    if planned_parent is not None:
        descriptor=snapshot.to_dict()
        descriptor['files']=[{key:value for key,value in file.to_dict().items() if key!='text'} for file in snapshot.files]
        if digest(descriptor)!=digest(planned_parent['snapshot']):
            raise ValueError('Planned continuation snapshot differs from its admitted parent')
        validate_prepared_evidence(planned_parent['case'],
            [row for row in planned_parent['evidence'] if row['kind']!='repository_context'],snapshot)
    statement=case['problem_statement'];checkpoints={};tape=None
    def emit(stage,record):
        ref=on_stage(stage,record);checkpoints[stage]=ref;return ref
    def checkpoint(stage,payload):
        if tape is not None:return tape.commit(stage,payload)
        if on_stage is not None:
            record={'schema':'product_investigation_stage_v1','stage':stage,
                    'case_id':case['case_id'],'query_sha256':hashlib.sha256(statement.encode()).hexdigest(),
                    'snapshot_sha256':snapshot.snapshot_sha256,'commit_sha':snapshot.commit_sha,
                    'provider':provider,'model':model,'refinement_rounds':refinement_rounds,
                    'payload':deepcopy(payload),'evaluation_performed':False,'automatic_retry':False}
            if planned_workflow:record['planned_workflow']=True
            emit(stage,record)
    def stage_value(stage,inputs,produce):
        if tape is not None:return tape.run(stage,inputs,produce)
        payload=produce();checkpoint(stage,payload);return payload
    if recoverable:
        continuation=None
        if recovery_origin is not None:
            from .planned_waiting import _inherit
            continuation={'origin':deepcopy(recovery_origin),'link':deepcopy(planned_continuation),'inherited':_inherit(planned_parent)}
        tape=StageTape(case,snapshot,graph,{'provider':provider,'model':model,'top_k':top_k,
            'independent_critic':independent_critic,'max_output_tokens':max_output_tokens,
            'refinement_rounds':refinement_rounds,'resumable':resumable,'planned_workflow':planned_workflow,
            'planner_policy':planner_policy,'repository_context':repository_context},
            on_stage=emit,records=recovery_records,prior_provider_calls=prior_provider_calls,on_attempt=on_recovery_attempt,continuation=continuation,fork=recovery_fork)
        if complete_json is not None:complete_json=tape.wrap(complete_json)
    if planned_parent is not None:
        evidence=deepcopy([row for row in planned_parent['evidence'] if row['kind']=='code'])
    elif prepared_evidence is not None:
        evidence=deepcopy(prepared_evidence)
    elif tape is not None:
        evidence=stage_value('initial_retrieval',{'query_sha256':hashlib.sha256(statement.encode()).hexdigest(),'top_k':top_k},
            lambda:{'evidence':retrieve_evidence(statement,snapshot,graph,top_k=top_k)})['evidence']
    else:
        evidence=retrieve_evidence(statement,snapshot,graph,top_k=top_k)
    intake = inspect_issue(statement)
    if planned_continuation is not None:
        from .intake import inspect_user_content
        intake=inspect_user_content(statement,planned_continuation['cue_text'])
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
    if tape is not None:
        validate_prepared_evidence(case,[row for row in evidence if row['kind']!='repository_context'],snapshot)
    controller = controller or RuleController()
    if waiting_policy_hash is not None and complete_json is not None:
        original_complete=complete_json
        def policy_guard():
            if policy_sha256()!=waiting_policy_hash:
                raise ValueError('Planned policy changed before a model call')
            snapshot.verify()
        def guarded_complete(messages,max_output_tokens):
            policy_guard()
            answer=original_complete(messages,max_output_tokens)
            policy_guard()
            return answer
        if hasattr(original_complete,'complete_structured'):
            def guarded_structured(messages,*,max_output_tokens,schema):
                policy_guard()
                answer=original_complete.complete_structured(messages,max_output_tokens=max_output_tokens,schema=schema)
                policy_guard()
                return answer
            guarded_complete.complete_structured=guarded_structured
        if hasattr(original_complete,'complete_json'):
            def guarded_json(messages,*,max_output_tokens):
                policy_guard()
                answer=original_complete.complete_json(messages,max_output_tokens=max_output_tokens)
                policy_guard()
                return answer
            guarded_complete.complete_json=guarded_json
        complete_json=guarded_complete
    trace = [{"stage": "snapshot", "protocol": snapshot.protocol, "sha256": snapshot.snapshot_sha256},
             {"stage": "retrieval", "evidence_ids": [e["id"] for e in evidence]}]
    if repository_context is not None:
        trace.append({'stage':'repository_context','sources':len(context_records),
                      'sha256':repository_context['sha256'],'historical_verified':False})
    annotation, verification = None, None
    refinement = {'policy':FOCUSED_POLICY,'requested_rounds':refinement_rounds,
                  'retrieval_rounds':0,'generation_passes':0,'stop_reason':'not_started'}
    workflow_plan=None
    def generate(stage,**extra):
        payload=stage_value(stage,{'case_sha256':digest(case),'evidence_sha256':digest(evidence)},
            lambda:{**extra,'annotation':generate_annotation(case,evidence,complete_json,
                provider=provider,model=model,max_output_tokens=max_output_tokens).to_dict()})
        if set(payload)!={'annotation'}|set(extra) or any(payload[k]!=v for k,v in extra.items()):
            raise ValueError('Recovered generation stage fields changed')
        value=Annotation.from_dict(payload['annotation']).validate_evidence(evidence)
        if (value.case_id!=case['case_id'] or value.provenance.kind!='llm' or value.provenance.provider!=provider
                or value.provenance.model!=model or value.source_quality or value.judgment
                or any(h.correctness!='unverified' for h in value.hypotheses)):
            raise ValueError('Recovered generation differs from the public recipe')
        return value.to_dict()
    def retrieve_stage(stage,produce,*,used_queries=(),**extra):
        payload=stage_value(stage,{'annotation_sha256':digest(annotation),'evidence_sha256':digest(evidence),
            'excluded_query_sha256s':sorted(used_queries)},lambda:{**extra,**produce()})
        if set(payload)!={'search','added_evidence'}|set(extra) or any(payload[k]!=v for k,v in extra.items()):
            raise ValueError('Recovered retrieval stage fields changed')
        if tape is not None:
            if any(row['kind']!='code' for row in payload['added_evidence']):
                raise ValueError('Recovered retrieval must contain code excerpts only')
            validate_prepared_evidence(case,[row for row in evidence if row['kind']=='issue']+payload['added_evidence'],snapshot)
        return payload['added_evidence'],payload['search']
    def verify_stage(critic):
        payload=stage_value('verification',{'annotation_sha256':digest(annotation),'evidence_sha256':digest(evidence),
            'independent_critic':critic is not None},lambda:{'verification':verify_hypotheses(annotation,evidence,critic,max_output_tokens=max_output_tokens)})
        if set(payload)!={'verification'}:raise ValueError('Recovered verification stage fields changed')
        value=payload['verification']
        if tape is not None:
            structural=verify_hypotheses(annotation,evidence)
            if (value.get('reports')!=structural['reports'] or value.get('independent_human') is not False
                    or (critic is not None)!=(value.get('kind')=='llm_separate_pass')):
                raise ValueError('Recovered verification changed its structural source')
            if value.get('kind')=='llm_separate_pass':validate_critique({'reviews':value.get('reviews')},annotation,evidence)
            elif value.get('kind')!='structural':raise ValueError('Unknown recovered verification')
        return value
    def finished(pack):return tape.finish(pack) if tape is not None else pack
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
        if workflow_plan is not None:
            from .planner import planned_run_id
            pack['workflow_plan']=deepcopy(workflow_plan)
        if planned_continuation is not None:
            pack['continued_from']=deepcopy(planned_continuation)
        if checkpoints:
            pack['stage_checkpoints'] = checkpoints
        pack["run_id"] = digest({"case": case, "snapshot": snapshot.snapshot_sha256, "provider": provider, "model": model, "evidence": [e["id"] for e in evidence], "annotation": annotation,
                                 **({'refinement_policy':FOCUSED_POLICY} if refinement_rounds else {}),
                                 **({'repository_context':repository_context['sha256']} if repository_context is not None else {})})[:24]
        if workflow_plan is not None:
            pack['run_id']=planned_run_id(case,snapshot.snapshot_sha256,evidence,annotation,
                                           workflow_plan,repository_context)
        return pack
    if planned_workflow:
        checkpoint('planned_input',{'case':case,'evidence':evidence,'policy':planner_policy,
            **({'policy_sha256':waiting_policy_hash} if waiting_policy_hash is not None else {})})
    code_count = sum(e["kind"] == "code" for e in evidence)
    if code_count == 0:
        stop_reason = "insufficient_evidence"
        refinement['stop_reason'] = 'insufficient_evidence'
    elif complete_json is None:
        stop_reason = "provider_not_configured"
        refinement['stop_reason'] = 'provider_not_configured'
    else:
        if not planned_workflow:
            checkpoint('prepared_input',{'case':case,'evidence':evidence})
        annotation = generate('seed_generation')
        refinement['generation_passes'] = 1
        if planned_workflow:
            from .planner import start_plan, choose_action, seal_plan
            from issueintel.annotations.public_inputs import model_hypotheses
            waiting_enabled=planner_policy==WAIT_POLICY
            if waiting_enabled:
                from .planned_waiting import start_plan as start_waiting_plan, remaining, used_queries as prior_queries, can_generate, maybe_waiting
                workflow_plan=start_waiting_plan(case,snapshot,graph,evidence,annotation,
                    provider=provider,model=model,parent=planned_parent)
                maximum=remaining(workflow_plan)['retrieval_rounds']
                used_queries=prior_queries(workflow_plan)
            else:
                workflow_plan=start_plan(case,snapshot,graph,evidence,annotation,provider=provider,model=model,
                                         policy=planner_policy)
                maximum=workflow_plan['limits']['max_retrieval_rounds']
                used_queries={workflow_plan['source']['query_sha256']}
            planned_stop=None;decision=None;decision_record=None
            for number in range(1,maximum+1):
                if waiting_enabled and not can_generate(workflow_plan):
                    planned_stop='generation_limit'
                    break
                if hasattr(controller,'bind_context'):
                    controller.bind_context(case,snapshot.to_dict(),evidence,annotation)
                decision,decision_record=choose_action(controller,sum(e['kind']=='code' for e in evidence),
                    len(annotation['hypotheses']),intake['missing_information'],maximum-number+1)
                step={'round':number,'input_annotation_sha256':digest(annotation),
                      'input_evidence_ids':[row['id'] for row in evidence],
                      'input_hypotheses':model_hypotheses(annotation['hypotheses']),
                      **decision_record,'search':None,'added_ids':[],
                      'output_annotation_sha256':None,'output_annotation':None}
                workflow_plan['rounds'].append(step)
                trace.append({'stage':'planned_decision','round':number,**decision_record,
                              'rounds_remaining':maximum-number+1})
                if decision!=Action.RETRIEVE:
                    planned_stop='controller_stopped' if decision==Action.STOP else 'controller_skipped_retrieval'
                    break
                if planner_policy in {GAP_POLICY,WAIT_POLICY}:
                    from issueintel.retrieval.gap_directed import expand_for_gaps
                    def planned_search():
                        found,search=expand_for_gaps(annotation['hypotheses'],evidence,snapshot,graph,exclude_query_sha256s=used_queries)
                        return {'added_evidence':found,'search':search}
                else:
                    def planned_search():
                        found,search=expand_for_hypotheses(annotation['hypotheses'],evidence,snapshot,graph,exclude_query_sha256s=used_queries)
                        return {'added_evidence':found,'search':search}
                added,search=retrieve_stage(f'planned_retrieval_{number}',planned_search,used_queries=used_queries,
                    round=number,input_annotation_sha256=step['input_annotation_sha256'],input_evidence_ids=step['input_evidence_ids'])
                step.update(search=search,added_ids=[row['id'] for row in added])
                used_queries.update(row['query_sha256'] for row in search['queries'])
                trace.append({'stage':'planned_retrieval','round':number,'policy':search['policy'],
                    'query_count':len(search['queries']),'added_ids':search['added_ids'],
                    'stop_reason':search['stop_reason'],'rounds_remaining':maximum-number})
                if not added:
                    if waiting_enabled:
                        waiting=maybe_waiting(case,annotation,workflow_plan,
                            cue_text=(planned_continuation or {}).get('cue_text'),
                            answered_fields=(planned_continuation or {}).get('answered_fields',()))
                        if waiting is not None:
                            workflow_plan['waiting']=waiting
                            planned_stop='waiting_for_user'
                            decision=Action.ASK
                            decision_record={'action':'ask','reason':'unreviewed_user_input','controller':'rules','fallback':False}
                            break
                    if planner_policy in {GAP_POLICY,WAIT_POLICY} and search['stop_reason']!='no_new_queries':
                        continue
                    planned_stop=search['stop_reason']
                    break
                evidence.extend(added)
                annotation=generate(f'planned_generation_{number}',round=number)
                workflow_plan['generation_passes']+=1
                workflow_plan['provider_calls']+=1+annotation['provenance']['repair_count']
                step.update(output_annotation_sha256=digest(annotation),output_annotation=deepcopy(annotation))
            if decision not in (Action.VERIFY,Action.STOP,Action.ASK):
                if hasattr(controller,'bind_context'):
                    controller.bind_context(case,snapshot.to_dict(),evidence,annotation)
                decision,decision_record=choose_action(controller,sum(e['kind']=='code' for e in evidence),
                    len(annotation['hypotheses']),intake['missing_information'],0,final=True)
            workflow_plan['final_decision']=decision_record
            trace.append({'stage':'decision','controller':decision_record['controller'],'action':decision.value})
            critic=complete_json if independent_critic and decision==Action.VERIFY else None
            verification=verify_stage(critic)
            workflow_plan['verifier_passes']=int(critic is not None)
            workflow_plan['provider_calls']+=int(critic is not None)
            stop_reason='controller_stopped' if decision==Action.STOP else planned_stop or 'round_limit'
            workflow_plan['stop_reason']=stop_reason
            workflow_plan=seal_plan(workflow_plan)
            checkpoint('planned_complete',{'workflow_plan':workflow_plan,'verification':verification})
            pack=assemble(stop_reason,verification)
            if hasattr(controller,'export_decisions'):
                pack['controller_decisions']=controller.export_decisions(pack)
            from .planner import validate_planned
            validate_planned(pack)
            return finished(pack)
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
                def focused_search():
                    found,search=expand_for_hypotheses(annotation['hypotheses'],evidence,snapshot,graph)
                    return {'added_evidence':found,'search':search}
                added,search=retrieve_stage('focused_retrieval',focused_search)
                refinement.update(retrieval_rounds=1,seed_annotation=deepcopy(annotation),search=search,
                                  stop_reason=search['stop_reason'])
                trace.append({'stage':'focused_retrieval','policy':FOCUSED_POLICY,
                              'query_count':len(search['queries']),'added_ids':search['added_ids'],
                              'stop_reason':search['stop_reason'],'rounds_remaining':0})
                if added:
                    evidence.extend(added)
                    annotation=generate('refined_generation')
                    refinement['generation_passes'] = 2
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
            stage_value('verification_input',{'annotation_sha256':digest(annotation),'evidence_sha256':digest(evidence)},
                lambda:{'prepared':assemble('verification_pending',None),'policy_sha256':resume_policy(),'max_output_tokens':max_output_tokens})
        verification = verify_stage(complete_json if independent_critic and decision == Action.VERIFY else None)
        stop_reason = "controller_stopped" if decision==Action.STOP else "bounded_workflow_complete"
    if planned_workflow:
        from .planner import start_plan, seal_plan, validate_planned
        workflow_plan=start_plan(case,snapshot,graph,evidence,None,provider=provider,model=model,
                                 policy=planner_policy)
        workflow_plan.update(stop_reason=stop_reason,final_decision={
            'action':'stop','reason':stop_reason,'controller':'rules','fallback':False})
        workflow_plan=seal_plan(workflow_plan)
        checkpoint('planned_complete',{'workflow_plan':workflow_plan,'verification':None})
        pack=assemble(stop_reason,verification)
        if hasattr(controller,'export_decisions'):
            pack['controller_decisions']=controller.export_decisions(pack)
        validate_planned(pack)
        return finished(pack)
    return finished(assemble(stop_reason, verification))


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
    """Write a new pack and derive presentation without changing inference data."""
    from .report import build_report, render_markdown
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path=directory / "pack.json"
    write_json(path, pack)
    report=build_report(pack,source_pack_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    write_json(directory / "report.json",report)
    (directory / "report.md").write_text(render_markdown(report),encoding="utf-8")
    from .hypothesis_review import build_hypothesis_review, render_markdown as render_review
    review=build_hypothesis_review(report)
    write_json(directory / 'hypothesis-review.json',review)
    (directory / 'hypothesis-review.md').write_text(render_review(review),encoding='utf-8')
    return directory / "report.md"
