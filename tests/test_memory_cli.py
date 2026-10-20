"""Memory commands share the private store and never spend API budget."""
import json

import pytest

from issueintel.interfaces.cli import main
from issueintel.repository.memory_store import MemoryStore
from test_repository_memory_store import lesson_source
from test_focused_retrieval import fixture_repo


def invoke(root,capsys,*arguments):
    code=main(['--root',str(root),*arguments]);output=capsys.readouterr()
    return code,json.loads(output.out) if code==0 else output.err


def test_capture_search_show_assess_and_lesson_use_the_same_private_store(tmp_path,lesson_source,capsys,monkeypatch):
    case,_,snapshot,_,raw,model=lesson_source
    path=tmp_path/'source-pack.json';path.write_bytes(raw);before=len(model.calls)
    monkeypatch.setattr('issueintel.interfaces.cli.load_local_env',lambda *a:pytest.fail('Memory command read credentials'))
    monkeypatch.setattr('issueintel.runtime.config.RuntimeConfig.load',lambda *a:pytest.fail('Memory command read model config'))
    code,architecture=invoke(tmp_path,capsys,'memory-capture','--pack',str(path),'--max-facts','2')
    assert code==0 and len(architecture['memory']['facts'])==2
    identifier=architecture['receipt']['id']
    code,listed=invoke(tmp_path,capsys,'memory-list','--repo',case['repository'],'--query','recover_none')
    assert code==0 and listed['results'][0]['id']==identifier
    assert invoke(tmp_path,capsys,'memory-show','--id',identifier)==(0,architecture)
    code,assessment=invoke(tmp_path,capsys,'memory-assess','--id',identifier,'--pack',str(path))
    assert code==0 and assessment['counts']=={'revalidated_static':2}
    code,lesson=invoke(tmp_path,capsys,'memory-capture','--kind','lesson','--pack',str(path),'--hypothesis','h0','--note','Review chưa xác minh')
    assert code==0 and lesson['memory']['author_note']=='Review chưa xác minh'
    assert MemoryStore(tmp_path).read(lesson['receipt']['id'])==lesson
    assert len(model.calls)==before


@pytest.mark.parametrize('arguments',[
    ['--kind','lesson'],
    ['--hypothesis','h0'],
    ['--note','Ignored notes are not allowed'],
    ['--max-facts','0'],
    ['--kind','lesson','--hypothesis','missing'],
    ['--kind','lesson','--hypothesis','h0','--max-facts','10'],
])
def test_invalid_capture_does_not_create_memory_artifacts(tmp_path,lesson_source,capsys,arguments):
    _,_,_,_,raw,_=lesson_source;path=tmp_path/'source-pack.json';path.write_bytes(raw)
    code,error=invoke(tmp_path,capsys,'memory-capture','--pack',str(path),*arguments)
    assert code==2 and 'IssueIntel:' in error
    assert not (tmp_path/'artifacts/repository_memory').exists()


@pytest.mark.parametrize('change',[
    lambda p:p['case'].update(split='main_test'),
    lambda p:p['snapshot'].update(protocol='issue_time'),
    lambda p:p['case'].update(instance_id='synthetic restricted task'),
])
def test_restricted_pack_is_denied_before_reading_git(tmp_path,lesson_source,capsys,monkeypatch,change):
    _,_,_,pack,_,_=lesson_source;change(pack);path=tmp_path/'restricted.json';path.write_text(json.dumps(pack))
    monkeypatch.setattr('issueintel.investigation.question_jobs.replay_snapshot',lambda *a:pytest.fail('Restricted source read Git'))
    code,error=invoke(tmp_path,capsys,'memory-capture','--pack',str(path))
    assert code==2 and 'product source outside benchmark splits' in error
    assert not (tmp_path/'artifacts/repository_memory').exists()


def test_malformed_pack_and_missing_capture_return_controlled_errors(tmp_path,capsys):
    path=tmp_path/'malformed.json';path.write_text('{}')
    assert invoke(tmp_path,capsys,'memory-capture','--pack',str(path))[0]==2
    assert invoke(tmp_path,capsys,'memory-show','--id','capture_'+'a'*32)[0]==2
    db=tmp_path/'artifacts/repository_memory/memory.sqlite';db.write_bytes(b'not sqlite')
    code,error=invoke(tmp_path,capsys,'memory-list','--repo','synthetic/repo')
    assert code==2 and 'database or capture structure' in error
