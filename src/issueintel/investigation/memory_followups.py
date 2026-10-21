"""Explicit memory-assisted product children, one durable attempt per preview."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import uuid

from issueintel.runtime.io import digest,canonical_json
from .memory_selection import validate_selections,preview_memory_selection,validate_saved_selection


PUBLIC={'parent_job_id','parent_pack_sha256','selection_sha256','selections','provider','depth','confirmation'}
SAVED=(PUBLIC-{'confirmation'})|{'mode','policy_sha256'}


def policy_sha256():
    package=Path(__file__).resolve().parents[1]
    names=('investigation/memory_followups.py','investigation/memory_selection.py','investigation/product_memory.py',
           'repository/memory_store.py','repository/memory.py','repository/graph.py','repository/snapshot.py',
           'investigation/pipeline.py','investigation/workbench.py','investigation/verifier.py',
           'annotations/generator.py','annotations/models.py','annotations/public_inputs.py','annotations/structured.py',
           'investigation/providers.py','runtime/config.py','runtime/budget.py')
    return digest({name:sha256((package/name).read_bytes()).hexdigest() for name in names})


def validate_request(value,*,saved=False):
    if not isinstance(value,dict) or set(value)!=(SAVED if saved else PUBLIC):
        raise ValueError('Choose the exact memory preview and explicit provider/depth')
    if (not isinstance(value['parent_job_id'],str) or not re.fullmatch('[a-f0-9]{24}',value['parent_job_id'])
            or any(not isinstance(value[k],str) or not re.fullmatch('[a-f0-9]{64}',value[k]) for k in ('parent_pack_sha256','selection_sha256'))
            or not isinstance(value['provider'],str) or value['provider'] not in {'none','groq'}
            or not isinstance(value['depth'],str) or value['depth'] not in {'quick','deep'}):
        raise ValueError('Invalid memory investigation source or execution mode')
    if saved:
        if (value['mode']!='memory_followup' or not isinstance(value['policy_sha256'],str)
                or not re.fullmatch('[a-f0-9]{64}',value['policy_sha256'])):
            raise ValueError('Invalid saved memory execution authority')
    elif value['confirmation']!=('start_memory_investigation_with_api_cost' if value['provider']=='groq' else 'start_memory_investigation'):
        raise ValueError('Explicit confirmation is required for the selected memory execution')
    result=deepcopy(value);result['selections']=validate_selections(value['selections'])
    return result


def _key(request):
    return digest({key:request[key] for key in ('parent_job_id','parent_pack_sha256','selection_sha256')})


class MemoryFollowups:
    def __init__(self,host):self.host=host

    def _db(self):
        db=sqlite3.connect(self.host.directory/'memory_followups.sqlite',timeout=30)
        db.execute('CREATE TABLE IF NOT EXISTS intents ('
            'preview_key TEXT PRIMARY KEY, request_sha TEXT NOT NULL, target_job TEXT NOT NULL UNIQUE, selection BLOB NOT NULL)')
        return db

    def _parent(self,request):
        parent,raw,_=self.host.memory._source(request['parent_job_id'],request['parent_pack_sha256'])
        _,_,depth=self.host._load_pack(request['parent_job_id'])
        self.host._reject_waiting_fork(parent)
        if depth>=8:raise ValueError('Memory investigation ancestry exceeds the product bound')
        return parent,raw,depth

    def _admit(self,request,snapshot=None):
        parent,raw,depth=self._parent(request)
        if request['policy_sha256']!=policy_sha256():raise ValueError('Memory execution policy changed')
        from .question_jobs import replay_snapshot
        snapshot=snapshot or replay_snapshot(parent)
        selection=preview_memory_selection(self.host.memory.store,raw,snapshot,request['selections'])
        if selection['sha256']!=request['selection_sha256']:
            raise ValueError('Memory preview/source changed; inspect a fresh preview')
        if len(parent['case']['problem_statement'])+len(selection['additional_context'])+50>100000:
            raise ValueError('Combined memory-assisted query exceeds the product bound')
        return parent,raw,depth,selection,snapshot

    def assert_intent(self,identifier,request,*,wait_for_dispatch=False):
        validate_request(request,saved=True)
        db=(self._db() if wait_for_dispatch else sqlite3.connect(
            (self.host.directory/'memory_followups.sqlite').as_uri()+'?mode=ro',uri=True,timeout=30))
        try:
            if wait_for_dispatch:db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT request_sha,target_job,selection FROM intents WHERE preview_key=?',(_key(request),)).fetchone()
            db.commit()
        finally:db.close()
        if row is None or row[:2]!=(digest(request),identifier):raise ValueError('Missing or changed durable memory execution intent')
        saved=json.loads(self.host._job_path(identifier).read_bytes())
        if (saved.get('id')!=identifier or saved.get('request')!=request
                or any(saved.get(k)!=request[k] for k in ('mode','provider','depth'))):
            raise ValueError('Memory execution job authority changed')
        parent,raw,depth=self._parent(request)
        selection=validate_saved_selection(json.loads(row[2]),raw)
        if selection['sha256']!=request['selection_sha256'] or selection['selections']!=request['selections']:
            raise ValueError('Stored memory selection differs from its execution request')
        return parent,raw,depth,selection

    def input(self,identifier,request,*,admission=True,wait_for_dispatch=False):
        recorded=self.assert_intent(identifier,request,wait_for_dispatch=wait_for_dispatch)
        return self._admit(request) if admission else recorded

    def submit(self,request):
        public=validate_request(request)
        candidate={k:v for k,v in public.items() if k!='confirmation'}
        saved=dict(candidate,mode='memory_followup',policy_sha256=policy_sha256())
        # Check an existing intent before current Git/policy. A failed or
        # cancelled attempt consumes this preview too; it cannot mint retries.
        db=self._db()
        try:existing=db.execute('SELECT target_job FROM intents WHERE preview_key=?',(_key(saved),)).fetchone()
        finally:db.close()
        if existing:
            old=json.loads(self.host._job_path(existing[0]).read_bytes())['request']
            if any(old.get(k)!=v for k,v in candidate.items()):raise ValueError('This preview already has a different execution intent')
            self.assert_intent(existing[0],old)
            return self.host.job(existing[0])
        # Share the same capacity budget as preview/capture before expensive
        # admission; release it before enqueue acquires its own worker slot.
        parent,_,_=self._parent(saved)
        with self.host.memory._snapshot(parent) as snapshot:
            _,_,_,selection,_=self._admit(saved,snapshot)
        db=self._db()
        try:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT request_sha,target_job FROM intents WHERE preview_key=?',(_key(saved),)).fetchone()
            if existing:
                old=json.loads(self.host._job_path(existing[1]).read_bytes())['request']
                if any(old.get(k)!=v for k,v in candidate.items()):raise ValueError('This preview already has a different execution intent')
                db.commit()
                self.assert_intent(existing[1],old)
                return self.host.job(existing[1])
            identifier=uuid.uuid4().hex[:24]
            db.execute('INSERT INTO intents VALUES (?,?,?,?)',(_key(saved),digest(saved),identifier,canonical_json(selection).encode()))
            row=self.host._enqueue(saved,self.host._run,identifier=identifier)
            db.commit();return row
        except BaseException:db.rollback();raise
        finally:db.close()

    def validate_child(self,identifier,pack,request):
        parent,_,_,selection=self.input(identifier,request,admission=False)
        expected={'case_id':'followup:'+identifier,'repository':parent['case']['repository'],
                  'revision':parent['snapshot']['commit_sha'],'issue_created_at':None,
                  'problem_statement':parent['case']['problem_statement']+'\n\n[User follow-up context; unverified]\n'+selection['additional_context']}
        if parent['case'].get('issue_url'):expected['issue_url']=parent['case']['issue_url']
        link=pack.get('followup') or {}
        if (digest(pack['case'])!=digest(expected) or digest(pack['snapshot'])!=digest(parent['snapshot'])
                or link.get('memory_selection')!=selection or link.get('memory_policy_sha256')!=request['policy_sha256']
                or link.get('additional_context')!=selection['additional_context']
                or link.get('parent_job_id')!=request['parent_job_id'] or link.get('parent_pack_sha256')!=request['parent_pack_sha256']
                or link.get('clarification') is not None or link.get('check_observations') is not None
                or pack.get('repository_context') is not None or pack['leakage_audit']['strict_issue_time_verified'] is not False
                or self.host._inspect_query(pack['case']['problem_statement'],self.host._cue_text(parent))!=pack.get('intake')):
            raise ValueError('Memory-assisted child differs from its immutable selection/source')
