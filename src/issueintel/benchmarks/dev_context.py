"""Public investigation contexts from the original accepted Main Dev only.

Recovery checkpoints supplement the immutable frozen cohort; they are not a
new freeze, an evaluation authorization, or proof of global search exhaustion.
Eligibility is replayed afresh for the selected case before inference.
"""
from pathlib import Path
import hashlib
import json
import re

from issueintel.runtime.io import digest
from issueintel.repository.cache import load_snapshot
from .strict_dev import load_pool, _checked, verify_eligibility_receipt
from .strict_recovery import replay_recovery_receipt, recovered_query_text
from .temporal import reconstruct_query_text

PARENT = 'cbb62d3f6e9fed9488cdc6e4a301790a28d633d9583d7c29e40935f0c669216a'


def _read(path):
    return json.loads(path.read_bytes())


def development_catalog(root):
    root = Path(root).resolve()
    path = root/'data/development/cohorts'/f'{PARENT}.json'
    if not path.exists():
        return {'cases': [], 'accepted': 0, 'pending': None, 'original_total': None,
                'frozen_count': 0, 'checkpoint_sha256': None, 'evaluation_authorized': False}
    parent = _read(path)
    if (digest(parent) != PARENT or parent.get('evaluation_authorized') is not False
            or parent.get('name') != 'main-dev-strict-v1'):
        raise ValueError('Frozen Main Dev identity changed')
    authority = load_pool(root, parent['pool_sha256'])
    candidates = {x['case']['case_id']: x for x in authority['candidates']}
    members = {x['case_id']: dict(x, origin='frozen') for x in parent['members']}
    exclusions = {x['case_id'] for x in parent['exclusions']}
    if (len(candidates) != 400 or len(members) != parent['accepted_count']
            or list(members) != parent['ordered_case_ids'] or set(members) & exclusions
            or set(members) | exclusions != set(candidates)
            or any(x['case']['split'] != 'main_dev' for x in candidates.values())):
        raise ValueError('Original 400 Main Dev partition changed')
    checkpoint_hash = None
    pointer_path = root/'artifacts/strict_recovery/CHECKPOINT.json'
    if pointer_path.exists():
        pointer = _read(pointer_path)
        reference = pointer['immutable_checkpoint']
        checkpoint = _read(_checked(root, reference))
        if (checkpoint.get('parent_cohort_sha256') != PARENT
                or checkpoint.get('original_main_dev_total') != 400
                or checkpoint.get('evaluation_authorized') is not False
                or checkpoint.get('gold_used') is not False
                or checkpoint.get('llm_calls') != 0):
            raise ValueError('Recovery checkpoint is not the original public Main Dev audit')
        checkpoint_hash = reference['sha256']
        entries = {x['case_id']: x for x in checkpoint['entries']}
        if len(entries) != len(checkpoint['entries']) or set(entries) != exclusions:
            raise ValueError('Recovery partition changed')
        for cid, entry in entries.items():
            if entry['status'] == 'accepted_recovery_replayed':
                replay = entry['selected_recovery']
                if replay.get('case_id') != cid or replay.get('replayed') is not True:
                    raise ValueError('Recovery member is not a replayed original case')
                members[cid] = {'case_id': cid, 'receipt': replay['receipt'], 'origin': 'recovery'}
        if (len(members) != checkpoint['total_proof_verified']
                or 400-len(members) != checkpoint['remaining_pending']):
            raise ValueError('Recovery count differs from its exact membership')
    rows = []
    for cid in sorted(members):
        case = candidates[cid]['case']
        rows.append({'case_id': cid, 'repository': case['repository'],
                     'summary': case['problem_statement'].splitlines()[0][:160],
                     'proof_origin': members[cid]['origin']})
    return {'cases': rows, 'accepted': len(rows), 'pending': 400-len(rows),
            'original_total': 400, 'frozen_count': parent['accepted_count'],
            'checkpoint_sha256': checkpoint_hash, 'evaluation_authorized': False,
            '_members': members, '_candidates': candidates, '_authority': authority,
            '_parent': parent}


def public_catalog(root):
    return {key: value for key, value in development_catalog(root).items() if not key.startswith('_')}


def accepted_context(root, case_id):
    """Reject pending/holdout/Live before reading a query, snapshot or provider."""
    if not isinstance(case_id, str) or not re.fullmatch(r'verified:[A-Za-z0-9_./-]+', case_id):
        raise ValueError('Select an accepted original Main Dev case')
    root = Path(root).resolve()
    catalog = development_catalog(root)
    if case_id not in catalog.get('_members', {}):
        raise ValueError('Only accepted original Main Dev is available here')
    member = catalog['_members'][case_id]
    candidate = catalog['_candidates'][case_id]
    case = candidate['case']
    if member['origin'] == 'recovery':
        replay_recovery_receipt(root, PARENT, candidate, member['receipt'], catalog['_authority'])
        record = _read(_checked(root, member['receipt']))
    else:
        record = verify_eligibility_receipt(root, case, member['receipt'], catalog['_parent']['pool_sha256'])
    proof = _read(_checked(root, record['proof']))
    query = (recovered_query_text(case, proof) if member['origin'] == 'recovery'
             else reconstruct_query_text(case, proof))
    if hashlib.sha256(query.encode()).hexdigest() != record['query_sha256']:
        raise ValueError('Opening query bytes changed')
    asset = _read(_checked(root, record['snapshot']))
    snapshot = load_snapshot(_checked(root, asset['index']), root/'data/derived')
    receipts = proof.get('issue_receipts') or proof.get('current_issue_receipts') or []
    urls = [x['source_url'] for x in receipts if isinstance(x.get('source_url'), str)]
    if not urls:
        urls = [x['source_url'] for x in proof.get('opening_matches', [])]
    runtime_case = dict(case, revision=snapshot.commit_sha, issue_created_at=snapshot.cutoff,
                        problem_statement=query)
    query_receipt = {'schema': 'main_dev_original_query_receipt_v1', 'verified': True,
                     'case_id': case_id, 'created_at': snapshot.cutoff,
                     'text_sha256': record['query_sha256'],
                     'strict_dev_proof_sha256': record['proof']['sha256'], 'source_urls': urls}
    return runtime_case, snapshot, query_receipt
