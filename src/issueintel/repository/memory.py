"""Revision-bound architecture observations, not inferred runtime knowledge.

The source snapshot is required for replay. Storage/interfaces can retain it
with the existing deduplicated snapshot cache; a memory record does not copy a
repository or acquire new sources. Investigation lessons use a separate truth
level and must never be mixed into these deterministic AST observations.
"""
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import re

from issueintel.runtime.io import digest
from .graph import build_graph
from .snapshot import source_lines


SCHEMA = 'repository_architecture_memory_v1'
MAX_FACTS = 1000
LIMITATIONS = [
    'Definitions are observed syntax; calls/imports/inheritance are static references, not runtime execution.',
    'Only exact resolved relationships are retained; unresolved and dynamic references remain unknown.',
    'Omitted facts, parse errors and skipped files do not prove that an entity or relationship is absent.',
    'A matching dependency hash does not alone revalidate relationship resolution in a different revision.',
    'Historical availability requires the complete independently verified source receipt; Git dates are not proof.',
    'Receipt authenticity remains the trusted caller responsibility; hashes prove internal consistency only.',
    'Saved replay checks captured spans and bindings; relationship revalidation uses the current graph separately.',
]


def _graph_policy():
    return sha256(Path(__file__).with_name('graph.py').read_bytes()).hexdigest()


def _node(node):
    return {key:getattr(node,key) for key in
            ('id','kind','path','name','qualname','start_line','end_line','sha256')}


def _observations(snapshot,graph):
    nodes={node.id:node for node in graph.nodes}
    files={file.path:file for file in snapshot.files}
    def sealed(kind,source,target=None,reference=None,inferred=False):
        dependencies=sorted({source.path}|({target.path} if target else set()))
        row={'kind':kind,'source':_node(source),'target':_node(target) if target else None,
             'reference':reference,'inferred':inferred,
             'truth_level':'observed_syntax' if kind=='definition' else 'static_reference',
             'dependencies':[{'path':path,'sha256':files[path].sha256} for path in dependencies]}
        return dict(row,id='memory_'+digest(row)[:24],sha256=digest(row))
    for node in sorted(graph.nodes,key=lambda node:(node.path,node.start_line,node.id)):
        if node.kind in {'class','function'}:
            yield sealed('definition',node)
    for edge in sorted(graph.edges,key=lambda edge:(edge.kind,edge.source,edge.target or '',edge.reference)):
        if (edge.kind in {'calls','imports','inherits'} and edge.resolution=='exact' and edge.target is not None):
            yield sealed(edge.kind,nodes[edge.source],nodes[edge.target],edge.reference,edge.inferred)


def build_architecture_memory(snapshot,*,max_facts=500):
    """Derive only bounded code observations; never read issue/gold/model text."""
    if type(max_facts) is not int or not 1<=max_facts<=MAX_FACTS:
        raise ValueError('Architecture memory requires a bound of 1–1000 facts')
    snapshot.verify()
    graph=build_graph(snapshot)
    buckets={kind:[] for kind in ('definition','imports','calls','inherits')}
    counts=Counter()
    for row in _observations(snapshot,graph):
        counts[row['kind']]+=1
        if len(buckets[row['kind']])<max_facts:buckets[row['kind']].append(row)
    facts=[]
    # Retain each kind in turn so a large symbol inventory cannot consume the
    # whole bound before any call/import/inheritance observation is retained.
    for index in range(max_facts):
        for rows in buckets.values():
            if index<len(rows) and len(facts)<max_facts:facts.append(rows[index])
    result={'schema':SCHEMA,'source':{'repository':snapshot.repository,
        'commit_sha':snapshot.commit_sha,'snapshot_sha256':snapshot.snapshot_sha256,
        'protocol':snapshot.protocol,'availability_sha256':digest(snapshot.availability),
        'graph_sha256':digest(graph.to_dict()),'parser_version':graph.parser_version,'graph_policy_sha256':_graph_policy()},
        'max_facts':max_facts,'facts':facts,'fact_counts':{kind:counts[kind] for kind in buckets},
        'facts_omitted':sum(counts.values())-len(facts),
        'parse_errors':list(graph.parse_errors),'skipped_files':list(snapshot.skipped),
        'runtime_verified':False,'causal_verified':False,'model_calls':0,
        'limitations':list(LIMITATIONS)}
    return dict(result,sha256=digest(result))


def validate_architecture_memory(memory,snapshot):
    """Replay captured spans, including old parser versions, without new AST/Git."""
    try:
        snapshot.verify()
        if (not isinstance(memory,dict) or set(memory)!={'schema','source','max_facts','facts','fact_counts','facts_omitted',
                'parse_errors','skipped_files','runtime_verified','causal_verified','model_calls','limitations','sha256'}
                or memory['schema']!=SCHEMA or memory['sha256']!=digest({k:v for k,v in memory.items() if k!='sha256'})
                or type(memory['max_facts']) is not int or not 1<=memory['max_facts']<=MAX_FACTS
                or not isinstance(memory['facts'],list) or len(memory['facts'])>memory['max_facts']
                or type(memory['facts_omitted']) is not int or memory['facts_omitted']<0
                or memory['runtime_verified'] is not False or memory['causal_verified'] is not False
                or type(memory['model_calls']) is not int or memory['model_calls']!=0
                or memory['limitations']!=LIMITATIONS or memory['skipped_files']!=list(snapshot.skipped)):
            raise ValueError('Invalid architecture memory envelope')
        source=memory['source']
        counts=memory['fact_counts']
        if (not isinstance(counts,dict) or set(counts)!={'definition','calls','imports','inherits'}
                or any(type(value) is not int or value<0 for value in counts.values())
                or sum(counts.values())!=len(memory['facts'])+memory['facts_omitted']):
            raise ValueError('Invalid bounded observation counts')
        expected={'repository':snapshot.repository,'commit_sha':snapshot.commit_sha,
                  'snapshot_sha256':snapshot.snapshot_sha256,'protocol':snapshot.protocol,
                  'availability_sha256':digest(snapshot.availability)}
        if (set(source)!=set(expected)|{'graph_sha256','parser_version','graph_policy_sha256'}
                or any(source[key]!=value for key,value in expected.items())
                or not isinstance(source['parser_version'],str) or not 1<=len(source['parser_version'])<=200
                or any(not isinstance(source[key],str) or not re.fullmatch('[a-f0-9]{64}',source[key])
                       for key in ('graph_sha256','graph_policy_sha256'))):
            raise ValueError('Architecture memory differs from its pinned source')
        files={file.path:file for file in snapshot.files}
        if not isinstance(memory['parse_errors'],list) or any(not isinstance(row,dict)
                or row.get('path') not in files for row in memory['parse_errors']):
            raise ValueError('Invalid recorded parse errors')
        seen=set()
        for fact in memory['facts']:
            if (not isinstance(fact,dict) or set(fact)!={'kind','source','target','reference','inferred','truth_level',
                    'dependencies','id','sha256'} or fact['kind'] not in {'definition','calls','imports','inherits'}
                    or type(fact['inferred']) is not bool):
                raise ValueError('Invalid memory observation')
            payload={k:v for k,v in fact.items() if k not in {'id','sha256'}}
            if (fact['sha256']!=digest(payload) or fact['id']!='memory_'+digest(payload)[:24] or fact['id'] in seen):
                raise ValueError('Memory observation hash or identity differs')
            seen.add(fact['id'])
            if fact['kind']=='definition':
                if (fact['target'] is not None or fact['reference'] is not None or fact['inferred']
                        or fact['truth_level']!='observed_syntax' or fact['source']['kind'] not in {'class','function'}):
                    raise ValueError('Definition is not a captured syntax observation')
            elif (fact['target'] is None or fact['truth_level']!='static_reference'
                  or not isinstance(fact['reference'],str) or not 1<=len(fact['reference'])<=100000):
                raise ValueError('Relationship is not a captured static reference')
            paths=set()
            for node in [fact['source']]+([fact['target']] if fact['target'] else []):
                if (not isinstance(node,dict) or set(node)!={'id','kind','path','name','qualname','start_line','end_line','sha256'}
                        or node['path'] not in files or node['kind'] not in {'file','class','function'}
                        or type(node['start_line']) is not int or type(node['end_line']) is not int
                        or not 1<=node['start_line']<=node['end_line']
                        or any(not isinstance(node[k],str) or not node[k] for k in ('id','name'))
                        or not isinstance(node['qualname'],str)):
                    raise ValueError('Invalid memory source span')
                file=files[node['path']];lines=source_lines(file.text);paths.add(file.path)
                if node['end_line']>max(1,len(lines)):
                    raise ValueError('Memory span exceeds its source')
                text=file.text if node['kind']=='file' else '\n'.join(lines[node['start_line']-1:node['end_line']])
                expected_id=('file:'+file.path if node['kind']=='file' else
                             f"symbol:{file.path}:{node['qualname']}:{node['start_line']}")
                if (node['sha256']!=sha256(text.encode()).hexdigest() or node['id']!=expected_id
                        or node['kind']=='file' and (node['start_line']!=1 or node['end_line']!=max(1,len(lines)))):
                    raise ValueError('Memory source span or content differs')
            if fact['dependencies']!=[{'path':path,'sha256':files[path].sha256} for path in sorted(paths)]:
                raise ValueError('Memory dependencies differ from source spans')
        if any(sum(row['kind']==kind for row in memory['facts'])>count for kind,count in counts.items()):
            raise ValueError('Saved observation counts exclude retained facts')
        return memory
    except (KeyError,TypeError,AttributeError):
        raise ValueError('Invalid architecture memory structure') from None


def _time(value):
    if not isinstance(value,str):raise ValueError('Expected a timestamp with timezone')
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Expected a timestamp with timezone')
    return result.astimezone(timezone.utc)


def historical_availability(memory,snapshot,cutoff):
    """Strict `<` gate over full source receipts, never capture/commit dates."""
    validate_architecture_memory(memory,snapshot)
    return _snapshot_availability(snapshot,cutoff)


def _snapshot_availability(snapshot,cutoff):
    snapshot.verify()
    boundary=_time(cutoff)
    availability=snapshot.availability
    receipt=availability.get('receipt')
    reason='missing_independent_availability_receipt'
    if (snapshot.protocol=='issue_time' and availability.get('historical_certified') is True
            and availability.get('status')=='caller_verified_receipt' and isinstance(receipt,dict)
            and receipt.get('verified') is True):
        # verify() binds the complete receipt to this exact revision, timestamp
        # and source. A lone receipt hash is insufficient for memory admission.
        snapshot.verify()
        before=_time(availability['available_at'])<boundary
        reason=None if before else 'source_not_strictly_before_cutoff'
    return {'eligible':reason is None,'reason':reason,'cutoff':cutoff,
            'available_at':availability.get('available_at') if reason is None else None,
            'receipt_sha256':availability.get('receipt_sha256') if reason is None else None,
            'authenticity':'trusted_caller_receipt','git_dates_used':False}


def assess_architecture_memory(memory,source_snapshot,current_snapshot,*,cutoff=None):
    """Describe dependency invalidation; preserve every original observation.

    Rebuild the current AST before affirming exact relationship resolution.
    Output keeps source/current revisions distinct and never edits the memory.
    """
    validate_architecture_memory(memory,source_snapshot)
    current_snapshot.verify()
    if current_snapshot.repository!=source_snapshot.repository:
        raise ValueError('Memory revision assessment requires the same repository')
    graph=build_graph(current_snapshot)
    wanted={row['sha256'] for row in memory['facts']}
    matching={row['sha256'] for row in _observations(current_snapshot,graph) if row['sha256'] in wanted}
    files={file.path:file for file in current_snapshot.files}
    skipped={row['path'] for row in current_snapshot.skipped if 'path' in row}
    temporal=None
    if cutoff is not None:
        original=_snapshot_availability(source_snapshot,cutoff)
        current=_snapshot_availability(current_snapshot,cutoff)
        temporal={'eligible':original['eligible'] and current['eligible'],'source':original,'current':current}
    parser_changed=(graph.parser_version!=memory['source']['parser_version']
                    or _graph_policy()!=memory['source']['graph_policy_sha256'])
    rows=[];counts=Counter()
    for fact in memory['facts']:
        states=[]
        for dependency in fact['dependencies']:
            path=dependency['path'];file=files.get(path)
            status=('unavailable' if path in skipped else 'missing' if file is None else
                    'unchanged' if file.sha256==dependency['sha256'] else 'changed')
            states.append({'path':path,'status':status,'source_sha256':dependency['sha256'],
                           'current_sha256':file.sha256 if file else None})
        status=('source_unavailable' if any(row['status']=='unavailable' for row in states) else
                'source_missing' if any(row['status']=='missing' for row in states) else
                'source_changed' if any(row['status']=='changed' for row in states) else
                'parser_changed' if parser_changed else
                'relationship_changed' if fact['sha256'] not in matching else 'revalidated_static')
        counts[status]+=1
        rows.append({'fact_id':fact['id'],'fact_sha256':fact['sha256'],'status':status,
                     'dependencies':states,'static_eligible':status=='revalidated_static',
                     'historical_eligible':bool(status=='revalidated_static' and temporal and temporal['eligible'])})
    result={'schema':'repository_memory_assessment_v1','memory_sha256':memory['sha256'],
            'source':memory['source'],'current':{'repository':current_snapshot.repository,
                'commit_sha':current_snapshot.commit_sha,'snapshot_sha256':current_snapshot.snapshot_sha256},
            'facts':rows,'counts':dict(sorted(counts.items())),'historical_availability':temporal,
            'original_memory_unchanged':True,'runtime_verified':False,'model_calls':0}
    return dict(result,sha256=digest(result))
