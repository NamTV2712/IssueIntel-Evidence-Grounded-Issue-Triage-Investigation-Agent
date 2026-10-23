"""CLI and clean-checkout entry points start only the explicit signed relay."""
from http.client import HTTPConnection
import os
import queue
import re
import shutil
import subprocess
import sys
import threading

import pytest

from issueintel.interfaces.cli import main
from test_quick_start import launcher,ROOT


def test_cli_relay_uses_local_secret_without_constructing_workbench(tmp_path,monkeypatch,capsys):
    (tmp_path/'.env').write_text('GITHUB_WEBHOOK_SECRET=synthetic-relay-secret\n',encoding='utf-8')
    monkeypatch.setenv('GITHUB_WEBHOOK_SECRET','temporary-value-for-monkeypatch-restore')
    monkeypatch.delenv('GITHUB_WEBHOOK_SECRET')
    calls=[]
    monkeypatch.setattr('issueintel.integrations.webhook_relay.serve_relay',lambda **kw:calls.append(kw))
    def deny(*args,**kwargs):pytest.fail('Relay constructed a Workbench/provider/config/benchmark')
    monkeypatch.setattr('issueintel.investigation.workbench.Workbench.__init__',deny)
    monkeypatch.setattr('issueintel.runtime.config.RuntimeConfig.load',deny)
    assert main(['--root',str(tmp_path),'webhook-relay','--port','8788','--workbench-port','8787'])==0
    assert calls==[{'port':8788,'workbench_port':8787,'secret':'synthetic-relay-secret'}]
    output=capsys.readouterr()
    assert 'synthetic-relay-secret' not in output.out+output.err
    assert not (tmp_path/'artifacts').exists() and not (tmp_path/'data').exists()


def test_launcher_relay_default_and_explicit_ports_are_separate_from_workbench(monkeypatch):
    calls=[]
    monkeypatch.setattr('issueintel.interfaces.cli.main',lambda words:calls.append(words) or 0)
    assert launcher.main(['--webhook-relay'])==0
    assert calls[-1][-5:]==['webhook-relay','--port','8788','--workbench-port','8787']
    assert launcher.main(['--webhook-relay','--port','0','--workbench-port','9876'])==0
    assert calls[-1][-5:]==['webhook-relay','--port','0','--workbench-port','9876']
    assert launcher.main([])==0 and calls[-1][-3:]==['serve','--port','8787']


@pytest.mark.parametrize('arguments',[
    ['--workbench-port','8787'],['--webhook-relay','--workbench-port','0'],
    ['--webhook-relay','--port','8787'],['--webhook-relay','--port','65536'],
])
def test_launcher_rejects_wrong_relay_scope_or_self_forwarding(arguments):
    with pytest.raises(SystemExit):launcher.main(arguments)


def test_clean_clone_relay_needs_no_site_packages_and_exposes_no_ui(tmp_path):
    clone=tmp_path/'clone';clone.mkdir()
    shutil.copy2(ROOT/'run.py',clone/'run.py')
    shutil.copytree(ROOT/'src',clone/'src',ignore=shutil.ignore_patterns('__pycache__','*.egg-info'))
    shutil.copytree(ROOT/'configs',clone/'configs')
    (clone/'.env').write_text('GITHUB_WEBHOOK_SECRET=synthetic-clean-clone-relay-secret\n',encoding='utf-8')
    environment={name:value for name,value in os.environ.items() if not re.search('TOKEN|SECRET|API_KEY|PRIVATE_KEY',name,re.I)}
    process=subprocess.Popen([sys.executable,'-I','-S',str(clone/'run.py'),'--webhook-relay','--port','0'],
        cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=environment)
    try:
        lines=queue.Queue();threading.Thread(target=lambda:lines.put(process.stdout.readline()),daemon=True).start()
        startup=lines.get(timeout=15)
        assert 'synthetic-clean-clone-relay-secret' not in startup
        port=int(re.search(r'http://127\.0\.0\.1:([0-9]+)',startup)[1])
        for path in ('/','/app.js','/api/readiness','/api/jobs'):
            connection=HTTPConnection('127.0.0.1',port,timeout=3)
            try:
                connection.request('GET',path);response=connection.getresponse();raw=response.read()
                assert response.status==404 and b'issueintel-session' not in raw
            finally:connection.close()
        assert not (clone/'artifacts').exists() and not (clone/'data').exists() and not (clone/'.venv').exists()
    finally:
        process.terminate();_,error=process.communicate(timeout=10)
        assert 'synthetic-clean-clone-relay-secret' not in error
