from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from issueintel.runtime.io import load_local_env, read_jsonl, write_json
from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.budget import BudgetDenied


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="issueintel")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Counts, integrity, pending layers and budget")
    handoff = commands.add_parser('verify-handoff', help='Verify a portable handoff offline without Git, credentials or model calls')
    handoff.add_argument('archive',type=Path)
    handoff.add_argument('--expected-sha256')
    memory_capture=commands.add_parser('memory-capture',help='Retain source-bound architecture or unverified prior hypotheses locally; no model')
    memory_capture.add_argument('--pack',type=Path,required=True)
    memory_capture.add_argument('--kind',choices=('architecture','lesson'),default='architecture')
    memory_capture.add_argument('--max-facts',type=int)
    memory_capture.add_argument('--hypothesis',action='append',default=[])
    memory_capture.add_argument('--note',default='')
    memory_list=commands.add_parser('memory-list',help='Search private memory without Git, credentials or model calls')
    memory_list.add_argument('--repo',required=True)
    memory_list.add_argument('--query',default='')
    memory_list.add_argument('--limit',type=int,default=20)
    memory_show=commands.add_parser('memory-show',help='Replay an immutable private memory capture')
    memory_show.add_argument('--id',required=True)
    memory_assess=commands.add_parser('memory-assess',help='Compare captured dependencies with another product pack revision; no model')
    memory_assess.add_argument('--id',required=True)
    memory_assess.add_argument('--pack',type=Path,required=True)
    commands.add_parser("bootstrap", help="Download pinned SWE-bench sources and import cases")
    web = commands.add_parser("serve", help="Run the read-only local investigation workbench")
    web.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    web.add_argument("--port", type=int, default=8787)
    repository_sync = commands.add_parser("sync-repository", help="Index public GitHub code/issues/PRs locally; resume committed pages, no LLM")
    repository_sync.add_argument("--repo", required=True)
    repository_sync.add_argument("--revision", default="HEAD")
    repository_sync.add_argument("--max-requests", type=int, default=50, help="GitHub GET budget for this invocation (1–500)")
    repository_sync.add_argument("--refresh", action="store_true", help="After completion, start a new reconciliation scan")
    repository_status = commands.add_parser("repository-status", help="Read local repository index coverage without network")
    repository_status.add_argument("--repo", required=True)
    repository_search = commands.add_parser("search-repository", help="Search local GitHub history or revision-bound code, without network/LLM")
    repository_search.add_argument("--repo", required=True)
    repository_search.add_argument("--query", required=True)
    repository_search.add_argument("--code", action="store_true")
    repository_search.add_argument("--limit", type=int, default=10)
    repository_item = commands.add_parser("repository-item", help="Read a source/hash-bound local issue/PR/comment/file/commit")
    repository_item.add_argument("--repo", required=True)
    repository_item.add_argument("--key", required=True, help="For example issue:12 or pull:34")
    repository_connect = commands.add_parser("connect-repository", help="Enable durable automatic sync while the local server runs")
    repository_connect.add_argument("--repo", required=True)
    repository_connect.add_argument("--revision", default="HEAD")
    repository_pause = commands.add_parser("pause-repository", help="Pause automatic sync; preserve all local receipts/data")
    repository_pause.add_argument("--repo", required=True)
    commands.add_parser("github-connections", help="Read automatic GitHub connections without network or secrets")
    strict_dev = commands.add_parser("build-strict-dev", help="DEV-only deterministic eligibility; no LLM or evaluation")
    strict_dev.add_argument("--namespace", choices=("main-dev-strict-v1",), default="main-dev-strict-v1")
    strict_dev.add_argument("--target", type=int, choices=(400,), default=400)
    strict_dev.add_argument("--concurrency", type=int, default=2)
    strict_dev.add_argument("--pool")
    strict_dev.add_argument("--resume", action="store_true", help="Replay/reuse valid stage receipts (always enforced)")
    strict_dev.add_argument("--acquire", action="store_true", help="Acquire missing public historical proofs; never calls a model")
    strict_dev.add_argument("--limit", type=int)
    strict_dev.add_argument("--depth", type=int, default=256)
    strict_dev.add_argument("--hours", type=int, default=3)
    pilot = commands.add_parser("register-dev-pilot", help="Register paired 20b/120b diagnostic on frozen accepted Main Dev")
    pilot.add_argument("--cohort", required=True)
    pilot.add_argument("--count", type=int, default=30)
    pilot_run = commands.add_parser("run-dev-pilot", help="Resume registered DEV-only diagnostic; never evaluates holdout")
    pilot_run.add_argument("--pilot", required=True)
    pilot_run.add_argument("--limit", type=int)
    policy_register=commands.add_parser('register-policy-pilot',help='Preregister paired DEV policies or the four-case output reliability regression')
    policy_register.add_argument('--reliability-parent',help='Exact sealed parent experiment SHA for the authorized four-case regression (no gold scoring)')
    policy_run=commands.add_parser('run-policy-pilot',help='Run registered DEV policies; first --limit 1, one receipted output repair per arm, no transport retry')
    policy_run.add_argument('--experiment',required=True)
    policy_run.add_argument('--limit',type=int,default=25)
    policy_score=commands.add_parser('score-policy-pilot',help='Separate sealed DEV-only patch-file scoring and blank blind check review export')
    policy_score.add_argument('--experiment',required=True)
    dev_assets = commands.add_parser("prepare-dev-artifacts", help="Accepted Main Dev graph/retrieval and isolated localization; no LLM")
    dev_assets.add_argument("--cohort", required=True)
    dev_assets.add_argument("--limit", type=int)
    dev_assets.add_argument("--concurrency", type=int, choices=(1, 2), default=2)
    dev_registration = commands.add_parser("register-dev-artifacts", help="Freeze DEV artifact code/policy after replaying paired pilot receipts")
    dev_registration.add_argument("--cohort", required=True)
    dev_registration.add_argument("--reuse-from", help="Bind eligible existing paid fallback outputs with identical public inputs and generation semantics")
    dev_run = commands.add_parser("run-dev-artifacts", help="Resume independent paid stages on accepted Main Dev only")
    dev_run.add_argument("--recipe", required=True)
    dev_run.add_argument("--limit", type=int)
    dev_run.add_argument("--concurrency", type=int, choices=(1, 2), default=2)
    assets = commands.add_parser("build-assets", help="Dataset snapshots/graphs/localization, no predictions or evaluation")
    assets.add_argument("--limit", type=int, default=1)
    assets.add_argument("--all-splits", action="store_true", help="Dataset preparation only; never opens a model evaluation")
    dates = commands.add_parser("audit-dates", help="Targeted metadata lookup for Live candidates, not model inference")
    dates.add_argument("--limit", type=int, default=10)
    mapping = commands.add_parser("map-main", help="Map original Verified issue identities; dataset construction only")
    mapping.add_argument("--limit", type=int, default=10)
    mapping.add_argument("--all-splits", action="store_true")
    mapping.add_argument("--remap-pending", action="store_true")
    historical = commands.add_parser("prepare-asof", help="Bounded original-text/public-revision audit and historical assets, no inference")
    historical.add_argument("--limit", type=int, default=10)
    historical.add_argument("--all-splits", action="store_true")
    historical.add_argument("--case-id")
    historical.add_argument("--split", choices=("main_dev", "main_test", "temporal_candidate"))
    historical.add_argument("--retry-pending", action="store_true")
    historical.add_argument("--max-archive-hours", type=int, choices=(1, 2, 3), default=3)
    historical.add_argument("--query-policy", choices=("source_query", "original_opening_query"), default="original_opening_query")
    investigate = commands.add_parser("investigate", help="Investigate local repo or public GitHub issue/repo")
    investigate.add_argument("--repo", required=True, help="Local Git directory, owner/name or GitHub URL")
    investigate.add_argument("--revision", help="Exact revision, required except for a benchmark case")
    investigate.add_argument("--issue-file", type=Path)
    investigate.add_argument("--case-id", help="main_dev case only")
    investigate.add_argument("--provider", choices=("none", "groq"), default="none")
    investigate.add_argument('--durable',action='store_true',help='Use owned Workbench product jobs/journals and receipts; no automatic retry')
    investigate.add_argument("--controller", choices=("rules","jev"), default="rules",
        help="Jev is opt-in for --planned with Groq; requires separate Typesafe configuration and limits")
    investigate.add_argument("--output", type=Path, default=Path("artifacts/investigations"))
    investigate.add_argument("--no-critic", action="store_true")
    investigate.add_argument("--planned", action="store_true",
        help="Product-only planned workflow: 1–3 search rounds, at most four generation passes and one critic; generation repair slots remain bounded")
    investigate.add_argument("--refinement-rounds", type=int, choices=(0,1), default=0,
                             help="At most one hypothesis-focused retrieval round; may add a generation call")
    recovery=commands.add_parser('resume-stages',help='Preview owned product stage recovery; execution requires the exact binding SHA and explicit consent')
    recovery.add_argument('--job',required=True)
    recovery.add_argument('--binding-sha256')
    consent=recovery.add_mutually_exclusive_group()
    consent.add_argument('--with-api',action='store_true',help='Approve the preview-bound remaining API calls and their costs')
    consent.add_argument('--without-api',action='store_true',help='Approve only the preview-bound completion without new API calls')
    batch = commands.add_parser("annotate", help="Resumable dev annotation, budget guarded")
    batch.add_argument("--limit", type=int, default=1)
    batch.add_argument("--case-id")
    batch.add_argument("--judge", action="store_true", help="Automatic reference-relative gold judgment (dev only)")
    registration = commands.add_parser("register-annotation", help="Register immutable label-construction recipe before generation")
    registration.add_argument("--protocol", required=True, choices=("benchmark_base", "issue_time"))
    registration.add_argument("--query-policy", choices=("source_query", "original_opening_query"))
    registration.add_argument("--splits", required=True, nargs="+", choices=("main_dev", "main_test", "temporal_candidate"))
    registration.add_argument("--case-id", action="append")
    registration.add_argument("--top-k", type=int, default=8)
    registration.add_argument("--no-critic", action="store_true")
    registration.add_argument("--no-judge", action="store_true")
    construction = commands.add_parser("annotate-dataset", help="Construct registered labels, no evaluation or tuning")
    construction.add_argument("--recipe", required=True)
    construction.add_argument("--limit", type=int, default=800)
    release = commands.add_parser("register-release", help="Freeze complete strict constructions with explicit exclusions; no evaluation")
    release.add_argument("--recipe", required=True)
    release.add_argument("--splits", nargs="+", choices=("main_dev", "main_test", "temporal_candidate"))
    verification = commands.add_parser("verify-release", help="Replay a release's source, query, historical and stage bindings")
    verification.add_argument("--release", required=True)
    cohort = commands.add_parser("register-cohort", help="Register a separately selected Live extension; bootstrap remains immutable")
    cohort.add_argument("--selection", required=True)
    cohort_check = commands.add_parser("verify-cohort", help="Verify an explicit construction cohort, not an evaluation capability")
    cohort_check.add_argument("--cohort", required=True)
    commands.add_parser("review", help="Export blind review CSV for existing automatic dev annotations")
    agreement = commands.add_parser("agreement")
    agreement.add_argument("left", type=Path)
    agreement.add_argument("right", type=Path)
    args = parser.parse_args(argv)
    args.root = args.root.resolve()
    if args.command=='verify-handoff':
        from issueintel.investigation.portable_verify import main as verify_handoff
        return verify_handoff([str(args.archive)]+(['--expected-sha256',args.expected_sha256] if args.expected_sha256 else []))
    if args.command=='resume-stages' or args.command=='investigate' and args.durable:
        try:
            result=_owned_product(args)
            print(json.dumps(result,ensure_ascii=True,indent=2))
            return 2 if result.get('status') in {'failed','cancelled','interrupted'} else 0
        except (ValueError,RuntimeError,OSError) as error:
            print(f'IssueIntel: {error}',file=sys.stderr);return 2
    if args.command.startswith('memory-'):
        try:
            print(json.dumps(_memory(args),ensure_ascii=True,indent=2))
            return 0
        except (ValueError,RuntimeError,OSError) as error:
            print(f'IssueIntel: {error}',file=sys.stderr)
            return 2
    load_local_env(args.root / ".env")
    try:
        if args.command in {"connect-repository", "pause-repository", "github-connections"}:
            from issueintel.integrations.github_sync import GitHubSync
            sync = GitHubSync(args.root)
            if args.command == "connect-repository":
                result = sync.connect(args.repo, revision=args.revision)
            elif args.command == "pause-repository":
                from issueintel.repository.acquire import parse_github_reference
                result = sync.change(parse_github_reference(args.repo).repository.lower(), paused=True)
            else:
                result = sync.connections()
            print(json.dumps(result, indent=2))
        elif args.command in {"sync-repository", "repository-status", "search-repository", "repository-item"}:
            from issueintel.integrations.github_index import RepositoryIndex
            index = RepositoryIndex(args.root, args.repo)
            if args.command == "sync-repository":
                result = index.sync(revision=args.revision, max_requests=args.max_requests, refresh=args.refresh)
            elif args.command == "repository-status":
                result = index.status()
            elif args.command == "repository-item":
                result = index.item(args.key)
            else:
                result = (index.search_code if args.code else index.search)(args.query, limit=args.limit)
            print(json.dumps(result, ensure_ascii=True, indent=2))
        elif args.command == "prepare-dev-artifacts":
            from issueintel.benchmarks.dev_artifacts import prepare_dev_artifacts
            report = prepare_dev_artifacts(args.root, args.cohort, limit=args.limit, concurrency=args.concurrency)
            print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
        elif args.command == "register-dev-artifacts":
            from issueintel.benchmarks.dev_generation import register_dev_recipe
            print(json.dumps(register_dev_recipe(args.root, args.cohort, reuse_from=args.reuse_from), indent=2))
        elif args.command == "run-dev-artifacts":
            from issueintel.benchmarks.dev_generation import run_dev_artifacts
            report = run_dev_artifacts(args.root, args.recipe, limit=args.limit, concurrency=args.concurrency)
            print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
        elif args.command=='register-policy-pilot':
            from issueintel.benchmarks.policy_pilot import register_policy_pilot
            print(json.dumps(register_policy_pilot(args.root,reliability_parent=args.reliability_parent),indent=2))
        elif args.command=='run-policy-pilot':
            from issueintel.benchmarks.policy_pilot import run_policy_pilot
            print(json.dumps(run_policy_pilot(args.root,args.experiment,limit=args.limit),indent=2))
        elif args.command=='score-policy-pilot':
            from issueintel.benchmarks.policy_pilot import score_policy_pilot
            report=score_policy_pilot(args.root,args.experiment)
            print(json.dumps({key:value for key,value in report.items() if key!='cases'},indent=2))
        elif args.command == "register-dev-pilot":
            from issueintel.benchmarks.dev_pilot import register_pilot
            print(json.dumps(register_pilot(args.root, args.cohort, count=args.count), indent=2))
        elif args.command == "run-dev-pilot":
            from issueintel.benchmarks.dev_pilot import run_pilot
            print(json.dumps(run_pilot(args.root, args.pilot, limit=args.limit), indent=2))
        elif args.command == "build-strict-dev":
            from issueintel.benchmarks.strict_dev import build_strict_dev
            result = build_strict_dev(args.root, pool_id=args.pool, target=args.target, concurrency=args.concurrency,
                acquire=args.acquire, limit=args.limit, depth=args.depth, hours=args.hours)
            print(json.dumps({k: v for k, v in result.items() if k != "outcomes"}, indent=2))
        elif args.command == "bootstrap":
            from issueintel.benchmarks.importer import bootstrap
            result = bootstrap(args.root)
            print(json.dumps(result["counts"], indent=2))
        elif args.command == "status":
            from issueintel.benchmarks.store import verify_manifest
            config = RuntimeConfig.load(args.root)
            result = {"model": config.model, "budget": config.ledger().status()}
            if (args.root / "data/manifests/dataset.json").exists():
                from issueintel.benchmarks.inventory import verified_inventory
                result["dataset"] = verified_inventory(args.root)
            else:
                result["dataset"] = "not_imported"
            print(json.dumps(result, indent=2))
        elif args.command == "serve":
            from issueintel.interfaces.web import serve
            serve(args.root, host=args.host, port=args.port)
        elif args.command == "investigate":
            _investigate(args)
        elif args.command == "build-assets":
            from issueintel.benchmarks.workflow import build_assets
            print(json.dumps(build_assets(args.root, limit=args.limit, all_splits=args.all_splits), indent=2))
        elif args.command == "audit-dates":
            from issueintel.benchmarks.enrichment import enrich_temporal_dates
            print(json.dumps(enrich_temporal_dates(args.root, limit=args.limit), indent=2))
        elif args.command == "map-main":
            from issueintel.benchmarks.main_metadata import enrich_main_metadata
            print(json.dumps(enrich_main_metadata(args.root, limit=args.limit, all_splits=args.all_splits,
                remap_pending=args.remap_pending), indent=2))
        elif args.command == "prepare-asof":
            from issueintel.benchmarks.asof_workflow import prepare_asof
            print(json.dumps(prepare_asof(args.root, limit=args.limit, all_splits=args.all_splits,
                case_id=args.case_id, retry_pending=args.retry_pending, max_archive_hours=args.max_archive_hours,
                split=args.split, query_policy=args.query_policy), indent=2))
        elif args.command == "annotate":
            from issueintel.benchmarks.workflow import annotate_dev
            if not 1 <= args.limit <= 400:
                raise ValueError("limit must be between 1 and 400")
            print(json.dumps(annotate_dev(args.root, limit=args.limit, case_id=args.case_id, judge=args.judge), indent=2))
        elif args.command == "register-annotation":
            from issueintel.benchmarks.construction import register_annotation_recipe
            query_policy = args.query_policy
            if query_policy is None:
                decisions_path = args.root / "configs/author_decisions.json"
                decisions = json.loads(decisions_path.read_text(encoding="utf-8")) if decisions_path.exists() else {}
                query_policy = decisions.get("historical_query_policy", "source_query") if args.protocol == "issue_time" else "source_query"
            record = register_annotation_recipe(args.root, protocol=args.protocol, splits=args.splits,
                case_ids=args.case_id, top_k=args.top_k, judge=not args.no_judge, independent_critic=not args.no_critic,
                query_policy=query_policy)
            print(json.dumps({"recipe_sha256": record["recipe_sha256"], "registered_at": record["registered_at"],
                "protocol": record["recipe"]["protocol"], "case_count": len(record["recipe"]["cases"]),
                "path": "data/annotations/recipes/" + record["recipe_sha256"] + ".json",
                "mode": "dataset_construction", "evaluation_performed": False}, indent=2))
        elif args.command == "annotate-dataset":
            from issueintel.benchmarks.construction import annotate_dataset
            print(json.dumps(annotate_dataset(args.root, args.recipe, limit=args.limit), indent=2))
        elif args.command == "register-release":
            from issueintel.benchmarks.releases import register_dataset_release
            record = register_dataset_release(args.root, args.recipe, splits=args.splits)
            print(json.dumps({"release_sha256": record["release_sha256"], "path": record["path"],
                "counts": record["release"]["counts"], "evaluation_performed": False}, indent=2))
        elif args.command == "verify-release":
            from issueintel.benchmarks.releases import verify_dataset_release
            verified = verify_dataset_release(args.root, args.release)
            print(json.dumps({"release_sha256": verified["release_sha256"],
                "counts": verified["manifest"]["counts"], "integrity_verified": True,
                "evaluation_capability_granted": False, "evaluation_performed": False}, indent=2))
        elif args.command == "register-cohort":
            from issueintel.benchmarks.cohorts import register_extension_cohort
            print(json.dumps(register_extension_cohort(args.root, args.selection), indent=2))
        elif args.command == "verify-cohort":
            from issueintel.benchmarks.cohorts import verify_cohort
            verified = verify_cohort(args.root, args.cohort)
            print(json.dumps({"cohort_sha256": args.cohort, "counts": verified["counts"],
                "integrity_verified": True, "evaluation_authorized": False}, indent=2))
        elif args.command == "review":
            from issueintel.annotations import export_review
            annotations = [json.loads(path.read_text(encoding="utf-8")) for path in (args.root / "data/annotations/automatic").glob("*/annotation.json")]
            target = args.root / "artifacts/review/blind_review.csv"
            target.parent.mkdir(parents=True, exist_ok=True)
            export_review(annotations, target)
            print(target)
        elif args.command == "agreement":
            from issueintel.annotations import score_agreement
            print(json.dumps(score_agreement(args.left, args.right), indent=2))
        return 0
    except (ValueError, RuntimeError, OSError, PermissionError) as error:
        print(f"IssueIntel: {error}", file=sys.stderr)
        return 2


def _product_workbench(root):
    from issueintel.investigation.workbench import Workbench
    from issueintel.investigation.providers import GroqProvider
    from issueintel.runtime.typesafe import TypesafeClient
    def provider(config,**options):
        # Read credentials only when a worker actually needs a new paid call.
        # Preview and duplicate-intent reads never enter this factory.
        load_local_env(root/'.env')
        return GroqProvider(config,**options)
    def typesafe(config,**options):
        load_local_env(root/'.env')
        return TypesafeClient(config,**options)
    return Workbench(root,provider_factory=provider,typesafe_factory=typesafe,automatic_sync=False)


def _owned_product(args):
    if args.command=='resume-stages':
        execute=args.with_api or args.without_api
        if bool(args.binding_sha256)!=execute:
            raise ValueError('Preview without flags, or choose --binding-sha256 and exactly one of --with-api/--without-api')
        request=({'binding_sha256':args.binding_sha256,'confirmation':
            'resume_product_stages_with_api_cost' if args.with_api else 'resume_product_stages_without_api'} if execute else None)
    else:
        if (args.case_id or args.no_critic or args.controller=='jev' and (not args.planned or args.provider!='groq') or not args.revision
                or args.planned and args.refinement_rounds or args.output!=Path('artifacts/investigations')):
            raise ValueError('--durable requires original product input/revision and critic; Jev requires Planned/Groq; no benchmark case, custom output or combined Planned/refinement')
        request={'mode':'local' if args.issue_file else 'github','provider':args.provider,
            'revision':args.revision,'depth':'planned' if args.planned else 'deep' if args.refinement_rounds else 'quick'}
        if args.controller=='jev':request['controller']='jev'
        if args.issue_file:request.update(repository=args.repo,issue_text=args.issue_file.read_text(encoding='utf-8'))
        else:request['issue_url']=args.repo
        from issueintel.investigation.workbench import validate_request
        request=validate_request(request)
        if request.get('controller')=='jev' or request['mode']=='github' or not Path(request['repository']).is_dir():
            # Explicit initial remote acquisition may need the local GitHub
            # credential; a recovery preview/duplicate never enters this path.
            load_local_env(args.root/'.env')
    wb=_product_workbench(args.root);job=None
    try:
        if args.command=='resume-stages' and request is None:return wb.stage_recoveries.preview(args.job)
        job=wb.stage_recoveries.submit(args.job,request) if args.command=='resume-stages' else wb.submit(request)
        print(json.dumps({'job_id':job['id'],'automatic_retry':False}),file=sys.stderr,flush=True)
    except KeyboardInterrupt:
        if job is not None:wb.cancel(job['id'])
        raise
    finally:wb.close()
    # close() joins only this process's executor. An existing intent owned by
    # another live worker returns its current status; it is never resubmitted.
    return wb.job(job['id'])


def _memory(args):
    from issueintel.repository.memory_store import MemoryStore,MAX_OBJECT_BYTES,product_memory_pack
    store=MemoryStore(args.root)
    if args.command=='memory-list':return store.list(args.repo,query=args.query,limit=args.limit)
    if args.command=='memory-show':return store.read(args.id)
    if args.pack.stat().st_size>MAX_OBJECT_BYTES:raise ValueError('Memory source pack exceeds the private object bound')
    raw=args.pack.read_bytes();pack=product_memory_pack(raw)
    from issueintel.investigation.question_jobs import replay_snapshot
    snapshot=replay_snapshot(pack)
    repository=pack['case']['repository']
    if args.command=='memory-assess':
        if store.read(args.id)['receipt']['repository']!=repository:
            raise ValueError('Memory assessment requires the same product repository')
        return store.assess(args.id,snapshot)
    if args.kind=='architecture':
        if args.hypothesis or args.note:raise ValueError('Hypothesis selection and notes require --kind lesson')
        return store.capture_architecture(snapshot,repository,max_facts=args.max_facts if args.max_facts is not None else 500,
                                          source_raw=raw)
    if args.max_facts is not None:raise ValueError('--max-facts applies to architecture captures only')
    return store.capture_lesson(raw,snapshot,repository,hypothesis_ids=args.hypothesis,note=args.note)


def _investigate(args):
    from issueintel.benchmarks.store import load_dev_cases
    from issueintel.investigation.pipeline import investigate, export_pack
    from issueintel.investigation.providers import GroqProvider
    from issueintel.repository.acquire import acquire_repository, fetch_github_issue
    planned=getattr(args,'planned',False)
    if planned and (args.case_id or args.refinement_rounds):
        raise ValueError('--planned cannot use benchmark cases or --refinement-rounds')
    jev=getattr(args,'controller','rules')=='jev'
    if jev and (not planned or args.provider!='groq'):
        raise ValueError('--controller jev requires --planned --provider groq')
    if jev:
        from issueintel.runtime.typesafe import TypesafeConfig, TypesafeClient
        typesafe_config=TypesafeConfig.load(args.root)
        if not typesafe_config.status()['ready']:
            raise ValueError('Typesafe requires its own enabled configuration, key and positive spending limits')
    output_base = (args.root / args.output).resolve()
    if not output_base.is_relative_to(args.root / "artifacts"):
        raise ValueError("Investigation outputs must stay inside artifacts/")
    if args.case_id:
        matches = [case for case in load_dev_cases(args.root) if case["case_id"] == args.case_id]
        if not matches:
            raise ValueError("case-id must be an existing main_dev case")
        case = matches[0]
    elif args.issue_file:
        if not args.revision:
            raise ValueError("--revision is required for a supplied issue")
        case = {"case_id": "local", "problem_statement": args.issue_file.read_text(encoding="utf-8"), "revision": args.revision,
                "repository": args.repo, "issue_created_at": None}
    else:
        issue = fetch_github_issue(args.repo)
        if not args.revision:
            raise ValueError("--revision is required; latest HEAD is not assumed to be historical")
        case = {"case_id": issue["url"], "problem_statement": issue["problem_statement"], "revision": args.revision,
                "repository": issue["repository"], "issue_url": issue["url"], "issue_created_at": issue["created_at"]}
    local = Path(args.repo)
    local = local if local.is_dir() else acquire_repository(case.get("repository", args.repo), case["revision"], args.root / "data/repos").path
    context=None
    if not args.case_id and case.get('issue_url'):
        from issueintel.integrations.github_sync import GitHubSync
        connection=GitHubSync(args.root).connection(case['repository'])
        if connection and not connection['paused']:
            from issueintel.integrations.github_index import RepositoryIndex
            from issueintel.retrieval.repository_context import collect_context
            context=collect_context(RepositoryIndex(args.root,case['repository']),case['issue_url'],case['problem_statement'])
    import uuid
    controller=None
    if jev:
        from issueintel.investigation.typesafe_controller import TypesafePlannedController
        controller_id=uuid.uuid4().hex[:24]
        def capture_controller_receipt(receipt):
            write_json(output_base/'controller_receipts'/controller_id/(receipt['reservation_id']+'.json'),receipt)
        controller=TypesafePlannedController(TypesafeClient(typesafe_config,on_receipt=capture_controller_receipt),controller_id)
    provider = GroqProvider(RuntimeConfig.load(args.root)) if args.provider == "groq" else None
    from issueintel.investigation.stages import StageCheckpoints
    stages=StageCheckpoints(args.root,output_base/'stages'/uuid.uuid4().hex)
    try:
        pack = investigate(case, local, complete_json=provider if provider else None,
            provider="groq" if provider else "none", model=provider.model if provider else "none", independent_critic=not args.no_critic,
            max_output_tokens=provider.config.max_output_tokens if provider else 2048,
            refinement_rounds=args.refinement_rounds,on_stage=stages,repository_context=context,
            **({'planned_workflow':True,'planner_policy':'bounded_planner_v2'} if planned else {}),
            **({'controller':controller} if controller is not None else {}))
    except Exception:
        if stages.receipts:
            print(json.dumps({'completed_stage_checkpoints':stages.receipts,'automatic_retry':False}),file=sys.stderr)
        raise
    directory = (output_base / (pack["run_id"]+('-'+controller_id if jev else ''))).resolve()
    if not directory.is_relative_to(args.root / "artifacts"):
        raise ValueError("Investigation outputs must stay inside artifacts/")
    print(export_pack(pack, directory))
