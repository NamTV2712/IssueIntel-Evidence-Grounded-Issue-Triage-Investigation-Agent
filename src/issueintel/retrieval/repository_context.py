"""Bounded, replayable current repository context for product GitHub issues only."""
from hashlib import sha256
import re

from issueintel.repository.acquire import parse_github_reference
from issueintel.repository.snapshot import validate_repo_path
from issueintel.runtime.io import digest
from issueintel.integrations.github_index import record_links


SCHEMA = 'product_repository_context_v1'
MAX_SOURCES = 6
MAX_EXCERPT = 1200


def validate_selections(value):
    """Validate explicit record/hash choices without reading or expanding them."""
    if not isinstance(value,list) or not 1<=len(value)<=MAX_SOURCES:
        raise ValueError('Select 1–6 distinct repository sources')
    selected=[]
    for item in value:
        if not isinstance(item,dict) or set(item)!={'key','record_sha256','receipt_sha256'}:
            raise ValueError('Repository selection requires exactly key, record_sha256 and receipt_sha256')
        key,record_sha,receipt_sha=item['key'],item['record_sha256'],item['receipt_sha256']
        if (not isinstance(key,str) or len(key)>300 or any(ord(c)<32 or ord(c)==127 for c in key)
                or not isinstance(record_sha,str) or not re.fullmatch('[0-9a-f]{64}',record_sha)
                or not isinstance(receipt_sha,str) or not re.fullmatch('[0-9a-f]{64}',receipt_sha)):
            raise ValueError('Invalid repository source key/hash')
        if not re.fullmatch(r'(?:issue|pull|comments|review_comments):[1-9][0-9]*|commit:[1-9][0-9]*:(?:[a-f0-9]{40}|[a-f0-9]{64})',key):
            file_key=re.fullmatch(r'file:([1-9][0-9]*):(.+)',key)
            if not file_key:
                raise ValueError('Invalid repository source key')
            validate_repo_path(file_key[2])
        if key in {entry['key'] for entry in selected}:
            raise ValueError('Repository selections must be distinct')
        selected.append({'key':key,'record_sha256':record_sha,'receipt_sha256':receipt_sha})
    return selected


def _context_scope(index,issue_url,query,*,maximum=120000):
    source=parse_github_reference(issue_url)
    if source.issue_number is None or source.repository.lower()!=index.repository:
        raise ValueError('Repository context belongs to another issue/repository')
    if (not isinstance(query,str) or not query.strip() or len(query)>maximum
            or '\x00' in query):
        raise ValueError('Bounded issue query is required')
    return source


def _context(index,source,query,records,status,*,selection=None):
    context={'schema':SCHEMA,'repository':index.repository,'issue_url':source.issue_url,
             'query_sha256':sha256(query.encode()).hexdigest(),'sources':records,
             'scan_id':(status['run'] or {}).get('id'),'scan_complete':status['scan_complete'],
             'pending_updates':status['pending_updates'],'historical_verified':False}
    if selection is not None:
        repository_id=status['github_repository_id']
        if type(repository_id) is not int or repository_id<=0:
            raise ValueError('Selected context requires a verified repository identity')
        context['selection']={'kind':'user_selected','repository_id':repository_id,'sources':selection}
    context['sha256']=digest(context)
    return context


def _product_case(index,source,query):
    return {'case_id':source.issue_url,'issue_url':source.issue_url,
            'repository':index.repository,'problem_statement':query}


def collect_selected_context(index,issue_url,query,selections):
    """Replay only the exact current sources explicitly chosen by the user."""
    source=_context_scope(index,issue_url,query)
    selections=validate_selections(selections)
    status=index.status()
    records=[]
    for selection in selections:
        if selection['key'] in {f'issue:{source.issue_number}',f'pull:{source.issue_number}'}:
            raise ValueError('The target issue/PR cannot be an extra context source')
        item=index.item(selection['key'])
        if (item['record_sha256']!=selection['record_sha256']
                or item['receipt_sha256']!=selection['receipt_sha256']
                or item['record']['key']!=selection['key']):
            raise ValueError('Selected repository source changed; search and select again')
        records.append(item)
    context=_context(index,source,query,records,status,selection=selections)
    evidence=context_evidence(context,_product_case(index,source,query),protocol='benchmark_base')
    if len(evidence)!=len(selections):
        raise ValueError('Selected repository source has no usable excerpt')
    return context


def search_context(index,issue_url,query,*,evidence_query=None):
    """Bounded local search, replaying receipts before exposing selectable hits."""
    source=_context_scope(index,issue_url,query,maximum=2000)
    evidence_query=query if evidence_query is None else evidence_query
    _context_scope(index,issue_url,evidence_query)
    search=index.search(query,limit=20)
    status=index.status()
    terms=list(dict.fromkeys(re.findall(r'\w+',query.casefold())))[:32]
    results=[]
    seen=set()
    for hit in search['results'][:20]:
        if hit['key'] in {f'issue:{source.issue_number}',f'pull:{source.issue_number}'} or hit['key'] in seen:
            continue
        selected=validate_selections([{'key':hit['key'],'record_sha256':hit['record_sha256'],
                                       'receipt_sha256':hit['receipt_sha256']}])
        item=index.item(hit['key'])
        if (item['record_sha256']!=hit['record_sha256'] or item['receipt_sha256']!=hit['receipt_sha256']
                or item['record']['key']!=hit['key']):
            raise ValueError('Repository search source changed; search again')
        if item['availability'].get('reason'):
            continue
        context=_context(index,source,evidence_query,[item],status,selection=selected)
        evidence=context_evidence(context,_product_case(index,source,evidence_query),protocol='benchmark_base')
        if not evidence:
            continue
        excerpt=evidence[0]['text']
        record=item['record']
        results.append({'key':hit['key'],'record_sha256':hit['record_sha256'],
                        'receipt_sha256':hit['receipt_sha256'],
                        'kind':record['kind'],'title':record['title'][:300],
                        'url':record['url'],'excerpt':excerpt,
                        'excerpt_sha256':sha256(excerpt.encode()).hexdigest(),
                        'matched_terms':[term for term in terms if term in excerpt.casefold()]})
        seen.add(hit['key'])
    result={'schema':'product_repository_context_search_v1','repository':index.repository,
            'github_repository_id':status['github_repository_id'],
            'issue_url':source.issue_url,'query':query,'query_sha256':sha256(query.encode()).hexdigest(),
            'scan_id':(status['run'] or {}).get('id'),'scan_complete':status['scan_complete'],
            'pending_updates':status['pending_updates'],'historical_verified':False,
            'results':results}
    result['sha256']=digest(result)
    return result


def collect_context(index, issue_url, query):
    source = parse_github_reference(issue_url)
    if source.issue_number is None or source.repository.lower()!=index.repository:
        raise ValueError('Repository context belongs to another issue/repository')
    if not isinstance(query,str) or not query.strip() or len(query)>120000:
        raise ValueError('Bounded issue query is required')
    status = index.status()
    candidates = []
    with index.connect() as db:
        # Direct source links precede lexical hits, without scanning every body.
        rows = db.execute('''SELECT DISTINCT owner,sha FROM links WHERE target=?
            AND NOT EXISTS(SELECT 1 FROM unavailable WHERE unavailable.key=links.owner)
            ORDER BY CASE relation WHEN 'text_mention_unverified' THEN 0 ELSE 1 END,owner LIMIT 12''',(f'thread:{source.issue_number}',)).fetchall()
        for row in rows:
            item=index.item(row['owner'])
            if item['record_sha256']!=row['sha']:
                raise ValueError('Repository relationship source changed')
            candidates.append(row['owner'])
    for result in index.search(query[:2000],limit=20)['results']:
        if result['key'] not in candidates:
            candidates.append(result['key'])
    chosen=[]
    for key in candidates:
        if key in {f'issue:{source.issue_number}',f'pull:{source.issue_number}'}:
            continue
        item=index.item(key)
        if item['availability'].get('reason'):
            continue
        chosen.append(item)
        if len(chosen)>=4:
            break
    # Add at most two explicit PR membership records to explain code paths/commits.
    for item in list(chosen):
        if len(chosen)>=MAX_SOURCES or item['record']['kind']!='pull':
            continue
        with index.connect() as db:
            rows=db.execute('''SELECT owner FROM links WHERE source=? AND relation IN ('api_pr_file','api_pr_commit')
                AND NOT EXISTS(SELECT 1 FROM unavailable WHERE unavailable.key=links.owner)
                ORDER BY relation DESC,owner LIMIT 2''',(item['record']['key'],)).fetchall()
        for row in rows:
            if len(chosen)>=MAX_SOURCES:
                break
            if row['owner'] not in {i['record']['key'] for i in chosen}:
                chosen.append(index.item(row['owner']))
    records=[item for item in chosen if _source_text(item['record']).strip()]
    context={'schema':SCHEMA,'repository':index.repository,'issue_url':source.issue_url,
             'query_sha256':sha256(query.encode()).hexdigest(),'sources':records,
             'scan_id':(status['run'] or {}).get('id'),'scan_complete':status['scan_complete'],
             'pending_updates':status['pending_updates'],'historical_verified':False}
    context['sha256']=digest(context)
    return context


def _source_text(record):
    if record['kind']=='files':
        return f'PR #{record["number"]} API file membership: {record["path"]} ({record["status"]}), blob {record["blob_sha"]}'
    return record['title']+'\n\n'+record['body']


def _context_evidence(context, case, *, protocol):
    if (protocol!='benchmark_base' or case.get('split') is not None
            or str(case.get('case_id','')).startswith(('verified:','live:','main_dev:'))):
        raise ValueError('Current repository memory cannot enter benchmark or strict inference')
    source=parse_github_reference(case.get('issue_url',''))
    if (case.get('case_id')!=source.issue_url or case.get('instance_id') is not None
            or parse_github_reference(case.get('repository','')).repository.lower()!=source.repository.lower()):
        raise ValueError('Repository context is limited to a current product GitHub issue')
    if (source.issue_number is None or context.get('schema')!=SCHEMA
            or context.get('repository')!=source.repository.lower()
            or context.get('issue_url')!=source.issue_url
            or context.get('query_sha256')!=sha256(case['problem_statement'].encode()).hexdigest()
            or context.get('historical_verified') is not False
            or digest({k:v for k,v in context.items() if k!='sha256'})!=context.get('sha256')
            or not isinstance(context.get('sources'),list) or len(context['sources'])>MAX_SOURCES):
        raise ValueError('Repository context binding/integrity failed')
    if 'selection' in context:
        selection=context['selection']
        if (not isinstance(selection,dict) or set(selection)!={'kind','repository_id','sources'}
                or selection['kind']!='user_selected'
                or type(selection['repository_id']) is not int or selection['repository_id']<=0):
            raise ValueError('Invalid explicit repository context selection')
        selected=validate_selections(selection['sources'])
        if selected!=[{'key':item['record']['key'],'record_sha256':item['record_sha256'],
                       'receipt_sha256':item['receipt_sha256']}
                      for item in context['sources']]:
            raise ValueError('Repository context selection/source binding failed')
    evidence=[]
    for item in context['sources']:
        record,receipt=item['record'],item['receipt']
        if (digest(record)!=item['record_sha256'] or digest(receipt)!=item['receipt_sha256']
                or receipt['records'].get(record['key'])!=item['record_sha256']
                or record['repository']!=context['repository'] or record.get('historical_verified') is not False
                or record['kind'] not in {'issue','pull','comments','review_comments','files','commits'}
                or item['availability'].get('reason') or record_links(record)!=item.get('links')):
            raise ValueError('Repository source/receipt/relationship integrity failed')
        if 'selection' in context and receipt.get('repository_id')!=context['selection']['repository_id']:
            raise ValueError('Selected repository global identity binding failed')
        text=_source_text(record)
        url_pattern=re.escape('https://github.com/'+context['repository'])+r'/(?:issues/[1-9][0-9]*(?:#issuecomment-[1-9][0-9]*)?|pull/[1-9][0-9]*(?:/files|#(?:issuecomment-[1-9][0-9]*|discussion_r[1-9][0-9]*))?|commit/[a-f0-9]{40,64})'
        if (len(text)>122002 or not re.fullmatch(url_pattern,record['url'])
                or record['key'] in {f'issue:{source.issue_number}',f'pull:{source.issue_number}'}):
            raise ValueError('Repository context source exceeds bounds or has wrong identity')
        # Store a bounded contiguous excerpt, preserving its exact offset/hash.
        terms=re.findall(r'\w+',case['problem_statement'][:2000].casefold())
        lower=text.casefold()
        match=next((lower.find(term) for term in terms if len(term)>2 and lower.find(term)>=0),0)
        # Unicode casefold can change string length: offsets are always sliced
        # from the original text, never treated as code line coordinates.
        start=max(0,min(match,len(text))-200)
        excerpt=text[start:start+MAX_EXCERPT]
        if not excerpt.strip():
            continue
        proof={'source_record_sha256':item['record_sha256'],'receipt_sha256':item['receipt_sha256'],
               'start_character':start,'end_character':start+len(excerpt),'source_key':record['key']}
        evidence.append({'id':'ctx_'+digest(proof)[:20], 'kind':'repository_context',
                         'locator':record['url']+(' · '+record['path'] if record['kind']=='files' else ''),
                         'text':excerpt,'sha256':sha256(excerpt.encode()).hexdigest(),
                         'revision':None,'available_at':None,
                         'provenance':{**proof,'repository':record['repository'],'record_kind':record['kind'],
                                       'fetched_at':receipt['fetched_at'],'retrieval':'bounded_current_repository',
                                       'source_url':record['url'],
                                       'edit_history':'unknown',
                                       'historical_verified':False,'causal_correctness':'unverified',
                                       'truncated':len(excerpt)!=len(text)}})
    if len({e['id'] for e in evidence})!=len(evidence):
        raise ValueError('Duplicate repository context source')
    if 'selection' in context and (len(evidence)!=len(context['sources'])
            or len({(e['kind'],e['locator']) for e in evidence})!=len(evidence)):
        raise ValueError('Selected repository source is blank or has a duplicate locator')
    return evidence


def context_evidence(context, case, *, protocol):
    try:
        return _context_evidence(context,case,protocol=protocol)
    except (KeyError,TypeError,AttributeError,IndexError):
        raise ValueError('Invalid repository context structure') from None
