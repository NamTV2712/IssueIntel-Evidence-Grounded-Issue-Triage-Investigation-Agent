"""Run a clean source checkout with Python + Git; no pip/venv/data bootstrap."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys


def check_runtime(root):
    root=Path(root).resolve()
    helper=Path(__file__).resolve().parent/'src/issueintel/runtime/readiness.py'
    if not helper.is_file():
        return {'ready':False,'python':'.'.join(map(str,sys.version_info[:3])),
                'git_available':False,'required_python_packages':[],'root':str(root),
                'problems':['Run this file from a complete IssueIntel source checkout.']}
    # Load just the standard-library helper, including under Python -I -S.
    spec=importlib.util.spec_from_file_location('issueintel_prerequisites',helper)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report=module.prerequisites(root)
    return {'ready':report['ready'],'python':report['python'],
            'git_available':report['git_available'],'required_python_packages':[],
            'root':str(root),'problems':[module.PREREQUISITE_MESSAGES[code] for code in report['problems']]}


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
