"""Run a clean source checkout with Python + Git; no pip/venv/data bootstrap."""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


def check_runtime(root):
    root=Path(root).resolve()
    problems=[]
    if sys.version_info<(3,11):
        problems.append('Python 3.11 or newer is required.')
    if not (root/'src/issueintel/interfaces/web.py').is_file() or not (root/'configs/runtime.toml').is_file():
        problems.append('Run this file from a complete IssueIntel source checkout.')
    git=shutil.which('git')
    if not git:
        problems.append('Git is required. Install Git and restart the terminal.')
    else:
        try:
            result=subprocess.run([git,'--version'],capture_output=True,timeout=10)
            if result.returncode:
                problems.append('Git could not start. Check the Git installation.')
        except (OSError,subprocess.TimeoutExpired):
            problems.append('Git could not start. Check the Git installation.')
    try:
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE VIRTUAL TABLE check_fts USING fts5(text)')
    except sqlite3.DatabaseError:
        problems.append('This Python runtime needs SQLite with FTS5 support.')
    return {'ready':not problems,'python':'.'.join(map(str,sys.version_info[:3])),
            'git_available':git is not None,'required_python_packages':[],
            'root':str(root),'problems':problems}


def main(argv=None, *, root=None):
    parser=argparse.ArgumentParser(description='Start IssueIntel locally with Python + Git. No dependency install is needed.')
    parser.add_argument('--port',type=int,default=8787)
    parser.add_argument('--check',action='store_true',help='Check prerequisites without starting the server or calling any API')
    args=parser.parse_args(argv)
    root=Path(root).resolve() if root is not None else Path(__file__).resolve().parent
    if not 0<=args.port<=65535:
        parser.error('Choose a port from 0 to 65535')
    report=check_runtime(root)
    if args.check:
        print(json.dumps(report,indent=2))
        return 0 if report['ready'] else 2
    if report['problems']:
        for problem in report['problems']:
            print('IssueIntel: '+problem,file=sys.stderr)
        return 2
    # Package source is explicit, independent of the terminal's current folder.
    # The core is standard-library-only; optional tokenizer/Parquet extras stay optional.
    sys.path.insert(0,str(root/'src'))
    from issueintel.interfaces.cli import main as cli
    return cli(['--root',str(root),'serve','--port',str(args.port)])


if __name__=='__main__':
    raise SystemExit(main())
