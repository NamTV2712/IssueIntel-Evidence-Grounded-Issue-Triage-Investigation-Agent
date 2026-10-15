"""Bounded, replayable current repository context for product GitHub issues only."""
from hashlib import sha256
import re

from issueintel.repository.acquire import parse_github_reference
from issueintel.runtime.io import digest
from issueintel.integrations.github_index import record_links


SCHEMA = 'product_repository_context_v1'
MAX_SOURCES = 6
MAX_EXCERPT = 1200


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
    evidence=[]
    for item in context['sources']:
        record,receipt=item['record'],item['receipt']
        if (digest(record)!=item['record_sha256'] or digest(receipt)!=item['receipt_sha256']
                or receipt['records'].get(record['key'])!=item['record_sha256']
                or record['repository']!=context['repository'] or record.get('historical_verified') is not False
                or record['kind'] not in {'issue','pull','comments','review_comments','files','commits'}
                or item['availability'].get('reason') or record_links(record)!=item.get('links')):
            raise ValueError('Repository source/receipt/relationship integrity failed')
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
    return evidence


def context_evidence(context, case, *, protocol):
    try:
        return _context_evidence(context,case,protocol=protocol)
    except (KeyError,TypeError,AttributeError,IndexError):
        raise ValueError('Invalid repository context structure') from None
