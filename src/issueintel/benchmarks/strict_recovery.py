"""Deterministic recovery of the original Main Dev pending partition only.

The frozen v1 cohort and validator remain intact. Resource limits, partial
searches and unavailable endpoints are never represented as proof of absence.
"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import base64
import hashlib
import json
import re
import os
from urllib.request import Request
from urllib.parse import urlparse

from issueintel.runtime.io import digest, write_json
from .strict_dev import load_pool, _checked, _json, _descriptor, _publish, opening_versions
from .temporal import audit_historical_case, reconstruct_query_text
from .sources import inside_root, sha256_file

_LOADED_SOURCE = Path(__file__).read_bytes()

SCHEMA = "original_main_dev_proof_recovery_v1"
PATHS = ("cached_proof_replay", "original_issue_identity", "opening_archive_full_hour",
         "opening_edit_history", "repository_identity_history", "deep_git_ancestry",
         "full_dag_ancestry",
         "preissue_archive_shifted_hours", "preissue_archive_extended_frontier",
         "independent_web_snapshot", "query_source_binding", "receipt_integrity")


def _bounded_git_runner(command, *, capture_output, check, timeout, env):
    """Kill this command's process tree on timeout, including Windows Git helpers.

    Killing only Git's Windows launcher leaves remote children holding stdout
    open; subprocess.run then waits indefinitely while draining those pipes.
    """
    import subprocess
    import signal
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **options)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.stdout.close()
            process.stderr.close()
        raise TimeoutError("Recovery Git command exceeded its bound; process tree stopped") from None
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _prefetch_snapshot_objects(root, case, report, proof_ref):
    """Download exact witnessed snapshot first; shared snapshot replay stays local.

    Publication was already proved by the independent event. This fetch only
    materializes raw Git objects and cannot itself establish historical time.
    """
    from .strict_dev import _lock
    from issueintel.repository.acquire import acquire_repository, _run_git
    selected = report["selected_snapshot_revision"]
    availability = {"commit_sha": selected, "verified": True,
        "source_id": report["revision_availability"]["source_url"],
        "available_at": report["revision_availability"]["available_at"],
        "strict_dev_proof_sha256": proof_ref["sha256"]}
    target = root / "data/development/snapshots" / (digest([case["repository"], selected, availability, report["query_time"]]) + ".json")
    if target.exists():
        return
    with _lock(root, "repo:" + case["repository"].lower()):
        repo = acquire_repository(case["repository"], selected, root / "data/repos", runner=_bounded_git_runner)
        # Fetching the tree at depth 1 can add a shallow boundary at an existing
        # ancestor and truncate the next history traversal. Materialization
        # must preserve the previously recorded local traversal boundaries.
        shallow = inside_root(root, repo.path / "shallow")
        before = shallow.read_bytes() if shallow.exists() else None
        try:
            _run_git(repo.path, ["fetch", "--refetch", "--no-filter", "--depth=1", "--no-tags",
                "--no-recurse-submodules", "--", "origin", selected], 180, runner=_bounded_git_runner)
        finally:
            if before is not None:
                shallow.write_bytes(before)
            elif shallow.exists():
                shallow.unlink()
    # Avoid unbounded lazy network subprocesses inside the immutable v1 builder.
    os.environ["GIT_NO_LAZY_FETCH"] = "1"


def inventory(root, parent_id):
    root = Path(root).resolve()
    parent = _json(root / "data/development/cohorts" / (parent_id + ".json"))
    if (digest(parent) != parent_id or parent["name"] != "main-dev-strict-v1"
            or parent["main_dev_total"] != 400 or parent.get("evaluation_authorized") is not False):
        raise ValueError("Recovery requires the frozen original Main Dev cohort")
    authority = load_pool(root, parent["pool_sha256"])
    cases = {row["case"]["case_id"]: row for row in authority["candidates"]}
    members = set(parent["ordered_case_ids"])
    if len(cases) != 400 or members | {row["case_id"] for row in parent["exclusions"]} != set(cases):
        raise ValueError("Recovery cannot expand/relabel the original selection")
    entries = []
    for row in parent["exclusions"]:
        candidate = cases[row["case_id"]]
        case = candidate["case"]
        if row["status"] != "pending" or case["split"] != "main_dev":
            raise ValueError("Only the current pending original Main Dev partition is eligible")
        categories = []
        if "historical_lookup_required" in row["reasons"]:
            categories += ["opening_version_proof", "repo_rename_transfer", "query_source_binding"]
        if "independent_code_availability_missing" in row["reasons"]:
            categories += ["preissue_snapshot_proof"]
        if "opening_version_ambiguous" in row["reasons"]:
            categories += ["opening_version_proof", "historical_ambiguity"]
        entries.append({"case_id": case["case_id"], "repository": case["repository"], "case_sha256": digest(case),
            "tier": row["tier"], "initial_reasons": row["reasons"], "recovery_categories": categories,
            "status": "pending", "all_admissible_paths_exhausted": False,
            "paths": {name: {"status": "not_checked", "coverage_complete": False, "absence_claim": False} for name in PATHS}})
    entries.sort(key=lambda row: ("ABC".index(row["tier"]), row["case_id"]))
    report = {"schema": SCHEMA, "parent_cohort_sha256": parent_id, "pool_sha256": parent["pool_sha256"],
        "main_dev_total": 400, "accepted_preserved": parent["accepted_count"], "pending_target": len(entries),
        "tiers": dict(Counter(row["tier"] for row in entries)),
        "initial_reason_counts_nonexclusive": dict(Counter(reason for row in entries for reason in row["initial_reasons"])),
        "llm_calls": 0, "gold_used": False, "human_rationale_used": False, "evaluation_authorized": False,
        "bulk_artifacts_paused": True, "entries": entries,
        "stop_contract": "No below-400 completion while any admissible proof path remains incomplete or access/cap blocked."}
    write_json(root / "artifacts/strict_recovery/INVENTORY.json", report)
    return report, authority, cases


def verify_long_ancestry(receipt, base, selected):
    """Same raw object/parent checks as v1, without its 256-commit search cap.

    An explicit 65536-object/64KiB-per-object safety bound limits decoding; a
    capped search stays pending. Increasing ancestry search is not time proof.
    """
    if not isinstance(receipt, dict) or receipt.get("schema") not in {"raw_git_first_parent_chain_v2", "raw_git_ancestor_chain_v3"}:
        return False
    if receipt.get("base_revision") != base or receipt.get("selected_snapshot_revision") != selected:
        return False
    objects = receipt.get("commit_parent_chain")
    if not isinstance(objects, list) or not 1 <= len(objects) <= 65536 or digest(objects) != receipt.get("parent_chain_sha256"):
        return False
    if objects[0].get("revision") != base or objects[-1].get("revision") != selected:
        return False
    for index, item in enumerate(objects):
        try:
            if not re.fullmatch(r"[a-f0-9]{40}", item["revision"]) or len(item["raw_commit_base64"]) > 90000:
                return False
            raw = base64.b64decode(item["raw_commit_base64"], validate=True)
            if len(raw) > 65536 or hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest() != item["revision"]:
                return False
            parents = [line[7:].decode("ascii") for line in raw.split(b"\n\n", 1)[0].splitlines() if line.startswith(b"parent ")]
            if index + 1 < len(objects) and objects[index+1]["revision"] not in parents:
                return False
        except (ValueError, TypeError, KeyError, UnicodeError):
            return False
    return True


def check_recovered_proof(case, bundle):
    """Keep v1 temporal/opening checks; replay separately extended ancestry.

    The audit view is explicit and never replaces the source case/base. Only
    its ancestry search bound differs; Git dates never certify availability.
    Repository alias and pre-2015 event support are not inferred here.
    """
    from .strict_dev import check_proof, PUBLIC_KEYS
    if set(case) != PUBLIC_KEYS or case.get("split") != "main_dev":
        return None, ["public_projection_or_dev_membership_invalid"]
    if bundle.get("query_policy") != "original_opening_query":
        return None, ["original_query_provenance_missing"]
    if bundle.get("event_schema_version") == "raw_archive_identity_v2":
        return check_identity_archive_proof(case, bundle)
    if bundle.get("event_schema_version") == "official_initial_edit_snapshot_v1":
        return check_initial_edit_proof(case, bundle)
    if bundle.get("event_schema_version") == "transferred_initial_issue_archive_v1":
        return check_transferred_issue_proof(case, bundle)
    if bundle.get("event_schema_version") == "original_opening_with_archive_identity_v1":
        return check_original_opening_archive_identity(case, bundle)
    if not bundle.get("long_ancestry_receipt"):
        return check_proof(case, bundle)
    selected = bundle.get("selected_snapshot_revision")
    ancestry = bundle["long_ancestry_receipt"]
    ancestry_valid = verify_long_ancestry(ancestry, case["revision"], selected)
    audit_view = dict(case, revision=selected)
    report = audit_historical_case(audit_view, bundle["issue_receipts"], bundle.get("revision_proof"),
        issue_references=bundle["issue_references"], selected_snapshot_revision=selected,
        query_policy="original_opening_query")
    report.update(case_sha256=digest(case), upstream_base_revision=case["revision"],
        uses_historical_ancestor=selected != case["revision"], snapshot_ancestry_verified=ancestry_valid,
        ancestry_receipt_sha256=digest(ancestry), recovery_validation_version=SCHEMA,
        audit_view_sha256=digest(audit_view), source_case_was_rewritten=False)
    reasons = []
    for passed, reason in ((opening_versions(bundle), "opening_version_ambiguous"),
            (report["original_query_text_supported"], "original_query_provenance_missing"),
            (report["all_linked_issue_text_existed_at_query_time"], "issue_identity_or_query_time_unverified"),
            (ancestry_valid, "snapshot_identity_unverified")):
        if not passed:
            reasons.append(reason)
    for key, reason in (("independent_availability", "independent_code_availability_missing"),
            ("available_before_issue", "snapshot_not_pre_issue"), ("exact_revision", "snapshot_revision_unbound")):
        if not report["revision_availability"][key]:
            reasons.append(reason)
    report["strict_issue_time_receipt_checks_passed"] = not reasons
    report["status"] = "strict_receipt_checks_passed" if not reasons else "strict_recovery_proof_pending"
    return report, reasons


def repository_fact(root, repository):
    """An official numeric identity fact; not a historical publication receipt."""
    from issueintel.repository.acquire import open_github_request, parse_github_reference
    parse_github_reference(repository)
    path = root / "data/development/recovery/repository_facts" / (digest(repository.lower()) + ".json")
    if path.exists():
        return _json(_checked(root, _json(path)["output"]))
    url = "https://api.github.com/repos/" + repository
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "IssueIntel/strict-recovery"}
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        if any(ord(char) < 32 for char in token):
            raise ValueError("Invalid GitHub token header")
        headers["Authorization"] = "Bearer " + token
    with open_github_request(Request(url, headers=headers), timeout=30) as response:
        raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("Repository metadata exceeds bound")
        payload = json.loads(raw)
    if type(payload.get("id")) is not int or payload["id"] <= 0 or payload.get("private") is not False:
        raise ValueError("Independent public repository identity missing")
    value = {"requested_repository": repository, "source_url": url, "payload": payload,
             "response_sha256": hashlib.sha256(raw).hexdigest(), "fetched_at": datetime.now(timezone.utc).isoformat(),
             "publication_time_proof": False}
    ref = _publish(root, root / "data/development/recovery/repository_fact_objects", value)
    write_json(path, {"output": ref})
    return value


def archive_repo(event):
    """Read either official archive schema without altering its raw event."""
    repo = event.get("repo") or event.get("repository") or {}
    if not isinstance(repo, dict):
        return None, None
    name = repo.get("full_name")
    if not name:
        owner = repo.get("owner")
        if isinstance(owner, dict):
            owner = owner.get("login")
        name = repo.get("name")
        if name and "/" not in name and isinstance(owner, str):
            name = owner + "/" + name
    return name, repo.get("id")


def event_replays(match, fact, kind):
    """Exact raw bytes/hour, public flag, kind and numeric repository identity."""
    try:
        raw = match["raw_event_line"].encode()
        event = json.loads(raw)
        if len(raw) > 64*1024*1024 or event != match["event"] or hashlib.sha256(raw).hexdigest() != match["raw_line_sha256"]:
            return False
        if digest(event) != match["event_sha256"] or event.get("type") != kind or event.get("public") is False:
            return False
        name, identifier = archive_repo(event)
        if (not name or type(identifier) is not int or identifier <= 0
                or identifier != fact["payload"]["id"]
                or (event.get("repo") or event.get("repository") or {}).get("private") is True):
            return False
        parsed = urlparse(match["source_url"])
        hour = re.fullmatch(r"/(\d{4}-\d{2}-\d{2})-(\d|1\d|2[0-3])\.json\.gz", parsed.path)
        if parsed.scheme != "https" or parsed.netloc != "data.gharchive.org" or parsed.query or parsed.fragment or not hour:
            return False
        observed = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
        if observed.utcoffset() is None:
            return False
        expected = datetime.fromisoformat(hour[1]).replace(hour=int(hour[2]), tzinfo=timezone.utc)
        # Earlier Timeline archives use the offset-bearing source wall clock
        # as the filename label. Actual temporal eligibility still compares
        # aware instants; neither raw timestamps nor their hashes are rewritten.
        utc_hour = observed.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        native_hour = observed.replace(tzinfo=None, minute=0, second=0, microsecond=0)
        return utc_hour == expected or ("repository" in event and native_hour == expected.replace(tzinfo=None))
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def push_revisions(event):
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        return set()
    commits = payload.get("commits") or []
    legacy = payload.get("shas") or []
    values = {str(entry.get("sha")) for entry in commits if isinstance(entry, dict)} if isinstance(commits, list) else set()
    if isinstance(legacy, list):
        values.update(str(entry[0]) for entry in legacy if isinstance(entry, list) and entry)
    values.add(str(payload.get("head")))
    return {sha for sha in values if re.fullmatch(r"[a-f0-9]{40}", sha)}


def opening_event_valid(match, current, fact):
    if not event_replays(match, fact, "IssuesEvent"):
        return False
    try:
        event = match["event"]
        issue = event["payload"]["issue"]
        source = current["payload"]
        expected_url = "https://api.github.com/repos/" + fact["requested_repository"] + "/issues/" + str(source.get("number"))
        if (current.get("source_url") != expected_url or source.get("url") != expected_url
                or "pull_request" in source or digest(source) != current.get("payload_sha256")):
            return False
        created = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
        updated = datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
        emitted = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
        fetched = datetime.fromisoformat(current["fetched_at"].replace("Z", "+00:00"))
        return (event["payload"]["action"] == "opened" and "pull_request" not in issue
            and type(issue.get("id")) is int and issue["id"] == source.get("id")
            and str(issue.get("number")) == str(source.get("number"))
            and created == datetime.fromisoformat(source["created_at"].replace("Z", "+00:00"))
            and created.utcoffset() is not None and updated == created and created <= emitted <= fetched)
    except (ValueError, KeyError, TypeError):
        return False


def check_identity_archive_proof(case, bundle):
    """Replay official legacy/renamed events by immutable IDs, never normalize raw."""
    from .strict_dev import PUBLIC_KEYS, check_proof
    if set(case) != PUBLIC_KEYS or case.get("split") != "main_dev":
        return None, ["public_projection_or_dev_membership_invalid"]
    if bundle.get("query_policy") != "original_opening_query" or bundle.get("source_case_sha256") != digest(case):
        return None, ["query_source_binding_invalid"]
    reasons = []
    fact = bundle.get("repository_identity_fact", {})
    payload = fact.get("payload", {})
    if (fact.get("requested_repository") != case["repository"] or fact.get("source_url") != "https://api.github.com/repos/" + case["repository"]
            or type(payload.get("id")) is not int or payload.get("id", 0) <= 0 or payload.get("private") is not False):
        reasons.append("repository_identity_unresolved")
    matches = bundle.get("opening_matches", [])
    currents = bundle.get("current_issue_receipts", [])
    refs = bundle.get("issue_references", [])
    if len(refs) != len(currents) or any(ref.get("repository") != case["repository"] or str(ref.get("number")) != str(c["payload"].get("number")) for ref, c in zip(refs, currents)):
        reasons.append("query_source_binding_invalid")
    if len(matches) != len(currents) or not matches or any(not opening_event_valid(m, c, fact) for m, c in zip(matches, currents)):
        reasons.append("opening_version_or_identity_unverified")
    if "repository_identity_unresolved" in reasons or "opening_version_or_identity_unverified" in reasons or "query_source_binding_invalid" in reasons:
        return None, reasons
    dates = [c["payload"].get("created_at") for c in currents]
    if not dates or None in dates or len(set(dates)) != 1:
        reasons.append("linked_issues_not_coexistent_at_opening")
    query_time = dates[0] if dates else None
    statement = "".join(f"{m['event']['payload']['issue'].get('title') or ''}\n{m['event']['payload']['issue'].get('body') or ''}\n" for m in matches)
    selected = bundle.get("selected_snapshot_revision")
    ancestry = bundle.get("long_ancestry_receipt")
    if not verify_long_ancestry(ancestry, case["revision"], selected):
        reasons.append("snapshot_identity_unverified")
    push = bundle.get("push_match", {})
    if not event_replays(push, fact, "PushEvent") or selected not in push_revisions(push.get("event", {})):
        reasons.append("independent_code_availability_missing")
    available = push.get("event", {}).get("created_at")
    if not query_time or not available or datetime.fromisoformat(available.replace("Z", "+00:00")) >= datetime.fromisoformat(query_time.replace("Z", "+00:00")):
        reasons.append("snapshot_not_pre_issue")
    report = {"case_id": case["case_id"], "case_sha256": digest(case), "source_query_sha256": hashlib.sha256(case["problem_statement"].encode()).hexdigest(),
        "query_text_sha256": hashlib.sha256(statement.encode()).hexdigest(), "query_time": query_time,
        "upstream_base_revision": case["revision"], "selected_snapshot_revision": selected,
        "strict_issue_time_receipt_checks_passed": not reasons, "source_case_was_rewritten": False,
        "repository_identity_basis": "official_current_repository_numeric_id_matches_original_raw_archive_repo_id",
        "historical_repository_names": sorted({archive_repo(m["event"])[0] for m in matches + ([push] if push.get("event") else [])}),
        "revision_availability": {"available_at": available, "source_url": push.get("source_url"),
                                  "independent_availability": "independent_code_availability_missing" not in reasons,
                                  "available_before_issue": "snapshot_not_pre_issue" not in reasons,
                                  "exact_revision": isinstance(selected, str) and bool(re.fullmatch(r"[a-f0-9]{40}", selected)) and selected in push_revisions(push.get("event", {}))},
        "validation_version": SCHEMA, "raw_events_rewritten": False, "gold_used": False, "llm_calls": 0, "evaluation_performed": False}
    return report, reasons


def identity_opening_bundle(root, case):
    """Official older schemas/renames, tied to the original metadata selection."""
    from datetime import timedelta
    from .history import scan_archive_hour, ArchiveLimits, _hour
    directory = root / "data/development/acquisition" / digest(case)
    if not (directory / "metadata.json").exists():
        return None, {"status": "source_metadata_stage_missing", "absence_claim": False}
    pointer = _json(directory / "metadata.json")
    if pointer["case_sha256"] != digest(case):
        raise ValueError("Alias recovery metadata case changed")
    metadata = _json(_checked(root, pointer["output"]))
    refs, currents = metadata["issue_references"], metadata["issue_receipts"]
    if not refs or len(refs) != len(currents) or any(ref.get("source_kind") == "django_trac" for ref in refs):
        return None, {"status": "identity_archive_path_not_applicable", "absence_claim": False}
    fact = repository_fact(root, case["repository"])
    matches, attempts = [], []
    for reference, current in zip(refs, currents):
        created = current["payload"]["created_at"]
        if created < "2011-02-12T00:00:00Z":
            return None, {"status": "opening_predates_GH_Archive", "absence_claim": False}
        for offset in ((0, 1, -1, -7, -8, -6, -9) if created < "2015-01-01" else (0, 1, -1)):
            hour = _hour(created) + timedelta(hours=offset)
            key = digest([current["payload"]["id"], fact["payload"]["id"], hour.isoformat(), "raw_identity_initial_version_v4"])
            path = root / "data/development/recovery/identity_opening" / (key + ".json")
            def predicate(event):
                payload = event.get("payload", {})
                if event.get("type") != "IssuesEvent" or not isinstance(payload, dict) or payload.get("action") != "opened":
                    return False
                issue = payload.get("issue", {})
                if not (archive_repo(event)[1] == fact["payload"]["id"] and isinstance(issue, dict)
                        and issue.get("id") == current["payload"]["id"] and "pull_request" not in issue):
                    return False
                # Feed copies of one opened event can carry different later
                # issue states. Continue through late copies to find an actual
                # initial payload; never treat the first match as authoritative.
                try:
                    created_at = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
                    return (created_at.utcoffset() is not None
                        and created_at == datetime.fromisoformat(current["payload"]["created_at"].replace("Z", "+00:00"))
                        and created_at == datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00")))
                except (ValueError, TypeError, KeyError):
                    return False
            if path.exists():
                scan = _json(_checked(root, _json(path)["output"]))
            else:
                scan = scan_archive_hour(hour, predicate, limits=ArchiveLimits(max_compressed_bytes=1024*1024*1024,
                    max_uncompressed_bytes=8*1024*1024*1024, max_line_bytes=64*1024*1024,
                    max_records=5_000_000, max_seconds=600, read_timeout=30))
                output = _publish(root, root / "data/development/recovery/identity_scan_objects", scan)
                write_json(path, {"output": output})
            attempts.append({k: v for k, v in scan.items() if k != "match"})
            if scan.get("match") and opening_event_valid(scan["match"], current, fact):
                matches.append(scan["match"])
                break
        else:
            return None, {"status": "initial_identity_event_not_recovered", "attempts": attempts, "absence_claim": False}
    return {"event_schema_version": "raw_archive_identity_v2", "query_policy": "original_opening_query",
        "repository_identity_fact": fact, "issue_references": refs, "current_issue_receipts": currents,
        "opening_matches": matches, "metadata_stage_reference": pointer["output"],
        "source_case_sha256": digest(case)}, {"status": "identity_initial_event_recovered", "attempts": attempts,
                                            "raw_events_rewritten": False, "absence_claim": False}


def check_transferred_issue_proof(case, bundle):
    """Keep the assigned current issue while replaying its original transferred identity."""
    from .issue_transfer import verify_transferred_opening
    opening, reasons = verify_transferred_opening(case, bundle)
    if reasons or opening is None:
        return None, reasons
    reasons = []
    selected = bundle.get("selected_snapshot_revision")
    if not verify_long_ancestry(bundle.get("long_ancestry_receipt"), case["revision"], selected):
        reasons.append("snapshot_identity_unverified")
    fact, push = bundle["repository_identity_fact"], bundle.get("push_match", {})
    if not event_replays(push, fact, "PushEvent") or selected not in push_revisions(push.get("event", {})):
        reasons.append("independent_code_availability_missing")
    available = push.get("event", {}).get("created_at")
    query_time = opening["query_time"]
    try:
        published = datetime.fromisoformat(available.replace("Z", "+00:00"))
        cutoff = datetime.fromisoformat(query_time.replace("Z", "+00:00"))
        if published.utcoffset() is None or published >= cutoff:
            reasons.append("snapshot_not_pre_issue")
    except (AttributeError, ValueError, TypeError):
        reasons.append("snapshot_not_pre_issue")
    report = {"case_id": case["case_id"], "case_sha256": digest(case),
        "source_query_sha256": hashlib.sha256(case["problem_statement"].encode()).hexdigest(),
        "query_text_sha256": hashlib.sha256(opening["query_text"].encode()).hexdigest(), "query_time": query_time,
        "upstream_base_revision": case["revision"], "selected_snapshot_revision": selected,
        "strict_issue_time_receipt_checks_passed": not reasons, "source_case_was_rewritten": False,
        "repository_identity_basis": "official_issue_transfer_and_redirect_for_query_separate_from_code_repository",
        "query_identity_lineage": {key:value for key,value in opening.items() if key not in {"query_text", "title", "body"}},
        "revision_availability": {"available_at": available, "source_url": push.get("source_url"),
            "independent_availability": "independent_code_availability_missing" not in reasons,
            "available_before_issue": "snapshot_not_pre_issue" not in reasons,
            "exact_revision": isinstance(selected, str) and bool(re.fullmatch(r"[a-f0-9]{40}", selected))
                and selected in push_revisions(push.get("event", {}))},
        "validation_version": SCHEMA, "raw_events_rewritten": False,
        "gold_used": False, "llm_calls": 0, "evaluation_performed": False}
    return report, reasons


def transferred_issue_opening_bundle(root, case):
    """Load existing authentic origin/transfer sources; never fill historical text."""
    from .recovery_sources import _authorize_pending_case
    from .issue_transfer import verify_transferred_opening
    stage = root / "artifacts/strict_recovery/modern_alternatives/TRANSFER_INITIAL_OPENING.json"
    if not stage.exists():
        return None, {"status": "transfer_initial_source_not_available", "absence_claim": False}
    pointer = _json(stage)
    if pointer.get("case_id") != case["case_id"]:
        return None, {"status": "transfer_initial_source_not_for_this_case", "absence_claim": False}
    capture = _json(_checked(root, pointer["receipt"]))
    if capture.get("case_id") != case["case_id"] or capture.get("schema") != "original_main_dev_transferred_initial_capture_v1":
        raise ValueError("Transferred origin source case changed")
    metadata_pointer = _json(root / "data/development/acquisition" / digest(case) / "metadata.json")
    if metadata_pointer.get("case_sha256") != digest(case):
        raise ValueError("Transferred issue source metadata changed")
    metadata = _json(_checked(root, metadata_pointer["output"]))
    if len(metadata["issue_references"]) != 1:
        raise ValueError("Transferred source must keep one original assigned issue")
    _authorize_pending_case(root, case, "edits", metadata["issue_references"][0]["number"])
    evidence = [pointer["receipt"], capture["official_transfer_receipt"], capture["official_redirect_receipt"]]
    bundle = {"event_schema_version": "transferred_initial_issue_archive_v1", "query_policy": "original_opening_query",
        "source_case_sha256": digest(case), "metadata_stage_reference": metadata_pointer["output"],
        "issue_references": metadata["issue_references"], "current_issue_receipts": metadata["issue_receipts"],
        "repository_identity_fact": repository_fact(root, case["repository"]),
        "transfer_records": [_json(_checked(root, capture["official_transfer_receipt"]))],
        "redirect_receipts": [_json(_checked(root, capture["official_redirect_receipt"]))],
        "opening_matches": [capture["scan"]["match"]], "transfer_source_references": evidence}
    opening, reasons = verify_transferred_opening(case, bundle)
    if reasons:
        return None, {"status": "transfer_origin_binding_unverified", "reasons": reasons,
            "source_receipts": evidence, "absence_claim": False}
    return bundle, {"status": "original_transferred_initial_version_recovered", "source_receipts": evidence,
        "origin_creation_time": opening["query_time"], "raw_events_rewritten": False, "absence_claim": False}


def check_initial_edit_proof(case, bundle):
    """Independent stored version 0, complete title history and original strict code proof."""
    from .strict_dev import PUBLIC_KEYS
    from .recovery_sources import initial_edit_snapshot_candidate
    if set(case) != PUBLIC_KEYS or case.get("split") != "main_dev":
        return None, ["public_projection_or_dev_membership_invalid"]
    if bundle.get("source_case_sha256") != digest(case) or bundle.get("query_policy") != "original_opening_query":
        return None, ["query_source_binding_invalid"]
    fact = bundle.get("repository_identity_fact", {})
    payload = fact.get("payload", {})
    if (fact.get("requested_repository") != case["repository"] or fact.get("source_url") != "https://api.github.com/repos/" + case["repository"]
            or type(payload.get("id")) is not int or payload.get("id", 0) <= 0 or payload.get("private") is not False):
        return None, ["repository_identity_unresolved"]
    currents, records, refs = bundle.get("current_issue_receipts", []), bundle.get("initial_edit_records", []), bundle.get("issue_references", [])
    if not currents or len(currents) != len(records) or len(currents) != len(refs):
        return None, ["initial_edit_source_missing"]
    components = []
    for ref, current, pages in zip(refs, currents, records):
        expected = "https://api.github.com/repos/" + case["repository"] + "/issues/" + str(ref.get("number"))
        if (ref.get("repository") != case["repository"] or current.get("source_url") != expected
                or digest(current.get("payload")) != current.get("payload_sha256")):
            return None, ["initial_edit_issue_identity_invalid"]
        component, reasons = initial_edit_snapshot_candidate(case, current, pages)
        if reasons:
            return None, reasons
        components.append(component)
    dates = {row["created_at"] for row in components}
    reasons = []
    if len(dates) != 1:
        reasons.append("linked_issues_not_coexistent_at_opening")
    query_time = min(dates)
    statement = "".join(f"{row['title']}\n{row['body']}\n" for row in components)
    selected = bundle.get("selected_snapshot_revision")
    if not verify_long_ancestry(bundle.get("long_ancestry_receipt"), case["revision"], selected):
        reasons.append("snapshot_identity_unverified")
    push = bundle.get("push_match", {})
    if not event_replays(push, fact, "PushEvent") or selected not in push_revisions(push.get("event", {})):
        reasons.append("independent_code_availability_missing")
    available = push.get("event", {}).get("created_at")
    if not available or datetime.fromisoformat(available.replace("Z", "+00:00")) >= datetime.fromisoformat(query_time.replace("Z", "+00:00")):
        reasons.append("snapshot_not_pre_issue")
    report = {"case_id": case["case_id"], "case_sha256": digest(case), "source_query_sha256": hashlib.sha256(case["problem_statement"].encode()).hexdigest(),
        "query_text_sha256": hashlib.sha256(statement.encode()).hexdigest(), "query_time": query_time,
        "upstream_base_revision": case["revision"], "selected_snapshot_revision": selected,
        "strict_issue_time_receipt_checks_passed": not reasons, "source_case_was_rewritten": False,
        "opening_proof_kind": "official_stored_initial_body_revision_and_replayed_title_history",
        "initial_version_ids": [row["initial_body_revision_id"] for row in components],
        "initial_version_edited_at": [row["initial_revision_edited_at"] for row in components],
        "retrospective_version_record_created_at": [row["initial_revision_record_created_at"] for row in components],
        "retrospective_capture": True, "stored_text_quality_human_verified": False,
        "revision_availability": {"available_at": available, "source_url": push.get("source_url"),
            "independent_availability": "independent_code_availability_missing" not in reasons,
            "available_before_issue": "snapshot_not_pre_issue" not in reasons,
            "exact_revision": isinstance(selected, str) and bool(re.fullmatch(r"[a-f0-9]{40}", selected)) and selected in push_revisions(push.get("event", {}))},
        "validation_version": SCHEMA, "raw_events_rewritten": False, "gold_used": False, "llm_calls": 0, "evaluation_performed": False}
    return report, reasons


def initial_edit_opening_bundle(root, case):
    from .recovery_sources import collect_graphql_path, initial_edit_snapshot_candidate
    pointer = _json(root / "data/development/acquisition" / digest(case) / "metadata.json")
    if pointer.get("case_sha256") != digest(case):
        raise ValueError("Initial edit source metadata changed")
    metadata = _json(_checked(root, pointer["output"]))
    refs, currents = metadata["issue_references"], metadata["issue_receipts"]
    if not refs or len(refs) != len(currents) or any(ref.get("source_kind") == "django_trac" for ref in refs):
        return None, {"status": "initial_edit_path_not_applicable", "absence_claim": False}
    records, paths = [], []
    for ref, current in zip(refs, currents):
        path_ref, path = collect_graphql_path(root, case, kind="edits", number=ref["number"])
        pages = [_json(_checked(root, reference)) for reference in path["records"]]
        paths.append(path_ref)
        candidate, reasons = initial_edit_snapshot_candidate(case, current, pages)
        if reasons:
            return None, {"status": "stored_initial_revision_not_proven", "reasons": reasons, "path_receipts": paths, "absence_claim": False}
        records.append(pages)
    bundle = {"event_schema_version": "official_initial_edit_snapshot_v1", "query_policy": "original_opening_query",
        "source_case_sha256": digest(case), "issue_references": refs, "current_issue_receipts": currents,
        "metadata_stage_reference": pointer["output"], "initial_edit_records": records,
        "repository_identity_fact": repository_fact(root, case["repository"])}
    return bundle, {"status": "stored_initial_revision_recovered", "path_receipts": paths,
                    "retrospective_capture": True, "absence_claim": False}


def recovered_query_text(case, proof):
    schema = proof.get("event_schema_version")
    if schema == "transferred_initial_issue_archive_v1":
        from .issue_transfer import verify_transferred_opening
        opening, reasons = verify_transferred_opening(case, proof)
        if reasons:
            raise ValueError("Transferred opening query does not replay")
        return opening["query_text"]
    if schema == "raw_archive_identity_v2":
        return "".join(f"{m['event']['payload']['issue'].get('title') or ''}\n{m['event']['payload']['issue'].get('body') or ''}\n" for m in proof["opening_matches"])
    if schema == "official_initial_edit_snapshot_v1":
        from .recovery_sources import initial_edit_snapshot_candidate
        parts = []
        for current, pages in zip(proof["current_issue_receipts"], proof["initial_edit_records"]):
            component, reasons = initial_edit_snapshot_candidate(case, current, pages)
            if reasons:
                raise ValueError("Initial stored query does not replay")
            parts.append(f"{component['title']}\n{component['body']}\n")
        return "".join(parts)
    return reconstruct_query_text(case, proof)


def check_original_opening_archive_identity(case, bundle):
    """Keep original Trac/opened proofs; replay older PushEvent schema by numeric ID."""
    fact = bundle.get("repository_identity_fact", {})
    payload = fact.get("payload", {})
    if (bundle.get("source_case_sha256") != digest(case) or fact.get("requested_repository") != case["repository"]
            or fact.get("source_url") != "https://api.github.com/repos/" + case["repository"]
            or type(payload.get("id")) is not int or payload.get("id", 0) <= 0 or payload.get("private") is not False):
        return None, ["repository_identity_unresolved"]
    original = {key: value for key, value in bundle.items() if key != "event_schema_version"}
    report, reasons = check_recovered_proof(case, original)
    if report is None:
        return report, reasons
    availability_reasons = {"independent_code_availability_missing", "snapshot_not_pre_issue", "snapshot_revision_unbound"}
    reasons = [reason for reason in reasons if reason not in availability_reasons]
    push, selected = bundle.get("push_match", {}), bundle.get("selected_snapshot_revision")
    independent = event_replays(push, fact, "PushEvent") and selected in push_revisions(push.get("event", {}))
    available = push.get("event", {}).get("created_at")
    before = bool(available and report.get("query_time") and datetime.fromisoformat(available.replace("Z", "+00:00")) < datetime.fromisoformat(report["query_time"].replace("Z", "+00:00")))
    exact = isinstance(selected, str) and bool(re.fullmatch(r"[a-f0-9]{40}", selected)) and selected in push_revisions(push.get("event", {}))
    for passed, reason in ((independent, "independent_code_availability_missing"), (before, "snapshot_not_pre_issue"), (exact, "snapshot_revision_unbound")):
        if not passed:
            reasons.append(reason)
    report["revision_availability"] = {"available_at": available, "source_url": push.get("source_url"),
        "independent_availability": independent, "available_before_issue": before, "exact_revision": exact}
    report["strict_issue_time_receipt_checks_passed"] = not reasons
    report["status"] = "strict_receipt_checks_passed" if not reasons else "strict_recovery_proof_pending"
    report["snapshot_publication_schema"] = "raw_archive_numeric_repository_identity"
    return report, reasons


def collect_deep_history(root, case, *, depth=4096):
    """Commit clocks only plan archive requests; they never accept a case."""
    from .strict_dev import _lock
    from issueintel.repository.acquire import acquire_repository, _run_git
    if type(depth) is not int or not 256 <= depth <= 65536:
        raise ValueError("Explicit bounded deep-history search required")
    root = Path(root).resolve()
    directory = root / "data/development/recovery/history" / digest(case)
    target = directory / (f"depth{depth}.json")
    with _lock(root, "recovery_history:" + digest(case)):
        if target.exists():
            saved = _json(target)
            if saved["case_sha256"] != digest(case):
                raise ValueError("Deep-history receipt identity changed")
            _checked(root, saved["output"])
            return _json(_checked(root, saved["output"])), True
        with _lock(root, "repo:" + case["repository"].lower()):
            acquired = acquire_repository(case["repository"], case["revision"], root / "data/repos", runner=_bounded_git_runner)
            _run_git(acquired.path, ["fetch", f"--depth={depth}", "--filter=blob:none", "--no-tags",
                                    "--no-recurse-submodules", "--", "origin", case["revision"]], 180, runner=_bounded_git_runner)
            hints = _run_git(acquired.path, ["log", "--first-parent", f"--max-count={depth}",
                "--format=%H %cI", case["revision"]], 90).stdout.decode("ascii").splitlines()
            if not hints:
                raise ValueError("Requested base has no inspected history")
            oldest = hints[-1].split(" ", 1)[0]
            raw = _run_git(acquired.path, ["cat-file", "commit", oldest], 30).stdout
            has_parent = any(line.startswith(b"parent ") for line in raw.split(b"\n\n", 1)[0].splitlines())
            shallow = _run_git(acquired.path, ["rev-parse", "--is-shallow-repository"], 30).stdout.decode().strip() == "true"
        value = {"case_id": case["case_id"], "case_sha256": digest(case), "source_base_revision": case["revision"],
            "repository": case["repository"], "depth_requested": depth, "commits_read": len(hints),
            "commits": [{"revision": entry.split(" ", 1)[0], "committer_hint": entry.split(" ", 1)[1]} for entry in hints],
            "oldest_has_parent": has_parent, "repository_is_shallow": shallow,
            "first_parent_history_complete": not has_parent,
            "git_dates_are_availability_proof": False, "llm_calls": 0, "absence_claim": False}
        reference = _publish(root, root / "data/development/recovery/history_objects", value)
        write_json(target, {"case_sha256": digest(case), "output": reference})
        return value, False


def _transfer_source_binding(root, case, proof):
    """Bind each embedded transfer object to its checked primary source file."""
    refs = proof.get("transfer_source_references")
    if not isinstance(refs, list) or len(refs) != 3:
        raise ValueError("Transferred opening primary source references missing")
    capture, graph, redirect = [_json(_checked(root, ref)) for ref in refs]
    if (capture.get("schema") != "original_main_dev_transferred_initial_capture_v1"
            or capture.get("case_id") != case["case_id"]
            or capture.get("official_transfer_receipt") != refs[1]
            or capture.get("official_redirect_receipt") != refs[2]
            or proof.get("opening_matches") != [capture.get("scan", {}).get("match")]
            or proof.get("transfer_records") != [graph]
            or proof.get("redirect_receipts") != [redirect]):
        raise ValueError("Transferred opening objects differ from their primary source receipts")
    return refs


def _recovery_issue_keys(proof, report):
    keys = {f"{'trac' if ref.get('source_kind') == 'django_trac' else 'github'}:{ref['repository'].lower()}#{ref['number']}"
            for ref in proof["issue_references"]}
    if proof.get("event_schema_version") == "transferred_initial_issue_archive_v1":
        origin = report["query_identity_lineage"]
        keys.add(f"github:{origin['origin_repository'].lower()}#{origin['origin_issue_number']}")
    return keys


def _source_metadata_binding(root, case, proof):
    """Replay only this Dev case's original declared PR-to-issue relation.

    Content-addressed metadata is not sufficient if its issue selection has
    drifted. Replay the original mapping without opening gold or human labels.
    """
    from .main_metadata import verify_main_mapping_record
    selected_row = None
    with (root / "data/annotations/main_metadata.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            if json.dumps(case["case_id"]) not in line:
                continue
            row = json.loads(line)
            if row.get("case_id") == case["case_id"]:
                selected_row = row
    if selected_row is None:
        raise ValueError("Original Main Dev mapping is missing")
    original = verify_main_mapping_record(root, case, selected_row)
    selected = ([selected_row["original_query_candidate"]] if selected_row.get("original_query_candidate")
                else selected_row.get("selected_issues", []))
    refs = [{k: ref[k] for k in ("repository", "number", "source_kind") if k in ref} for ref in selected]
    pointer = _json(root / "data/development/acquisition" / digest(case) / "metadata.json")
    if pointer.get("case_sha256") != digest(case):
        raise ValueError("Original metadata case binding changed")
    metadata = _json(_checked(root, pointer["output"]))
    if refs != metadata["issue_references"] or refs != proof["issue_references"]:
        raise ValueError("Recovered query selected different issue identities")
    if refs and refs[0].get("source_kind") == "django_trac":
        from .trac import audit_issue_receipt
        numbers = {str(ref["number"]) for ref in refs}
        currents = [audit_issue_receipt(root, pair["initial_receipt"]) for pair in original["trac_pairs"]
                    if str(pair["initial_receipt"]["payload"]["ticket_number"]) in numbers]
    else:
        from .temporal import issue_reference_url
        urls = {issue_reference_url(ref) for ref in refs}
        currents = [receipt for receipt in original["issue_receipts"] if receipt["source_url"] in urls]
    if currents != metadata["issue_receipts"]:
        raise ValueError("Original metadata receipt selection changed")
    recovered = proof.get("current_issue_receipts", proof.get("issue_receipts", []))
    if len(recovered) != len(currents):
        raise ValueError("Recovered opening receipt selection differs from source")
    from .asof import _opening_receipt
    for opened, current in zip(recovered, currents):
        if "current_issue_receipts" in proof or current["payload"].get("ticket_number") is not None:
            expected = current
        else:
            provenance = opened.get("body_provenance") or {}
            match = {"event": provenance.get("payload"), "source_url": provenance.get("source_url"),
                **{k: provenance[k] for k in ("raw_event_line", "raw_line_sha256", "event_sha256") if k in provenance}}
            for key in ("id", "number", "created_at"):
                if opened["payload"].get(key) != current["payload"].get(key):
                    raise ValueError("Recovered opening changed immutable issue identity")
            # This recreates the explicitly authorized opening-text overlay,
            # preserving current source URL/response hash/fetch time lineage.
            expected = _opening_receipt(current, match)
        if opened != expected:
            raise ValueError("Recovered opening is not bound to the original source receipt")
    if proof.get("event_schema_version") in {"raw_archive_identity_v2", "official_initial_edit_snapshot_v1", "original_opening_with_archive_identity_v1", "transferred_initial_issue_archive_v1"}:
        if proof.get("metadata_stage_reference") != pointer["output"]:
            raise ValueError("Alias recovery source metadata reference changed")
        fact = proof["repository_identity_fact"]
        base_repo = original["pull_receipt"]["payload"]["base"]["repo"]
        if fact["payload"].get("id") != base_repo.get("id") or base_repo.get("private") is True:
            raise ValueError("Current repository ID does not match the original PR repository")
    if proof.get("event_schema_version") == "transferred_initial_issue_archive_v1":
        _transfer_source_binding(root, case, proof)
    return {"mapping_id": selected_row["mapping_id"], "mapping_receipt": selected_row["receipt_bundle"],
            "metadata_receipt": pointer["output"]}


def audit_linked_creation_constraints(root, case):
    """A fixed merged query cannot exist at the earliest of unequal openings.

    The result applies only to the verified original issue selection. It does
    not authorize dropping a reference, changing cutoff or inventing an issue.
    """
    from .main_metadata import verify_main_mapping_record
    from .strict_dev import PUBLIC_KEYS
    if set(case) != PUBLIC_KEYS or case.get("split") != "main_dev":
        raise ValueError("Constraint audit accepts public original Dev only")
    root = Path(root).resolve()
    selected = None
    with (root / "data/annotations/main_metadata.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            if json.dumps(case["case_id"]) in line:
                row = json.loads(line)
                if row.get("case_id") == case["case_id"]:
                    selected = row
    if selected is None:
        return None
    original = verify_main_mapping_record(root, case, selected)
    refs = ([selected["original_query_candidate"]] if selected.get("original_query_candidate") else selected.get("selected_issues", []))
    if len(refs) < 2 or any(ref.get("source_kind") == "django_trac" for ref in refs):
        return None
    from .temporal import issue_reference_url
    urls = {issue_reference_url(ref) for ref in refs}
    currents = [receipt for receipt in original["issue_receipts"] if receipt["source_url"] in urls]
    if len(currents) != len(refs) or any("pull_request" in receipt["payload"] for receipt in currents):
        raise ValueError("Merged-query constraint has unresolved source identities")
    dates = [datetime.fromisoformat(receipt["payload"]["created_at"].replace("Z", "+00:00")) for receipt in currents]
    if len(set(dates)) == 1:
        return None
    evidence = {"case_id": case["case_id"], "case_sha256": digest(case), "mapping_id": selected["mapping_id"],
        "mapping_receipt": selected["receipt_bundle"], "constraint": "all_original_linked_queries_must_exist_at_earliest_issue_opening",
        "observed_issue_creation_times": [{"source_url": receipt["source_url"], "issue_id": receipt["payload"]["id"],
            "number": receipt["payload"]["number"], "created_at": receipt["payload"]["created_at"],
            "payload_sha256": receipt["payload_sha256"]} for receipt in currents],
        "earliest_query_time": min(dates).isoformat(), "conflict_verified": True,
        "cannot_accept_fixed_original_selection_without_contract_change": True,
        "dropping_or_rebinding_an_issue_authorized": False, "cutoff_shift_authorized": False,
        "llm_calls": 0, "gold_used": False, "evaluation_authorized": False,
        "global_proof_search_exhaustion_claim": False}
    return _publish(root, root / "data/development/recovery/constraint_conflicts", evidence)


def replay_recovery_receipt(root, parent_id, candidate, reference, authority):
    """Offline integrity, source selection, strict proof and actual snapshot replay."""
    from issueintel.repository.cache import load_snapshot
    root = Path(root).resolve()
    case = candidate["case"]
    saved = _json(_checked(root, reference))
    if (candidate["origin"] != "bootstrap_main_dev" or candidate["source_case_sha256"] != digest(case)
            or case["split"] != "main_dev" or saved.get("case_id") != case["case_id"]
            or saved.get("case_sha256") != digest(case) or saved.get("parent_cohort_sha256") != parent_id
            or digest({k: v for k, v in saved.items() if k != "receipt_sha256"}) != saved.get("receipt_sha256")
            or saved.get("accepted") is not True or saved.get("llm_calls") != 0
            or saved.get("gold_used") is not False or saved.get("human_rationale_used") is not False
            or saved.get("evaluation_authorized") is not False):
        raise ValueError("Recovered eligibility identity/isolation mismatch")
    for ref in saved["evidence_refs"]:
        _checked(root, ref)
    proof = _json(_checked(root, saved["proof"]))
    report, reasons = check_recovered_proof(case, proof)
    if reasons or report != _json(_checked(root, saved["report"])):
        raise ValueError("Recovered temporal proof no longer replays")
    creation_attestation = None
    if proof.get("event_schema_version") == "official_initial_edit_snapshot_v1":
        from .creation_attestation import verify_creation_attestation
        creation_attestation = verify_creation_attestation(root, case, proof)
    lineage = _source_metadata_binding(root, case, proof)
    issue_keys = _recovery_issue_keys(proof, report)
    if issue_keys & set(authority["reserved_issues"]) or report["query_text_sha256"] in authority["reserved_queries"]:
        raise ValueError("Recovered query overlaps a reserved issue/query")
    query = recovered_query_text(case, proof)
    if hashlib.sha256(query.encode()).hexdigest() != report["query_text_sha256"]:
        raise ValueError("Recovered opening query bytes changed")
    asset = _json(_checked(root, saved["snapshot"]))
    snapshot = load_snapshot(_checked(root, asset["index"]), root / "data/derived")
    expected = {"commit_sha": report["selected_snapshot_revision"], "verified": True,
                "source_id": report["revision_availability"]["source_url"],
                "available_at": report["revision_availability"]["available_at"],
                "strict_dev_proof_sha256": saved["proof"]["sha256"]}
    if (snapshot.snapshot_sha256 != saved["snapshot_sha256"] or snapshot.snapshot_sha256 != asset["snapshot_sha256"]
            or snapshot.commit_sha != report["selected_snapshot_revision"] or asset["revision"] != snapshot.commit_sha
            or snapshot.tree_sha != asset["tree_sha"] or snapshot.protocol != "issue_time"
            or snapshot.cutoff != report["query_time"] or snapshot.availability.get("receipt") != expected
            or asset["proof"] != saved["proof"] or saved["query_sha256"] != report["query_text_sha256"]
            or saved["source_query_sha256"] != report["source_query_sha256"]):
        raise ValueError("Recovered snapshot/source/query binding mismatch")
    return {"case_id": case["case_id"], "receipt": reference, "replayed": True,
            "issue_identity_keys": sorted(issue_keys), "query_sha256": saved["query_sha256"],
            "snapshot_sha256": snapshot.snapshot_sha256, "source_binding": lineage,
            **({"creation_attestation": creation_attestation} if creation_attestation else {})}


def recovery_checkpoint(root, parent_id):
    """Deduplicate all waves; preserve incomplete paths and replay positive cases.

    This is a checkpoint, never a below-400 completion or a cohort freeze.
    Every open path remains explicit even when a bounded scan reaches EOF.
    """
    from .strict_dev import verify_main_dev_cohort
    root = Path(root).resolve()
    code_sha = hashlib.sha256(_LOADED_SOURCE).hexdigest()
    code_path = inside_root(root, root / "data/development/recovery/code" / (code_sha + ".py"))
    if code_path.exists() and code_path.read_bytes() != _LOADED_SOURCE:
        raise ValueError("Immutable recovery replay source changed")
    code_path.parent.mkdir(parents=True, exist_ok=True)
    if not code_path.exists():
        code_path.write_bytes(_LOADED_SOURCE)
    # Record all additive replay gates as exact source bytes. These do not
    # replace any immutable dependency binding of the frozen parent cohort.
    from . import recovery_sources, creation_attestation, issue_transfer
    support_code = []
    for module in (recovery_sources, creation_attestation, issue_transfer):
        content = module._LOADED_SOURCE
        support_sha = hashlib.sha256(content).hexdigest()
        support_path = inside_root(root, root / "data/development/recovery/code" / (support_sha + ".py"))
        if support_path.exists() and support_path.read_bytes() != content:
            raise ValueError("Immutable additive replay source changed")
        if not support_path.exists():
            support_path.write_bytes(content)
        support_code.append({"module": module.__name__, **_descriptor(root, support_path)})
    initial, authority, candidates = inventory(root, parent_id)
    parent = verify_main_dev_cohort(root, parent_id)
    by_case = {entry["case_id"]: [] for entry in initial["entries"]}
    for path in sorted((root / "data/development/recovery/waves").glob("*/receipt.json")):
        saved = _json(path)
        if saved.get("parent_cohort_sha256") != parent_id:
            continue
        if saved.get("case_id") not in by_case:
            raise ValueError("Recovery wave outside the original pending partition")
        if (saved["case_sha256"] != digest(candidates[saved["case_id"]]["case"])
                or digest({k: v for k, v in saved.items() if k != "receipt_sha256"}) != saved["receipt_sha256"]):
            raise ValueError("Recovery checkpoint receipt hash mismatch")
        for ref in saved.get("evidence_refs", []):
            _checked(root, ref)
        by_case[saved["case_id"]].append((saved, _descriptor(root, path)))
    entries = []
    recovered = []
    for entry in initial["entries"]:
        waves = by_case[entry["case_id"]]
        case = candidates[entry["case_id"]]["case"]
        metadata_pointer = root / "data/development/acquisition" / digest(case) / "metadata.json"
        if metadata_pointer.exists():
            pointer = _json(metadata_pointer)
            if pointer.get("case_sha256") != digest(case):
                raise ValueError("Checkpoint metadata stage changed source case")
            metadata = _json(_checked(root, pointer["output"]))
            entry["source_metadata_receipt"] = pointer["output"]
            entry["paths"]["original_issue_identity"].update(
                status="selected_source_identities_recorded" if metadata["issue_references"] else "unresolved",
                selected_issue_count=len(metadata["issue_references"]))
        else:
            entry["paths"]["original_issue_identity"]["status"] = "metadata_stage_missing"
            entry["recovery_categories"] = sorted(set(entry["recovery_categories"] + ["receipt_hash_repair"]))
        histories = []
        for history_pointer in sorted((root / "data/development/recovery/history" / digest(case)).glob("depth*.json")):
            pointer = _json(history_pointer)
            value = _json(_checked(root, pointer["output"]))
            if pointer.get("case_sha256") != digest(case) or value.get("case_sha256") != digest(case):
                raise ValueError("Checkpoint ancestry history changed source case")
            histories.append({"receipt": pointer["output"], "depth_requested": value["depth_requested"],
                "commits_read": value["commits_read"], "first_parent_history_complete": value["first_parent_history_complete"]})
        if histories:
            entry["paths"]["deep_git_ancestry"].update(status="bounded_history_inspected", histories=histories)
        entry["waves"] = [ref for _, ref in waves]
        entry["bounded_search_results"] = [{"receipt": ref, "status": row["status"], "opening": row["opening"],
            "snapshot_search": row.get("snapshot_search"), "reasons": row.get("reasons", [])} for row, ref in waves]
        positives = [(row, ref) for row, ref in waves if row["accepted"]]
        if positives:
            replayed = [replay_recovery_receipt(root, parent_id, candidates[entry["case_id"]], ref, authority) for _, ref in positives]
            entry.update(status="accepted_recovery_replayed", selected_recovery=replayed[0])
            recovered.append(replayed[0])
        else:
            entry["remaining_reason"] = "admissible_proof_paths_not_exhausted"
            if waves:
                openings = [row["opening"] for row, _ in waves]
                valid = any(row.get("status") in {"valid_opening_replayed", "opening_recovered", "identity_initial_event_recovered"} for row in openings)
                if valid:
                    for name in ("cached_proof_replay", "opening_archive_full_hour"):
                        entry["paths"][name].update(status="positive_opening_proof_recovered", proof_found=True, absence_claim=False)
                elif any(row.get("attempts") for row in openings):
                    entry["paths"]["opening_archive_full_hour"].update(status="bounded_attempts_recorded", absence_claim=False)
                if any(row.get("snapshot_search") for row, _ in waves):
                    entry["paths"]["preissue_archive_shifted_hours"].update(status="bounded_attempts_recorded", absence_claim=False)
                entry["source_access_or_cap_blocks"] = [attempt for row, _ in waves
                    for stage in (row["opening"], row.get("snapshot_search", {})) for attempt in stage.get("attempts", [])
                    if not attempt.get("coverage_complete")]
            entry["remaining_paths"] = [name for name, state in entry["paths"].items() if not state.get("proof_found")]
        entries.append(entry)
    report = {"schema": SCHEMA, "parent_cohort_sha256": parent_id, "baseline_replayed": True,
        "original_main_dev_total": 400, "baseline_accepted_preserved": parent["accepted_count"],
        "newly_accepted_replayed": len(recovered), "total_proof_verified": parent["accepted_count"] + len(recovered),
        "remaining_pending": len(entries) - len(recovered), "entries": entries,
        "llm_calls": 0, "gold_used": False, "human_rationale_used": False, "evaluation_authorized": False,
        "bulk_artifacts_paused": True, "phase_complete": False, "all_admissible_paths_exhausted": False,
        "parent_frozen_membership_changed": False, "new_cohort_frozen": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "replay_code": _descriptor(root, code_path),
        "replay_support_code": support_code,
        "replay_parent_code_bindings": authority["code_bindings"],
        "authorization": _descriptor(root, root / "configs/strict_recovery_authorization.json"),
        **({"scope_decision": _descriptor(root, root / "configs/strict_recovery_scope_decision_2026-10-13.json")}
           if (root / "configs/strict_recovery_scope_decision_2026-10-13.json").exists() else {})}
    ref = _publish(root, root / "data/development/recovery/checkpoints", report)
    write_json(root / "artifacts/strict_recovery/CHECKPOINT.json", {**report, "immutable_checkpoint": ref})
    return report


def create_long_ancestry(root, case, selected, *, depth=65536):
    from issueintel.repository.acquire import acquire_repository, _run_git
    from .strict_dev import _lock
    with _lock(root, "repo:" + case["repository"].lower()):
        repo = acquire_repository(case["repository"], case["revision"], root / "data/repos", runner=_bounded_git_runner)
        chain = _run_git(repo.path, ["rev-list", "--first-parent", f"--max-count={depth}", case["revision"]], 90).stdout.decode().splitlines()
        if selected not in chain:
            _run_git(repo.path, ["fetch", f"--depth={depth}", "--filter=blob:none", "--no-tags", "--no-recurse-submodules",
                                "--", "origin", case["revision"]], 180, runner=_bounded_git_runner)
            chain = _run_git(repo.path, ["rev-list", "--first-parent", f"--max-count={depth}", case["revision"]], 90).stdout.decode().splitlines()
        if selected not in chain:
            raise ValueError("Selected revision not in inspected first-parent history")
        import subprocess
        objects = []
        selected_chain = chain[:chain.index(selected)+1]
        for offset in range(0, len(selected_chain), 128):
            requested = selected_chain[offset:offset+128]
            request = "".join(revision + "\n" for revision in requested).encode("ascii")
            output = _run_git(repo.path, ["cat-file", "--batch"], 60,
                runner=lambda *args, **kwargs: subprocess.run(*args, input=request, **kwargs)).stdout
            objects.extend(parse_commit_batch(output, requested))
    receipt = {"schema": "raw_git_first_parent_chain_v2", "base_revision": case["revision"],
        "selected_snapshot_revision": selected, "commit_parent_chain": objects, "parent_chain_sha256": digest(objects)}
    if not verify_long_ancestry(receipt, case["revision"], selected):
        raise ValueError("Extended ancestry does not replay raw Git objects")
    return receipt


def collect_all_parent_history(root, case, *, depth=65536):
    """Explore merge parents as well; clocks remain search hints, not availability."""
    from .strict_dev import _lock
    from issueintel.repository.acquire import acquire_repository, _run_git
    if type(depth) is not int or not 256 <= depth <= 65536:
        raise ValueError("Explicit bounded all-parent history required")
    root = Path(root).resolve()
    target = root / "data/development/recovery/history_all" / digest(case) / (f"depth{depth}.json")
    with _lock(root, "recovery_history_all:" + digest(case)):
        if target.exists():
            row = _json(target)
            if row.get("case_sha256") != digest(case):
                raise ValueError("All-parent history source case changed")
            return _json(_checked(root, row["output"])), True
        with _lock(root, "repo:" + case["repository"].lower()):
            repo = acquire_repository(case["repository"], case["revision"], root / "data/repos", runner=_bounded_git_runner)
            _run_git(repo.path, ["fetch", f"--depth={depth}", "--filter=blob:none", "--no-tags", "--no-recurse-submodules",
                "--", "origin", case["revision"]], 180, runner=_bounded_git_runner)
            output = _run_git(repo.path, ["log", "--topo-order", f"--max-count={depth}", "--format=%H%x09%P%x09%cI", case["revision"]], 90).stdout
        commits = []
        for line in output.decode("ascii").splitlines():
            revision, parents, hint = line.split("\t")
            commits.append({"revision": revision, "parents": parents.split(), "committer_hint": hint})
        if not commits or commits[0]["revision"] != case["revision"]:
            raise ValueError("All-parent exploration lost the original base")
        value = {"case_id": case["case_id"], "case_sha256": digest(case), "source_base_revision": case["revision"],
            "depth_requested": depth, "commits_read": len(commits), "commits": commits,
            "all_ancestry_exhausted": False, "git_dates_are_availability_proof": False, "absence_claim": False, "llm_calls": 0}
        ref = _publish(root, root / "data/development/recovery/history_all_objects", value)
        write_json(target, {"case_sha256": digest(case), "output": ref})
        return value, False


def create_all_parent_ancestry(root, case, selected, history):
    from collections import deque
    from .strict_dev import _lock
    from issueintel.repository.acquire import acquire_repository, _run_git
    import subprocess
    parents = {item["revision"]: item["parents"] for item in history["commits"]}
    queue, reached = deque([case["revision"]]), {case["revision"]: None}
    while queue and selected not in reached:
        current = queue.popleft()
        for parent in parents.get(current, []):
            if parent in parents and parent not in reached:
                reached[parent] = current
                queue.append(parent)
    if selected not in reached:
        raise ValueError("Published revision is outside inspected ancestry")
    path, cursor = [], selected
    while cursor is not None:
        path.append(cursor)
        cursor = reached[cursor]
    path.reverse()
    objects = []
    with _lock(root, "repo:" + case["repository"].lower()):
        repo = acquire_repository(case["repository"], case["revision"], root / "data/repos", runner=_bounded_git_runner)
        for offset in range(0, len(path), 128):
            requested = path[offset:offset+128]
            raw = "".join(revision + "\n" for revision in requested).encode("ascii")
            result = _run_git(repo.path, ["cat-file", "--batch"], 60,
                runner=lambda *args, **kwargs: subprocess.run(*args, input=raw, **kwargs))
            objects.extend(parse_commit_batch(result.stdout, requested))
    receipt = {"schema": "raw_git_ancestor_chain_v3", "base_revision": case["revision"],
        "selected_snapshot_revision": selected, "commit_parent_chain": objects, "parent_chain_sha256": digest(objects)}
    if not verify_long_ancestry(receipt, case["revision"], selected):
        raise ValueError("All-parent path does not replay actual raw Git objects")
    return receipt


def parse_commit_batch(output, requested):
    """Validate framing, size, type and actual Git hash before recording a chain."""
    if len(requested) > 128 or len(output) > 128 * (65536 + 100):
        raise ValueError("Raw commit batch exceeds bound")
    offset, objects = 0, []
    for revision in requested:
        newline = output.find(b"\n", offset)
        if newline < 0:
            raise ValueError("Truncated commit batch header")
        header = output[offset:newline].decode("ascii").split()
        if len(header) != 3 or header[:2] != [revision, "commit"]:
            raise ValueError("Commit batch identity/type mismatch")
        size = int(header[2])
        if not 0 <= size <= 65536:
            raise ValueError("Raw commit exceeds ancestry bound")
        offset = newline + 1
        raw = output[offset:offset+size]
        if len(raw) != size or output[offset+size:offset+size+1] != b"\n":
            raise ValueError("Truncated commit batch object")
        if hashlib.sha1(b"commit " + str(size).encode() + b"\0" + raw).hexdigest() != revision:
            raise ValueError("Commit batch object hash mismatch")
        objects.append({"revision": revision, "raw_commit_base64": base64.b64encode(raw).decode()})
        offset += size + 1
    if offset != len(output):
        raise ValueError("Trailing commit batch data")
    return objects


def recover_history_batch(root, parent_id, *, limit=None, depth=4096, concurrency=2):
    """Inspect deeper ancestry in tier order; still zero accepted from clocks."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from issueintel.runtime.io import write_progress
    from .strict_dev import _lock
    root = Path(root).resolve()
    report, authority, candidates = inventory(root, parent_id)
    selected = report["entries"][:limit]
    results = []
    with _lock(root, "strict_recovery_history_worker"):
        for tier in "ABC":
            group = [row for row in selected if row["tier"] == tier]
            with ThreadPoolExecutor(max_workers=concurrency) as workers:
                tasks = {workers.submit(collect_deep_history, root, candidates[row["case_id"]]["case"], depth=depth): row for row in group}
                for future in as_completed(tasks):
                    row = tasks[future]
                    try:
                        history, reused = future.result()
                        results.append({"case_id": row["case_id"], "tier": tier, "status": "history_inspected",
                            "depth": depth, "commits_read": history["commits_read"], "oldest_hint": history["commits"][-1]["committer_hint"],
                            "first_parent_history_complete": history["first_parent_history_complete"], "receipt_reused": reused,
                            "accepted_from_git_dates": False})
                    except (ValueError, OSError, RuntimeError) as error:
                        results.append({"case_id": row["case_id"], "tier": tier, "status": "history_recovery_failed", "error": str(error)[:200]})
                    write_progress(root / "artifacts/strict_recovery" / (f"HISTORY_DEPTH_{depth}.json"), {
                        "parent_cohort_sha256": parent_id, "pending_scope": len(report["entries"]), "results": results,
                        "counts": dict(Counter(item["status"] for item in results)), "llm_calls": 0,
                        "gold_used": False, "evaluation_authorized": False, "all_admissible_paths_exhausted": False})
    return {"results": results, "llm_calls": 0}


def opening_bundle(root, case, previous):
    """Reuse verified opening stages; retry partial scans with larger limits."""
    from .history import ArchiveLimits, find_issue_opened_event
    from .history_cache import cache_archive_events
    from .asof import _opening_receipt
    from .strict_dev import check_proof
    for relative in previous.get("proof_refs", []):
        path = inside_root(root, root / relative)
        value = _json(path)
        if digest(value) != path.stem:
            raise ValueError("Pending proof object changed; original hash must not be rewritten")
        if opening_versions(value):
            _, reasons = check_proof(case, value)
            if not set(reasons) & {"original_query_provenance_missing", "issue_identity_or_query_time_unverified"}:
                return value, {"status": "valid_opening_replayed", "network_calls": 0}
    directory = root / "data/development/acquisition" / digest(case)
    metadata_path = directory / "metadata.json"
    if not metadata_path.exists():
        return None, {"status": "original_issue_identity_unresolved", "absence_claim": False}
    stage = _json(metadata_path)
    if stage["case_sha256"] != digest(case):
        raise ValueError("Original metadata case identity changed")
    metadata = _json(_checked(root, stage["output"]))
    if not metadata["issue_references"] or len(metadata["issue_references"]) != len(metadata["issue_receipts"]):
        return None, {"status": "original_issue_identity_unresolved", "absence_claim": False}
    receipts, attempts = [], []
    for reference, current in zip(metadata["issue_references"], metadata["issue_receipts"]):
        if reference.get("source_kind") == "django_trac":
            receipts.append(current)
            continue
        created = current["payload"]["created_at"]
        # GH Archive itself documents the earliest source date; do not request
        # nonexistent pre-2011 hours and call a 404 a task exclusion.
        if created < "2011-02-12T00:00:00Z":
            return None, {"status": "opening_predates_GH_Archive", "created_at": created, "absence_claim": False}
        key = digest([reference, created, "expanded_full_hour_v1"])
        path = root / "data/development/recovery/opening" / (key + ".json")
        if path.exists():
            lookup = _json(_checked(root, _json(path)["output"]))
        else:
            limits = ArchiveLimits(max_compressed_bytes=1024*1024*1024, max_uncompressed_bytes=8*1024*1024*1024,
                max_line_bytes=64*1024*1024, max_records=5_000_000, max_seconds=600, read_timeout=30)
            with cache_archive_events(root, [case["repository"]]):
                lookup = find_issue_opened_event(reference["repository"], reference["number"], created,
                    expected_problem_statement=None, hour_offsets=(0, 1, -1), limits=limits, save_root=root)
            output = _publish(root, root / "data/development/recovery/lookups", lookup)
            write_json(path, {"output": output})
        attempts.extend(lookup["attempts"])
        if not lookup.get("match"):
            return None, {"status": "opening_not_recovered_in_selected_hours", "attempts": attempts,
                          "coverage_complete": all(x["coverage_complete"] for x in attempts), "absence_claim": False}
        receipts.append(_opening_receipt(current, lookup["match"]))
    bundle = {"query_policy": "original_opening_query", "issue_references": metadata["issue_references"],
              "issue_receipts": receipts, "selected_snapshot_revision": None, "revision_proof": None, "ancestry_receipt": None}
    if not opening_versions(bundle):
        return None, {"status": "opening_version_ambiguous", "attempts": attempts, "absence_claim": False}
    _, reasons = check_proof(case, bundle)
    if set(reasons) & {"original_query_provenance_missing", "issue_identity_or_query_time_unverified"}:
        return None, {"status": "opening_binding_or_coexistence_invalid", "reasons": reasons, "absence_claim": False}
    return bundle, {"status": "opening_recovered", "attempts": attempts, "absence_claim": False}


def seek_snapshot(root, case, bundle, *, depth=4096, hours_limit=12):
    """Search full nearby public archive hours, keeping every attempted scope."""
    from datetime import timedelta
    from .history import ArchiveLimits, scan_archive_hour, _hour
    from .history_cache import cache_archive_events
    from .strict_dev import check_proof
    opening_schema = bundle.get("event_schema_version")
    identity_mode = opening_schema in {"raw_archive_identity_v2", "official_initial_edit_snapshot_v1", "original_opening_with_archive_identity_v1", "transferred_initial_issue_archive_v1"}
    query_time = (bundle["current_issue_receipts"][0]["payload"]["created_at"] if opening_schema == "raw_archive_identity_v2"
                  else bundle["current_issue_receipts"][0]["payload"]["created_at"] if opening_schema in {"official_initial_edit_snapshot_v1", "transferred_initial_issue_archive_v1"}
                  else check_proof(case, bundle)[0]["query_time"])
    query = datetime.fromisoformat(query_time.replace("Z", "+00:00"))
    all_parents = bundle.get("ancestry_search_mode") == "all_parents"
    history, reused = (collect_all_parent_history(root, case, depth=depth) if all_parents else collect_deep_history(root, case, depth=depth))
    targets = {entry["revision"] for entry in history["commits"]}
    hinted = sorted({_hour(entry["committer_hint"]) for entry in history["commits"]
                     if datetime.fromisoformat(entry["committer_hint"]) < query}, reverse=True)
    hours = []
    for base in hinted:
        # Publication may happen after a commit clock: search neighboring hours.
        offsets = (-7, -8, -6, -9, 0, 1, 2, -1, 3, 6, 12, 24) if base.year < 2015 else (0, 1, 2, -1, 3, 6, 12, 24)
        for offset in offsets:
            hour = base + timedelta(hours=offset)
            if datetime(2011, 2, 12, tzinfo=timezone.utc) <= hour < query and hour not in hours:
                hours.append(hour)
            if len(hours) == hours_limit:
                break
        if len(hours) == hours_limit:
            break
    attempts = []
    for hour in hours:
        key = digest([case["repository"], case["revision"], query_time, depth, hour.isoformat(),
                      "identity_schema_v2" if identity_mode else "expanded_full_hour_v1"] + (["all_parents"] if all_parents else []))
        path = root / "data/development/recovery/push_lookups" / (key + ".json")
        def predicate(event):
            payload, repo = event.get("payload", {}), event.get("repo", {})
            if (event.get("type") != "PushEvent" or event.get("public") is False or not isinstance(repo, dict)
                    or (archive_repo(event)[1] != bundle["repository_identity_fact"]["payload"]["id"] if identity_mode
                        else repo.get("name", "").lower() != case["repository"].lower()) or not isinstance(payload, dict)):
                return False
            if not event.get("created_at"):
                return False
            listed = payload.get("commits") or []
            if not isinstance(listed, list):
                return False
            commits = push_revisions(event) if identity_mode else {str(entry.get("sha")) for entry in listed if isinstance(entry, dict)} | {str(payload.get("head"))}
            return datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")) < query and bool(commits & targets)
        if path.exists():
            scan = _json(_checked(root, _json(path)["output"]))
        else:
            limits = ArchiveLimits(max_compressed_bytes=1024*1024*1024, max_uncompressed_bytes=8*1024*1024*1024,
                max_line_bytes=64*1024*1024, max_records=5_000_000, max_seconds=600, read_timeout=30)
            if identity_mode:
                scan = scan_archive_hour(hour, predicate, limits=limits)
            else:
                with cache_archive_events(root, [case["repository"]]):
                    scan = scan_archive_hour(hour, predicate, limits=limits)
            ref = _publish(root, root / "data/development/recovery/push_scan_objects", scan)
            write_json(path, {"output": ref})
        attempts.append({k: v for k, v in scan.items() if k != "match"})
        if scan.get("match"):
            match = scan["match"]
            payload = match["event"]["payload"]
            witnessed = push_revisions(match["event"])
            selected = next(x["revision"] for x in history["commits"] if x["revision"] in witnessed)
            value = dict(bundle, selected_snapshot_revision=selected,
                long_ancestry_receipt=(create_all_parent_ancestry(root, case, selected, history) if all_parents else create_long_ancestry(root, case, selected, depth=depth)),
                revision_proof={"repository": case["repository"], "revision": selected, "basis": "gharchive_push_event",
                    "source_url": match["source_url"], "payload": match["event"],
                    "raw_event_line": match["raw_event_line"], "raw_line_sha256": match["raw_line_sha256"], "event_sha256": match["event_sha256"]})
            if identity_mode:
                value["push_match"] = match
            return value, {"status": "independent_push_recovered", "attempts": attempts, "history_reused": reused,
                           "search_frontier_exhausted": False, "absence_claim": False}
    return None, {"status": "no_snapshot_recovered_in_current_frontier", "depth": depth, "attempts": attempts,
                  "history_reused": reused, "hinted_hours_total": len(hinted), "hours_searched": len(hours),
                  "search_frontier_exhausted": False, "absence_claim": False}


def recover_case_wave(root, parent_id, candidate, previous, *, depth=4096, hours_limit=12, strategy="standard"):
    from .strict_dev import _snapshot, _lock
    case = candidate["case"]
    identity = [parent_id, case["case_id"], depth, hours_limit] + ([strategy] if strategy != "standard" else [])
    directory = root / "data/development/recovery/waves" / digest(identity)
    path = directory / "receipt.json"
    with _lock(root, "recovery_case:" + digest(case)):
        if path.exists():
            saved = _json(path)
            if (saved["case_sha256"] != digest(case) or saved["parent_cohort_sha256"] != parent_id
                    or digest({k: v for k, v in saved.items() if k != "receipt_sha256"}) != saved["receipt_sha256"]):
                raise ValueError("Recovery wave receipt identity changed")
            for reference in saved.get("evidence_refs", []):
                _checked(root, reference)
            return saved, True
        bundle, opening = (identity_opening_bundle(root, case) if strategy in {"raw_identity_v2", "raw_identity_local_clock_v3", "raw_initial_payload_scan_v4"}
                           else initial_edit_opening_bundle(root, case) if strategy == "official_initial_edit_snapshot_v1"
                           else transferred_issue_opening_bundle(root, case) if strategy == "transferred_initial_issue_archive_v1"
                           else opening_bundle(root, case, previous))
        if bundle and strategy in {"original_opening_with_archive_identity_v1", "original_opening_archive_dag_v3", "original_opening_native_clock_dag_v4"}:
            pointer = _json(root / "data/development/acquisition" / digest(case) / "metadata.json")
            if pointer.get("case_sha256") != digest(case):
                raise ValueError("Original opening metadata source changed")
            bundle = dict(bundle, event_schema_version="original_opening_with_archive_identity_v1", source_case_sha256=digest(case),
                repository_identity_fact=repository_fact(root, case["repository"]), metadata_stage_reference=pointer["output"])
            if strategy in {"original_opening_archive_dag_v3", "original_opening_native_clock_dag_v4"}:
                bundle["ancestry_search_mode"] = "all_parents"
        result = {"schema": SCHEMA, "case_id": case["case_id"], "case_sha256": digest(case), "tier": previous["tier"],
            "parent_cohort_sha256": parent_id, "opening": opening, "status": "pending", "accepted": False,
            "all_admissible_paths_exhausted": False, "llm_calls": 0, "gold_used": False,
            "human_rationale_used": False, "evaluation_authorized": False, "evidence_refs": []}
        if bundle:
            recovered, search = seek_snapshot(root, case, bundle, depth=depth, hours_limit=hours_limit)
            result["snapshot_search"] = search
            if recovered:
                report, reasons = check_recovered_proof(case, recovered)
                proof_ref = _publish(root, root / "data/development/recovery/proofs", recovered)
                report_ref = _publish(root, root / "data/development/recovery/reports", report)
                result.update(proof=proof_ref, report=report_ref, reasons=reasons, evidence_refs=[proof_ref, report_ref])
                for source_ref in recovered.get("transfer_source_references", []):
                    _checked(root, source_ref)
                    result["evidence_refs"].append(source_ref)
                if not reasons:
                    _prefetch_snapshot_objects(root, case, report, proof_ref)
                    snapshot_ref, snapshot, reused = _snapshot(root, case, recovered, report, proof_ref)
                    query = recovered_query_text(case, recovered)
                    if hashlib.sha256(query.encode()).hexdigest() != report["query_text_sha256"]:
                        raise ValueError("Recovered query differs from the independently recorded opening")
                    result.update(status="accepted_recovery", accepted=True, snapshot=snapshot_ref,
                        snapshot_sha256=snapshot.snapshot_sha256, query_sha256=report["query_text_sha256"],
                        source_query_sha256=report["source_query_sha256"], snapshot_reused=reused)
                    result["evidence_refs"].append(snapshot_ref)
        result["receipt_sha256"] = digest(result)
        write_json(path, result)
        return result, False


def run_recovery_wave(root, parent_id, *, limit=None, depth=4096, hours_limit=12, concurrency=2, strategy="standard", only_ids=None):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from issueintel.runtime.io import write_progress
    from .strict_dev import _lock
    if type(concurrency) is not int or not 1 <= concurrency <= 4 or type(hours_limit) is not int or not 1 <= hours_limit <= 72 or strategy not in {"standard", "raw_identity_v2", "raw_identity_local_clock_v3", "raw_initial_payload_scan_v4", "official_initial_edit_snapshot_v1", "transferred_initial_issue_archive_v1", "original_opening_with_archive_identity_v1", "original_opening_archive_dag_v3", "original_opening_native_clock_dag_v4"}:
        raise ValueError("Explicit bounded recovery concurrency/frontier required")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 107):
        raise ValueError("Recovery limit must cover at least one original pending case")
    root = Path(root).resolve()
    initial, authority, candidates = inventory(root, parent_id)
    parent = _json(root / "data/development/cohorts" / (parent_id + ".json"))
    previous = {row["case_id"]: row for row in parent["exclusions"]}
    if only_ids is not None and (not only_ids or len(only_ids) != len(set(only_ids)) or not set(only_ids) <= set(previous)):
        raise ValueError("Recovery subset cannot include accepted/holdout/Live/additional tasks")
    results = []
    with _lock(root, "strict_recovery_wave_worker" + (":" + strategy if strategy != "standard" else "")):
        for tier in "ABC":
            selected = [row for row in initial["entries"][:limit] if row["tier"] == tier and (only_ids is None or row["case_id"] in only_ids)]
            with ThreadPoolExecutor(max_workers=concurrency) as workers:
                tasks = {workers.submit(recover_case_wave, root, parent_id, candidates[row["case_id"]], previous[row["case_id"]],
                                        depth=depth, hours_limit=hours_limit, strategy=strategy): row for row in selected}
                for future in as_completed(tasks):
                    row = tasks[future]
                    try:
                        value, reused = future.result()
                        results.append({"case_id": row["case_id"], "status": value["status"], "accepted": value["accepted"],
                            "tier": tier, "receipt_reused": reused,
                            "wave_receipt": _descriptor(root, root / "data/development/recovery/waves" /
                                digest([parent_id, row["case_id"], depth, hours_limit] + ([strategy] if strategy != "standard" else [])) / "receipt.json")})
                    except (ValueError, OSError, RuntimeError) as error:
                        results.append({"case_id": row["case_id"], "tier": tier, "status": "recovery_runtime_failure", "accepted": False,
                                        "error": str(error)[:200]})
                    progress = {"schema": SCHEMA, "parent_cohort_sha256": parent_id, "original_main_dev_total": 400,
                        "accepted_preserved": parent["accepted_count"], "newly_accepted_in_wave": sum(row["accepted"] for row in results),
                        "processed": len(results), "results": results, "counts": dict(Counter(row["status"] for row in results)),
                        "llm_calls": 0, "all_admissible_paths_exhausted": False, "evaluation_authorized": False,
                        "parent_frozen_membership_changed": False}
                    write_progress(root / "artifacts/strict_recovery" / f"WAVE_DEPTH_{depth}_HOURS_{hours_limit}{'_'+strategy if strategy != 'standard' else ''}.json", progress)
    return progress
