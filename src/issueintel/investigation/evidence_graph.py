"""A descriptive view of saved citations, distinct from the static code graph."""
from issueintel.runtime.io import digest
from .comparison import compare_packs
from .verifier import validate_critique


MAX_NODES = 100
MAX_EDGES = 300


def evidence_graph(pack):
    try:
        return _build(pack)
    except (KeyError, TypeError, AttributeError):
        raise ValueError('Invalid saved evidence graph input') from None


def _build(pack):
    compare_packs(pack, pack)  # Replay query, citation hashes and source bindings.
    hypotheses = (pack.get('annotation') or {}).get('hypotheses', [])
    verification = pack.get('verification') or {}
    reviews = verification.get('reviews', [])
    if reviews:
        if verification.get('kind') != 'llm_separate_pass' or not hypotheses:
            raise ValueError('Reviews require a separate model pass and hypotheses')
        reviews = validate_critique({'reviews':reviews},pack['annotation'],pack['evidence'])
    nodes = []; edges = []; seen_edges = set()
    def add_node(identifier, kind, title, text, **fields):
        nodes.append({'id':identifier,'kind':kind,'title':title[:180],
                      'text':text[:8000],'text_truncated':len(text)>8000,**fields})
    def edge(source, target, relation):
        key = (source,target,relation)
        if key not in seen_edges:
            seen_edges.add(key)
            edges.append({'source':source,'target':target,'relation':relation})
    cited = {eid for h in hypotheses for eid in h['support_ids']+h['counter_ids']}
    cited.update(eid for review in reviews for eid in review['evidence_ids'])
    # Keep assertions and their cited sources before uncited retrieval candidates.
    for h in hypotheses:
        identifier = 'hypothesis:'+h['id']
        add_node(identifier,'hypothesis',h['id']+' · '+h['statement'],h['statement'],
                 hypothesis_id=h['id'],counter_status=h['counter_status'],
                 falsification_test=h['falsification_test'][:8000],correctness='unverified')
        for eid in h['support_ids']: edge(identifier,'evidence:'+eid,'claimed_support')
        for eid in h['counter_ids']: edge(identifier,'evidence:'+eid,'claimed_counter')
    for review in reviews:
        identifier = 'review:'+review['hypothesis_id']
        add_node(identifier,'model_review',review['hypothesis_id']+' · '+review['assessment'],review['reason'],
                 assessment=review['assessment'],independent_human=False)
        edge(identifier,'hypothesis:'+review['hypothesis_id'],'model_assessment')
        for eid in review['evidence_ids']: edge(identifier,'evidence:'+eid,'critic_citation')
    query = next(e for e in pack['evidence'] if e['kind']=='issue')
    records = sorted(pack['evidence'],key=lambda e:(e['kind']!='issue',e['id'] not in cited,e['id']))
    for record in records:
        kind = 'query' if record['kind']=='issue' else record['kind']
        add_node('evidence:'+record['id'],kind,record['locator'],record['text'],
                 evidence_id=record['id'],locator=record['locator'],sha256=record['sha256'],
                 revision=record.get('revision'),available_at=record.get('available_at'),
                 historical_verified=(pack.get('leakage_audit',{}).get('strict_issue_time_verified') is True
                                      and kind!='repository_context'))
        if kind!='query':edge('evidence:'+query['id'],'evidence:'+record['id'],'retrieved_context')
    # Validate the complete structure before applying display bounds.
    ids = {node['id'] for node in nodes}
    if len(ids)!=len(nodes) or any(e['source'] not in ids or e['target'] not in ids for e in edges):
        raise ValueError('Ambiguous or dangling evidence graph reference')
    retained = nodes[:MAX_NODES]; visible = {node['id'] for node in retained}
    selected_edges = [edge for edge in edges if edge['source'] in visible and edge['target'] in visible][:MAX_EDGES]
    result = {'schema':'saved_evidence_graph_v1','nodes':retained,'edges':selected_edges,
              'omitted_nodes':len(nodes)-len(retained),'omitted_edges':len(edges)-len(selected_edges),
              'commit_sha':pack['snapshot']['commit_sha'],'snapshot_sha256':pack['snapshot']['snapshot_sha256'],
              'query_sha256':query['sha256'],'correctness':'unverified','evaluation_performed':False,
              'independent_human':False,'model_calls':0,
              'limitations':['Edges describe saved retrieval and model citations, not established causal relationships.',
                  'Model critique is distinct from an independent human review.',
                  'Missing counter-evidence is not evidence that a hypothesis is true.',
                  'This view is not the AST code graph; source excerpts retain their original revision.']}
    result['sha256'] = digest(result)
    return result
