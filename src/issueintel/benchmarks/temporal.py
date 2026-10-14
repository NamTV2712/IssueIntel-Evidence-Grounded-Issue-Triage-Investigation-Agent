"""Targeted temporal metadata audits; never reinterpret PR dates as issue dates.

Independent receipts are an append-only overlay. The benchmark's source and
dataset manifests are checked and remain unchanged. Git commit timestamps alone
are not proof of when a revision became publicly available.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.request
from issueintel.repository.acquire import open_github_request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from .importer import DEFAULT_MODEL_CUTOFF
from .models import utc_timestamp
from .sources import inside_root, sha256_file
from .history import verify_ancestry_receipt


def _canonical(content: Any) -> bytes:
    return json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(content: Any) -> str:
    return hashlib.sha256(_canonical(content)).hexdigest()


def _time(value: Any) -> datetime:
    normalized = utc_timestamp(value)
    return datetime.fromisoformat(normalized.replace("Z", "+00:00"))


def _optional_event_bytes_match(receipt: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    """Validate captured bytes when supplied; absent bytes are not a verified raw hash.

    Early acquired receipts retained the event payload and a raw-line digest in
    separate files. Those cannot replay the raw-line check from this bundle, so
    their digest is only syntactically checked here. No raw-byte verification
    claim is inferred from a digest without its bytes.
    """
    if "event_sha256" in receipt and receipt["event_sha256"] != _digest(event):
        return False
    if "payload_sha256" in receipt and receipt["payload_sha256"] != _digest(event):
        return False
    raw_digest = receipt.get("raw_line_sha256")
    if raw_digest is not None and not re.fullmatch(r"[0-9a-f]{64}", str(raw_digest)):
        return False
    if "raw_event_line" not in receipt:
        return True
    raw = receipt["raw_event_line"]
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 512 * 1024:
        return False
    if raw_digest != hashlib.sha256(raw.encode("utf-8")).hexdigest():
        return False
    try:
        return json.loads(raw) == dict(event)
    except (json.JSONDecodeError, UnicodeError, ValueError):
        return False


def _source_event_bound(
    receipt: Mapping[str, Any], event: Mapping[str, Any], repository: str, event_type: str,
) -> bool:
    """Bind a public event to an exact HTTPS archive hour or repository event API.

    Legacy events may omit ``public``; official public archive acquisition and
    the exact source binding supply that context. An explicit private flag is
    always rejected, including during replay of previously saved receipts.
    """
    try:
        url = urlparse(str(receipt.get("source_url", "")))
        if (url.scheme != "https" or url.username or url.password or url.port is not None
                or url.fragment or event.get("type") != event_type or event.get("public") is False):
            return False
        repo, payload = event.get("repo", {}), event.get("payload", {})
        if (not isinstance(repo, Mapping) or not isinstance(payload, Mapping)
                or str(repo.get("name", "")).lower() != repository.lower()):
            return False
        observed = _time(event.get("created_at"))
        if url.hostname == "data.gharchive.org":
            match = re.fullmatch(r"/(\d{4}-\d{2}-\d{2})-([0-9]|1[0-9]|2[0-3])\.json\.gz", url.path)
            if not match or url.query:
                return False
            hour = datetime.strptime(match[1], "%Y-%m-%d").replace(hour=int(match[2]), tzinfo=timezone.utc)
            if observed.replace(minute=0, second=0, microsecond=0) != hour:
                return False
        elif url.hostname == "api.github.com":
            if url.path.lower() != f"/repos/{repository}/events".lower():
                return False
        else:
            return False
        return _optional_event_bytes_match(receipt, event)
    except (TypeError, ValueError, OverflowError):
        return False


def fetch_issue_receipt(repository: str, issue_number: str | int, *, timeout: int = 30) -> dict[str, Any]:
    """One metadata lookup with optional local token; no crawl or retry loop."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Expected a GitHub repository owner/name")
    number = str(issue_number)
    if not number.isdigit() or int(number) <= 0:
        raise ValueError("Expected a positive issue number")
    url = f"https://api.github.com/repos/{repository}/issues/{number}"
    request = urllib.request.Request(url, headers={
        "User-Agent": "IssueIntel/0.2 targeted-temporal-audit",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if any(ord(character) < 32 for character in token):
        raise ValueError("Invalid GitHub token header")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    with open_github_request(request, timeout=timeout) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Issue metadata exceeds the 2 MB response limit")
        payload = json.loads(raw)
        headers = {key: response.headers[key] for key in ("Date", "ETag", "Last-Modified")
                   if key in response.headers}
    return {
        "source_url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "response_headers": headers,
        "payload": payload,
        "body_provenance": None,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
    }


def issue_reference_url(reference: Mapping[str, Any]) -> str:
    """Canonical source identity; a Trac ticket number is never a GitHub issue ID."""
    kind = reference.get("source_kind", "github_issues")
    repository, number = str(reference["repository"]), str(reference["number"])
    if not re.fullmatch(r"[1-9][0-9]*", number):
        raise ValueError("Issue references require positive numbers")
    if kind == "django_trac":
        if repository.lower() != "django/django":
            raise ValueError("Django Trac source must belong to django/django")
        from .trac import ticket_url
        return ticket_url(number, version=0)
    if kind not in {"github", "github_issues"}:
        raise ValueError("Unsupported issue source kind")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Expected repository owner/name")
    return f"https://api.github.com/repos/{repository}/issues/{number}"


def _trac_issue_receipt(receipt: Mapping[str, Any], repository: str, number: str) -> dict[str, Any]:
    """Reparse actual initial-version HTML stored in this private receipt."""
    from .trac import parse_trac_ticket_html
    payload = receipt.get("payload", {})
    if not isinstance(payload, Mapping):
        raise ValueError("Trac receipt payload must be an object")
    source_url = receipt.get("source_url")
    expected = issue_reference_url({"source_kind": "django_trac", "repository": repository, "number": number})
    raw = receipt.get("raw_html_text")
    original = False
    if isinstance(raw, str) and len(raw.encode("utf-8")) <= 2_000_000:
        encoded = raw.encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() == receipt.get("response_sha256"):
            try:
                original = parse_trac_ticket_html(encoded, number, version=0) == payload
            except (ValueError, UnicodeError, TypeError):
                pass
    identity = bool(source_url == expected and payload.get("source_kind") == "django_trac"
        and payload.get("repository") == "django/django" and str(payload.get("ticket_number")) == number
        and payload.get("version") == 0 and original)
    created = utc_timestamp(payload.get("created_at"), optional=True)
    fetched = utc_timestamp(receipt.get("fetched_at"), optional=True)
    text = str(payload.get("problem_statement", ""))
    return {"issue_number": number, "source_kind": "django_trac", "source_url": source_url,
        "receipt_sha256": _digest(receipt), "identity_valid": identity,
        "created_at": created, "fetched_at": fetched,
        "created_before_fetch": bool(created and fetched and _time(created) <= _time(fetched)),
        "updated_at": None, "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "original_text_supported": bool(identity and original), "rendered_issue_text": text,
        "note": "Official Trac initial-version history, not a cryptographic or complete-history guarantee."}


def _issue_receipt(receipt: Mapping[str, Any], repository: str, number: str,
                   source_kind: str = "github_issues") -> dict[str, Any]:
    if source_kind == "django_trac":
        return _trac_issue_receipt(receipt, repository, number)
    payload = receipt.get("payload", receipt)
    if not isinstance(payload, Mapping):
        raise ValueError("Issue receipt payload must be an object")
    expected_url = f"https://api.github.com/repos/{repository}/issues/{number}"
    source_url = receipt.get("source_url", payload.get("url"))
    identity_valid = (
        source_url == expected_url
        and payload.get("url", expected_url) == expected_url
        and str(payload.get("number")) == number
        and "pull_request" not in payload
        and ("payload_sha256" not in receipt or receipt["payload_sha256"] == _digest(payload))
    )
    created_at = utc_timestamp(payload.get("created_at"), optional=True)
    fetched_at = utc_timestamp(receipt.get("fetched_at"), optional=True)
    created_before_fetch = bool(created_at and fetched_at and _time(created_at) <= _time(fetched_at))
    title = str(payload.get("title") or "")
    body = str(payload.get("body") or "")
    issue_text = f"{title}\n{body}\n"
    content_sha256 = hashlib.sha256(issue_text.encode("utf-8")).hexdigest()
    provenance = receipt.get("body_provenance")
    original_text_supported = False
    if isinstance(provenance, Mapping) and created_at:
        kind = provenance.get("kind")
        url = provenance.get("source_url", "")
        host = urlparse(url).hostname
        available_at = provenance.get("available_at")
        proof_payload = provenance.get("payload", {})
        independently_bound = False
        if kind == "issue_creation_event" and isinstance(proof_payload, Mapping):
            event = proof_payload.get("payload", {})
            event_issue = event.get("issue", {}) if isinstance(event, Mapping) else {}
            independently_bound = bool(
                _source_event_bound(provenance, proof_payload, repository, "IssuesEvent")
                and isinstance(event_issue, Mapping)
                and event.get("action") == "opened"
                and "pull_request" not in event_issue
                and str(event_issue.get("number")) == number
                and event_issue.get("url") == expected_url
                and utc_timestamp(event_issue.get("created_at"), optional=True) == created_at
                and str(event_issue.get("title") or "") == title
                and str(event_issue.get("body") or "") == body
            )
        elif kind == "archived_snapshot" and isinstance(proof_payload, Mapping):
            archived_issue = proof_payload.get("issue", {})
            independently_bound = bool(
                host == "web.archive.org"
                and proof_payload.get("captured_at")
                and _time(proof_payload["captured_at"]) == _time(created_at)
                and archived_issue.get("url") == expected_url
                and str(archived_issue.get("number")) == number
                and str(archived_issue.get("title") or "") == title
                and str(archived_issue.get("body") or "") == body
            )
        original_text_supported = bool(
            independently_bound
            and available_at
            and _time(available_at) == _time(created_at)
            and provenance.get("content_sha256") == content_sha256
            and provenance.get("history_complete") is True
        )
    return {
        "issue_number": number,
        "source_url": source_url,
        "receipt_sha256": _digest(receipt),
        "identity_valid": identity_valid,
        "created_at": created_at,
        "fetched_at": fetched_at,
        "created_before_fetch": created_before_fetch,
        "updated_at": utc_timestamp(payload.get("updated_at"), optional=True),
        "content_sha256": content_sha256,
        "original_text_supported": original_text_supported,
        "rendered_issue_text": issue_text,
        "note": "updated_at may reflect state changes; it is not body edit history.",
    }


def _revision_availability(
    proof: Mapping[str, Any] | None, repository: str, revision: str, query_time: str | None
) -> dict[str, Any]:
    result = {"exact_revision": False, "independent_availability": False,
              "available_before_issue": False, "available_at": None, "basis": None}
    if not proof:
        result["reason"] = "Missing exact-revision public-availability receipt"
        return result
    result["receipt_sha256"] = _digest(proof)
    result["basis"] = proof.get("basis")
    result["source_url"] = proof.get("source_url")
    result["exact_revision"] = proof.get("repository") == repository and proof.get("revision") == revision
    payload = proof.get("payload", {})
    basis = proof.get("basis")
    host = urlparse(str(proof.get("source_url", ""))).hostname
    available_at = None
    if basis in {"github_push_event", "gharchive_push_event"} and isinstance(payload, Mapping):
        event_payload = payload.get("payload", {})
        if not isinstance(event_payload, Mapping):
            event_payload = {}
        commits = event_payload.get("commits", [])
        if not isinstance(commits, list):
            commits = []
        revisions = {str(item.get("sha")) for item in commits if isinstance(item, Mapping)}
        revisions.add(str(event_payload.get("head")))
        result["independent_availability"] = bool(
            _source_event_bound(proof, payload, repository, "PushEvent")
            and revision in revisions
            and (basis != "gharchive_push_event" or host == "data.gharchive.org")
            and (basis != "github_push_event" or host == "api.github.com")
        )
        available_at = payload.get("created_at")
    elif basis == "archived_snapshot" and isinstance(payload, Mapping):
        result["independent_availability"] = bool(
            host == "web.archive.org"
            and payload.get("repository") == repository
            and payload.get("revision") == revision
            and revision in str(proof.get("source_url", ""))
            and re.fullmatch(r"[0-9a-f]{64}", str(payload.get("content_sha256", "")))
        )
        available_at = payload.get("captured_at")
    if available_at:
        result["available_at"] = utc_timestamp(available_at)
        result["available_before_issue"] = bool(query_time and _time(available_at) < _time(query_time))
    if basis in {"git_commit_timestamp", "github_commit_api"}:
        result["reason"] = "Commit author/committer dates do not prove public availability"
    elif not result["independent_availability"]:
        result["reason"] = "Unsupported or incomplete independent availability receipt"
    return result


def _audit_case(
    case: Mapping[str, Any],
    issue_receipts: Iterable[Mapping[str, Any]],
    revision_proof: Mapping[str, Any] | None = None,
    *,
    model_cutoff: str = DEFAULT_MODEL_CUTOFF,
    cutoff_provenance: Mapping[str, Any] | None = None,
    selected_snapshot_revision: str | None = None,
    ancestry_receipt: Mapping[str, Any] | None = None,
    issue_references: Iterable[Mapping[str, Any]] | None = None,
    require_post_cutoff: bool = True,
    query_policy: str = "source_query",
) -> dict[str, Any]:
    """Validate supplied independent receipts; performs no inference or network calls."""
    if query_policy not in {"source_query", "original_opening_query"}:
        raise ValueError("Unsupported historical query policy")
    repository = str(case["repository"])
    references = list(issue_references) if issue_references is not None else [
        {"repository": repository, "number": number} for number in case.get("issue_numbers", [])
    ]
    normalized_refs = [(str(ref["repository"]), str(ref["number"])) for ref in references]
    if any(not number.isdigit() or int(number) <= 0 for _, number in normalized_refs):
        raise ValueError("Issue references require positive numbers")
    urls = [issue_reference_url(ref) for ref in references]
    keys = [("django_trac:" if ref.get("source_kind") == "django_trac" else "") + f"{repo.lower()}#{number}"
            for ref, (repo, number) in zip(references, normalized_refs)]
    if len(keys) != len(set(keys)):
        raise ValueError("Issue references must be unique global identities")
    numbers = [number for _, number in normalized_refs]
    receipts = list(issue_receipts)
    matches: dict[str, list[Mapping[str, Any]]] = {key: [] for key in keys}
    for receipt in receipts:
        payload = receipt.get("payload", receipt)
        number = str(payload.get("number", payload.get("ticket_number"))) if isinstance(payload, Mapping) else ""
        source_url = receipt.get("source_url", payload.get("url") if isinstance(payload, Mapping) else None)
        for key, (_, ref_number), expected_url in zip(keys, normalized_refs, urls):
            if number == ref_number and source_url == expected_url:
                matches[key].append(receipt)
    duplicate_keys = [key for key, entries in matches.items() if len(entries) > 1]
    missing_keys = [key for key, entries in matches.items() if not entries]
    duplicates = [key.rsplit("#", 1)[1] for key in duplicate_keys]
    missing = [key.rsplit("#", 1)[1] for key in missing_keys]
    issues = []
    for key, (ref_repo, number), reference in zip(keys, normalized_refs, references):
        if len(matches[key]) == 1:
            parsed = _issue_receipt(matches[key][0], ref_repo, number, reference.get("source_kind", "github_issues"))
            parsed["repository"] = ref_repo
            issues.append(parsed)
    cutoff = utc_timestamp(model_cutoff)
    dates_known = bool(numbers) and not missing and not duplicates and all(
        row["identity_valid"] and row["created_before_fetch"] and row["created_at"] for row in issues
    )
    all_after_cutoff = dates_known and all(_time(row["created_at"]) > _time(cutoff) for row in issues)
    query_time = min((row["created_at"] for row in issues if row["created_at"]), default=None, key=_time)
    base_revision = str(case["revision"])
    snapshot_revision = selected_snapshot_revision or base_revision
    if not re.fullmatch(r"[0-9a-f]{40}", snapshot_revision):
        raise ValueError("selected_snapshot_revision must be an exact full Git SHA")
    ancestry_verified = snapshot_revision == base_revision or verify_ancestry_receipt(
        ancestry_receipt, base_revision, snapshot_revision
    )
    revision_checks = _revision_availability(revision_proof, repository, snapshot_revision, query_time)
    reconstructed = "".join(row["rendered_issue_text"] for row in issues)
    statement_matches = dates_known and reconstructed.replace("\r\n", "\n").strip() == str(
        case["problem_statement"]
    ).replace("\r\n", "\n").strip()
    text_original = dates_known and (statement_matches or query_policy == "original_opening_query") and all(
        row["original_text_supported"] for row in issues)
    # A combined query uses the earliest linked issue: no later issue body existed then.
    linked_issues_coexisted = bool(dates_known and all(_time(row["created_at"]) == _time(query_time)
                                                    for row in issues))
    cutoff_source_supplied = bool(
        cutoff_provenance
        and cutoff_provenance.get("model_id")
        and urlparse(str(cutoff_provenance.get("source_url", ""))).scheme == "https"
        and cutoff_provenance.get("conservative_cutoff") == cutoff
    )
    strict = bool(
        (not require_post_cutoff or (all_after_cutoff and cutoff_source_supplied))
        and text_original and linked_issues_coexisted
        and ancestry_verified
        and all(revision_checks[key] for key in (
            "exact_revision", "independent_availability", "available_before_issue"
        ))
    )
    if strict:
        status = "strict_receipt_checks_passed"
    elif require_post_cutoff and dates_known and not all_after_cutoff:
        status = "excluded_issue_not_after_cutoff"
    elif all_after_cutoff:
        status = "post_cutoff_dates_confirmed_snapshot_unverified"
    elif dates_known:
        status = "issue_dates_confirmed_snapshot_unverified"
    else:
        status = "missing_or_invalid_issue_metadata"
    issues_public = [{key: value for key, value in row.items() if key != "rendered_issue_text"} for row in issues]
    report = {
        "schema_version": 1,
        "case_id": case["case_id"],
        "case_sha256": _digest(case),
        "status": status,
        "configured_model_cutoff": cutoff,
        "cutoff_provenance": cutoff_provenance,
        "cutoff_source_supplied": cutoff_source_supplied,
        "post_model_cutoff_required": require_post_cutoff,
        "missing_issue_numbers": missing,
        "missing_issue_references": missing_keys,
        "duplicate_issue_receipts": duplicates,
        "issues": issues_public,
        "query_time": query_time,
        "upstream_base_revision": base_revision,
        "selected_snapshot_revision": snapshot_revision,
        "uses_historical_ancestor": snapshot_revision != base_revision,
        "snapshot_ancestry_verified": ancestry_verified,
        "ancestry_receipt_sha256": _digest(ancestry_receipt) if ancestry_receipt else None,
        "all_issue_dates_after_cutoff": bool(all_after_cutoff),
        "benchmark_statement_matches_receipt_text": bool(statement_matches),
        "original_query_text_supported": bool(text_original),
        "all_linked_issue_text_existed_at_query_time": linked_issues_coexisted,
        "revision_availability": revision_checks,
        "strict_issue_time_receipt_checks_passed": strict,
        "validation_scope": "supplied independent receipts, not a cryptographic truth or complete history guarantee",
        "boundary": {"historical_code": "available_at < earliest_issue_created_at",
                     "query_original_text": "available_at == its_issue_created_at"},
        "contamination_free_claim": False,
        "evaluation_performed": False,
        "validation_role": "dataset_construction_only",
    }
    if query_policy == "original_opening_query":
        report.update({"query_policy": query_policy,
            "query_text_sha256": hashlib.sha256(reconstructed.encode("utf-8")).hexdigest(),
            "source_query_sha256": hashlib.sha256(str(case["problem_statement"]).encode("utf-8")).hexdigest(),
            "upstream_query_matches_selected": bool(statement_matches),
            "source_human_label_scope": "upstream_query_only",
            "original_query_overlay": True})
    return report


def audit_temporal_case(
    case: Mapping[str, Any], issue_receipts: Iterable[Mapping[str, Any]],
    revision_proof: Mapping[str, Any] | None = None, *, model_cutoff: str = DEFAULT_MODEL_CUTOFF,
    cutoff_provenance: Mapping[str, Any] | None = None,
    selected_snapshot_revision: str | None = None, ancestry_receipt: Mapping[str, Any] | None = None,
    query_policy: str = "source_query",
) -> dict[str, Any]:
    """Live validity additionally requires every issue after the model cutoff."""
    if case.get("split") != "temporal_candidate":
        raise ValueError("Temporal audit accepts only reserved temporal_candidate cases")
    return _audit_case(case, issue_receipts, revision_proof, model_cutoff=model_cutoff,
        cutoff_provenance=cutoff_provenance, selected_snapshot_revision=selected_snapshot_revision,
        ancestry_receipt=ancestry_receipt, require_post_cutoff=True, query_policy=query_policy)


def audit_historical_case(
    case: Mapping[str, Any], issue_receipts: Iterable[Mapping[str, Any]],
    revision_proof: Mapping[str, Any] | None = None, *,
    issue_references: Iterable[Mapping[str, Any]] | None = None,
    selected_snapshot_revision: str | None = None, ancestry_receipt: Mapping[str, Any] | None = None,
    query_policy: str = "source_query",
) -> dict[str, Any]:
    """Main pre-issue snapshot construction; does not authorize predictions or evaluation."""
    if case.get("split") not in {"main_dev", "main_test"}:
        raise ValueError("Historical Main audit accepts only main_dev/main_test dataset construction")
    return _audit_case(case, issue_receipts, revision_proof, issue_references=issue_references,
        selected_snapshot_revision=selected_snapshot_revision, ancestry_receipt=ancestry_receipt,
        require_post_cutoff=False, query_policy=query_policy)


def reconstruct_query_text(case: Mapping[str, Any], bundle: Mapping[str, Any]) -> str:
    """Return a selected query only after replaying its original-body identities.

    Source-query mode preserves the original case bytes. Opening-query mode is
    derived from the bound private receipts; no caller-provided text is trusted.
    Snapshot/publication checks are separately required by the strict auditor.
    """
    policy = bundle.get("query_policy", "source_query")
    if policy == "source_query":
        return str(case["problem_statement"])
    if policy != "original_opening_query":
        raise ValueError("Unsupported historical query policy")
    references = bundle.get("issue_references")
    if references is None:
        references = [{"repository": case["repository"], "number": number} for number in case.get("issue_numbers", [])]
    receipts = bundle["issue_receipts"]
    selected = []
    for ref in references:
        matches = [receipt for receipt in receipts if receipt.get("source_url") == issue_reference_url(ref)]
        if len(matches) != 1:
            raise ValueError("Original-query receipt identity missing or duplicated")
        parsed = _issue_receipt(matches[0], str(ref["repository"]), str(ref["number"]), ref.get("source_kind", "github_issues"))
        if not all(parsed[key] for key in ("identity_valid", "created_before_fetch", "original_text_supported")):
            raise ValueError("Original-query text does not replay from opening evidence")
        selected.append(parsed["rendered_issue_text"])
    if not selected:
        raise ValueError("Original query requires at least one actual issue receipt")
    return "".join(selected)


def _atomic_bytes(root: Path, path: Path, content: bytes) -> None:
    path = inside_root(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".temporal-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _append_audit(
    root: str | Path, case_id: str, issue_receipts: Iterable[Mapping[str, Any]],
    revision_proof: Mapping[str, Any] | None = None, *, model_cutoff: str = DEFAULT_MODEL_CUTOFF,
    cutoff_provenance: Mapping[str, Any] | None = None,
    selected_snapshot_revision: str | None = None,
    ancestry_receipt: Mapping[str, Any] | None = None,
    issue_references: Iterable[Mapping[str, Any]] | None = None,
    historical: bool = False,
    overlay_name: str = "temporal_audit",
    query_policy: str = "source_query",
    upstream_issue_receipts: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Add an immutable audit event; do not rewrite original benchmark manifests."""
    root = Path(root).resolve()
    manifest_path = root / "data/manifests/dataset.json"
    manifest_digest = sha256_file(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for descriptor in manifest["files"].values():
        path = inside_root(root, root / descriptor["path"])
        if sha256_file(path) != descriptor["sha256"]:
            raise ValueError(f"Pinned benchmark file changed: {descriptor['path']}")
    cases_path = root / manifest["files"]["cases"]["path"]
    cases = (json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines())
    case = next((row for row in cases if row["case_id"] == case_id), None)
    if case is None:
        raise KeyError(f"Unknown benchmark case: {case_id}")
    receipts = list(issue_receipts)
    references = list(issue_references) if issue_references is not None else None
    if historical:
        report = audit_historical_case(case, receipts, revision_proof, issue_references=references,
            selected_snapshot_revision=selected_snapshot_revision, ancestry_receipt=ancestry_receipt,
            query_policy=query_policy)
    else:
        report = audit_temporal_case(case, receipts, revision_proof, model_cutoff=model_cutoff,
            cutoff_provenance=cutoff_provenance, selected_snapshot_revision=selected_snapshot_revision,
            ancestry_receipt=ancestry_receipt, query_policy=query_policy)
    receipt_bundle = {"issue_receipts": receipts, "revision_proof": revision_proof,
                      "cutoff_provenance": cutoff_provenance,
                      "selected_snapshot_revision": selected_snapshot_revision,
                      "ancestry_receipt": ancestry_receipt,
                      "issue_references": references}
    if query_policy == "original_opening_query":
        receipt_bundle["query_policy"] = query_policy
        receipt_bundle["upstream_issue_receipts"] = list(upstream_issue_receipts or [])
    bundle_bytes = _canonical(receipt_bundle) + b"\n"
    bundle_sha = hashlib.sha256(bundle_bytes).hexdigest()
    bundle_path = root / "data/annotations/temporal_receipts" / f"{bundle_sha}.json"
    report["receipt_bundle"] = {"path": bundle_path.relative_to(root).as_posix(), "sha256": bundle_sha}
    report["benchmark_manifest_sha256"] = manifest_digest
    report["audit_id"] = _digest(report)
    if overlay_name not in {"temporal_audit", "historical_audit"}:
        raise ValueError("Unsupported annotation overlay name")
    overlay = inside_root(root, root / "data/annotations" / f"{overlay_name}.jsonl")
    overlay.parent.mkdir(parents=True, exist_ok=True)
    lock = inside_root(root, overlay.with_suffix(".lock"))
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise RuntimeError("Temporal annotation overlay is currently being written") from error
    try:
        original = overlay.read_bytes() if overlay.exists() else b""
        if original and not original.endswith(b"\n"):
            raise ValueError("Temporal overlay has a partial line; repair it explicitly before appending")
        previous = [json.loads(line) for line in original.splitlines()]
        if any(item["audit_id"] == report["audit_id"] for item in previous):
            return report
        if bundle_path.exists() and bundle_path.read_bytes() != bundle_bytes:
            raise ValueError("Content-addressed receipt bundle has changed")
        if not bundle_path.exists():
            _atomic_bytes(root, bundle_path, bundle_bytes)
        _atomic_bytes(root, overlay, original + _canonical(report) + b"\n")
        if sha256_file(manifest_path) != manifest_digest:
            raise ValueError("Original benchmark manifest changed during temporal audit")
        return report
    finally:
        os.close(lock_fd)
        lock.unlink(missing_ok=True)


def append_temporal_audit(
    root: str | Path, case_id: str, issue_receipts: Iterable[Mapping[str, Any]],
    revision_proof: Mapping[str, Any] | None = None, *, model_cutoff: str = DEFAULT_MODEL_CUTOFF,
    cutoff_provenance: Mapping[str, Any] | None = None,
    selected_snapshot_revision: str | None = None, ancestry_receipt: Mapping[str, Any] | None = None,
    query_policy: str = "source_query", upstream_issue_receipts: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Append Live metadata; no authorization for holdout inference/evaluation."""
    return _append_audit(root, case_id, issue_receipts, revision_proof, model_cutoff=model_cutoff,
        cutoff_provenance=cutoff_provenance, selected_snapshot_revision=selected_snapshot_revision,
        ancestry_receipt=ancestry_receipt, query_policy=query_policy, upstream_issue_receipts=upstream_issue_receipts)


def append_historical_audit(
    root: str | Path, case_id: str, issue_receipts: Iterable[Mapping[str, Any]],
    revision_proof: Mapping[str, Any] | None = None, *,
    issue_references: Iterable[Mapping[str, Any]] | None = None,
    selected_snapshot_revision: str | None = None, ancestry_receipt: Mapping[str, Any] | None = None,
    query_policy: str = "source_query", upstream_issue_receipts: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Append Main pre-issue metadata into the generic as-of overlay."""
    return _append_audit(root, case_id, issue_receipts, revision_proof,
        historical=True, overlay_name="historical_audit", issue_references=issue_references,
        selected_snapshot_revision=selected_snapshot_revision, ancestry_receipt=ancestry_receipt,
        query_policy=query_policy, upstream_issue_receipts=upstream_issue_receipts)


def _read_metadata(path: str | Path) -> list[Mapping[str, Any]]:
    path = Path(path)
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    content = json.loads(path.read_text(encoding="utf-8-sig"))
    return content if isinstance(content, list) else [content]


def import_temporal_metadata(
    root: str | Path, receipts_path: str | Path, revision_proofs_path: str | Path | None = None,
    *, model_cutoff: str = DEFAULT_MODEL_CUTOFF,
    cutoff_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit only explicitly supplied matching receipts, not all Live instances."""
    root = Path(root).resolve()
    receipts = _read_metadata(receipts_path)
    proofs = _read_metadata(revision_proofs_path) if revision_proofs_path else []
    cases = [json.loads(line) for line in (root / "data/benchmark/cases.jsonl").read_text(
        encoding="utf-8").splitlines()]
    reports = []
    for case in cases:
        if case["split"] != "temporal_candidate":
            continue
        urls = {f"https://api.github.com/repos/{case['repository']}/issues/{number}"
                for number in case["issue_numbers"]}
        matching = [receipt for receipt in receipts if receipt.get("source_url",
                    receipt.get("url")) in urls]
        if not matching:
            continue
        matching_proofs = []
        for entry in proofs:
            proof = entry.get("revision_proof", entry)
            ancestry = entry.get("ancestry_receipt")
            selected = entry.get("selected_snapshot_revision")
            if not isinstance(proof, Mapping) or proof.get("repository") != case["repository"]:
                continue
            if selected:
                if not isinstance(ancestry, Mapping) or ancestry.get("base_revision") != case["revision"]:
                    continue
            elif proof.get("revision") != case["revision"]:
                continue
            matching_proofs.append((proof, selected, ancestry))
        if len(matching_proofs) > 1:
            raise ValueError("Provide one unambiguous revision availability proof per case")
        proof, selected, ancestry = matching_proofs[0] if matching_proofs else (None, None, None)
        reports.append(append_temporal_audit(
            root, case["case_id"], matching, proof,
            model_cutoff=model_cutoff, cutoff_provenance=cutoff_provenance,
            selected_snapshot_revision=selected, ancestry_receipt=ancestry,
        ))
    return {"audited_cases": len(reports), "statuses": dict(Counter(
        report["status"] for report in reports)), "audit_ids": [report["audit_id"] for report in reports],
        "evaluation_performed": False}
