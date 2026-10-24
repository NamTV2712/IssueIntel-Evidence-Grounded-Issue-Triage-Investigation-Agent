"""Bounded hypothesis-focused search over the same verified snapshot only."""
from copy import deepcopy
import hashlib
import re

from issueintel.annotations.public_inputs import model_hypotheses, model_evidence
from issueintel.repository.snapshot import source_lines
from .search import retrieve_evidence


POLICY = 'hypothesis_focused_retrieval_v1'
COUNTER_POLICY = 'falsification_focused_retrieval_v1'
MAX_QUERIES = 3
MAX_ADDED = 6


def _replay(record, snapshot, files):
    model_evidence([record])
    match = re.fullmatch(r'(.+):(\d+)-(\d+)', record['locator'])
    if (record['kind'] != 'code' or not match or match[1] not in files
            or record['revision'] != snapshot.commit_sha
            or record['provenance'].get('snapshot_sha256') != snapshot.snapshot_sha256
            or record['provenance'].get('protocol') != snapshot.protocol
            or record.get('available_at') != snapshot.availability.get('available_at')):
        raise ValueError('Focused evidence differs from selected snapshot')
    start,end = int(match[2]),int(match[3])
    lines = source_lines(files[match[1]].text)
    text = '\n'.join(lines[start-1:end])
    if (not 1<=start<=end<=len(lines) or text != record['text']
            or len(text)>1600 or hashlib.sha256(text.encode()).hexdigest()!=record['sha256']):
        raise ValueError('Focused excerpt differs from exact snapshot lines')
    return match[1],start,end


def expand_for_hypotheses(hypotheses, evidence, snapshot, graph, *, exclude_query_sha256s=None,
                          query_mode='statement_and_falsification'):
    """Search new hypothesis queries; exclusions are local, never prompt input.

    The omitted default preserves the original one-round trace byte for byte.
    Planned investigations pass the already executed query hashes and receive
    explicit records for skipped queries without executing those searches.
    """
    if query_mode not in {'statement_and_falsification', 'falsification_only'}:
        raise ValueError('Unsupported focused retrieval query mode')
    counter = query_mode == 'falsification_only'
    policy = COUNTER_POLICY if counter else POLICY
    track_queries=exclude_query_sha256s is not None
    if exclude_query_sha256s is None:
        exclude_query_sha256s=()
    if (not isinstance(exclude_query_sha256s, (tuple, list, set, frozenset))
            or any(not isinstance(item, str) or not re.fullmatch(r'[a-f0-9]{64}', item)
                   for item in exclude_query_sha256s)):
        raise ValueError('Focused query exclusions must be SHA256 values')
    excluded=set(exclude_query_sha256s)
    initial_exclusions=sorted(excluded)
    snapshot.verify()
    if graph.snapshot_sha256!=snapshot.snapshot_sha256 or graph.commit_sha!=snapshot.commit_sha:
        raise ValueError('Focused graph belongs to another snapshot')
    projected = model_hypotheses(hypotheses)
    model_evidence(evidence)
    files = {file.path:file for file in snapshot.files}
    covered = [_replay(record,snapshot,files) for record in evidence if record['kind']=='code']
    ids = {record['id'] for record in evidence}
    added,queries,skipped = [],[],[]
    selected = ([hypothesis for hypothesis in projected if hypothesis['counter_status'] == 'not_found']
                if counter else projected)
    for hypothesis in selected[:MAX_QUERIES]:
        if len(added)==MAX_ADDED:
            break
        query = (hypothesis['falsification_test'][:600] if counter else
                 hypothesis['statement'][:600]+'\n'+hypothesis['falsification_test'][:600])
        query_hash=hashlib.sha256(query.encode()).hexdigest()
        if track_queries and query_hash in excluded:
            skipped.append({'seed_hypothesis_id':hypothesis['id'],'query_sha256':query_hash,
                            'reason':'already_executed','executed':False})
            continue
        records = retrieve_evidence(query,snapshot,graph,top_k=3,expand=0)
        entry = {'seed_hypothesis_id':hypothesis['id'],'query':query,
                 'query_sha256':query_hash,
                 'returned_ids':[],'added_ids':[],
                 'purpose':'unverified_falsification_search' if counter else 'unverified_hypothesis_focus'}
        for record in records:
            path,start,end = _replay(record,snapshot,files)
            entry['returned_ids'].append(record['id'])
            if record['id'] in ids or any(path==p and max(start,a)<=min(end,b) for p,a,b in covered):
                continue
            copied = deepcopy(record)
            copied['provenance'].update(search_stage=policy,seed_hypothesis_id=hypothesis['id'],
                                        counter_evidence_confirmed=False)
            added.append(copied);ids.add(record['id']);covered.append((path,start,end))
            entry['added_ids'].append(record['id'])
            if len(added)==MAX_ADDED:
                break
        queries.append(entry)
        if track_queries:
            excluded.add(query_hash)
    result={'policy':policy,'max_queries':MAX_QUERIES,'max_added_evidence':MAX_ADDED,
                 'queries':queries,'added_ids':[record['id'] for record in added],
                 'stop_reason':'new_candidates_added' if added else 'no_new_evidence',
                 'counter_evidence_confirmed':False,'snapshot_sha256':snapshot.snapshot_sha256}
    if track_queries:
        result.update(excluded_query_sha256s=initial_exclusions,skipped_queries=skipped)
        if not queries:
            result['stop_reason']='no_new_queries'
    return added,result
