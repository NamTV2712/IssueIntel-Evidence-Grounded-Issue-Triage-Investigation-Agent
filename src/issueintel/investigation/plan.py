"""Versioned user-reported checks; never execute code or certify a fix."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
import json

from issueintel.runtime.io import digest


class PlanStore:
    def __init__(self, path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(self.connect()) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS events (job_id TEXT NOT NULL, revision INTEGER NOT NULL, sha256 TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(job_id,revision))')
            connection.commit()

    def connect(self):
        return sqlite3.connect(self.path,timeout=5)

    @staticmethod
    def initial(job_id, pack_sha256, steps):
        if (not isinstance(job_id,str) or not re.fullmatch(r'[a-f0-9]{24}',job_id)
                or not isinstance(pack_sha256,str) or not re.fullmatch(r'[a-f0-9]{64}',pack_sha256)
                or not isinstance(steps,list) or len(steps)>50
                or any(not isinstance(step,str) or not step.strip() or len(step)>10000 for step in steps)):
            raise ValueError('Invalid source plan')
        state={'schema':'investigation_plan_v1','job_id':job_id,'source_pack_sha256':pack_sha256,
               'revision':0,'parent_sha256':None,'provenance':'user_reports_unverified',
               'fix_verified':False,'evaluation_performed':False,
               'steps':[{'id':f'step_{i+1}','description':text,'status':'todo','note':''} for i,text in enumerate(steps)]}
        return dict(state,sha256=digest(state))

    @staticmethod
    def validate_change(change, state):
        if not isinstance(change,dict) or set(change)!={'step_id','status','note'}:
            raise ValueError('Only step status and note may change')
        if (not isinstance(change['step_id'],str) or not isinstance(change['status'],str)
                or change['step_id'] not in {s['id'] for s in state['steps']}
                or change['status'] not in {'todo','in_progress','blocked','reported_done'}
                or not isinstance(change['note'],str) or len(change['note'])>2000
                or (change['status'] in {'blocked','reported_done'} and not change['note'].strip())):
            raise ValueError('Provide a valid check and an observation for blocked/completed checks')

    def _replay(self, connection, initial):
        state=initial
        rows=connection.execute('SELECT revision,sha256,payload FROM events WHERE job_id=? ORDER BY revision',(state['job_id'],)).fetchall()
        if len(rows)>200:
            raise ValueError('Plan revision limit exceeded')
        history=[]
        for revision,sha,payload in rows:
            event=json.loads(payload)
            if (digest(event)!=sha or event.get('schema')!='investigation_plan_event_v1'
                    or event.get('provenance')!='user_report_unverified'
                    or revision!=state['revision']+1 or event['revision']!=revision
                    or event['source_pack_sha256']!=initial['source_pack_sha256']
                    or event['job_id']!=initial['job_id'] or event['parent_sha256']!=state['sha256']):
                raise ValueError('Plan history integrity changed')
            self.validate_change(event['change'],state)
            state=self._apply(state,event,sha)
            history.append(dict(event,sha256=sha))
        return dict(state,history=history)

    @staticmethod
    def _apply(state,event,sha):
        change=event['change']
        return dict(state,revision=event['revision'],parent_sha256=event['parent_sha256'],sha256=sha,
                    steps=[dict(step,status=change['status'],note=change['note']) if step['id']==change['step_id'] else dict(step) for step in state['steps']])

    def load(self,job_id,pack_sha256,steps):
        initial=self.initial(job_id,pack_sha256,steps)
        with closing(self.connect()) as connection:
            return self._replay(connection,initial)

    def update(self,job_id,pack_sha256,steps,request):
        if not isinstance(request,dict) or set(request)!={'expected_sha256','step_id','status','note'}:
            raise ValueError('An exact plan revision is required')
        initial=self.initial(job_id,pack_sha256,steps)
        with closing(self.connect()) as connection:
            try:
                # Serializes writers across HTTP threads and app processes.
                connection.execute('BEGIN IMMEDIATE')
                state=self._replay(connection,initial)
                if request['expected_sha256']!=state['sha256']:
                    raise ValueError('Plan changed; reload before saving')
                if state['revision']>=200:
                    raise ValueError('Plan revision limit reached')
                change={key:request[key] for key in ('step_id','status','note')}
                self.validate_change(change,state)
                event={'schema':'investigation_plan_event_v1','job_id':job_id,'source_pack_sha256':pack_sha256,
                       'revision':state['revision']+1,'parent_sha256':state['sha256'],'change':change,
                       'recorded_at':datetime.now(timezone.utc).isoformat(),'provenance':'user_report_unverified'}
                sha=digest(event)
                connection.execute('INSERT INTO events VALUES (?,?,?,?)',(job_id,event['revision'],sha,json.dumps(event,ensure_ascii=False)))
                result=self._replay(connection,initial)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
