"""Bounded official-source diagnostics for an original pending Dev case.

These records never accept a case by themselves. Edit summaries, missing
archive captures and incomplete pagination remain unverified proof paths.
"""
import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from issueintel.repository.acquire import parse_github_reference
from issueintel.runtime.io import digest, write_json
from .strict_dev import _json, _checked, _publish, PUBLIC_KEYS

_LOADED_SOURCE = Path(__file__).read_bytes()


class _NoGraphQLRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Read-only GraphQL requests cannot redirect credentials")


def _authorize_pending_case(root, case, kind, number):
    from .strict_dev import load_pool
    from .main_metadata import derive_pr_identity
    authorization = _json(root / "configs/strict_recovery_authorization.json")
    parent_id = authorization["parent_cohort_sha256"]
    parent = _json(root / "data/development/cohorts" / (parent_id + ".json"))
    if digest(parent) != parent_id or case["case_id"] not in {row["case_id"] for row in parent["exclusions"]}:
        raise ValueError("Source lookup must remain in the original pending Dev partition")
    pool = load_pool(root, parent["pool_sha256"])
    candidate = next((row for row in pool["candidates"] if row["case"]["case_id"] == case["case_id"]), None)
    if not candidate or candidate["case"] != case or candidate["origin"] != "bootstrap_main_dev":
        raise ValueError("Source lookup cannot rewrite/import a benchmark case")
    if kind == "closing":
        if number != derive_pr_identity(case)[1]:
            raise ValueError("Closing-reference lookup must be for the original source PR")
    else:
        stage = _json(root / "data/development/acquisition" / digest(case) / "metadata.json")
        metadata = _json(_checked(root, stage["output"]))
        if stage["case_sha256"] != digest(case) or not any(ref["repository"] == case["repository"]
                and ref["number"] == number and ref.get("source_kind") != "django_trac" for ref in metadata["issue_references"]):
            raise ValueError("Edit history lookup must bind an original selected GitHub issue")


def recover_missing_metadata_stage(root, case):
    """Rebuild only a missing stage from unchanged, independently verified mapping."""
    from .main_metadata import derive_pr_identity, verify_main_mapping_record
    from .strict_dev import _lock
    root = Path(root).resolve()
    if set(case) != PUBLIC_KEYS or case.get("split") != "main_dev":
        raise ValueError("Metadata repair accepts public original Dev only")
    _authorize_pending_case(root, case, "closing", derive_pr_identity(case)[1])
    path = root / "data/development/acquisition" / digest(case) / "metadata.json"
    with _lock(root, "acquisition:" + digest(case)):
        if path.exists():
            pointer = _json(path)
            if pointer.get("case_sha256") != digest(case):
                raise ValueError("Existing metadata identity changed; do not repair its hash")
            _checked(root, pointer["output"])
            return pointer["output"], True
        selected = None
        with (root / "data/annotations/main_metadata.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if json.dumps(case["case_id"]) in line:
                    row = json.loads(line)
                    if row.get("case_id") == case["case_id"]:
                        selected = row
        if selected is None:
            raise ValueError("No verified original relation available to repair metadata")
        source = verify_main_mapping_record(root, case, selected)
        original = ([selected["original_query_candidate"]] if selected.get("original_query_candidate") else selected.get("selected_issues", []))
        refs = [{key: ref[key] for key in ("repository", "number", "source_kind") if key in ref} for ref in original]
        if refs and refs[0].get("source_kind") == "django_trac":
            from .trac import audit_issue_receipt
            numbers = {str(ref["number"]) for ref in refs}
            receipts = [audit_issue_receipt(root, pair["initial_receipt"]) for pair in source["trac_pairs"]
                if str(pair["initial_receipt"]["payload"]["ticket_number"]) in numbers]
        else:
            from .temporal import issue_reference_url
            urls = {issue_reference_url(ref) for ref in refs}
            receipts = [receipt for receipt in source["issue_receipts"] if receipt["source_url"] in urls]
        output = _publish(root, root / "data/development/metadata", {"issue_references": refs, "issue_receipts": receipts})
        write_json(path, {"case_sha256": digest(case), "output": output,
            "recovery_mapping_id": selected["mapping_id"], "repair": "rebuild_missing_stage_only", "existing_hashes_rewritten": False})
        return output, False

EDIT_QUERY = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){
  repository(owner:$owner,name:$name){id nameWithOwner
    issue(number:$number){id databaseId number url createdAt updatedAt lastEditedAt title body
      timelineItems(itemTypes:[RENAMED_TITLE_EVENT],first:100){totalCount pageInfo{hasNextPage endCursor}
        nodes{... on RenamedTitleEvent{id createdAt previousTitle currentTitle}}}
      userContentEdits(first:100,after:$cursor){totalCount pageInfo{hasNextPage endCursor}
        nodes{id createdAt editedAt deletedAt diff}}}}
}"""
CLOSING_QUERY = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){
  repository(owner:$owner,name:$name){id nameWithOwner
    pullRequest(number:$number){id databaseId number url createdAt
      closingIssuesReferences(first:100,after:$cursor){totalCount pageInfo{hasNextPage endCursor}
        nodes{id databaseId number url createdAt updatedAt title body repository{id nameWithOwner}}}}}
}"""


def fetch_graphql_record(root, case, *, kind, number, cursor=None):
    from .strict_dev import _lock
    root = Path(root).resolve()
    if set(case) != PUBLIC_KEYS or case.get("split") != "main_dev":
        raise ValueError("Source recovery accepts a public original Dev projection only")
    if kind not in {"edits", "closing"} or type(number) is not int or number <= 0:
        raise ValueError("Require an explicit bounded issue or PR identity")
    if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 1024):
        raise ValueError("Invalid source pagination cursor")
    _authorize_pending_case(root, case, kind, number)
    repository = parse_github_reference(case["repository"])
    variables = {"owner": repository.owner, "name": repository.repo, "number": number, "cursor": cursor}
    query = EDIT_QUERY if kind == "edits" else CLOSING_QUERY
    identity = digest([digest(case), kind, variables, digest(query), "read_only_graphql_v2"])
    path = root / "data/development/recovery/graphql_stages" / (identity + ".json")
    with _lock(root, "source_recovery:" + identity):
        if path.exists():
            return _json(_checked(root, _json(path)["output"])), _json(path)["output"]
        value = {"case_id": case["case_id"], "case_sha256": digest(case), "source_url": "https://api.github.com/graphql",
            "kind": kind, "query": query, "variables": variables, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "llm_calls": 0, "gold_used": False, "evaluation_authorized": False, "absence_claim": False}
        sha = hashlib.sha256(_LOADED_SOURCE).hexdigest()
        source = root / "data/development/recovery/code" / (sha + ".py")
        source.parent.mkdir(parents=True, exist_ok=True)
        if source.exists() and source.read_bytes() != _LOADED_SOURCE:
            raise ValueError("Immutable source collector code changed")
        if not source.exists():
            source.write_bytes(_LOADED_SOURCE)
        value["collector_code"] = {"path": source.relative_to(root).as_posix(), "sha256": sha}
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            value["status"] = "github_key_missing"
        else:
            if any(ord(char) < 32 for char in token):
                raise ValueError("Invalid token header")
            request = Request(value["source_url"], json.dumps({"query": query, "variables": variables}).encode(),
                {"Authorization": "Bearer " + token, "Content-Type": "application/json", "User-Agent": "IssueIntel/strict-proof-recovery"}, method="POST")
            try:
                with build_opener(_NoGraphQLRedirect()).open(request, timeout=30) as response:
                    raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    value["status"] = "response_cap_reached"
                else:
                    value.update(response_sha256=hashlib.sha256(raw).hexdigest(),
                        raw_response_base64=base64.b64encode(raw).decode(), payload=json.loads(raw))
                    value["status"] = "graphql_errors" if value["payload"].get("errors") else "response_received"
            except HTTPError as error:
                value.update(status="http_access_failure", http_status=error.code)
            except (URLError, TimeoutError, OSError, ValueError):
                value["status"] = "source_transport_or_parse_failure"
        ref = _publish(root, root / "data/development/recovery/graphql_records", value)
        write_json(path, {"output": ref})
        return value, ref


def collect_graphql_path(root, case, *, kind, number, max_pages=10):
    """Read complete available pages, while refusing to call summaries full text."""
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ValueError("Require bounded official-source pagination")
    cursor, seen, records, nodes = None, set(), [], []
    complete, reason, total = False, "pagination_cap", None
    for _ in range(max_pages):
        record, ref = fetch_graphql_record(root, case, kind=kind, number=number, cursor=cursor)
        records.append(ref)
        if record["status"] != "response_received":
            reason = record["status"]
            break
        repo = (record["payload"].get("data") or {}).get("repository") or {}
        item = repo.get("issue" if kind == "edits" else "pullRequest")
        if not item or item.get("number") != number or repo.get("nameWithOwner", "").lower() != case["repository"].lower():
            reason = "official_source_identity_unresolved"
            break
        field = item.get("userContentEdits" if kind == "edits" else "closingIssuesReferences")
        if not isinstance(field, dict) or not isinstance(field.get("nodes"), list):
            reason = "source_connection_unresolved"
            break
        total = field.get("totalCount")
        nodes.extend(field["nodes"])
        page = field.get("pageInfo") or {}
        if page.get("hasNextPage") is False:
            complete = type(total) is int and total == len(nodes) and all(isinstance(node, dict) for node in nodes)
            reason = "available_connection_replayed" if complete else "source_count_or_deleted_node_mismatch"
            break
        next_cursor = page.get("endCursor")
        if not next_cursor or next_cursor in seen:
            reason = "pagination_cursor_unresolved"
            break
        seen.add(next_cursor)
        cursor = next_cursor
    value = {"case_id": case["case_id"], "case_sha256": digest(case), "kind": kind, "number": number,
        "records": records, "total_count": total, "nodes_read": len(nodes), "available_connection_complete": complete,
        "status": reason, "deleted_edit_count": sum(bool(node.get("deletedAt")) for node in nodes if isinstance(node, dict)),
        "full_opening_body_or_title_history_proven": False, "absence_claim": False, "all_admissible_paths_exhausted": False,
        "interpretation": "UserContentEdit.diff is documented as a summary; complete pagination alone does not certify reversible opening text. Closing references are source-binding candidates only.",
        "documentation": "https://docs.github.com/en/graphql/reference/users#usercontentedit",
        "llm_calls": 0, "gold_used": False, "evaluation_authorized": False}
    return _publish(Path(root).resolve(), Path(root).resolve() / "data/development/recovery/graphql_paths", value), value


def initial_edit_snapshot_candidate(case, current, records):
    """Strictly identify a stored initial revision; never infer it from zero edits.

    The API's historical field has a confusing name. This candidate is usable
    only after checking full current-body equality, an explicit stored version
    at creation, complete undeleted records, and reversible title history. It
    does not itself authorize acceptance or relax the opening proof contract.
    """
    if set(case) != PUBLIC_KEYS or case.get("split") != "main_dev":
        return None, ["public_projection_or_dev_membership_invalid"]
    if not records:
        return None, ["initial_edit_source_missing"]
    items, versions, titles = [], [], []
    for record in records:
        try:
            raw = base64.b64decode(record["raw_response_base64"], validate=True)
            payload = json.loads(raw)
            if (hashlib.sha256(raw).hexdigest() != record["response_sha256"] or payload != record["payload"]
                    or record["case_sha256"] != digest(case) or record["source_url"] != "https://api.github.com/graphql"
                    or record["kind"] != "edits" or record["status"] != "response_received" or payload.get("errors")):
                return None, ["initial_edit_source_integrity_invalid"]
            variables = record["variables"]
            repo = payload["data"]["repository"]
            item = repo["issue"]
            number = current["payload"]["number"]
            if (repo["nameWithOwner"].lower() != case["repository"].lower()
                    or variables["owner"] + "/" + variables["name"] != case["repository"] or variables["number"] != number
                    or item["databaseId"] != current["payload"]["id"] or item["number"] != number
                    or item["url"] != "https://github.com/" + case["repository"] + "/issues/" + str(number)
                    or item["createdAt"] != current["payload"]["created_at"] or "pull_request" in current["payload"]):
                return None, ["initial_edit_issue_identity_invalid"]
            items.append(item)
            versions.extend(item["userContentEdits"]["nodes"])
            timeline = item["timelineItems"]
            if timeline["pageInfo"]["hasNextPage"] is not False or timeline["totalCount"] != len(timeline["nodes"]):
                return None, ["initial_title_history_incomplete"]
            titles.append(timeline["nodes"])
        except (ValueError, TypeError, KeyError, UnicodeError):
            return None, ["initial_edit_source_integrity_invalid"]
    first = items[0]
    history = first["userContentEdits"]
    if (any(item["body"] != first["body"] or item["title"] != first["title"] or item["lastEditedAt"] != first["lastEditedAt"] for item in items)
            or history["totalCount"] != len(versions) or items[-1]["userContentEdits"]["pageInfo"]["hasNextPage"] is not False
            or any(nodes != titles[0] for nodes in titles)):
        return None, ["initial_edit_history_incomplete_or_concurrent"]
    if not versions:
        return None, ["no_explicit_stored_initial_version"]
    required = {"id", "createdAt", "editedAt", "deletedAt", "diff"}
    if any(not isinstance(node, dict) or not required <= set(node) or node.get("deletedAt") is not None
            or not isinstance(node.get("diff"), str) or not isinstance(node.get("id"), str) for node in versions):
        return None, ["initial_edit_history_deleted_or_summary_unresolved"]
    if len({node["id"] for node in versions}) != len(versions) or len({node["editedAt"] for node in versions}) != len(versions):
        return None, ["initial_edit_history_ambiguous"]
    versions = sorted(versions, key=lambda node: node["editedAt"])
    if (versions[0]["editedAt"] != first["createdAt"] or versions[-1]["editedAt"] != first["lastEditedAt"]
            or versions[-1]["diff"] != first["body"]):
        return None, ["initial_edit_snapshot_format_or_version_time_unverified"]
    original_title = first["title"]
    if len({event.get("createdAt") for event in titles[0]}) != len(titles[0]):
        return None, ["initial_title_history_ambiguous"]
    for event in sorted(titles[0], key=lambda node: node["createdAt"], reverse=True):
        if event["createdAt"] < first["createdAt"] or event["currentTitle"] != original_title:
            return None, ["initial_title_chain_invalid"]
        original_title = event["previousTitle"]
    try:
        instant = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
        dates = [instant(node[key]) for node in versions for key in ("createdAt", "editedAt")]
        fetches = [instant(record["fetched_at"]) for record in records]
        if (any(value.utcoffset() is None for value in dates + fetches)
                or any(value > min(fetches) for value in dates)
                or any(instant(node["createdAt"]) < instant(node["editedAt"]) for node in versions)):
            return None, ["initial_edit_source_time_invalid"]
    except (ValueError, TypeError, AttributeError):
        return None, ["initial_edit_source_time_invalid"]
    return {"issue_id": first["databaseId"], "number": first["number"], "created_at": first["createdAt"],
            "initial_body_revision_id": versions[0]["id"], "initial_revision_edited_at": versions[0]["editedAt"],
            "initial_revision_record_created_at": versions[0]["createdAt"], "title": original_title, "body": versions[0]["diff"],
            "current_body_correspondence_verified": True, "title_history_replayed": True,
            "retrospective_capture": True, "new_acceptance_authorized_by_candidate_alone": False}, []
