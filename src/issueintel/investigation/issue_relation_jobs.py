"""Explicit two-source product comparisons with frozen input and paid-call ownership.

Search never calls a model. A selected pair has one durable intent per exact
source/policy. Saved comparisons use frozen synced bytes, while local source
pack authority is replayed without acquiring Git or current provider settings.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from issueintel.runtime.io import canonical_json,digest,write_json
from issueintel.runtime.config import RuntimeConfig
from .question_jobs import replay_snapshot
from . import issue_relations as contract


MODE='issue_relation'
DEPTH='relation'
CONFIRMATION='compare_issue_pair_with_api_cost'
PUBLIC={'source_job_id','source_pack_sha256','candidate','comparison_source_sha256','policy_sha256','confirmation'}
SAVED=PUBLIC|{'mode','provider','depth'}


def _hex(value,size):
    return isinstance(value,str) and re.fullmatch('[a-f0-9]{'+str(size)+'}',value) is not None


def selector(value):
    if not isinstance(value,dict):raise ValueError('Choose a saved issue candidate')
    if value.get('kind')=='investigation':
        valid=(set(value)=={'kind','job_id','pack_sha256'} and _hex(value['job_id'],24) and _hex(value['pack_sha256'],64))
    else:
        valid=(set(value)=={'kind','key','record_sha256','receipt_sha256'} and value.get('kind')=='synced_issue'
            and isinstance(value['key'],str) and 0<len(value['key'])<=2000
            and _hex(value['record_sha256'],64) and _hex(value['receipt_sha256'],64))
    if not valid:raise ValueError('Choose an exact issue candidate and source hashes')
    return deepcopy(value)


def policy_sha256():
    package=Path(__file__).resolve().parents[1]
    names=('investigation/issue_relation_jobs.py','investigation/workbench.py','investigation/question_jobs.py',
        'investigation/comparison.py','investigation/resume.py','investigation/providers.py',
        'runtime/config.py','runtime/budget.py')
    return digest({'contract':contract.policy_sha256(),
        **{name:sha256((package/name).read_bytes()).hexdigest() for name in names}})


def _authority(row):
    return {key:row[key] for key in ('id','mode','provider','depth','request','result')}


class IssueRelationJobs:
    def __init__(self,host):self.host=host

    def _db(self):
        db=sqlite3.connect(self.host.directory/'issue_relations.sqlite',timeout=30)
        db.execute('CREATE TABLE IF NOT EXISTS intents (pair_sha TEXT PRIMARY KEY, source_job TEXT NOT NULL, '
            'source_sha TEXT NOT NULL, target_job TEXT NOT NULL UNIQUE, request BLOB NOT NULL, inputs BLOB NOT NULL)')
        return db

    def _source_request(self,request,*,candidate=False,query=False):
        keys={'source_job_id','source_pack_sha256'}|({'candidate'} if candidate else set())
        if (not isinstance(request,dict) or set(request)-(keys|({'query'} if query else set())) or not keys<=set(request)
                or not _hex(request.get('source_job_id'),24) or not _hex(request.get('source_pack_sha256'),64)):
            raise ValueError('Choose an exact saved product issue source')
        if candidate:selector(request['candidate'])
        if 'query' in request and (not isinstance(request['query'],str) or not request['query'].strip() or len(request['query'])>2000):
            raise ValueError('Use a bounded issue search query')
        return request

    def _request(self,request,*,saved=False):
        if (not isinstance(request,dict) or set(request)!=(SAVED if saved else PUBLIC)
                or request.get('confirmation')!=CONFIRMATION
                or not _hex(request.get('comparison_source_sha256'),64) or not _hex(request.get('policy_sha256'),64)
                or saved and (request['mode']!=MODE or request['provider']!='groq' or request['depth']!=DEPTH)):
            raise ValueError('Explicit pair, policy and API-cost confirmation are required')
        self._source_request({key:request[key] for key in ('source_job_id','source_pack_sha256','candidate')},candidate=True)
        return deepcopy(request)

    def _pack(self,identifier,checksum):
        pack,raw,_=self.host._load_pack(identifier)
        self.host._clarification_product(identifier,pack)
        if sha256(raw).hexdigest()!=checksum:raise ValueError('Issue comparison source pack changed')
        row=json.loads(self.host._job_path(identifier).read_bytes())
        if row['status']!='completed':raise ValueError('A completed product investigation is required')
        side=contract.project_pack(pack,identifier,checksum)
        return pack,side,{'kind':'investigation','job_id':identifier,'pack_json':raw.decode('utf-8'),'job':row}

    def _repository_identity(self,pack,row):
        """A local path alias cannot establish ownership of a GitHub repository."""
        from issueintel.repository.acquire import parse_github_reference
        actual=Path(pack['snapshot']['repository']).resolve()
        seen=set()
        for _ in range(20):
            if row['id'] in seen:raise ValueError('Cyclic comparison source authority')
            seen.add(row['id']);request=row['request'];mode=request['mode']
            if mode=='github':return ('github',parse_github_reference(request['issue_url']).repository.casefold())
            if mode=='example':return ('local',os.path.normcase(str(actual)))
            if mode=='local':
                locator=Path(request['repository'])
                suffix=tuple(os.path.normcase(p) for p in locator.parts)
                tail=tuple(os.path.normcase(p) for p in actual.parts[-len(suffix):]) if suffix else ()
                if locator.resolve()==actual or not locator.is_absolute() and suffix and suffix==tail:
                    return ('local',os.path.normcase(str(actual)))
                reference=parse_github_reference(request['repository'])
                expected=(self.host.root/'data/repos'/(reference.owner.lower()+'__'+reference.repo.lower()+'.git')).resolve()
                if actual!=expected:raise ValueError('Unbound local repository alias')
                return ('github',reference.repository.casefold())
            key=('parent_job_id' if mode in {'followup','clarification','check_followup','memory_followup','context','planned_continuation'}
                 else 'source_job_id' if mode in {'resume','finalization','stage_resume'} else None)
            if key is None or not _hex(request.get(key),24):raise ValueError('Unsupported comparison source authority')
            row=json.loads(self.host._job_path(request[key]).read_bytes())
        raise ValueError('Comparison source authority exceeds its bound')

    def _same_repository(self,left_pack,left_witness,right_witness):
        left=self._repository_identity(left_pack,left_witness['job'])
        if right_witness['kind']=='investigation':
            right=self._repository_identity(json.loads(right_witness['pack_json']),right_witness['job'])
        else:right=('github',contract.canonical_repository(right_witness['repository']))
        if left!=right:raise ValueError('Issue comparison requires the same bound repository, not a path alias')

    def _pair(self,request,*,replay=False):
        with self.host._pack_read_scope():
            left_pack,left,left_witness=self._pack(request['source_job_id'],request['source_pack_sha256'])
            selected=selector(request['candidate'])
            packs=[left_pack]
            if selected['kind']=='investigation':
                right_pack,right,right_witness=self._pack(selected['job_id'],selected['pack_sha256'])
                packs.append(right_pack)
            else:
                from issueintel.integrations.github_index import RepositoryIndex
                item=RepositoryIndex(self.host.root,left['repository']).item(selected['key'])
                if (item['record_sha256']!=selected['record_sha256'] or item['receipt_sha256']!=selected['receipt_sha256']):
                    raise ValueError('Synced issue changed since candidate selection')
                right=contract.project_synced(item,left['repository'])
                right_witness={'kind':'synced_issue','repository':left['repository'],'item':deepcopy(item)}
            source=contract.build_source(left,right)
            self._same_repository(left_pack,left_witness,right_witness)
            if replay:
                for pack in packs:replay_snapshot(pack)
            return source,left_witness,right_witness

    def candidates(self,request):
        self._source_request(request,query=True)
        pack,left,left_witness=self._pack(request['source_job_id'],request['source_pack_sha256'])
        identity=self._repository_identity(pack,left_witness['job'])
        issue=next(row for row in left['sources'] if row['id']==left['issue_source_id'])
        query=request.get('query',issue['text'][:2000]);documents=[];witnesses={};seen=set();warnings=[]
        from issueintel.retrieval.related import rank_related
        for row in self.host.history():
            if row['status']!='completed' or row['id']==request['source_job_id']:continue
            try:
                raw=self.host._export_bytes(row['id']);checksum=sha256(raw).hexdigest()
                _,right,right_witness=self._pack(row['id'],checksum)
                contract.build_source(left,right)
                self._same_repository(pack,left_witness,right_witness)
                if right['issue_key'] in seen:continue
                seen.add(right['issue_key']);text=next(e['text'] for e in right['sources'] if e['id']==right['issue_source_id'])
                documents.append({'job_id':row['id'],'case_id':right['issue_key'],'repository':right['repository'],
                    'pack_sha256':checksum,'query_sha256':sha256(text.encode()).hexdigest(),'text':text})
                witnesses[row['id']]=right
            except (ValueError,KeyError,TypeError,OSError):continue
        rows=[]
        for item in rank_related(query,documents,limit=10):
            right=witnesses[item['job_id']]
            rows.append({'kind':'investigation','selector':{'kind':'investigation','job_id':item['job_id'],'pack_sha256':item['pack_sha256']},
                'title':item['snippet']['text'],'repository':right['repository'],'issue_key':right['issue_key'],
                'score':item['score'],'score_is_probability':False,'relationship':'candidate_unverified'})
        if identity[0]=='github':
            try:
                from issueintel.integrations.github_index import RepositoryIndex
                index=RepositoryIndex(self.host.root,left['repository'])
                for item in index.search(query,limit=20)['results']:
                    if item['kind']!='issue':continue
                    try:
                        frozen=index.item(item['key']);right=contract.project_synced(frozen,left['repository'])
                        contract.build_source(left,right)
                    except (ValueError,KeyError,TypeError,OSError):continue
                    if right['issue_key'] in seen:continue
                    seen.add(right['issue_key'])
                    rows.append({'kind':'synced_issue','selector':right['reference'],'title':item['title'],
                        'repository':right['repository'],'issue_key':right['issue_key'],'score':item['score'],
                        'score_is_probability':False,'relationship':'candidate_unverified'})
                    if sum(r['kind']=='synced_issue' for r in rows)>=10:break
            except (ValueError,KeyError,TypeError,OSError,sqlite3.DatabaseError):warnings.append('synced_issue_index_unavailable')
        return {'schema':'issue_relation_candidates_v1','source_job_id':request['source_job_id'],
            'source_pack_sha256':request['source_pack_sha256'],'candidates':rows,'warnings':warnings,'model_calls':0}

    def preview(self,request):
        self._source_request(request,candidate=True)
        source,_,_=self._pair(request)
        return {'schema':'issue_relation_preview_v1','source_job_id':request['source_job_id'],
            'source_pack_sha256':request['source_pack_sha256'],'candidate':deepcopy(request['candidate']),
            'source':source,'comparison_source_sha256':source['sha256'],'policy_sha256':policy_sha256(),
            'available':True,'max_provider_calls':2,'model_calls':0,'confirmation':CONFIRMATION}

    def _pair_key(self,request):return digest({k:request[k] for k in PUBLIC})

    def _saved_inputs(self,request,target):
        self._request(request,saved=True)
        db=self._db()
        try:
            db.execute('BEGIN IMMEDIATE')
            saved=db.execute('SELECT target_job,request,inputs FROM intents WHERE pair_sha=?',(self._pair_key(request),)).fetchone()
            db.commit()
        finally:db.close()
        if not saved or saved[0]!=target or json.loads(saved[1])!=request:
            raise ValueError('Missing owned issue comparison intent')
        witness=json.loads(saved[2])
        if (set(witness)!={'schema','owner_job_id','request','policy_sha256','source','left','right','sha256'}
                or witness['schema']!='issue_relation_inputs_v1' or witness['owner_job_id']!=target
                or witness['request']!=request or witness['policy_sha256']!=request['policy_sha256']
                or digest({k:v for k,v in witness.items() if k!='sha256'})!=witness['sha256']):
            raise ValueError('Frozen issue comparison input ownership changed')
        sides=[]
        for key in ('left','right'):
            value=witness[key]
            if value['kind']=='investigation':
                if set(value)!={'kind','job_id','pack_json','job'}:raise ValueError('Invalid frozen product comparison input')
                checksum=sha256(value['pack_json'].encode()).hexdigest();pack=json.loads(value['pack_json'])
                side=contract.project_pack(pack,value['job_id'],checksum)
                _,current,current_witness=self._pack(value['job_id'],checksum)
                if (digest(current)!=digest(side) or _authority(current_witness['job'])!=_authority(value['job'])
                        or current_witness['pack_json']!=value['pack_json']):
                    raise ValueError('Frozen comparison source authority changed')
            else:
                if set(value)!={'kind','repository','item'} or value['kind']!='synced_issue':raise ValueError('Invalid frozen synced issue input')
                side=contract.project_synced(value['item'],value['repository'])
            sides.append(side)
        source=contract.build_source(*sides)
        self._same_repository(json.loads(witness['left']['pack_json']),witness['left'],witness['right'])
        if (digest(source)!=digest(witness['source']) or source['sha256']!=request['comparison_source_sha256']
                or source['left']['reference']!={'kind':'investigation','job_id':request['source_job_id'],'pack_sha256':request['source_pack_sha256']}
                or source['right']['reference']!=request['candidate']):raise ValueError('Frozen pair differs from its saved selection')
        return witness

    def submit(self,request):
        request=self._request(request);saved_request=dict(request,mode=MODE,provider='groq',depth=DEPTH)
        db=self._db()
        try:
            db.execute('BEGIN IMMEDIATE')
            found=db.execute('SELECT target_job FROM intents WHERE pair_sha=?',(self._pair_key(request),)).fetchone()
            if found:
                db.commit();self._saved_inputs(saved_request,found[0]);return self.host.job(found[0])
            if request['policy_sha256']!=policy_sha256():raise ValueError('Comparison policy changed after preview')
            source,left,right=self._pair(request)
            if source['sha256']!=request['comparison_source_sha256']:raise ValueError('Comparison sources changed after preview')
            count=db.execute('SELECT count(*) FROM intents WHERE source_job=? AND source_sha=?',
                (request['source_job_id'],request['source_pack_sha256'])).fetchone()[0]
            if count>=20:raise ValueError('A saved source supports at most twenty issue comparisons')
            target=uuid.uuid4().hex[:24]
            witness={'schema':'issue_relation_inputs_v1','owner_job_id':target,'request':saved_request,
                'policy_sha256':request['policy_sha256'],'source':source,'left':left,'right':right}
            witness['sha256']=digest(witness)
            if len(canonical_json(witness).encode())>4_000_000:raise ValueError('Frozen issue pair exceeds the bounded input size')
            db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?)',(self._pair_key(request),request['source_job_id'],
                request['source_pack_sha256'],target,canonical_json(saved_request),canonical_json(witness)))
            row=self.host._enqueue(saved_request,self._run,identifier=target)
            db.commit();return row
        except BaseException:db.rollback();raise
        finally:db.close()

    def history(self,identifier):
        pack,raw,_=self.host._load_pack(identifier);checksum=sha256(raw).hexdigest()
        try:self.host._clarification_product(identifier,pack);contract.project_pack(pack,identifier,checksum);available,reason=True,None
        except (ValueError,KeyError,TypeError):available,reason=False,'requires_saved_product_investigation'
        db=self._db()
        try:rows=db.execute('SELECT target_job FROM intents WHERE source_job=? AND source_sha=? ORDER BY rowid',(identifier,checksum)).fetchall()
        finally:db.close()
        return {'schema':'issue_relation_history_v1','source_job_id':identifier,'source_pack_sha256':checksum,
            'available':available,'reason':reason,'relation_jobs':[self.host.job(row[0]) for row in rows]}

    def _row(self,identifier):
        row=json.loads(self.host._job_path(identifier).read_bytes());request=self._request(row['request'],saved=True)
        if (row['id']!=identifier or row['mode']!=MODE or row['provider']!='groq' or row['depth']!=DEPTH):
            raise ValueError('Issue comparison job authority changed')
        return row,request

    def _attempts(self,row,source):
        attempts=row.get('relation_attempts')
        if not isinstance(attempts,list) or len(attempts)>2:raise ValueError('Missing bounded comparison attempts')
        for index,attempt in enumerate(attempts,1):
            expected={'schema':'issue_relation_attempt_v1','owner_job_id':row['id'],'source_sha256':source['sha256'],
                'policy_sha256':row['request']['policy_sha256'],'call_index':index,'max_calls':2}
            if canonical_json(attempt)!=canonical_json(expected):raise ValueError('Comparison attempt owner or bound changed')
        owners=row.get('relation_receipt_owners',{});receipts=row.get('usage_receipts',{})
        if not isinstance(owners,dict) or not isinstance(receipts,dict) or set(owners)!=set(receipts):
            raise ValueError('Comparison receipt ownership inventory changed')
        seen=set()
        for identifier,owner in owners.items():
            if (set(owner)!={'owner_job_id','call_index','source_sha256'} or owner['owner_job_id']!=row['id']
                    or type(owner['call_index']) is not int or not 1<=owner['call_index']<=len(attempts)
                    or owner['source_sha256']!=source['sha256'] or owner['call_index'] in seen
                    or identifier!=receipts[identifier]['reservation_id'] or receipts[identifier].get('stage')!=MODE):
                raise ValueError('Comparison receipt belongs to another attempt')
            seen.add(owner['call_index'])
        return attempts

    def bundle(self,identifier):
        row,request=self._row(identifier)
        if row['status']!='completed':raise ValueError('Issue comparison has not completed')
        witness=self._saved_inputs(request,identifier)
        ref=row['issue_relation_result'];path=(self.host.root/ref['path']).resolve()
        expected=(self.host.directory/'issue_relation_runs'/identifier/'issue-relation.json').resolve()
        if path!=expected or not path.is_relative_to(self.host.directory.resolve()):raise ValueError('Issue comparison export path changed')
        raw=path.read_bytes()
        if sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('Issue comparison export bytes changed')
        bundle=contract.validate(json.loads(raw),witness['source'])
        if len(self._attempts(row,witness['source']))!=bundle['provenance']['attempts']:
            raise ValueError('Comparison provenance differs from its owned attempts')
        return bundle

    def witness(self,identifier):
        self.bundle(identifier)
        _,request=self._row(identifier)
        return deepcopy(self._saved_inputs(request,identifier))

    def export(self,identifier,format='json'):
        if format not in {'json','md'}:raise ValueError('Choose JSON or Markdown comparison')
        bundle=self.bundle(identifier)
        text=json.dumps(bundle,ensure_ascii=False,sort_keys=True,indent=2)+'\n' if format=='json' else contract.render_markdown(bundle)
        return text.encode(),bundle['source']['left']['reference']['pack_sha256'],bundle['sha256']

    def _run(self,row):
        host=self.host;lease=None
        try:
            lease=host._take_execution_lease(row['id'])
            host._check_cancelled(row['id']);saved,request=self._row(row['id'])
            witness=self._saved_inputs(request,row['id']);source=witness['source']
            if saved['request']!=row['request']:raise ValueError('Queued comparison authority changed')
            host._save(row,status='running',stage='prepare_issue_relation',relation_attempts=[],relation_receipt_owners={})
            current,_,_=self._pair(request,replay=True)
            if digest(current)!=digest(source) or policy_sha256()!=request['policy_sha256']:raise ValueError('Queued comparison source or policy changed')
            host._check_cancelled(row['id']);config=RuntimeConfig.load(host.root)
            if config.model!='openai/gpt-oss-120b':raise ValueError('Issue comparison model has no authorization')
            context=({'category':'ARTIFACT_GENERATION_COST','reasoning_effort':config.reasoning_effort,
                'stage':'local_demo_issue_relation','diagnostic':False} if config.reasoning_effort in {'medium','high'} else None)
            provider=host.provider_factory(config,call_context=context)
            if provider.model!=config.model:raise ValueError('Comparison model changed')
            if hasattr(provider,'on_receipt'):
                def capture(receipt):
                    index=len(row['relation_attempts'])
                    owner={'owner_job_id':row['id'],'call_index':index,'source_sha256':source['sha256']}
                    prior=row['relation_receipt_owners'].get(receipt['reservation_id'])
                    if prior is not None and prior!=owner:raise ValueError('Comparison reservation owner changed')
                    row['relation_receipt_owners'][receipt['reservation_id']]=owner
                    row.setdefault('usage_receipts',{})[receipt['reservation_id']]=dict(receipt,stage=MODE)
                    host._save(row,usage_tracking=True,usage_receipts=row['usage_receipts'],relation_receipt_owners=row['relation_receipt_owners'])
                    if receipt['status']=='reserved':guard()
                provider.on_receipt=capture;host._save(row,usage_tracking=True,usage_receipts={})
            def guard():
                host._check_cancelled(row['id']);current_row,current_request=self._row(row['id'])
                if current_request!=request or current_row.get('relation_attempts')!=row['relation_attempts']:
                    raise ValueError('Comparison call authority changed')
                self._attempts(current_row,source)
                if policy_sha256()!=request['policy_sha256']:raise ValueError('Comparison policy changed during generation')
                self._saved_inputs(request,row['id'])
                current,_,_=self._pair(request)
                if digest(current)!=digest(source):raise ValueError('Comparison sources changed during generation')
            def attempt():
                guard()
                if len(row['relation_attempts'])>=2:raise ValueError('Comparison call allowance exhausted')
                item={'schema':'issue_relation_attempt_v1','owner_job_id':row['id'],'source_sha256':source['sha256'],
                    'policy_sha256':request['policy_sha256'],'call_index':len(row['relation_attempts'])+1,'max_calls':2}
                host._save(row,relation_attempts=row['relation_attempts']+[item])
            def complete(messages,max_output_tokens):
                attempt();answer=provider(messages,max_output_tokens);guard();return answer
            if hasattr(provider,'complete_structured'):
                def structured(messages,*,max_output_tokens,schema):
                    attempt();answer=provider.complete_structured(messages,max_output_tokens=max_output_tokens,schema=schema);guard();return answer
                complete.complete_structured=structured
            host._save(row,stage='generate_issue_relation')
            bundle=contract.generate(source,complete,model=provider.model,max_output_tokens=min(config.max_output_tokens,8192))
            guard();contract.validate(bundle,source)
            path=host.directory/'issue_relation_runs'/row['id']/'issue-relation.json'
            with host.lock:
                host._check_cancelled(row['id'])
                if path.exists():raise ValueError('Saved issue comparisons cannot be overwritten')
                write_json(path,bundle)
                host._save(row,status='completed',stage='completed',issue_relation_result={
                    'path':path.relative_to(host.root).as_posix(),'sha256':sha256(path.read_bytes()).hexdigest()},
                    source_job_id=request['source_job_id'],source_pack_sha256=request['source_pack_sha256'])
        except InterruptedError:
            host._save(row,status='cancelled',stage='cancelled',error='Cancelled; an in-flight API call may already have been charged.')
        except Exception as error:
            host._save(row,status='failed',stage='failed',error=f'Issue comparison failed ({type(error).__name__}); no automatic retry.')
        finally:
            if lease is not None:lease.close()
            with host.lock:host.active.discard(row['id']);host.cancelled.discard(row['id'])
            host.capacity.release()
