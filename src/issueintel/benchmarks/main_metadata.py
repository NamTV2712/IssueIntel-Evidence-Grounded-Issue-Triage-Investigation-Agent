"""Targeted Verified PR-to-issue mapping, kept outside public model inputs.

Closing keywords provide candidates, not causal ground truth. An issue is
selected only when its actual title/body reproduces the SWE problem statement.
PR descriptions and current issue text do not prove issue-creation-time content.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import urllib.request
import urllib.error
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from issueintel.repository.acquire import open_github_request, parse_github_reference
from issueintel.runtime.io import write_json
from .models import utc_timestamp
from .sources import inside_root, sha256_file
from .store import verify_manifest
from .temporal import _atomic_bytes

BODY_REFERENCE_POLICY = "pr_body_closing_references_v1"
TITLE_BODY_REFERENCE_POLICY = "pr_title_body_closing_references_v2"
COMMIT_REFERENCE_POLICY = "pr_title_body_commits_closing_references_v2"
REFERENCE_POLICIES = {BODY_REFERENCE_POLICY, TITLE_BODY_REFERENCE_POLICY, COMMIT_REFERENCE_POLICY}
EXACT_MAPPING_STATUSES = {"exact_issue_match", "exact_multi_issue_match"}
TRAC_MAPPING_METHOD = "django_trac_current_query_and_declared_relation_v3"
DECLARED_GITHUB_MAPPING_METHOD = "github_current_query_and_declared_relation_v3"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _normalize_text(text: str) -> str:
    # Preserve internal whitespace/content; only normalize line endings and outer padding.
    return text.replace("\r\n", "\n").strip()


@dataclass(frozen=True)
class IssueReference:
    repository: str
    number: int
    keyword: str
    source_start: int
    source_end: int

    @property
    def global_id(self) -> str:
        return f"{self.repository.lower()}#{self.number}"

    @property
    def api_url(self) -> str:
        return f"https://api.github.com/repos/{self.repository}/issues/{self.number}"


def extract_closing_references(body: str, default_repository: str) -> list[IssueReference]:
    """Extract explicit closing references without treating every #number as a link.

    GitHub documents repeating the keyword for multiple issues. References inside
    examples/comments are skipped, and duplicate global identities are collapsed.
    Full issue URLs are accepted as candidates and still need identity/text checks.
    """
    default_repository = parse_github_reference(default_repository).repository
    if not isinstance(body, str):
        raise ValueError("PR body must be text")
    masked = re.sub(r"<!--.*?-->", lambda match: " " * len(match.group()), body, flags=re.DOTALL)
    masked = re.sub(r"(?m)^\s*(`{3,}|~{3,}).*?^\s*\1[^\n]*$",
                    lambda match: " " * len(match.group()), masked, flags=re.DOTALL)
    masked = re.sub(r"`+[^`\n]*`+", lambda match: " " * len(match.group()), masked)
    ref = (
        r"(?:https://github\.com/(?P<url_repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(?P<url_number>[1-9][0-9]*)"
        r"(?:[?#][^\s)\]>]*)?(?![A-Za-z0-9_/])"
        r"|(?P<qualified_repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s*#(?P<qualified_number>[1-9][0-9]*)(?![A-Za-z0-9_])"
        r"|#(?P<local_number>[1-9][0-9]*)(?![A-Za-z0-9_]))"
    )
    # Markdown labels may precede a full link; the full target remains authoritative.
    pattern = re.compile(
        r"(?<![A-Za-z0-9_])(?P<keyword>close[sd]?|fix(?:es|ed)?|resolve[sd]?)"
        r"(?:\s*:\s*|\s+)(?:\[[^\]\n]*\]\()?" + ref,
        re.IGNORECASE,
    )
    references = []
    seen = set()
    for match in pattern.finditer(masked):
        repository = match.group("url_repo") or match.group("qualified_repo") or default_repository
        number = int(match.group("url_number") or match.group("qualified_number") or match.group("local_number"))
        try:
            repository = parse_github_reference(repository).repository
        except ValueError:
            continue
        identity = repository.lower(), number
        if identity in seen:
            continue
        seen.add(identity)
        references.append(IssueReference(repository, number, match.group("keyword").lower(),
                                         match.start(), match.end()))
    return references


def derive_pr_identity(case: Mapping[str, Any]) -> tuple[str, int]:
    """The numeric suffix is a PR identifier, never an inferred issue identifier."""
    repository = parse_github_reference(str(case["repository"])).repository
    prefix = repository.replace("/", "__") + "-"
    instance_id = str(case["instance_id"])
    suffix = instance_id[len(prefix):] if instance_id.startswith(prefix) else ""
    if not re.fullmatch(r"[1-9][0-9]*", suffix):
        raise ValueError("Verified instance_id does not match its repository/PR identity")
    if case.get("case_id") != "verified:" + instance_id:
        raise ValueError("Require an original Verified case identity")
    return repository, int(suffix)


def fetch_api_receipt(url: str, *, timeout: int = 30, max_response_bytes: int = 2_000_000) -> dict[str, Any]:
    """Fetch one bounded public endpoint, including only the first 100 PR commits.

    Never follow pagination or redirects that forward authorization. Commit
    messages stay in private receipts and are only used to find issue candidates.
    """
    match = re.fullmatch(
        r"https://api\.github\.com/repos/([^/]+/[^/]+)/(pulls|issues)/([1-9][0-9]*)"
        r"(?P<commits>/commits\?per_page=100&page=1)?", url)
    if not match:
        raise ValueError("Require a specific GitHub PR or issue API URL")
    if match.group("commits") and match.group(2) != "pulls":
        raise ValueError("Commit listing must belong to a specific pull request")
    if timeout <= 0 or not 1 <= max_response_bytes <= 2_000_000:
        raise ValueError("Require positive bounded request limits")
    parse_github_reference(match.group(1))
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if any(ord(character) < 32 for character in token):
        raise ValueError("Invalid GitHub token header")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "IssueIntel/0.2 main-metadata",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    with open_github_request(request, timeout=timeout) as response:
        raw = response.read(max_response_bytes + 1)
        if len(raw) > max_response_bytes:
            raise ValueError("GitHub metadata exceeds the response size cap")
        payload = json.loads(raw)
        pagination_complete = not bool(re.search(r';\s*rel="next"', response.headers.get("Link", "")))
    receipt = {
        "source_url": url, "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload": payload, "response_sha256": hashlib.sha256(raw).hexdigest(),
        "payload_sha256": _digest(payload),
        "body_version_at_creation_verified": False,
    }
    if match.group("commits"):
        if not isinstance(payload, list) or len(payload) > 100:
            raise ValueError("PR commit receipt must contain at most one page of 100 commits")
        receipt["pagination_complete"] = pagination_complete
        receipt["maximum_pages"] = 1
        receipt["maximum_commits"] = 100
    return receipt


def _receipt_payload(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = receipt.get("payload", {})
    if not isinstance(payload, Mapping):
        raise ValueError("GitHub receipt payload must be an object")
    if receipt.get("payload_sha256") and receipt["payload_sha256"] != _digest(payload):
        raise ValueError("GitHub receipt payload digest does not match")
    return payload


def _commit_messages(
    receipt: Mapping[str, Any], repository: str, pull_number: int,
) -> tuple[list[str], bool]:
    expected_url = f"https://api.github.com/repos/{repository}/pulls/{pull_number}/commits?per_page=100&page=1"
    payload = receipt.get("payload")
    if receipt.get("source_url") != expected_url or not isinstance(payload, list) or len(payload) > 100:
        raise ValueError("Commit receipt does not match the bounded PR endpoint")
    if receipt.get("payload_sha256") and receipt["payload_sha256"] != _digest(payload):
        raise ValueError("PR commit payload digest does not match")
    if type(receipt.get("pagination_complete")) is not bool:
        raise ValueError("Commit receipt must record whether the first page was complete")
    messages = []
    for row in payload:
        if (not isinstance(row, Mapping) or not re.fullmatch(r"[0-9a-fA-F]{40}", str(row.get("sha", "")))
                or not isinstance(row.get("commit"), Mapping)
                or not isinstance(row["commit"].get("message"), str)):
            raise ValueError("Invalid commit identity/message in PR receipt")
        messages.append(row["commit"]["message"])
    return messages, receipt["pagination_complete"]


def _policy_references(
    pull: Mapping[str, Any], repository: str, pull_number: int, reference_policy: str,
    commit_receipt: Mapping[str, Any] | None,
) -> tuple[list[IssueReference], int, bool | None]:
    if reference_policy not in REFERENCE_POLICIES:
        raise ValueError("Unknown Main issue-reference policy")
    body = str(pull.get("body") or "")
    if reference_policy == BODY_REFERENCE_POLICY:
        return extract_closing_references(body, repository), 0, None
    title = pull.get("title") or ""
    if not isinstance(title, str) or not isinstance(pull.get("body") or "", str):
        raise ValueError("PR title/body must be text")
    text = title + "\n" + body
    messages, complete = [], None
    if reference_policy == COMMIT_REFERENCE_POLICY:
        if commit_receipt is None:
            raise ValueError("Commit-reference policy requires a private commit receipt")
        messages, complete = _commit_messages(commit_receipt, repository, pull_number)
        text += "\n" + "\n".join(messages)
    return extract_closing_references(text, repository), len(messages), complete


def map_verified_case(
    case: Mapping[str, Any], pull_receipt: Mapping[str, Any],
    issue_receipts: Iterable[Mapping[str, Any]], *, max_references: int = 10,
    reference_policy: str = BODY_REFERENCE_POLICY,
    commit_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure mapping with exact text matching; no model or network operation."""
    repository, pull_number = derive_pr_identity(case)
    if type(max_references) is not int or not 1 <= max_references <= 10:
        raise ValueError("max_references must be between one and ten")
    pull_url = f"https://api.github.com/repos/{repository}/pulls/{pull_number}"
    pull = _receipt_payload(pull_receipt)
    if (pull_receipt.get("source_url") != pull_url or pull.get("url", pull_url) != pull_url
            or str(pull.get("number")) != str(pull_number)
            or str(pull.get("base", {}).get("repo", {}).get("full_name", repository)).lower() != repository.lower()):
        raise ValueError("PR receipt does not match the source instance identity")
    references, commits_inspected, commits_complete = _policy_references(
        pull, repository, pull_number, reference_policy, commit_receipt)
    selected_references = references[:max_references]
    by_url: dict[str, list[Mapping[str, Any]]] = {}
    for receipt in issue_receipts:
        by_url.setdefault(str(receipt.get("source_url")), []).append(receipt)
    candidates, rejected = [], []
    for reference in selected_references:
        receipts = by_url.get(reference.api_url, [])
        if len(receipts) != 1:
            rejected.append({"global_id": reference.global_id, "reason": "missing_or_duplicate_issue_receipt"})
            continue
        receipt = receipts[0]
        issue = _receipt_payload(receipt)
        if "pull_request" in issue:
            rejected.append({"global_id": reference.global_id, "reason": "reference_is_a_pull_request"})
            continue
        if str(issue.get("number")) != str(reference.number) or issue.get("url") != reference.api_url:
            rejected.append({"global_id": reference.global_id, "reason": "issue_identity_mismatch"})
            continue
        if not isinstance(issue.get("title"), str) or not isinstance(issue.get("body") or "", str):
            rejected.append({"global_id": reference.global_id, "reason": "invalid_issue_text"})
            continue
        created = utc_timestamp(issue.get("created_at"), optional=True)
        if not created:
            rejected.append({"global_id": reference.global_id, "reason": "missing_actual_issue_created_at"})
            continue
        rendered = f"{issue['title']}\n{issue.get('body') or ''}\n"
        candidates.append({
            "repository": reference.repository, "number": reference.number,
            "global_id": reference.global_id, "issue_url": f"https://github.com/{reference.repository}/issues/{reference.number}",
            "issue_created_at": created, "source_url": reference.api_url,
            "receipt_sha256": _digest(receipt), "rendered_problem_statement": rendered,
        })
    target = _normalize_text(str(case["problem_statement"]))
    matches = []
    # Preserve documented first-appearance ordering; at most 2^10 - 1 subsets.
    for size in range(1, len(candidates) + 1):
        for group in itertools.combinations(candidates, size):
            if _normalize_text("".join(item["rendered_problem_statement"] for item in group)) == target:
                matches.append(group)
    if commits_complete is False:
        status, selected = "commit_reference_bound_exceeded", []
    elif len(references) > max_references:
        status, selected = "reference_bound_exceeded", []
    elif len(matches) > 1:
        status, selected = "ambiguous_exact_matches", []
    elif len(matches) == 1:
        selected = list(matches[0])
        status = "exact_issue_match" if len(selected) == 1 else "exact_multi_issue_match"
    elif not references:
        status = "no_closing_references_in_pr_body" if reference_policy == BODY_REFERENCE_POLICY else "no_closing_references_under_policy"
        selected = []
    else:
        status, selected = "no_exact_current_issue_text_match", []
    if repository.lower() == "django/django":
        # SWE's original Django collector reads Trac tickets, not GitHub issues.
        status, selected = "unsupported_django_trac_source", []
    report = {
        "schema_version": 1 if reference_policy == BODY_REFERENCE_POLICY else 2,
        "case_id": case["case_id"], "case_sha256": _digest(case), "status": status,
        "pull_request": {"repository": repository, "number": pull_number, "source_url": pull_url,
                         "pull_created_at": utc_timestamp(pull.get("created_at"), optional=True),
                         "receipt_sha256": _digest(pull_receipt)},
        "closing_candidates": [{**asdict(ref), "global_id": ref.global_id} for ref in references],
        "reference_limit": max_references,
        "candidates_fetched": len(selected_references) if reference_policy == BODY_REFERENCE_POLICY else sum(
            len(by_url.get(reference.api_url, [])) == 1 for reference in selected_references),
        "rejected_candidates": rejected,
        "exact_match_groups": [[item["global_id"] for item in group] for group in matches],
        "selected_issues": [{key: value for key, value in item.items() if key != "rendered_problem_statement"}
                            for item in selected],
        "issue_created_at": selected[0]["issue_created_at"] if len(selected) == 1 else None,
        "query_time": min((item["issue_created_at"] for item in selected), default=None),
        "mapping_rule": "explicit closing-reference candidates plus exact title/newline/body concatenation",
        "multi_issue_order": "closing-reference first appearance; unmatched alternatives require review",
        "source_issue_tracker": "django_trac" if repository.lower() == "django/django" else "github_issues",
        "current_text_matches_source_task": bool(selected),
        "original_text_at_issue_creation_verified": False,
        "strict_issue_time_snapshot_verified": False,
        "public_fix_text_used": False,
        "evaluation_performed": False,
        "limitations": [
            "PR body can change; keyword extraction is a candidate-generation rule, not proof of causal resolution.",
            "Exact current text matching does not prove the original issue body; historical proof is separate.",
            "Manual sidebar links, commit-only references and different multi-issue order may remain unresolved.",
        ],
    }
    if reference_policy != BODY_REFERENCE_POLICY:
        report.update({"reference_policy": reference_policy, "commits_inspected": commits_inspected,
                       "commit_list_complete": commits_complete,
                       "mapping_rule": "versioned explicit closing-reference candidates plus exact title/newline/body concatenation"})
        if repository.lower() == "django/django":
            report["closing_candidates"] = [
                {**asdict(ref), "global_id": f"django_trac#{ref.number}", "issue_tracker": "django_trac",
                 "ticket_url": f"https://code.djangoproject.com/ticket/{ref.number}"}
                if ref.repository.lower() == "django/django" else
                {**asdict(ref), "global_id": ref.global_id, "issue_tracker": "github_issues"}
                for ref in references]
    return report


def resolve_main_case(
    case: Mapping[str, Any], *, fetcher: Callable[[str], Mapping[str, Any]] | None = None,
    max_references: int = 10,
    reference_policy: str = TITLE_BODY_REFERENCE_POLICY,
    allow_commit_fallback: bool = True,
    pull_receipt: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Try PR title/body, then at most one commit page if still unresolved.

    Candidate generation follows SWE's documented source locations, but an
    identity is still selected only by exact issue-text matching. Trac numbers
    are recorded as ticket candidates without querying GitHub's issues endpoint.
    """
    repository, pull_number = derive_pr_identity(case)
    if type(max_references) is not int or not 1 <= max_references <= 10:
        raise ValueError("max_references must be between one and ten")
    if reference_policy not in {BODY_REFERENCE_POLICY, TITLE_BODY_REFERENCE_POLICY}:
        raise ValueError("Resolver starts with a PR body or title/body policy")
    if type(allow_commit_fallback) is not bool:
        raise ValueError("allow_commit_fallback must be boolean")
    fetcher = fetcher or fetch_api_receipt
    pull = pull_receipt or fetcher(f"https://api.github.com/repos/{repository}/pulls/{pull_number}")
    # Validate identity before deriving/fetching any candidate endpoint.
    map_verified_case(case, pull, [], max_references=max_references, reference_policy=reference_policy)
    references, _, _ = _policy_references(_receipt_payload(pull), repository, pull_number, reference_policy, None)
    issues, failures = [], []
    fetched = set()

    def fetch_candidates(candidates: list[IssueReference]) -> None:
        for reference in ([] if repository.lower() == "django/django" else candidates[:max_references]):
            if reference.api_url in fetched:
                continue
            fetched.add(reference.api_url)
            try:
                issues.append(fetcher(reference.api_url))
            except urllib.error.HTTPError as error:
                if error.code not in {404, 410}:
                    raise
                failures.append({"global_id": reference.global_id, "http_status": error.code})

    fetch_candidates(references)
    report = map_verified_case(case, pull, issues, max_references=max_references, reference_policy=reference_policy)
    commit_receipt = None
    retry_statuses = {"no_closing_references_under_policy", "no_exact_current_issue_text_match"}
    needs_commits = report["status"] in retry_statuses or (repository.lower() == "django/django" and not references)
    if reference_policy == TITLE_BODY_REFERENCE_POLICY and allow_commit_fallback and needs_commits:
        commit_receipt = fetcher(f"https://api.github.com/repos/{repository}/pulls/{pull_number}/commits?per_page=100&page=1")
        references, _, complete = _policy_references(_receipt_payload(pull), repository, pull_number,
                                                    COMMIT_REFERENCE_POLICY, commit_receipt)
        if complete:
            fetch_candidates(references)
        report = map_verified_case(case, pull, issues, max_references=max_references,
                                   reference_policy=COMMIT_REFERENCE_POLICY, commit_receipt=commit_receipt)
    bundle = {"pull_receipt": pull, "issue_receipts": issues, "fetch_failures": failures}
    if commit_receipt is not None:
        bundle["commit_receipt"] = commit_receipt
    return report, bundle


def _relation_fields(case: Mapping[str, Any], relation: Mapping[str, Any]) -> dict[str, Any]:
    return {**relation, "basis": "pr_declared_closing_reference", "parent_case_id": case["case_id"],
        "source_query_sha256": hashlib.sha256(str(case["problem_statement"]).encode("utf-8")).hexdigest(),
        "independent_causal_validation": False, "human_validated": False,
        "source_human_label_scope": "original_SWE_query_only"}


def map_declared_verified_case(
    case: Mapping[str, Any], pull_receipt: Mapping[str, Any], issue_receipts: Iterable[Mapping[str, Any]], *,
    max_references: int = 10, reference_policy: str = TITLE_BODY_REFERENCE_POLICY,
    commit_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Add explicit reference lineage; preserve the exact source-query decision.

    A uniquely declared real issue is only a candidate for fetching its opening
    event. Its current API text is never represented as verified original text.
    """
    receipts = list(issue_receipts)
    report = map_verified_case(case, pull_receipt, receipts, max_references=max_references,
        reference_policy=reference_policy, commit_receipt=commit_receipt)
    if str(case["repository"]).lower() == "django/django":
        raise ValueError("Django reference lineage requires the separate Trac mapping method")
    repository, pull_number = derive_pr_identity(case)
    references, _, _ = _policy_references(_receipt_payload(pull_receipt), repository, pull_number,
                                        reference_policy, commit_receipt)
    rejected = {item["global_id"]: item["reason"] for item in report["rejected_candidates"]}
    by_url = {str(receipt.get("source_url")): receipt for receipt in receipts}
    relations = []
    for reference in references[:max_references]:
        if reference.global_id in rejected:
            continue
        issue = _receipt_payload(by_url[reference.api_url])
        relations.append(_relation_fields(case, {"source_kind": "github_issues", "repository": reference.repository,
            "number": reference.number, "global_id": reference.global_id,
            "issue_url": f"https://github.com/{reference.repository}/issues/{reference.number}",
            "source_url": reference.api_url, "issue_created_at": utc_timestamp(issue["created_at"]),
            "reference_keyword": reference.keyword, "reference_policy": reference_policy,
            "identity_receipt_sha256": _digest(by_url[reference.api_url]),
            "exact_source_query_match": report["current_text_matches_source_task"]}))
    complete = len(references) <= max_references and report.get("commit_list_complete") is not False
    unidentified = any(reason != "reference_is_a_pull_request" for reason in rejected.values())
    candidate = None
    if complete and not unidentified and len(relations) == 1:
        candidate = {**relations[0], "candidate_usable_for": "original_opening_query_acquisition_only",
            "original_query_sha256": None, "original_issue_text_verified": False,
            "original_query_verification": "pending_independent_opened_event"}
    return {**report, "schema_version": 3, "mapping_method": DECLARED_GITHUB_MAPPING_METHOD,
        "declared_issue_relations": relations, "original_query_candidate": candidate,
        "original_query_candidate_status": "unique_declared_issue_pending_opening_receipt" if candidate else
            "pending_ambiguous_or_unverified_declared_issue_relation",
        "source_query_sha256": hashlib.sha256(str(case["problem_statement"]).encode("utf-8")).hexdigest()}


def map_trac_verified_case(
    root: str | Path, case: Mapping[str, Any], pull_receipt: Mapping[str, Any], trac_pairs: Iterable[Mapping[str, Any]], *,
    max_references: int = 10, reference_policy: str = TITLE_BODY_REFERENCE_POLICY,
    commit_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay official current/initial HTML, keeping query match and lineage separate."""
    from .trac import verify_trac_receipt, ticket_url
    repository, pull_number = derive_pr_identity(case)
    if repository.lower() != "django/django":
        raise ValueError("Trac mapping requires an original Django Main case")
    report = map_verified_case(case, pull_receipt, [], max_references=max_references,
        reference_policy=reference_policy, commit_receipt=commit_receipt)
    references, _, complete = _policy_references(_receipt_payload(pull_receipt), repository, pull_number,
                                                reference_policy, commit_receipt)
    pairs = {}
    for pair in trac_pairs:
        current = verify_trac_receipt(root, pair["current_receipt"])
        original = verify_trac_receipt(root, pair["initial_receipt"])
        if (current["version"] is not None or original["version"] != 0
                or current["ticket_number"] != original["ticket_number"]
                or current["created_at"] != original["created_at"]):
            raise ValueError("Trac current/initial receipt pair identity or creation time differs")
        number = current["ticket_number"]
        if number in pairs:
            raise ValueError("Ambiguous duplicate Trac ticket receipt pair")
        pairs[number] = (pair, current, original)
    candidates, relations, rejected = [], [], []
    for reference in references[:max_references]:
        if reference.repository.lower() != "django/django":
            rejected.append({"global_id": reference.global_id, "reason": "cross_repository_reference_is_not_django_trac"})
            continue
        if reference.number not in pairs:
            rejected.append({"global_id": f"django_trac#{reference.number}", "reason": "missing_trac_history_pair"})
            continue
        pair, current, original = pairs[reference.number]
        relation = _relation_fields(case, {"source_kind": "django_trac", "repository": "django/django",
            "number": reference.number, "ticket_number": reference.number, "global_id": f"django_trac#{reference.number}",
            "issue_url": ticket_url(reference.number), "source_url": ticket_url(reference.number, version=0),
            "issue_created_at": original["created_at"], "reference_keyword": reference.keyword,
            "reference_policy": reference_policy, "receipt_pair_sha256": _digest(pair),
            "initial_receipt_sha256": _digest(pair["initial_receipt"]),
            "original_query_sha256": original["problem_statement_sha256"],
            "exact_source_query_match": _normalize_text(current["problem_statement"]) == _normalize_text(str(case["problem_statement"])),
            "original_source_query_match": _normalize_text(original["problem_statement"]) == _normalize_text(str(case["problem_statement"]))})
        relations.append(relation)
        candidates.append({**relation, "rendered_problem_statement": current["problem_statement"],
                           "initial_problem_statement": original["problem_statement"]})
    matches = []
    for size in range(1, len(candidates) + 1):
        for group in itertools.combinations(candidates, size):
            if _normalize_text("".join(item["rendered_problem_statement"] for item in group)) == _normalize_text(str(case["problem_statement"])):
                matches.append(group)
    if complete is False:
        status, selected = "commit_reference_bound_exceeded", []
    elif len(references) > max_references:
        status, selected = "reference_bound_exceeded", []
    elif len(matches) > 1:
        status, selected = "ambiguous_exact_trac_matches", []
    elif len(matches) == 1:
        selected = list(matches[0])
        status = "exact_issue_match" if len(selected) == 1 else "exact_multi_issue_match"
    elif not references:
        status, selected = "no_closing_references_under_policy", []
    else:
        status, selected = "no_exact_current_trac_text_match", []
    candidate = None
    if complete is not False and len(references) <= max_references and not rejected and len(relations) == 1:
        candidate = {**relations[0], "candidate_usable_for": "original_opening_query_derivation",
            "original_issue_text_verified": True,
            "original_query_verification": "official_Trac_initial_version_replayed_from_private_HTML"}
    clean = lambda item: {key: value for key, value in item.items()
                          if key not in {"rendered_problem_statement", "initial_problem_statement"}}
    return {**report, "schema_version": 3, "mapping_method": TRAC_MAPPING_METHOD, "status": status,
        "reference_policy": reference_policy, "candidates_fetched": len(candidates), "rejected_candidates": rejected,
        "selected_issues": [clean(item) for item in selected],
        "exact_match_groups": [[item["global_id"] for item in group] for group in matches],
        "issue_created_at": selected[0]["issue_created_at"] if len(selected) == 1 else None,
        "query_time": min((item["issue_created_at"] for item in selected), default=None),
        "current_text_matches_source_task": bool(selected),
        "original_text_at_issue_creation_verified": bool(selected) and _normalize_text("".join(
            item["initial_problem_statement"] for item in selected)) == _normalize_text(str(case["problem_statement"])),
        "declared_issue_relations": relations, "original_query_candidate": candidate,
        "original_query_candidate_status": "unique_declared_ticket_initial_version_replayed" if candidate else
            "pending_ambiguous_or_unverified_declared_issue_relation",
        "source_query_sha256": hashlib.sha256(str(case["problem_statement"]).encode("utf-8")).hexdigest(),
        "mapping_rule": "PR-declared Trac references plus exact collector-rendered current query; initial version lineage separate"}


def _mapping_from_bundle(root: Path, case: Mapping[str, Any], mapping: Mapping[str, Any], bundle: Mapping[str, Any]) -> dict[str, Any]:
    arguments = {"max_references": mapping.get("reference_limit", 10),
        "reference_policy": mapping.get("reference_policy", BODY_REFERENCE_POLICY),
        "commit_receipt": bundle.get("commit_receipt")}
    method = mapping.get("mapping_method")
    if method == TRAC_MAPPING_METHOD:
        return map_trac_verified_case(root, case, bundle["pull_receipt"], bundle["trac_pairs"], **arguments)
    if method == DECLARED_GITHUB_MAPPING_METHOD:
        return map_declared_verified_case(case, bundle["pull_receipt"], bundle["issue_receipts"], **arguments)
    if method is not None:
        raise ValueError("Unknown Main metadata mapping method")
    return map_verified_case(case, bundle["pull_receipt"], bundle["issue_receipts"], **arguments)


def _trac_pair(root: Path, number: int, statement: str, *, pair_fetcher=None) -> Mapping[str, Any]:
    from .trac import fetch_trac_history_pair, verify_trac_receipt
    path = inside_root(root, root / "data/annotations/trac_pairs" / f"{number}.json")
    if path.exists():
        if path.stat().st_size > 8_000_000:
            raise ValueError("Private Trac pair cache exceeds its size bound")
        pair = json.loads(path.read_text(encoding="utf-8"))
    else:
        pair = (pair_fetcher or fetch_trac_history_pair)(root, number, expected_problem_statement=statement)
    current = verify_trac_receipt(root, pair["current_receipt"])
    original = verify_trac_receipt(root, pair["initial_receipt"])
    if (current["ticket_number"] != number or original["ticket_number"] != number
            or current["version"] is not None or original["version"] != 0
            or current["created_at"] != original["created_at"]):
        raise ValueError("Private Trac pair cache identity/version differs")
    if not path.exists():
        _atomic_bytes(root, path, _canonical(pair) + b"\n")
    return pair


def resolve_trac_main_case(
    root: str | Path, case: Mapping[str, Any], *, pull_receipt: Mapping[str, Any] | None = None,
    fetcher: Callable[[str], Mapping[str, Any]] | None = None, pair_fetcher=None,
    max_references: int = 10,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bounded official Trac acquisition; reuses verified private ticket pairs."""
    root = Path(root).resolve()
    repository, pull_number = derive_pr_identity(case)
    if repository.lower() != "django/django":
        raise ValueError("Trac resolver requires Django Main")
    fetcher = fetcher or fetch_api_receipt
    pull_receipt = pull_receipt or fetcher(f"https://api.github.com/repos/{repository}/pulls/{pull_number}")
    # This validates the PR and may fetch one commit page, but never a GitHub issue.
    candidate_report, base_bundle = resolve_main_case(case, fetcher=fetcher, pull_receipt=pull_receipt,
        max_references=max_references)
    policy = candidate_report["reference_policy"]
    references, _, complete = _policy_references(_receipt_payload(pull_receipt), repository, pull_number,
                                               policy, base_bundle.get("commit_receipt"))
    pairs, failures = [], []
    if complete is not False and len(references) <= max_references:
        for reference in references:
            if reference.repository.lower() != "django/django":
                continue
            try:
                pairs.append(_trac_pair(root, reference.number, str(case["problem_statement"]), pair_fetcher=pair_fetcher))
            except urllib.error.HTTPError as error:
                if error.code not in {404, 410}:
                    raise
                failures.append({"source_kind": "django_trac", "number": reference.number, "http_status": error.code})
    bundle = {**base_bundle, "trac_pairs": pairs, "trac_fetch_failures": failures}
    report = map_trac_verified_case(root, case, pull_receipt, pairs, max_references=max_references,
        reference_policy=policy, commit_receipt=base_bundle.get("commit_receipt"))
    return report, bundle


def _main_overlay_state(root: Path, cases: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    originals = {case["case_id"]: case for case in cases}
    overlay = root / "data/annotations/main_metadata.jsonl"
    latest, bundles = {}, {}
    if overlay.exists():
        for line in overlay.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            original = originals.get(row["case_id"])
            if original is None:
                continue
            bundles[row["case_id"]] = verify_main_mapping_record(root, original, row)
            latest[row["case_id"]] = row
    return latest, bundles


def enrich_trac_main_metadata(
    root: str | Path, *, limit: int = 10, all_splits: bool = False, fetcher=None, pair_fetcher=None,
) -> dict[str, Any]:
    """Append Trac metadata, current-query decision and initial-query candidate.

    Main holdout metadata is construction only. A completed schema3 event resumes
    even if the source-query match is pending; it is not repeatedly downloaded.
    """
    if type(limit) is not int or not 1 <= limit <= 500 or type(all_splits) is not bool:
        raise ValueError("Require bounded Main construction limit and explicit all_splits boolean")
    root = Path(root).resolve()
    manifest = verify_manifest(root)
    allowed = {"main_dev", "main_test"} if all_splits else {"main_dev"}
    cases = [row for line in (root / manifest["files"]["cases"]["path"]).read_text(encoding="utf-8").splitlines()
             if (row := json.loads(line))["split"] in allowed and row["repository"].lower() == "django/django"]
    latest, bundles = _main_overlay_state(root, cases)
    done = {case_id for case_id, row in latest.items() if row.get("mapping_method") == TRAC_MAPPING_METHOD}
    reports, failures = [], []
    for case in cases:
        if case["case_id"] in done:
            continue
        if len(reports) + len(failures) >= limit:
            break
        try:
            report, bundle = resolve_trac_main_case(root, case, fetcher=fetcher, pair_fetcher=pair_fetcher,
                pull_receipt=bundles.get(case["case_id"], {}).get("pull_receipt"))
            reports.append(append_main_mapping(root, case, report, bundle))
        except urllib.error.HTTPError as error:
            failures.append({"case_id": case["case_id"], "http_status": error.code})
            if error.code in {403, 429}:
                break
        except (ValueError, OSError) as error:
            failures.append({"case_id": case["case_id"], "error_type": type(error).__name__})
        write_json(root / "artifacts/trac_main_metadata_progress.json", {
            "mapped_this_run": len(reports), "already_mapped": len(done), "total_considered": len(cases),
            "statuses_this_run": dict(Counter(row["status"] for row in reports)), "failures": failures,
            "original_query_candidates": sum(row.get("original_query_candidate") is not None for row in reports),
            "last_case_id": case["case_id"], "all_splits_dataset_construction": all_splits,
            "evaluation_performed": False})
    result = {"mapped": len(reports), "already_mapped": len(done), "failures": failures,
        "statuses": dict(Counter(row["status"] for row in reports)),
        "original_query_candidates": sum(row.get("original_query_candidate") is not None for row in reports),
        "total_considered": len(cases), "all_splits_dataset_construction": all_splits,
        "evaluation_performed": False}
    write_json(root / "artifacts/trac_main_metadata_progress.json", {**result, "state": "finished"})
    return result


def upgrade_declared_main_relations(root: str | Path, *, limit: int = 500, all_splits: bool = False) -> dict[str, Any]:
    """Add schema3 lineage to pending GitHub mappings using existing receipts only."""
    if type(limit) is not int or not 1 <= limit <= 500 or type(all_splits) is not bool:
        raise ValueError("Require bounded Main construction limit and explicit all_splits boolean")
    root = Path(root).resolve()
    manifest = verify_manifest(root)
    allowed = {"main_dev", "main_test"} if all_splits else {"main_dev"}
    cases = [row for line in (root / manifest["files"]["cases"]["path"]).read_text(encoding="utf-8").splitlines()
             if (row := json.loads(line))["split"] in allowed and row["repository"].lower() != "django/django"]
    latest, bundles = _main_overlay_state(root, cases)
    reports = []
    for case in cases:
        previous = latest.get(case["case_id"])
        if (previous is None or previous["status"] in EXACT_MAPPING_STATUSES
                or previous.get("mapping_method") == DECLARED_GITHUB_MAPPING_METHOD):
            continue
        if len(reports) >= limit:
            break
        bundle = bundles[case["case_id"]]
        report = map_declared_verified_case(case, bundle["pull_receipt"], bundle["issue_receipts"],
            max_references=previous.get("reference_limit", 10),
            reference_policy=previous.get("reference_policy", BODY_REFERENCE_POLICY),
            commit_receipt=bundle.get("commit_receipt"))
        reports.append(append_main_mapping(root, case, report, bundle))
    result = {"mapped": len(reports), "original_query_candidates": sum(row["original_query_candidate"] is not None for row in reports),
              "statuses": dict(Counter(row["status"] for row in reports)), "network_calls": 0,
              "all_splits_dataset_construction": all_splits, "evaluation_performed": False}
    write_json(root / "artifacts/main_declared_relations_upgrade.json", result)
    return result


def append_main_mapping(
    root: str | Path, case: Mapping[str, Any], mapping: Mapping[str, Any], receipt_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep fix text in private receipts and append only metadata/hashes to the overlay."""
    root = Path(root).resolve()
    manifest_path = root / "data/manifests/dataset.json"
    manifest_sha = sha256_file(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for descriptor in manifest["files"].values():
        path = inside_root(root, root / descriptor["path"])
        if sha256_file(path) != descriptor["sha256"]:
            raise ValueError("Original benchmark artifact changed")
    cases = [json.loads(line) for line in (root / manifest["files"]["cases"]["path"]).read_text(
        encoding="utf-8").splitlines()]
    recorded_case = next((row for row in cases if row["case_id"] == case["case_id"]), None)
    if recorded_case is None or _digest(recorded_case) != _digest(case):
        raise ValueError("Main mapping must bind the original pinned case")
    expected = _mapping_from_bundle(root, case, mapping, receipt_bundle)
    if expected != mapping:
        raise ValueError("Mapping disagrees with its source receipts")
    content = _canonical(receipt_bundle) + b"\n"
    digest = hashlib.sha256(content).hexdigest()
    receipt_path = inside_root(root, root / "data/annotations/main_metadata_receipts" / f"{digest}.json")
    event = {**mapping, "receipt_bundle": {"path": receipt_path.relative_to(root).as_posix(), "sha256": digest},
             "benchmark_manifest_sha256": manifest_sha}
    event["mapping_id"] = _digest(event)
    overlay = inside_root(root, root / "data/annotations/main_metadata.jsonl")
    overlay.parent.mkdir(parents=True, exist_ok=True)
    lock = inside_root(root, overlay.with_suffix(".lock"))
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise RuntimeError("Main metadata overlay is being written") from error
    try:
        previous = overlay.read_bytes() if overlay.exists() else b""
        if previous and not previous.endswith(b"\n"):
            raise ValueError("Main metadata overlay contains a partial line")
        if any(json.loads(line)["mapping_id"] == event["mapping_id"] for line in previous.splitlines()):
            return event
        if receipt_path.exists() and receipt_path.read_bytes() != content:
            raise ValueError("Content-addressed Main receipt changed")
        if not receipt_path.exists():
            _atomic_bytes(root, receipt_path, content)
        _atomic_bytes(root, overlay, previous + _canonical(event) + b"\n")
        if sha256_file(manifest_path) != manifest_sha:
            raise ValueError("Original benchmark manifest changed during mapping")
        return event
    finally:
        os.close(lock_fd)
        lock.unlink(missing_ok=True)


def verify_main_mapping_record(root: str | Path, case: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    """Replay a recorded mapping while keeping earlier receipt bytes unchanged."""
    root = Path(root).resolve()
    content = {key: value for key, value in row.items() if key != "mapping_id"}
    if (_digest(content) != row.get("mapping_id") or row.get("case_sha256") != _digest(case)
            or row.get("benchmark_manifest_sha256") != sha256_file(root / "data/manifests/dataset.json")):
        raise ValueError("Existing Main mapping identity/hash binding changed")
    descriptor = row["receipt_bundle"]
    path = inside_root(root, root / descriptor["path"])
    if sha256_file(path) != descriptor["sha256"]:
        raise ValueError("Existing Main mapping private receipt changed")
    bundle = json.loads(path.read_text(encoding="utf-8"))
    replay = _mapping_from_bundle(root, case, row, bundle)
    for key, value in replay.items():
        # This descriptive field was added after the first three Astropy receipts.
        if key == "source_issue_tracker" and key not in row and case["repository"].lower() != "django/django":
            continue
        if row.get(key) != value:
            raise ValueError("Existing Main mapping no longer replays under the active mapping rule")
    return bundle


def enrich_main_metadata(
    root: str | Path, *, limit: int = 10, fetcher: Callable[[str], Mapping[str, Any]] | None = None,
    repository: str | None = None,
    all_splits: bool = False,
    remap_pending: bool = False,
) -> dict[str, Any]:
    """Resume metadata construction; all_splits includes Main holdout metadata only.

    By default terminal pending outcomes are resumed. ``remap_pending`` explicitly
    applies the newer reference policy to unresolved GitHub cases, retaining all
    earlier events. Trac pending outcomes require their own tracker/history path.
    """
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be between one and five hundred")
    if type(all_splits) is not bool:
        raise ValueError("all_splits must be a boolean dataset-construction choice")
    if type(remap_pending) is not bool:
        raise ValueError("remap_pending must be boolean")
    root = Path(root).resolve()
    manifest = verify_manifest(root)
    cases = [json.loads(line) for line in (root / manifest["files"]["cases"]["path"]).read_text(
        encoding="utf-8").splitlines()]
    allowed = {"main_dev", "main_test"} if all_splits else {"main_dev"}
    cases = [case for case in cases if case["split"] in allowed]
    if repository:
        parse_github_reference(repository)
        cases = [case for case in cases if case["repository"].lower() == repository.lower()]
    overlay = root / "data/annotations/main_metadata.jsonl"
    previous = [json.loads(line) for line in overlay.read_text(encoding="utf-8").splitlines()] if overlay.exists() else []
    originals = {case["case_id"]: case for case in cases}
    done = set()
    latest = {}
    bundles = {}
    for row in previous:
        original = originals.get(row["case_id"])
        if original is None:
            continue
        bundles[row["case_id"]] = verify_main_mapping_record(root, original, row)
        latest[row["case_id"]] = row
    for case_id, row in latest.items():
        if (not remap_pending or row["status"] in EXACT_MAPPING_STATUSES
                or originals[case_id]["repository"].lower() == "django/django"):
            done.add(case_id)
    reports, failures = [], []
    for case in cases:
        if case["case_id"] in done:
            continue
        if len(reports) + len(failures) >= limit:
            break
        try:
            report, bundle = resolve_main_case(case, fetcher=fetcher,
                pull_receipt=bundles.get(case["case_id"], {}).get("pull_receipt"))
            reports.append(append_main_mapping(root, case, report, bundle))
        except urllib.error.HTTPError as error:
            failures.append({"case_id": case["case_id"], "http_status": error.code})
            if error.code in {403, 429}:
                break
        except (ValueError, OSError) as error:
            failures.append({"case_id": case["case_id"], "error_type": type(error).__name__})
        write_json(root / "artifacts/main_metadata_progress.json", {
            "mapped_this_run": len(reports), "already_mapped": len(done), "total_considered": len(cases),
            "statuses_this_run": dict(Counter(row["status"] for row in reports)), "failures": failures,
            "all_splits_dataset_construction": all_splits, "last_case_id": case["case_id"],
            "remap_pending": remap_pending,
            "evaluation_performed": False,
        })
    result = {"mapped": len(reports), "already_mapped": len(done), "failures": failures,
            "statuses": dict(Counter(row["status"] for row in reports)),
            "all_splits_dataset_construction": all_splits,
            "remap_pending": remap_pending,
            "evaluation_performed": False}
    write_json(root / "artifacts/main_metadata_progress.json", {**result, "state": "finished",
                                                               "total_considered": len(cases)})
    return result
