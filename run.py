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
    parser.add_argument('--port',type=int,default=None)
    parser.add_argument('--webhook-relay',action='store_true',help='Receive only signed webhooks for a separate HTTPS gateway; no Workbench UI')
    parser.add_argument('--workbench-port',type=int,default=None,help='Local Workbench target port for --webhook-relay (default 8787)')
    parser.add_argument('--check',action='store_true',help='Check prerequisites without starting the server or calling any API')
    args=parser.parse_args(argv)
    root=Path(root).resolve() if root is not None else Path(__file__).resolve().parent
    port=args.port if args.port is not None else 8788 if args.webhook_relay else 8787
    if not 0<=port<=65535:
        parser.error('Choose a port from 0 to 65535')
    if args.workbench_port is not None and not args.webhook_relay:
        parser.error('--workbench-port requires --webhook-relay')
    workbench_port=args.workbench_port if args.workbench_port is not None else 8787
    if not 1<=workbench_port<=65535 or args.webhook_relay and port==workbench_port:
        parser.error('Choose a different Workbench target port from 1 to 65535')
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
    command=(['webhook-relay','--port',str(port),'--workbench-port',str(workbench_port)]
             if args.webhook_relay else ['serve','--port',str(port)])
    return cli(['--root',str(root),*command])


if __name__=='__main__':
    raise SystemExit(main())
