"""A clean clone must run without site-packages, pip, node or benchmark data."""
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.request import Request,urlopen

import pytest


ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('issueintel_quick_start',ROOT/'run.py')
launcher=importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_check_runtime_never_requires_optional_dependencies_or_secrets(capsys):
    assert launcher.main(['--check'])==0
    report=json.loads(capsys.readouterr().out)
    assert report['ready'] and report['required_python_packages']==[]
    assert not any(key in report for key in ('GITHUB_TOKEN','GROQ_API_KEY'))


def test_missing_git_is_actionable_without_install_or_network(monkeypatch,capsys):
    monkeypatch.setattr(shutil,'which',lambda _:None)
    assert launcher.main([])==2
    assert 'Install Git' in capsys.readouterr().err


def test_port_and_incomplete_source_checks(tmp_path,capsys):
    with pytest.raises(SystemExit):
        launcher.main(['--port','65536'])
    assert launcher.main([],root=tmp_path)==2
    assert 'complete IssueIntel source checkout' in capsys.readouterr().err


def test_clean_clone_runs_synthetic_investigation_with_no_site_packages(tmp_path):
    clone=tmp_path/'clone'
    clone.mkdir()
    shutil.copy2(ROOT/'run.py',clone/'run.py')
    shutil.copytree(ROOT/'src',clone/'src',ignore=shutil.ignore_patterns('__pycache__','*.egg-info'))
    shutil.copytree(ROOT/'configs',clone/'configs')
    # -I -S disables user/environment/site imports. No package installation occurs.
    check=subprocess.run([sys.executable,'-I','-S',str(clone/'run.py'),'--check'],cwd=tmp_path,
                         capture_output=True,text=True,timeout=15)
    assert check.returncode==0 and json.loads(check.stdout)['ready']
    process=subprocess.Popen([sys.executable,'-I','-S',str(clone/'run.py'),'--port','0'],cwd=tmp_path,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        import queue
        import threading
        lines=queue.Queue()
        threading.Thread(target=lambda:lines.put(process.stdout.readline()),daemon=True).start()
        line=lines.get(timeout=20)
        match=re.search(r'http://127\.0\.0\.1:[0-9]+',line)
        assert match,line
        url=match[0]
        html=urlopen(url,timeout=5).read().decode()
        token=re.search(r'name="issueintel-session" content="([^"]+)"',html)[1]
        headers={'X-IssueIntel-Token':token,'Content-Type':'application/json'}
        catalog=json.load(urlopen(Request(url+'/api/catalog',headers=headers),timeout=5))
        assert catalog['accepted']==0
        job=json.load(urlopen(Request(url+'/api/investigations',data=b'{"mode":"example"}',headers=headers),timeout=5))
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            state=json.load(urlopen(Request(url+'/api/jobs/'+job['id'],headers=headers),timeout=5))
            if state['status'] not in {'queued','running'}:
                break
            time.sleep(.05)
        assert state['status']=='completed',state
        pack=json.load(urlopen(Request(url+'/api/jobs/'+job['id']+'/pack.json',headers=headers),timeout=5))
        assert pack['annotation'] is None and any(e['kind']=='code' for e in pack['evidence'])
        assert not (clone/'.venv').exists() and not (clone/'data/benchmark').exists()
    finally:
        process.terminate()
        process.communicate(timeout=10)
