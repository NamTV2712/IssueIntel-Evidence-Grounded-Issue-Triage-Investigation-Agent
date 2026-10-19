"""Bounded gap-directed searches over already admitted product sources.

Actions describe searches, not confirmations of a hypothesis. Callers are exact
static incoming call edges; documentation and changelog searches use only the
pinned snapshot. History can inspect current repository-context excerpts already
bound to receipts, but never fetches a source or certifies historical availability.
"""
from collections import Counter
from copy import deepcopy
import hashlib
from pathlib import PurePosixPath
import re

from issueintel.annotations.public_inputs import model_evidence, model_hypotheses
from issueintel.repository.snapshot import source_lines
from issueintel.runtime.io import digest
from .focused import _replay
from .search import tokens


POLICY = 'gap_directed_retrieval_v1'
MAX_QUERIES = 3
MAX_ADDED = 6
PURPOSES = ('counter', 'caller', 'documentation', 'history', 'support')
_HASH = re.compile('[a-f0-9]{64}')
_SPAN = re.compile(r'(.+):(\d+)-(\d+)')
_REASONS = {
    'counter': ('missing_counter_evidence', 'Search the proposed falsification condition; matches are not confirmed counter-evidence.'),
    'caller': ('uninspected_callers', 'Inspect exact static callers of symbols overlapping cited code; execution remains unknown.'),
    'documentation': ('uninspected_documentation', 'Search documentation in the same pinned snapshot for applicable behavior and constraints.'),
    'history': ('uninspected_history', 'Inspect admitted receipt-bound context and pinned changelog text; historical availability remains unverified.'),
    'support': ('support_scope_incomplete', 'Search the hypothesis statement for additional same-snapshot code; lexical relevance is not semantic support.'),
}
_UNAVAILABLE = {
    'caller': 'no_exact_callers_in_available_graph',
    'documentation': 'no_documentation_in_snapshot',
    'history': 'no_history_in_available_sources',
}


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _descriptor(value):
    return value.to_dict() if hasattr(value, 'to_dict') else value


def _exclusions(value):
    if value is None:
        return set()
    if (not isinstance(value, (tuple, list, set, frozenset)) or len(value) > 10
            or any(not isinstance(item, str) or not _HASH.fullmatch(item) for item in value)):
        raise ValueError('Gap query exclusions require at most ten SHA256 values')
    return set(value)


def _span(record):
    match = _SPAN.fullmatch(record['locator'])
    if not match or not 1 <= int(match[2]) <= int(match[3]):
        raise ValueError('Gap code source needs an exact line span')
    return match[1], int(match[2]), int(match[3])


def _documentation(path):
    name = PurePosixPath(path).name.casefold()
    return (PurePosixPath(path).suffix.casefold() in {'.md', '.rst', '.txt', '.adoc'}
            or name.startswith(('readme', 'documentation')))


def _history(path):
    name = PurePosixPath(path).name.casefold()
    return (name.startswith(('changelog', 'changes', 'history', 'news', 'release_notes', 'release-notes'))
            or any(part.casefold() in {'changelog', 'changelogs', 'release_notes', 'releases', 'newsfragments'}
                   for part in PurePosixPath(path).parts[:-1]))


def _code(path):
    return not _documentation(path) and not _history(path)


def _targets(source_ids, known, graph):
    spans = [_span(known[item]) for item in source_ids if known[item]['kind'] == 'code']
    return sorted(node['id'] for node in graph['nodes'] if node['kind'] in {'function', 'class'}
                  and any(node['path'] == path and max(node['start_line'], start) <= min(node['end_line'], end)
                          for path, start, end in spans))[:32]


def _all_specs(hypotheses, evidence, graph, excluded):
    projected = model_hypotheses(hypotheses)
    model_evidence(evidence)
    known = {row['id']: row for row in evidence}
    graph = _descriptor(graph)
    if not projected:
        return []
    # One source-query hash precedes the action history in a Planned run. Each
    # attempted action, including unavailable scopes, rotates the purpose order.
    # Changed model text therefore cannot keep history/support behind round one.
    offset = max(0, len(excluded) - 1) % len(PURPOSES)
    purposes = PURPOSES[offset:] + PURPOSES[:offset]
    hypothesis_offset = (max(0, len(excluded) - 1) // MAX_QUERIES) % len(projected)
    ordered = projected[hypothesis_offset:] + projected[:hypothesis_offset]
    result = []
    for purpose in purposes:
        for hypothesis in ordered:
            if purpose == 'counter' and hypothesis['counter_status'] != 'not_found':
                continue
            ids = list(dict.fromkeys(hypothesis['support_ids'] + hypothesis['counter_ids']))
            if any(item not in known for item in ids):
                raise ValueError('Gap hypothesis cites an unavailable source')
            gap, reason = _REASONS[purpose]
            if purpose == 'support' and not any(known[item]['kind'] == 'code' for item in hypothesis['support_ids']):
                gap = 'missing_code_support'
            query = purpose + '\n' + (hypothesis['falsification_test'][:600] if purpose == 'counter'
                                        else hypothesis['statement'][:600])
            if purpose in {'caller', 'documentation', 'history'}:
                query += '\n' + hypothesis['falsification_test'][:300]
            targets = _targets(ids, known, graph) if purpose == 'caller' else []
            if purpose == 'caller':
                # Repeating text can name a different graph operation after
                # generation cites another callee. Bind the execution history
                # to those targets rather than suppressing the new operation.
                query += '\ntargets_sha256:' + digest(targets)
            spec = {'purpose': purpose, 'gap_code': gap, 'reason': reason,
                    'seed_hypothesis_id': hypothesis['id'], 'query': query,
                    'query_sha256': _hash(query), 'source_ids': ids,
                    'target_node_ids': targets}
            result.append({'action_id': 'a_' + digest(spec)[:20], **spec})
    return result


def _selection(hypotheses, evidence, graph, excluded):
    candidates = _all_specs(hypotheses, evidence, graph, excluded)
    selected, skipped, selected_purposes, skipped_hashes = [], [], set(), set()
    # Prefer different gap types before giving a second hypothesis the same
    # action; repeated query hashes are recorded and never executed again.
    for spec in candidates:
        if spec['query_sha256'] in excluded:
            if spec['query_sha256'] not in skipped_hashes:
                skipped.append({key: spec[key] for key in ('action_id', 'purpose', 'seed_hypothesis_id', 'query_sha256')}
                               | {'reason': 'already_executed', 'executed': False})
                skipped_hashes.add(spec['query_sha256'])
        elif spec['purpose'] not in selected_purposes:
            selected.append(spec); selected_purposes.add(spec['purpose'])
        if len(selected) == MAX_QUERIES:
            break
    if len(selected) < MAX_QUERIES:
        hashes = {row['query_sha256'] for row in selected}
        for spec in candidates:
            if spec['query_sha256'] not in excluded | hashes:
                selected.append(spec); hashes.add(spec['query_sha256'])
                if len(selected) == MAX_QUERIES:
                    break
    return selected, skipped


def action_specs(hypotheses, evidence, graph, *, exclude_query_sha256s=None):
    """Return at most three deterministic typed actions, without IO or search."""
    return _selection(hypotheses, evidence, graph, _exclusions(exclude_query_sha256s))[0]


def _record(snapshot, path, start, end, text, *, retrieval, score=0):
    locator = f'{path}:{start}-{end}'
    text_sha = _hash(text)
    return {'id': 'e_' + _hash(f'{snapshot.snapshot_sha256}:{locator}:{text_sha}')[:20],
            'kind': 'code', 'locator': locator, 'text': text, 'sha256': text_sha,
            'revision': snapshot.commit_sha, 'available_at': snapshot.availability.get('available_at'),
            'provenance': {'snapshot_sha256': snapshot.snapshot_sha256, 'repository': snapshot.repository,
                           'protocol': snapshot.protocol, 'retrieval': retrieval,
                           'excerpt_limit_characters': 1600, 'score': round(score, 6), 'score_is_probability': False}}


def _chunks(lines, start=0, end=None):
    end = len(lines) if end is None else end
    for offset in range(start, end, 15):
        excerpt, length, actual = [], 0, offset
        for index in range(offset, min(offset + 40, end)):
            line = lines[index]
            if len(line) > 1600:
                if excerpt:
                    break
                actual = index + 1
                continue
            if excerpt and length + len(line) + 1 > 1600:
                break
            excerpt.append(line); length += len(line) + 1
        text = '\n'.join(excerpt)
        if text.strip():
            yield actual + 1, actual + len(excerpt), text


def _lexical(spec, snapshot):
    purpose = spec['purpose']
    predicate = _documentation if purpose == 'documentation' else _history if purpose == 'history' else _code
    query_words = set(tokens(spec['query'].split('\n', 1)[1]))
    ranked = []
    for file in snapshot.files:
        if not predicate(file.path):
            continue
        for start, end, text in _chunks(source_lines(file.text)):
            words = Counter(tokens(text))
            score = sum(min(words[word], 3) for word in query_words) + 2 * len(set(tokens(file.path)) & query_words)
            if score:
                ranked.append((-score, file.path, start, end, text))
    result, spans = [], []
    for negative, path, start, end, text in sorted(ranked):
        if any(path == p and max(start, a) <= min(end, b) for p, a, b in spans):
            continue
        result.append(_record(snapshot, path, start, end, text, retrieval='gap_' + purpose + '_lexical', score=-negative))
        spans.append((path, start, end))
        if len(result) == 3:
            break
    return result


def _caller_candidates(spec, snapshot, graph):
    nodes = {node['id']: node for node in graph['nodes']}
    targets = set(spec['target_node_ids'])
    callers = {}
    for edge in graph['edges']:
        if (edge['kind'] == 'calls' and edge['resolution'] == 'exact' and edge['target'] in targets
                and edge['source'] in nodes):
            callers.setdefault(edge['source'], set()).add(edge['target'])
    files = {file.path: file for file in snapshot.files}
    result = []
    for identifier in sorted(callers):
        node = nodes[identifier]
        chunks = _chunks(source_lines(files[node['path']].text), node['start_line'] - 1, node['end_line'])
        first = next(chunks, None)
        if first is None:
            continue
        start, end, text = first
        record = _record(snapshot, node['path'], start, end, text, retrieval='exact_static_caller')
        record['provenance'].update(caller_node_id=identifier, caller_target_node_ids=sorted(callers[identifier]),
                                    caller_relation='exact_static_call', execution_observed=False)
        if record['id'] in {row['id'] for row in result}:
            continue
        result.append(record)
        if len(result) == 3:
            break
    return result, bool(callers)


def _history_context(evidence):
    result = []
    for record in evidence:
        if record['kind'] != 'repository_context':
            continue
        proof = record['provenance']
        start, end = proof.get('start_character'), proof.get('end_character')
        if (any(not isinstance(proof.get(key), str) or not _HASH.fullmatch(proof[key])
                for key in ('source_record_sha256', 'receipt_sha256'))
                or not isinstance(proof.get('source_key'), str) or not proof['source_key']
                or type(start) is not int or type(end) is not int or not 0 <= start < end
                or end - start != len(record['text']) or len(record['text']) > 1200
                or _hash(record['text']) != record['sha256']
                or record.get('revision') is not None or record.get('available_at') is not None
                or proof.get('historical_verified') is not False
                or proof.get('causal_correctness') != 'unverified'):
            raise ValueError('History inspection requires an admitted receipt-bound context excerpt')
        result.append(record['id'])
    return result


def _provenance(spec, inspected):
    return {'search_stage': POLICY, 'seed_hypothesis_id': spec['seed_hypothesis_id'],
            'counter_evidence_confirmed': False, 'gap_action_id': spec['action_id'],
            'gap_purpose': spec['purpose'], 'gap_code': spec['gap_code'],
            'gap_source_ids': spec['source_ids'], 'gap_target_node_ids': spec['target_node_ids'],
            'history_inspected_ids': inspected}


def expand_for_gaps(hypotheses, evidence, snapshot, graph, *, exclude_query_sha256s=None):
    """Execute bounded typed actions; never acquire network or new history."""
    excluded = _exclusions(exclude_query_sha256s)
    snapshot.verify()
    if graph.snapshot_sha256 != snapshot.snapshot_sha256 or graph.commit_sha != snapshot.commit_sha:
        raise ValueError('Gap graph belongs to another snapshot')
    graph_data = _descriptor(graph)
    files = {file.path: file for file in snapshot.files}
    covered = [_replay(row, snapshot, files) for row in evidence if row['kind'] == 'code']
    specs, skipped = _selection(hypotheses, evidence, graph_data, excluded)
    added, queries = [], []
    ids = {row['id'] for row in evidence}
    for spec in specs:
        purpose = spec['purpose']
        inspected = _history_context(evidence) if purpose == 'history' else []
        if purpose == 'caller':
            records, caller_edges = _caller_candidates(spec, snapshot, graph_data)
        else:
            records, caller_edges = _lexical(spec, snapshot), False
        unavailable = (purpose == 'caller' and not records
                       or purpose == 'documentation' and not any(_documentation(path) for path in files)
                       or purpose == 'history' and not inspected and not any(_history(path) for path in files))
        unavailable_reason = ('no_matching_snapshot_evidence' if purpose == 'caller' and caller_edges and not records
                              else _UNAVAILABLE[purpose] if unavailable else None)
        entry = {**spec, 'status': 'unavailable' if unavailable else 'executed',
                 'unavailable_reason': unavailable_reason,
                 'inspected_ids': inspected, 'returned_ids': [], 'added_ids': []}
        for record in records:
            path, start, end = _replay(record, snapshot, files)
            entry['returned_ids'].append(record['id'])
            if record['id'] in ids or any(path == p and max(start, a) <= min(end, b) for p, a, b in covered):
                continue
            copied = deepcopy(record)
            copied['provenance'].update(_provenance(spec, inspected))
            added.append(copied); ids.add(record['id']); covered.append((path, start, end))
            entry['added_ids'].append(record['id'])
            if len(added) == MAX_ADDED:
                break
        queries.append(entry)
        if len(added) == MAX_ADDED:
            break
    return added, {'policy': POLICY, 'max_queries': MAX_QUERIES, 'max_added_evidence': MAX_ADDED,
                   'queries': queries, 'added_ids': [row['id'] for row in added],
                   'stop_reason': 'new_candidates_added' if added else 'no_new_evidence' if queries else 'no_new_queries',
                   'counter_evidence_confirmed': False, 'snapshot_sha256': snapshot.snapshot_sha256,
                   'excluded_query_sha256s': sorted(excluded), 'skipped_queries': skipped}


def _ids(value, scope=None):
    if (not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value)
            or len(value) != len(set(value)) or scope is not None and not set(value) <= set(scope)):
        raise ValueError('Invalid gap source IDs')
    return value


def _saved_code(record, source, snapshot, graph):
    path, start, end = _span(record)
    provenance = record['provenance']
    file_nodes = {row['path']: row for row in graph['nodes'] if row['kind'] == 'file'}
    file_node = file_nodes.get(path)
    if (record['kind'] != 'code' or record['revision'] != source['commit_sha']
            or provenance.get('snapshot_sha256') != source['snapshot_sha256']
            or provenance.get('protocol') != snapshot['protocol']
            or record.get('available_at') != snapshot['availability'].get('available_at')
            or _hash(record['text']) != record['sha256'] or not 0 < len(record['text']) <= 1600
            or end - start + 1 != len(record['text'].split('\n'))
            or path not in {row['path'] for row in snapshot['files']}
            or not file_node or not file_node['start_line'] <= start <= end <= file_node['end_line']
            or record['id'] != 'e_' + _hash(f"{source['snapshot_sha256']}:{record['locator']}:{record['sha256']}")[:20]):
        raise ValueError('Gap candidate differs from exact snapshot source binding')
    return path, start, end


def validate_search(search, hypotheses, current, added, known, source, used, graph, snapshot):
    """Replay action identity/scope/provenance from saved descriptors only.

    Full Git-byte replay belongs to admission. This checks included-byte and
    graph relations without fetching Git or claiming semantic sufficiency.
    ``used`` is extended only after the whole round has validated.
    """
    try:
        return _validate_search(search, hypotheses, current, added, known, source, used, graph, snapshot)
    except (KeyError, TypeError, AttributeError, IndexError):
        raise ValueError('Invalid saved gap-directed search structure') from None


def _validate_search(search, hypotheses, current, added, known, source, used, graph, snapshot):
    fields = {'policy', 'max_queries', 'max_added_evidence', 'queries', 'added_ids', 'stop_reason',
              'counter_evidence_confirmed', 'snapshot_sha256', 'excluded_query_sha256s', 'skipped_queries'}
    excluded = _exclusions(used)
    graph, snapshot = _descriptor(graph), _descriptor(snapshot)
    if (not isinstance(search, dict) or set(search) != fields or search['policy'] != POLICY
            or type(search['max_queries']) is not int or search['max_queries'] != MAX_QUERIES
            or type(search['max_added_evidence']) is not int or search['max_added_evidence'] != MAX_ADDED
            or search['counter_evidence_confirmed'] is not False
            or search['snapshot_sha256'] != source['snapshot_sha256']
            or graph['snapshot_sha256'] != source['snapshot_sha256'] or graph['commit_sha'] != source['commit_sha']
            or snapshot['snapshot_sha256'] != source['snapshot_sha256'] or snapshot['commit_sha'] != source['commit_sha']
            or search['excluded_query_sha256s'] != sorted(excluded)):
        raise ValueError('Gap search policy or source binding differs')
    _ids(current, known); _ids(added, known)
    if set(current) & set(added) or len(added) > MAX_ADDED:
        raise ValueError('Gap search repeated evidence or exceeded bound')
    evidence = [known[item] for item in current]
    expected, skipped = _selection(hypotheses, evidence, graph, excluded)
    queries = search['queries']
    if (not isinstance(queries, list) or len(queries) > len(expected)
            or len(queries) < len(expected) and len(added) != MAX_ADDED
            or search['skipped_queries'] != skipped):
        raise ValueError('Gap action ordering or skipped query history differs')
    query_added, new_hashes = [], set()
    for entry, spec in zip(queries, expected):
        if (not isinstance(entry, dict) or set(entry) != set(spec) | {
                'status', 'unavailable_reason', 'inspected_ids', 'returned_ids', 'added_ids'}
                or any(entry[key] != value for key, value in spec.items())
                or entry['query_sha256'] in excluded | new_hashes):
            raise ValueError('Gap action identity, query, rationale or sources differ')
        new_hashes.add(entry['query_sha256'])
        purpose = spec['purpose']
        inspected = _history_context(evidence) if purpose == 'history' else []
        if entry['inspected_ids'] != inspected:
            raise ValueError('Gap history inspection differs from admitted receipt-bound sources')
        nodes = {node['id']: node for node in graph['nodes']}
        edges = [edge for edge in graph['edges'] if edge['kind'] == 'calls' and edge['resolution'] == 'exact'
                 and edge['target'] in spec['target_node_ids'] and edge['source'] in nodes]
        unavailable = (purpose == 'caller' and not edges
                       or purpose == 'documentation' and not any(_documentation(row['path']) for row in snapshot['files'])
                       or purpose == 'history' and not inspected and not any(_history(row['path']) for row in snapshot['files']))
        # A caller can have exact edges but no representable <=1600-character
        # line excerpt. Its path may be absent from the projected saved graph;
        # this explicit unknown cannot be replayed as a global no-caller claim.
        bounded_caller_unknown = (purpose == 'caller' and entry['status'] == 'unavailable'
                                  and entry['unavailable_reason'] == 'no_matching_snapshot_evidence')
        if not bounded_caller_unknown and (entry['status'] != ('unavailable' if unavailable else 'executed')
                or entry['unavailable_reason'] != (_UNAVAILABLE[purpose] if unavailable else None)):
            raise ValueError('Gap action availability differs from available source scope')
        returned = _ids(entry['returned_ids']); accepted = _ids(entry['added_ids'], returned)
        if len(returned) > 3 or not set(accepted) <= set(added) or (unavailable or bounded_caller_unknown) and returned:
            raise ValueError('Gap action candidates exceed admitted scope or bounds')
        for identifier in accepted:
            record = known[identifier]; path, start, end = _saved_code(record, source, snapshot, graph)
            proof = record['provenance']
            if any(proof.get(key) != value for key, value in _provenance(spec, inspected).items()):
                raise ValueError('Gap candidate action provenance differs')
            if purpose in {'counter', 'support'} and (not _code(path) or proof.get('retrieval') != 'gap_' + purpose + '_lexical'):
                raise ValueError('Gap code search returned an out-of-scope document')
            if purpose in {'documentation', 'history'}:
                predicate = _documentation if purpose == 'documentation' else _history
                if not predicate(path) or proof.get('retrieval') != 'gap_' + purpose + '_lexical':
                    raise ValueError('Gap document search returned an out-of-scope file')
            if purpose == 'caller':
                caller = nodes.get(proof.get('caller_node_id'))
                targets = sorted({edge['target'] for edge in edges if edge['source'] == proof.get('caller_node_id')})
                if (not caller or not targets or proof.get('caller_target_node_ids') != targets
                        or caller['path'] != path or not caller['start_line'] <= start <= end <= caller['end_line']
                        or proof.get('retrieval') != 'exact_static_caller'
                        or proof.get('caller_relation') != 'exact_static_call' or proof.get('execution_observed') is not False):
                    raise ValueError('Gap caller lacks the exact incoming static call relation')
        query_added.extend(accepted)
    if query_added != added or search['added_ids'] != added:
        raise ValueError('Gap action added inventory differs')
    stop = 'new_candidates_added' if added else 'no_new_evidence' if queries else 'no_new_queries'
    if search['stop_reason'] != stop:
        raise ValueError('Gap search stop reason differs')
    used.update(new_hashes)
    return stop
