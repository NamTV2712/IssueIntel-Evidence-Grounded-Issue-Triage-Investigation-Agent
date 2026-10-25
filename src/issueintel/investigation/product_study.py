"""A narrow, gold-free stage boundary for paired Planned product studies.

This policy does not authorize spending or establish source authenticity. It
binds the original public product input, fixed snapshot/graph and native recipe
so a study runner can own shared stages and their receipts separately. The
strict Main Dev experiment gate and product recovery tapes remain disjoint.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from issueintel.runtime.io import digest


SCHEMA = 'product_controller_study_policy_v1'
STAGE_SCHEMA = 'product_controller_study_stage_binding_v1'
RECIPE = {'protocol': 'benchmark_base', 'planned_workflow': True,
    'planner_policy': 'bounded_planner_v2', 'provider': 'groq',
    'model': 'openai/gpt-oss-120b', 'reasoning_effort': 'low',
    'max_output_tokens': 4096, 'top_k': 12, 'independent_critic': True,
    'refinement_rounds': 0, 'graph_expand': True,
    'controllers': ['rules', 'jev'], 'maximum_generation_repairs': 1}
_HASH = re.compile('[a-f0-9]{64}')
_COMMIT = re.compile('[a-f0-9]{40}')
_ISSUE = re.compile(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)')
_STAGE = re.compile(r'initial_retrieval|seed_generation|planned_(?:retrieval|generation)_[1-3]|verification')
_CASE_FIELDS = {'case_id', 'repository', 'revision', 'problem_statement',
                'issue_url', 'issue_created_at', 'issue_numbers'}
_SOURCE_FILES = ('investigation/product_study.py', 'investigation/pipeline.py',
    'investigation/planner.py', 'investigation/controller.py',
    'investigation/typesafe_controller.py', 'investigation/verifier.py',
    'annotations/generator.py', 'annotations/models.py',
    'annotations/public_inputs.py', 'annotations/structured.py',
    'retrieval/search.py', 'retrieval/focused.py', 'retrieval/gap_directed.py',
    'repository/graph.py', 'repository/snapshot.py', 'runtime/io.py')


def _encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError):
        raise ValueError('Product study values must be finite JSON data') from None


def _same(left, right):
    return _encoded(left) == _encoded(right)


def _hash(value, name):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError('Product study requires an exact SHA256: ' + name)
    return value


def source_hashes():
    package = Path(__file__).resolve().parents[1]
    return {name: sha256((package / name).read_bytes()).hexdigest() for name in _SOURCE_FILES}


def validate_case(case):
    """Accept original canonical GitHub issue inputs, never benchmark aliases."""
    from issueintel.annotations.public_inputs import reject_hidden
    reject_hidden(case)
    if (not isinstance(case, dict) or set(case) - _CASE_FIELDS
            or not {'case_id', 'repository', 'revision', 'problem_statement', 'issue_url'} <= set(case)):
        raise ValueError('Product study requires an original public GitHub issue case')
    match = _ISSUE.fullmatch(case['case_id']) if isinstance(case['case_id'], str) else None
    if (not match or any(part in {'.', '..'} for part in match.group(1, 2))
            or not isinstance(case['issue_url'], str) or not isinstance(case['repository'], str)
            or case['issue_url'] != case['case_id']
            or case['repository'] not in {'/'.join(match.group(1, 2)),
                                         'https://github.com/' + '/'.join(match.group(1, 2))}
            or not isinstance(case['revision'], str) or not _COMMIT.fullmatch(case['revision'])
            or not isinstance(case['problem_statement'], str) or not case['problem_statement'].strip()):
        raise ValueError('Product study requires a canonical issue and exact revision')
    if 'issue_numbers' in case and (not isinstance(case['issue_numbers'], list)
            or case['issue_numbers'] != [int(match.group(3))]
            or any(type(number) is not int for number in case['issue_numbers'])):
        raise ValueError('Product study issue numbers differ from its canonical identity')
    _encoded(case)
    return deepcopy(case)


def _descriptor(value):
    return value.to_dict() if hasattr(value, 'to_dict') else value


def context_binding(case, snapshot, graph):
    case = validate_case(case)
    snap, code = _descriptor(snapshot), _descriptor(graph)
    if (not isinstance(snap, dict) or not isinstance(code, dict)
            or snap.get('protocol') != 'benchmark_base' or snap.get('commit_sha') != case['revision']
            or not isinstance(snap.get('snapshot_sha256'), str) or not _HASH.fullmatch(snap['snapshot_sha256'])
            or code.get('commit_sha') != snap['commit_sha']
            or code.get('snapshot_sha256') != snap['snapshot_sha256']
            or not isinstance(code.get('parser_version'), str) or not code['parser_version']):
        raise ValueError('Product study snapshot and graph differ from the original fixed case')
    return {'case_sha256': digest(case), 'query_sha256': sha256(case['problem_statement'].encode()).hexdigest(),
        'case_id': case['case_id'], 'repository': case['repository'],
        'commit_sha': snap['commit_sha'], 'snapshot_sha256': snap['snapshot_sha256'],
        'snapshot_descriptor_sha256': digest(json.loads(_encoded(snap))),
        'graph_sha256': digest(json.loads(_encoded(code))), 'parser_version': code['parser_version']}


def make_policy(case, snapshot, graph, *, study_id, source_sha256, runtime_sha256):
    """Create a pair-scoped binding; arm/controller ownership is runner metadata."""
    for name, value in (('study_id', study_id), ('source_sha256', source_sha256),
                        ('runtime_sha256', runtime_sha256)):
        _hash(value, name)
    policy = {'schema': SCHEMA, 'study_id': study_id, 'source_sha256': source_sha256,
        'runtime_sha256': runtime_sha256, 'recipe': deepcopy(RECIPE),
        'context': context_binding(case, snapshot, graph), 'source_hashes': source_hashes()}
    policy['sha256'] = digest(policy)
    return validate_policy(policy)


def validate_policy(policy):
    fields = {'schema', 'study_id', 'source_sha256', 'runtime_sha256', 'recipe',
              'context', 'source_hashes', 'sha256'}
    if not isinstance(policy, dict) or set(policy) != fields or policy.get('schema') != SCHEMA:
        raise ValueError('Invalid product study policy structure')
    for name in ('study_id', 'source_sha256', 'runtime_sha256', 'sha256'):
        _hash(policy[name], name)
    if (digest({key: value for key, value in policy.items() if key != 'sha256'}) != policy['sha256']
            or not _same(policy['recipe'], RECIPE) or policy['source_hashes'] != source_hashes()):
        raise ValueError('Product study recipe, source code or policy seal changed')
    context = policy['context']
    context_fields = {'case_sha256', 'query_sha256', 'case_id', 'repository', 'commit_sha',
                      'snapshot_sha256', 'snapshot_descriptor_sha256', 'graph_sha256', 'parser_version'}
    if not isinstance(context, dict) or set(context) != context_fields:
        raise ValueError('Product study context structure changed')
    for name in ('case_sha256', 'query_sha256', 'snapshot_sha256', 'snapshot_descriptor_sha256', 'graph_sha256'):
        _hash(context[name], name)
    if (not isinstance(context['commit_sha'], str) or not _COMMIT.fullmatch(context['commit_sha'])
            or not isinstance(context['case_id'], str) or not _ISSUE.fullmatch(context['case_id'])
            or not isinstance(context['repository'], str) or not context['repository']
            or not isinstance(context['parser_version'], str) or not context['parser_version']):
        raise ValueError('Product study fixed context is invalid')
    _encoded(policy)
    return deepcopy(policy)


def validate_invocation(case, policy, executor, *, protocol, complete_json, provider, model,
        top_k, independent_critic, max_output_tokens, refinement_rounds, planned_workflow,
        planner_policy, controller, incompatible):
    """Fail before source/provider IO; this gate does not widen strict experiments."""
    from .controller import RuleController
    from .typesafe_controller import TypesafePlannedController
    validate_case(case)
    policy = validate_policy(policy)
    if (not callable(executor) or not callable(complete_json)
            or type(controller) not in {RuleController, TypesafePlannedController}
            or protocol != RECIPE['protocol'] or provider != RECIPE['provider'] or model != RECIPE['model']
            or type(top_k) is not int or top_k != 12
            or independent_critic is not True or type(max_output_tokens) is not int or max_output_tokens != 4096
            or type(refinement_rounds) is not int or refinement_rounds != 0
            or planned_workflow is not True or planner_policy != RECIPE['planner_policy']
            or not isinstance(incompatible, dict) or any(value is not None and value is not False for value in incompatible.values())
            or digest(case) != policy['context']['case_sha256']):
        raise ValueError('Product study requires its disjoint original Planned v2 recipe')
    return policy


def validate_context(policy, case, snapshot, graph):
    validate_policy(policy)
    snapshot.verify()
    if not _same(context_binding(case, snapshot, graph), policy['context']):
        raise ValueError('Product study public source or fixed graph changed')


def stage_binding(policy, stage, inputs):
    validate_policy(policy)
    if not isinstance(stage, str) or not _STAGE.fullmatch(stage) or not isinstance(inputs, dict):
        raise ValueError('Product study stage is outside its native recipe')
    _encoded(inputs)
    return {'schema': STAGE_SCHEMA, 'policy_sha256': policy['sha256'],
        'study_id': policy['study_id'], 'source_sha256': policy['source_sha256'],
        'runtime_sha256': policy['runtime_sha256'], 'recipe_sha256': digest(policy['recipe']),
        'context': deepcopy(policy['context']), 'stage': stage, 'inputs': deepcopy(inputs)}


def run_stage(policy, stage, inputs, produce, executor, *, case, snapshot, graph):
    """Replay deterministic searches; native pipeline validates model payloads."""
    validate_context(policy, case, snapshot, graph)
    binding = stage_binding(policy, stage, inputs)
    payload = executor(stage, deepcopy(binding), produce)
    if not isinstance(payload, dict):
        raise ValueError('Product study stage must retain its full native payload')
    _encoded(payload)
    validate_context(policy, case, snapshot, graph)
    if stage == 'initial_retrieval' or stage.startswith('planned_retrieval_'):
        # Search is deterministic and has no provider call. Exact replay also
        # rejects fabricated provenance/query/score or Boolean-as-integer edits.
        if not _same(payload, produce()):
            raise ValueError('Shared product retrieval differs from native deterministic replay')
        validate_context(policy, case, snapshot, graph)
    return deepcopy(payload)


def wrap_provider(complete, policy, *, case, snapshot, graph):
    """Retain native dispatch capabilities and guard every schema-repair call."""
    def guard():
        validate_context(policy, case, snapshot, graph)
    def guarded(messages, max_output_tokens):
        guard()
        response = complete(messages, max_output_tokens)
        guard()
        return response
    if callable(getattr(complete, 'complete_structured', None)):
        def structured(messages, *, max_output_tokens, schema):
            guard()
            response = complete.complete_structured(messages, max_output_tokens=max_output_tokens, schema=schema)
            guard()
            return response
        guarded.complete_structured = structured
    if callable(getattr(complete, 'complete_json', None)):
        def complete_json(messages, max_output_tokens=2048):
            guard()
            response = complete.complete_json(messages, max_output_tokens=max_output_tokens)
            guard()
            return response
        guarded.complete_json = complete_json
    return guarded


def validate_verification(value, annotation, evidence, *, critic_enabled):
    from issueintel.annotations.public_inputs import MODEL_INPUT_PROJECTION_VERSION
    from issueintel.annotations.structured import _validate_wire_schema, critic_schema
    from .verifier import verify_hypotheses, validate_critique
    structural = verify_hypotheses(annotation, evidence)
    if not isinstance(value, dict):
        raise ValueError('Shared product verifier must retain its native result')
    if not critic_enabled:
        if not _same(value, structural):
            raise ValueError('Shared structural verification differs from its source')
        return
    expected = {'kind', 'reports', 'reviews', 'model_input_projection',
                'independent_human', 'same_model_bias_possible'}
    if (set(value) != expected or value.get('kind') != 'llm_separate_pass'
            or not _same(value.get('reports'), structural['reports'])
            or value.get('model_input_projection') != MODEL_INPUT_PROJECTION_VERSION
            or value.get('independent_human') is not False or value.get('same_model_bias_possible') is not True):
        raise ValueError('Shared model verification changed its native structural result')
    response = {'reviews': value['reviews']}
    _validate_wire_schema(response, critic_schema([row['id'] for row in annotation['hypotheses']],
                                                 [row['id'] for row in evidence]))
    validate_critique(response, annotation, evidence)
