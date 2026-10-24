"""Explicit controls for registered strict-DEV component experiments.

The seal binds included policy bytes; it is not spending or evaluation authority.
The benchmark harness must separately replay its registration and authorization.
Normal product callers do not select this recipe.
"""
from copy import deepcopy
import re

from issueintel.runtime.io import digest


SCHEMA = 'controlled_pipeline_ablation_v1'
TOTAL_INITIAL_CODE = 12
FIELDS = {'schema', 'registration_sha256', 'arm_id', 'retrieval_mode', 'history_mode',
          'independent_critic', 'counter_search', 'top_k', 'graph_expand',
          'max_output_tokens', 'sha256'}


def build_policy(registration_sha256, arm_id, *, graph=False, history=False,
                 verifier=False, counter_search=False, max_output_tokens=2048):
    """Build the fixed allocation recipe, without authorizing any execution."""
    if any(type(value) is not bool for value in (graph, history, verifier, counter_search)):
        raise ValueError('Experimental component switches must be booleans')
    value = {'schema': SCHEMA, 'registration_sha256': registration_sha256, 'arm_id': arm_id,
             'retrieval_mode': 'bm25_symbol_graph' if graph else 'bm25_symbol',
             'history_mode': 'verified_issue_openings' if history else 'none',
             'independent_critic': verifier, 'counter_search': counter_search,
             'top_k': 8 if graph else TOTAL_INITIAL_CODE, 'graph_expand': 4 if graph else 0,
             'max_output_tokens': max_output_tokens}
    value['sha256'] = digest(value)
    return validate_policy(value)


def validate_policy(value):
    """Return an owned copy; reject rehashed unsupported allocations/options."""
    if (not isinstance(value, dict) or set(value) != FIELDS or value['schema'] != SCHEMA
            or not isinstance(value['registration_sha256'], str)
            or not re.fullmatch('[a-f0-9]{64}', value['registration_sha256'])
            or not isinstance(value['arm_id'], str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}', value['arm_id'])
            or not isinstance(value['retrieval_mode'], str) or value['retrieval_mode'] not in {'bm25_symbol', 'bm25_symbol_graph'}
            or not isinstance(value['history_mode'], str) or value['history_mode'] not in {'none', 'verified_issue_openings'}
            or type(value['independent_critic']) is not bool or type(value['counter_search']) is not bool
            or type(value['top_k']) is not int or type(value['graph_expand']) is not int
            or type(value['max_output_tokens']) is not int or not 1 <= value['max_output_tokens'] <= 8192
            or value['sha256'] != digest({k: v for k, v in value.items() if k != 'sha256'})):
        raise ValueError('Invalid controlled pipeline experiment policy')
    graph = value['retrieval_mode'] == 'bm25_symbol_graph'
    if value['top_k'] != (8 if graph else TOTAL_INITIAL_CODE) or value['graph_expand'] != (4 if graph else 0):
        raise ValueError('Experimental retrieval must retain the fixed total allocation of twelve')
    return deepcopy(value)
