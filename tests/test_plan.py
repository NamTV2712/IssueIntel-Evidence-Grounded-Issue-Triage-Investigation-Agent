from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from issueintel.investigation.plan import PlanStore
from test_workbench import completed, local_server, http


JOB='a'*24
PACK='b'*64


def test_plan_versions_replay_and_preserve_unverified_observations(tmp_path):
    path=tmp_path/'plans.sqlite';store=PlanStore(path)
    initial=store.load(JOB,PACK,['Reproduce with empty input'])
    assert initial['revision']==0 and initial['steps'][0]['status']=='todo'
    result=store.update(JOB,PACK,['Reproduce with empty input'],{'expected_sha256':initial['sha256'],
                       'step_id':'step_1','status':'reported_done','note':'Observed locally; not independently verified'})
    assert result['revision']==1 and result['parent_sha256']==initial['sha256']
    assert result['fix_verified'] is False and result['evaluation_performed'] is False
    assert PlanStore(path).load(JOB,PACK,['Reproduce with empty input'])==result
    with pytest.raises(ValueError,match='changed'):
        store.load(JOB,'c'*64,['Reproduce with empty input'])
    with sqlite3.connect(path) as connection:
        event=json.loads(connection.execute('SELECT payload FROM events').fetchone()[0])
        event['change']['note']='rewritten'
        connection.execute('UPDATE events SET payload=?',(json.dumps(event),))
    with pytest.raises(ValueError,match='integrity'):store.load(JOB,PACK,['Reproduce with empty input'])


def test_concurrent_updates_from_same_revision_have_one_winner(tmp_path):
    path=tmp_path/'plans.sqlite';stores=[PlanStore(path),PlanStore(path)]
    initial=stores[0].load(JOB,PACK,['Inspect parser'])
    def save(index):
        try:
            return stores[index].update(JOB,PACK,['Inspect parser'],{'expected_sha256':initial['sha256'],
                                'step_id':'step_1','status':'in_progress','note':str(index)})
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(save,[0,1]))
    assert sum(result is not None for result in results)==1
    assert stores[0].load(JOB,PACK,['Inspect parser'])['revision']==1


@pytest.mark.parametrize('status,note',[('reported_done',''),('blocked',''),('verified','claims a fix'),('todo','x'*2001)])
def test_invalid_or_unsubstantiated_status_rejected(tmp_path,status,note):
    store=PlanStore(tmp_path/'plans.sqlite');initial=store.load(JOB,PACK,['Inspect parser'])
    with pytest.raises(ValueError):store.update(JOB,PACK,['Inspect parser'],{'expected_sha256':initial['sha256'],
                'step_id':'step_1','status':status,'note':note})
    assert store.load(JOB,PACK,['Inspect parser'])==initial


def test_plan_http_persists_without_mutating_pack_or_running_provider(tmp_path):
    with local_server(tmp_path) as server:
        job=completed(server.workbench,server.workbench.submit({'mode':'example'})['id'])
        raw=server.workbench.result_bytes(job['id'])
        headers={'X-IssueIntel-Token':server.session_token,'Content-Type':'application/json'}
        route='/api/jobs/'+job['id']+'/plan'
        status,_,body=http(server,'GET',route,headers=headers);initial=json.loads(body)
        assert status==200
        request={'expected_sha256':initial['sha256'],'step_id':'step_1','status':'blocked','note':'Need a minimal reproducer'}
        status,_,body=http(server,'POST',route,json.dumps(request),headers)
        assert status==200 and json.loads(body)['steps'][0]['status']=='blocked'
        assert http(server,'POST',route,json.dumps(request),headers)[0]==400
        assert server.workbench.result_bytes(job['id'])==raw
