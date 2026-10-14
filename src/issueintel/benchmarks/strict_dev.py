"""DEV-only eligibility v1. No gold, human labels, graph, model or evaluation.

Historical proof acquisition is a trusted official-source boundary. Hashes bind
bytes, not historical truth. A bounded unsuccessful search is always pending.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import time
import zipfile
from io import BytesIO

from issueintel.repository.acquire import acquire_repository
from issueintel.repository.cache import load_snapshot, save_snapshot
from issueintel.repository.snapshot import build_snapshot
from issueintel.runtime.io import digest, read_jsonl, write_json, write_progress
from .models import utc_timestamp, string_list
from .sources import SOURCE_FILES, inside_root, sha256_file, source_path
from .temporal import audit_historical_case, reconstruct_query_text, issue_reference_url

VERSION = "main_dev_strict_eligibility_v1"
NAMESPACE = "main-dev-strict-v1"
PUBLIC_KEYS = {"case_id", "instance_id", "repository", "revision", "problem_statement",
               "issue_url", "issue_created_at", "pull_created_at", "issue_numbers", "split"}
DEPENDENCIES = ["benchmarks/strict_dev.py", "benchmarks/strict_dev_acquisition.py", "benchmarks/temporal.py", "benchmarks/history.py", "benchmarks/history_cache.py",
                "benchmarks/main_metadata.py", "benchmarks/trac.py", "benchmarks/sources.py",
                "benchmarks/models.py", "benchmarks/asof.py", "benchmarks/asof_assets.py",
                "repository/snapshot.py", "repository/cache.py", "repository/acquire.py", "runtime/io.py"]


@dataclass(frozen=True)
class StrictEligibilityResult:
    accepted: bool
    reasons: list[str]
    proof_refs: list[str]


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _descriptor(root, path):
    path = inside_root(root, path)
    return {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}


def _checked(root, ref):
    path = inside_root(root, root / ref["path"])
    if not path.is_relative_to(root / "data") or sha256_file(path) != ref["sha256"]:
        raise ValueError("hash_mismatch")
    return path


def _publish(root, directory, value):
    identifier = digest(value)
    path = inside_root(root, directory / (identifier + ".json"))
    if path.exists():
        if _json(path) != value:
            raise ValueError("immutable_object_changed")
    else:
        write_json(path, value)
    return _descriptor(root, path)


def _code(root):
    return {name: sha256_file(root / "src/issueintel" / name) for name in DEPENDENCIES}


def _issue_keys(case, mapping=None):
    keys = {f"github:{case['repository'].lower()}#{n}" for n in case.get("issue_numbers", [])}
    if mapping:
        refs = mapping.get("selected_issues", [])
        if mapping.get("original_query_candidate"):
            refs = refs + [mapping["original_query_candidate"]]
        for ref in refs:
            kind = "trac" if ref.get("source_kind") == "django_trac" else "github"
            keys.add(f"{kind}:{ref['repository'].lower()}#{ref['number']}")
    return sorted(keys)


def priority(candidate, audit=None):
    """Only provenance/repair cost: no gold, semantic labels or performance."""
    if candidate.get("exclusion_reasons") or candidate.get("tier_warnings"):
        return ("C", -100)
    if audit and audit.get("strict_issue_time_receipt_checks_passed"):
        return ("A", 100)
    if audit:
        # A prior unsuccessful historical search is weak coverage, not exclusion.
        # Defer those expensive repairs while fresh, simpler candidates remain.
        return ("C", 10)
    if len(candidate["case"].get("issue_numbers", [])) > 1:
        return ("C", 5)
    if candidate["case"].get("issue_numbers") or candidate.get("mapping_available"):
        return ("B", 60 if candidate.get("mapping_available") else 40)
    return ("C", 0)


def register_pool(root, *, publish=True):
    """Exactly the original Main Dev selection; never expand or relabel it."""
    root = Path(root).resolve()
    manifest_path = root / "data/manifests/dataset.json"
    manifest = _json(manifest_path)
    cases_path = _checked(root, manifest["files"]["cases"])
    old_cases = read_jsonl(cases_path)
    mappings = {}
    mapping_path = root / "data/annotations/main_metadata.jsonl"
    if mapping_path.exists():
        mappings = {r["case_id"]: r for r in read_jsonl(mapping_path)}
    reserved = [c for c in old_cases if c["split"] != "main_dev"]
    reservation_files = [_descriptor(root, cases_path)]
    reserved_ids = {c["case_id"] for c in reserved}
    reserved_groups = {(c["repository"].lower(), c["revision"]) for c in reserved}
    reserved_queries = {hashlib.sha256(c["problem_statement"].encode()).hexdigest() for c in reserved}
    reserved_issues = {key for c in reserved for key in _issue_keys(c)}
    pool = [{"case": c, "origin": "bootstrap_main_dev", "source_case_sha256": digest(c)}
            for c in old_cases if c["split"] == "main_dev"]
    sources = []
    import pyarrow.parquet as parquet
    columns = ["instance_id", "repo", "base_commit", "problem_statement", "created_at"]
    source_rows = {}
    for source in SOURCE_FILES:
        if source.source != "verified":
            continue
        path = source_path(root, source)
        if path.stat().st_size != source.size or sha256_file(path) != source.sha256:
            raise ValueError("Pinned source hash mismatch")
        sources.append(_descriptor(root, path))
        dev_instances = sorted(c["case"]["instance_id"] for c in pool)
        for row in parquet.read_table(path, columns=columns, filters=[("instance_id", "in", dev_instances)]).to_pylist():
            if row["instance_id"] in source_rows:
                raise ValueError("Duplicate pinned Verified instance identity")
            source_rows[row["instance_id"]] = row
    expected_count = manifest.get("counts", {}).get("main_dev", len(pool))
    if len(pool) != expected_count or len(pool) > 400:
        raise ValueError("Original Main Dev count/membership changed")
    for candidate in pool:
        c = candidate["case"]
        row = source_rows.get(c["instance_id"])
        if (row is None or c["case_id"] != "verified:" + row["instance_id"]
                or c["repository"] != row["repo"] or c["revision"] != row["base_commit"]
                or c["problem_statement"] != row["problem_statement"]
                or c["pull_created_at"] != utc_timestamp(row["created_at"])):
            raise ValueError("Original Main Dev projection differs from pinned Verified source")
    audits = {}
    audit_path = root / "data/annotations/historical_audit.jsonl"
    if audit_path.exists():
        audits = {r["case_id"]: r for r in read_jsonl(audit_path)
                  if r.get("query_policy") == "original_opening_query"}
    for candidate in pool:
        c = candidate["case"]
        mapping = mappings.get(c["case_id"])
        reasons = []
        if c["case_id"] in reserved_ids:
            reasons.append("reserved_case_id")
        candidate.update(exclusion_reasons=reasons, mapping_available=bool(mapping),
                         issue_keys=_issue_keys(c, mapping))
        audit = audits.get(c["case_id"])
        if audit and audit.get("strict_issue_time_receipt_checks_passed"):
            try:
                if digest({k: v for k, v in audit.items() if k != "audit_id"}) != audit["audit_id"]:
                    raise ValueError("Audit digest changed")
                if not opening_versions(_json(_checked(root, audit["receipt_bundle"]))):
                    candidate["tier_warnings"] = ["opening_version_ambiguous"]
            except (ValueError, OSError, KeyError):
                candidate["tier_warnings"] = ["invalid_or_missing_receipt"]
        tier, score = priority(candidate, audit)
        candidate.update(tier=tier, priority_score=score)
    pool.sort(key=lambda c: (c["tier"], -c["priority_score"], c["case"]["case_id"]))
    authority = {"schema": VERSION, "namespace": NAMESPACE, "purpose": "development_only_never_final_test",
        "parent_manifest": _descriptor(root, manifest_path), "sources": sources,
        "reservation_files": reservation_files, "reserved_ids": sorted(reserved_ids),
        "reserved_issues": sorted(reserved_issues), "reserved_groups": sorted([list(g) for g in reserved_groups]),
        "reserved_queries": sorted(reserved_queries), "main_dev_total": len(pool),
        "candidate_scope": "original_main_dev_only_no_expansion",
        "candidates": pool, "code_bindings": _code(root), "evaluation_authorized": False,
        "eligibility_llm_calls": 0, "query_policy": "original_opening_query"}
    # Freeze current source bytes at registration, not after the worker finishes.
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted((root / "src/issueintel").rglob("*.py")):
            name = path.relative_to(root / "src/issueintel").as_posix()
            info = zipfile.ZipInfo("src/issueintel/" + name, date_time=(2026, 10, 13, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            handle.writestr(info, (root / "src/issueintel" / name).read_bytes())
    content = archive.getvalue()
    archive_path = root / "data/development/code_archives" / (hashlib.sha256(content).hexdigest() + ".zip")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists() and archive_path.read_bytes() != content:
        raise ValueError("Immutable eligibility source archive changed")
    if not archive_path.exists():
        archive_path.write_bytes(content)
    authority["code_archive"] = _descriptor(root, archive_path)
    if mapping_path.exists():
        authority["mapping_metadata_at_registration"] = _descriptor(root, mapping_path)
    if not publish:
        return authority
    ref = _publish(root, root / "data/development/pools", authority)
    return ref, authority


def load_pool(root, identifier):
    root = Path(root).resolve()
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise ValueError("Require a full pool SHA256")
    value = _json(root / "data/development/pools" / (identifier + ".json"))
    if digest(value) != identifier or value["code_bindings"] != _code(root):
        raise ValueError("Pool identity/code version changed; register a new pool")
    if value["namespace"] != NAMESPACE or value["evaluation_authorized"] is not False:
        raise ValueError("Invalid DEV authority")
    for ref in value["sources"] + value["reservation_files"] + [value["parent_manifest"], value["code_archive"]]:
        _checked(root, ref)
    expected = register_pool(root, publish=False)
    if {k: v for k, v in value.items() if k != "code_archive"} != {k: v for k, v in expected.items() if k != "code_archive"}:
        raise ValueError("Pool selection/projection does not replay from pinned public sources and reservations")
    with zipfile.ZipFile(_checked(root, value["code_archive"])) as archive:
        for name, sha in value["code_bindings"].items():
            if hashlib.sha256(archive.read("src/issueintel/" + name)).hexdigest() != sha:
                raise ValueError("Eligibility source archive does not bind the validator")
    return value


def opening_versions(bundle):
    """Pure initial-version check, stronger than the legacy generic auditor."""
    for receipt in bundle["issue_receipts"]:
        event = (receipt.get("body_provenance") or {}).get("payload") or {}
        if event.get("type") != "IssuesEvent":
            # Trac initial HTML is separately replayed by the temporal auditor.
            if receipt.get("payload", {}).get("ticket_number") is None:
                return False
            continue
        issue = event["payload"]["issue"]
        created = utc_timestamp(issue.get("created_at"), optional=True)
        updated = utc_timestamp(issue.get("updated_at"), optional=True)
        emitted = utc_timestamp(event.get("created_at"), optional=True)
        fetched = utc_timestamp(receipt.get("fetched_at"), optional=True)
        instant = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
        if (not created or updated != created or not emitted or instant(emitted) < instant(created)
                or (fetched and instant(emitted) > instant(fetched))):
            return False
    return bool(bundle["issue_receipts"])


def check_proof(case, bundle):
    if set(case) != PUBLIC_KEYS or case.get("split") != "main_dev":
        return None, ["public_projection_or_dev_membership_invalid"]
    if bundle.get("query_policy") != "original_opening_query":
        return None, ["original_query_provenance_missing"]
    report = audit_historical_case(case, bundle["issue_receipts"], bundle.get("revision_proof"),
        issue_references=bundle["issue_references"], selected_snapshot_revision=bundle.get("selected_snapshot_revision"),
        ancestry_receipt=bundle.get("ancestry_receipt"), query_policy="original_opening_query")
    reasons = []
    if not opening_versions(bundle):
        reasons.append("opening_version_ambiguous")
    if not report["original_query_text_supported"]:
        reasons.append("original_query_provenance_missing")
    if not report["all_linked_issue_text_existed_at_query_time"]:
        reasons.append("issue_identity_or_query_time_unverified")
    if not report["snapshot_ancestry_verified"]:
        reasons.append("snapshot_identity_unverified")
    availability = report["revision_availability"]
    if not availability["independent_availability"]:
        reasons.append("independent_code_availability_missing")
    if not availability["available_before_issue"]:
        reasons.append("snapshot_not_pre_issue")
    if not availability["exact_revision"]:
        reasons.append("snapshot_revision_unbound")
    return report, reasons


@contextmanager
def _lock(root, key):
    """OS-released SQLite transaction survives killed workers, no stale lockfile."""
    path = inside_root(root, root / "data/development/locks" / (digest(key) + ".sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=120)
    try:
        db.execute("BEGIN IMMEDIATE")
        yield
        db.commit()
    finally:
        db.close()


def _legacy_bundle(root, candidate):
    from .asof_assets import verified_asof_receipt
    try:
        return verified_asof_receipt(root, candidate["case"], query_policy="original_opening_query")["bundle"]
    except ValueError as error:
        if "No verified historical receipt" not in str(error):
            raise
        return None


def _snapshot(root, case, bundle, report, proof_ref):
    selected = report["selected_snapshot_revision"]
    availability = {"commit_sha": selected, "verified": True,
        "source_id": report["revision_availability"]["source_url"],
        "available_at": report["revision_availability"]["available_at"],
        "strict_dev_proof_sha256": proof_ref["sha256"]}
    # Reuse snapshot stage independently of graph/localization/gold stages.
    target = root / "data/development/snapshots" / (digest([case["repository"], selected, availability, report["query_time"]]) + ".json")
    if target.exists():
        row = _json(target)
        index = _checked(root, row["index"])
        snapshot = load_snapshot(index, root / "data/derived")
        if (snapshot.snapshot_sha256 != row["snapshot_sha256"] or snapshot.commit_sha != selected
                or snapshot.protocol != "issue_time" or snapshot.cutoff != report["query_time"]
                or snapshot.availability.get("receipt") != availability):
            raise ValueError("snapshot_receipt_binding_invalid")
        return _descriptor(root, target), snapshot, True
    with _lock(root, "repo:" + case["repository"].lower()):
        repo = acquire_repository(case["repository"], selected, root / "data/repos")
        snapshot = build_snapshot(repo.path, selected, protocol="issue_time", cutoff=report["query_time"], availability=availability)
        index = save_snapshot(snapshot, root / "data/derived")
    write_json(target, {"index": _descriptor(root, index), "snapshot_sha256": snapshot.snapshot_sha256,
                        "tree_sha": snapshot.tree_sha, "revision": selected, "proof": proof_ref})
    return _descriptor(root, target), snapshot, False


def _acquire(root, candidate, *, depth, hours):
    """Public metadata only; persists separate content-addressed acquisition stages."""
    from .strict_dev_acquisition import acquire_proof
    return acquire_proof(root, candidate, depth=depth, hours=hours)


def process_candidate(root, pool_id, authority, candidate, *, acquire=False, depth=256, hours=3):
    root = Path(root).resolve()
    case = candidate["case"]
    key = digest([pool_id, case["case_id"]])
    target = root / "data/development/eligibility" / (key + ".json")
    with _lock(root, key):
        if digest(case) != candidate["source_case_sha256"] or case["split"] != "main_dev":
            raise ValueError("Candidate public authority changed")
        if candidate["exclusion_reasons"]:
            result = StrictEligibilityResult(False, candidate["exclusion_reasons"], [])
            return {"case_id": case["case_id"], "status": "excluded", **asdict(result), "tier": candidate["tier"]}
        manifest = _json(root / "data/manifests/dataset.json")
        public_cases = read_jsonl(_checked(root, manifest["files"]["cases"]))
        original = next((c for c in public_cases if c["case_id"] == case["case_id"]), None)
        if (original != case or original is None or original["split"] != "main_dev"
                or candidate["origin"] != "bootstrap_main_dev" or not case["case_id"].startswith("verified:")):
            raise ValueError("Case must be an unchanged original Main Dev; relabeling/expansion is forbidden")
        bundle = None
        reused = False
        if target.exists():
            record = _json(target)
            if record.get("pool_sha256") != pool_id or record.get("case_sha256") != digest(case) or record.get("version") != VERSION:
                raise ValueError("Eligibility receipt binding changed")
            bundle = _json(_checked(root, record["proof"]))
            reused = True
        elif candidate["origin"] == "bootstrap_main_dev":
            bundle = _legacy_bundle(root, candidate)
        if bundle is None and acquire:
            from .history_cache import cache_archive_events
            repositories = [c["case"]["repository"] for c in authority["candidates"]]
            with _lock(root, "acquisition:" + digest(case)):
                with cache_archive_events(root, repositories):
                    bundle = _acquire(root, candidate, depth=depth, hours=hours)
        if bundle is None:
            return {"case_id": case["case_id"], "accepted": False, "status": "pending", "tier": candidate["tier"],
                    "reasons": ["historical_lookup_required"], "proof_refs": []}
        report, reasons = check_proof(case, bundle)
        if report:
            derived_keys = {f"{'trac' if ref.get('source_kind') == 'django_trac' else 'github'}:{ref['repository'].lower()}#{ref['number']}"
                            for ref in bundle["issue_references"]}
        proof_ref = _publish(root, root / "data/development/proofs", bundle)
        if reasons:
            return {"case_id": case["case_id"], "accepted": False, "status": "pending", "tier": candidate["tier"],
                    "reasons": reasons, "proof_refs": [proof_ref["path"]]}
        snapshot_ref, snapshot, snapshot_reused = _snapshot(root, case, bundle, report, proof_ref)
        query = reconstruct_query_text(case, bundle)
        if hashlib.sha256(query.encode()).hexdigest() != report["query_text_sha256"]:
            raise ValueError("Opening query hash changed")
        record = {"version": VERSION, "namespace": NAMESPACE, "pool_sha256": pool_id,
            "case_id": case["case_id"], "case_sha256": digest(case), "proof": proof_ref,
            "snapshot": snapshot_ref, "snapshot_sha256": snapshot.snapshot_sha256,
            "query_sha256": report["query_text_sha256"], "source_query_sha256": report["source_query_sha256"],
            "report_sha256": digest(report), "gold_used": False, "human_rationale_used": False,
            "llm_calls": 0, "evaluation_authorized": False, "accepted": True}
        if target.exists() and _json(target) != record:
            raise ValueError("Eligibility record content changed")
        if not target.exists():
            write_json(target, record)
        return {"case_id": case["case_id"], "status": "accepted", "tier": candidate["tier"],
                **asdict(StrictEligibilityResult(True, [], [str(target.relative_to(root)), proof_ref["path"], snapshot_ref["path"]])),
                "receipt_reused": reused, "snapshot_reused": snapshot_reused, "receipt": _descriptor(root, target),
                "query_sha256": report["query_text_sha256"], "issue_identity_keys": sorted(derived_keys)}


def verify_eligibility_receipt(root, case, reference, pool_id):
    """Fresh replay at freeze/use, without acquisition or any LLM/gold read."""
    row = _json(_checked(root, reference))
    if (row["pool_sha256"] != pool_id or row["case_sha256"] != digest(case) or row["version"] != VERSION
            or row["namespace"] != NAMESPACE or row["accepted"] is not True
            or row["llm_calls"] != 0 or row["gold_used"] is not False or row["human_rationale_used"] is not False):
        raise ValueError("Eligibility receipt identity/isolation mismatch")
    proof = _json(_checked(root, row["proof"]))
    report, reasons = check_proof(case, proof)
    if reasons or digest(report) != row["report_sha256"]:
        raise ValueError("Eligibility proof no longer replays")
    asset = _json(_checked(root, row["snapshot"]))
    snapshot = load_snapshot(_checked(root, asset["index"]), root / "data/derived")
    expected_availability = {"commit_sha": report["selected_snapshot_revision"], "verified": True,
        "source_id": report["revision_availability"]["source_url"],
        "available_at": report["revision_availability"]["available_at"],
        "strict_dev_proof_sha256": row["proof"]["sha256"]}
    if (snapshot.snapshot_sha256 != row["snapshot_sha256"] or asset["snapshot_sha256"] != snapshot.snapshot_sha256
            or snapshot.commit_sha != report["selected_snapshot_revision"] or snapshot.cutoff != report["query_time"]
            or snapshot.protocol != "issue_time" or snapshot.availability.get("receipt") != expected_availability
            or row["query_sha256"] != report["query_text_sha256"] or row["source_query_sha256"] != report["source_query_sha256"]):
        raise ValueError("Eligibility snapshot/query binding mismatch")
    return row


def verify_main_dev_cohort(root, cohort_id):
    root = Path(root).resolve()
    if not re.fullmatch(r"[a-f0-9]{64}", cohort_id):
        raise ValueError("Require full cohort SHA256")
    manifest = _json(root / "data/development/cohorts" / (cohort_id + ".json"))
    if digest(manifest) != cohort_id or manifest["name"] != NAMESPACE or manifest["evaluation_authorized"] is not False:
        raise ValueError("Invalid original Main Dev cohort identity")
    authority = load_pool(root, manifest["pool_sha256"])
    cases = {c["case"]["case_id"]: c["case"] for c in authority["candidates"]}
    members = manifest["ordered_case_ids"]
    exclusions = [r["case_id"] for r in manifest["exclusions"]]
    if (len(cases) != 400 or len(members) != len(set(members)) or set(members) & set(exclusions)
            or set(members) | set(exclusions) != set(cases) or len(exclusions) != len(set(exclusions))
            or members != [r["case_id"] for r in manifest["members"]] or manifest["accepted_count"] != len(members)):
        raise ValueError("Frozen membership must partition exactly 400 original Main Dev")
    for member in manifest["members"]:
        verify_eligibility_receipt(root, cases[member["case_id"]], member["receipt"], manifest["pool_sha256"])
    return manifest


def build_strict_dev(root, *, pool_id=None, target=400, concurrency=2, acquire=False,
                     limit=None, depth=256, hours=3):
    if target != 400 or not 1 <= concurrency <= 8:
        raise ValueError("This phase audits exactly 400 original Main Dev; concurrency 1..8")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 400):
        raise ValueError("Partial audit limit must be in 1..400; partial audits cannot freeze")
    root = Path(root).resolve()
    if pool_id is None:
        ref, authority = register_pool(root)
        pool_id = Path(ref["path"]).stem
    else:
        authority = load_pool(root, pool_id)
    if len(authority["candidates"]) != 400:
        raise ValueError("main_dev_total must equal 400")
    started = time.monotonic()
    for name in ("proofs", "snapshots", "metadata", "opening", "selections", "lookups", "locks", "eligibility", "history_cache", "acquisition"):
        inside_root(root, root / "data/development" / name).mkdir(parents=True, exist_ok=True)
    outcomes = []
    tiers = Counter(c["tier"] for c in authority["candidates"])
    progress_path = root / "artifacts/strict_dev" / (pool_id + ".progress.json")
    def run(c):
        try:
            return process_candidate(root, pool_id, authority, c, acquire=acquire, depth=depth, hours=hours)
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            return {"case_id": c["case"]["case_id"], "accepted": False, "tier": c["tier"],
                    "status": "pending", "runtime_failure": True,
                    "reasons": [type(error).__name__ + ":" + str(error)[:200]]}
    # Audit all original cases; the accepted count is an outcome, not a quota.
    candidates = authority["candidates"][:limit] if limit else authority["candidates"]
    for tier in ("A", "B", "C"):
        selected = [c for c in candidates if c["tier"] == tier]
        for offset in range(0, len(selected), concurrency):
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [executor.submit(run, c) for c in selected[offset:offset + concurrency]]
                for future in as_completed(futures):
                    outcomes.append(future.result())
                    write_progress(progress_path, {"pool_sha256": pool_id, "outcomes": outcomes,
                        "accepted": sum(r["accepted"] for r in outcomes), "evaluation_performed": False})
    rank = {c["case"]["case_id"]: i for i, c in enumerate(authority["candidates"])}
    outcomes.sort(key=lambda row: rank[row["case_id"]])
    accepted = [r for r in outcomes if r["accepted"]]
    counts = Counter(r["status"] for r in outcomes)
    summary = {"version": VERSION, "namespace": NAMESPACE, "pool_sha256": pool_id, "main_dev_total": 400,
        "accepted_strict_dev": len(accepted), "pending": counts.get("pending", 0), "excluded": counts.get("excluded", 0),
        "runtime_failures": [r for r in outcomes if r.get("runtime_failure")],
        "strict_dev_accepted": len(accepted), "counts": dict(counts), "tiers": dict(tiers), "outcomes": outcomes,
        "not_processed": len(authority["candidates"]) - len(outcomes), "evaluation_authorized": False,
        "cost": {category: {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": "0"}
                 for category in ("DATA_ELIGIBILITY_COST", "ARTIFACT_GENERATION_COST", "EVALUATION_COST")},
        "exclusion_reasons": dict(Counter(reason for row in outcomes for reason in row.get("reasons", []))),
        "receipt_cache_hits": sum(r.get("receipt_reused", False) for r in outcomes),
        "snapshot_cache_hits": sum(r.get("snapshot_reused", False) for r in outcomes),
        "snapshot_cache_hit_rate": (sum(r.get("snapshot_reused", False) for r in accepted) / len(accepted)) if accepted else None,
        "early_rejection_count": sum(not r["accepted"] for r in outcomes),
        "artifact_case_calls_prevented_by_eligibility_gate": sum(not r["accepted"] for r in outcomes),
        "gpt_oss_20b_calls": 0, "gpt_oss_120b_calls": 0, "escalations": 0, "escalation_rate": None,
        "retries": 0, "llm_calls_avoided_by_receipt_reuse": 0,
        "note_on_avoided_calls": "Eligibility requires zero model calls; prevented artifact case attempts are counted separately, not estimated as paid-call savings.",
        "elapsed_seconds": round(time.monotonic() - started, 3), "maximum_defensible_count_proven": False,
        "cohort_sha256": None, "resume_status": "resume_supported"}
    if len(outcomes) == 400:
        if _code(root) != authority["code_bindings"]:
            raise ValueError("Validator code changed during acquisition; preserve receipts and register a new pool")
        load_pool(root, pool_id)
        by_id = {c["case"]["case_id"]: c["case"] for c in authority["candidates"]}
        for member in accepted:
            verify_eligibility_receipt(root, by_id[member["case_id"]], member["receipt"], pool_id)
        frozen = {"name": NAMESPACE, "version": VERSION, "purpose": authority["purpose"],
            "pool_sha256": pool_id, "main_dev_total": 400, "accepted_count": len(accepted),
            "members": accepted, "sources": authority["sources"],
            "parent_manifest": authority["parent_manifest"], "exclusions": [r for r in outcomes if not r["accepted"]],
            "ordered_case_ids": [r["case_id"] for r in accepted], "evaluation_authorized": False}
        # Strip runtime cache observations; membership/proof bindings are stable.
        frozen["members"] = [{k: v for k, v in r.items() if k not in {"receipt_reused", "snapshot_reused"}} for r in frozen["members"]]
        pointer = root / "data/development/freezes" / (digest([pool_id, target]) + ".json")
        with _lock(root, "freeze:" + digest([pool_id, target])):
            if pointer.exists():
                ref = _json(pointer)
                previous = _json(_checked(root, ref))
                if {k: v for k, v in previous.items() if k != "created_at_utc"} != frozen:
                    raise ValueError("Frozen DEV membership changed; requires a new cohort version")
            else:
                frozen["created_at_utc"] = datetime.now(timezone.utc).isoformat()
                ref = _publish(root, root / "data/development/cohorts", frozen)
                write_json(pointer, ref)
        summary["cohort_sha256"] = Path(ref["path"]).stem
    write_progress(root / "artifacts/strict_dev" / (pool_id + ".report.json"), summary)
    return summary
