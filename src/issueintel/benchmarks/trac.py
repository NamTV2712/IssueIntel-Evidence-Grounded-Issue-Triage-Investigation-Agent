"""Bounded Django Trac ticket/version receipts, independent of GitHub issue IDs.

The official SWE collector scrapes rendered Trac title/description, so current
and initial rendered versions are compared under that explicit normalization.
An edited source task remains pending; this adapter never silently replaces it.
Only private receipts retain title/description or raw HTML. Safe summaries carry
identity, timestamps, statuses and hashes, never ticket text or later comments.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urljoin, urlparse

from .models import utc_timestamp
from .sources import inside_root, sha256_file


OFFICIAL_COLLECTOR = "https://github.com/SWE-bench/SWE-bench/blob/main/swebench/collect/utils.py"
TRAC_VERSION_SOURCE = "https://github.com/edgewall/trac/blob/trac-1.6/trac/ticket/model.py"
NORMALIZATION_RECIPE = "swebench_django_rendered_text_v1"
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def _number(value: int | str) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ValueError("Trac ticket requires a positive integer identity")
    return int(value)


def ticket_url(number: int | str, *, version: int | None = None) -> str:
    if version is not None and (type(version) is not int or version != 0):
        raise ValueError("Only current and initial version=0 Trac tickets are supported")
    return f"https://code.djangoproject.com/ticket/{_number(number)}" + ("?version=0" if version == 0 else "")


def _official_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return bool(parsed.scheme == "https" and parsed.hostname == "code.djangoproject.com"
            and parsed.port is None and not parsed.username and not parsed.password and not parsed.fragment)
    except (ValueError, TypeError):
        return False


class _OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        target = urljoin(request.full_url, new_url)
        if not _official_url(target):
            raise ValueError("Trac redirect leaves the official HTTPS host")
        return super().redirect_request(request, response, code, message, headers, target)


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)

    def text(self) -> str:
        # BeautifulSoup's default get_text excludes script/style text, comments
        # and processing instructions. Keep rendered text and internal spacing.
        if self.tag in {"script", "style"}:
            return ""
        return "".join(child.text() if isinstance(child, _Node) else child for child in self.children)

    def find(self, tag: str | None = None, *, attr: str | None = None, value: str | None = None) -> list[_Node]:
        result = []
        for child in self.children:
            if not isinstance(child, _Node):
                continue
            values = (child.attrs.get(attr) or "").split() if attr == "class" else [child.attrs.get(attr)]
            if (tag is None or child.tag == tag) and (attr is None or value in values):
                result.append(child)
            result.extend(child.find(tag, attr=attr, value=value))
        return result


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]
        self.count = 0

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 100_000 or len(self.stack) > 128:
            raise ValueError("Trac HTML structure exceeds bounded parser limits")
        if len({key for key, _ in attrs}) != len(attrs):
            raise ValueError("Ambiguous duplicate HTML attributes")
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                return

    def handle_data(self, text):
        self.stack[-1].children.append(text)


def _one(nodes: list[_Node], description: str) -> _Node:
    if len(nodes) != 1:
        raise ValueError(f"Trac HTML requires one unambiguous {description}")
    return nodes[0]


def _rendered_statement(title: str, description: str) -> str:
    """Match the four normalization operations in SWE's Django collector."""
    title = re.sub(r"\s+", " ", title).strip()
    body = re.sub(r"\n+", "\n", description)
    body = re.sub(r"    ", "\t", body)
    body = re.sub(r"[ ]{2,}", " ", body).strip()
    return f"{title}\n{body}\n"


def _normalize_match(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def parse_trac_ticket_html(raw: bytes, number: int | str, *, version: int | None = None) -> dict[str, Any]:
    """Parse only ticket identity, opening time and the selected title/description.

    ``version=0`` is supplied by the exact response URL and must also be marked
    Initial Version in the heading. Current status/comments are intentionally
    omitted: Trac may display those from the current ticket even on version 0.
    """
    number = _number(number)
    ticket_url(number, version=version)
    if not isinstance(raw, bytes) or len(raw) > 2_000_000:
        raise ValueError("Trac HTML must be bytes within the 2 MB size cap")
    tree = _Tree()
    tree.feed(raw.decode("utf-8-sig"))
    ticket = _one(tree.root.find("div", attr="id", value="ticket"), "ticket container")
    identity = _one(ticket.find("a", attr="class", value="trac-id"), "ticket identity")
    if identity.attrs.get("href") != f"/ticket/{number}" or identity.text().strip() != f"#{number}":
        raise ValueError("Trac HTML ticket identity differs from its source URL")
    heading = _one(ticket.find("h1", attr="class", value="searchable"), "ticket title")
    description = _one(ticket.find("div", attr="class", value="description"), "ticket description")
    if version == 0:
        initial_links = [link for link in heading.find("a") if link.attrs.get("href") == "#comment:description"
                         and link.text().strip() == "Initial Version"]
        _one(initial_links, "Initial Version heading marker")
        # The version heading appends UI text to h1; use its summary span while
        # retaining the collector's text normalization. This exception is explicit.
        title = _one(heading.find("span", attr="class", value="summary"), "initial summary").text()
    else:
        if "Initial Version" in heading.text():
            raise ValueError("Current ticket response unexpectedly displays an initial version")
        title = heading.text()
    dates = _one(ticket.find("div", attr="class", value="date"), "ticket dates")
    opening_paragraphs = [node for node in dates.find("p") if re.match(r"Opened\s", node.text().strip())]
    opened = _one(opening_paragraphs, "Opened timestamp")
    timeline = _one(opened.find("a", attr="class", value="timeline"), "precise opening-time link")
    parsed = urlparse(str(timeline.attrs.get("href", "")))
    if parsed.path != "/timeline" or parsed.scheme or parsed.netloc or parsed.fragment:
        raise ValueError("Trac opening time must come from its local timeline link")
    query = parse_qs(parsed.query)
    if query.get("precision") != ["second"] or len(query.get("from", [])) != 1:
        raise ValueError("Trac opening-time link lacks one second-precision ISO timestamp")
    if datetime.fromisoformat(query["from"][0].replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("Trac opening-time ISO timestamp must include its timezone")
    created_at = utc_timestamp(query["from"][0])
    statement = _rendered_statement(title, description.text())
    return {"source_kind": "django_trac", "repository": "django/django", "ticket_number": number,
        "version": version, "created_at": created_at, "problem_statement": statement,
        "problem_statement_sha256": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
        "normalization_recipe": NORMALIZATION_RECIPE,
        "normalization_source": OFFICIAL_COLLECTOR,
        "version_heading_rule": "summary_span_excludes_initial_version_UI" if version == 0 else "collector_h1_text",
        "initial_version_marker_present": version == 0,
        "creation_time_basis": "Opened timeline ISO timestamp with explicit timezone, precision=second",
        "comments_included": False, "current_status_included": False}


def _save_raw(root: Path, raw: bytes) -> dict[str, Any]:
    sha = hashlib.sha256(raw).hexdigest()
    path = inside_root(root, root / "data/annotations/trac_receipts" / f"{sha}.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("Content-addressed Trac HTML changed")
    else:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".trac-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return {"path": path.relative_to(root).as_posix(), "sha256": sha, "bytes": len(raw)}


def fetch_trac_receipt(number: int | str, *, version: int | None = None,
                       timeout: int = 30, max_response_bytes: int = 2_000_000,
                       save_root: Path | str | None = None) -> dict[str, Any]:
    """One unauthenticated official request, no retry, with a private raw receipt."""
    url = ticket_url(number, version=version)
    if type(timeout) is not int or not 1 <= timeout <= 60 or type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 2_000_000:
        raise ValueError("Trac request limits must be positive, timeout<=60s and response<=2MB")
    request = urllib.request.Request(url, headers={"User-Agent": "IssueIntel/0.2 bounded-Trac-receipt", "Accept": "text/html"})
    with urllib.request.build_opener(_OfficialRedirect()).open(request, timeout=timeout) as response:
        if response.geturl() != url:
            raise ValueError("Trac response changed ticket/version URL")
        raw = response.read(max_response_bytes + 1)
        if len(raw) > max_response_bytes:
            raise ValueError("Trac response exceeds its size cap")
        headers = {name: response.headers[name] for name in ("Date", "ETag", "Last-Modified") if name in response.headers}
    payload = parse_trac_ticket_html(raw, number, version=version)
    receipt = {"source_url": url, "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "response_headers": headers, "response_sha256": hashlib.sha256(raw).hexdigest(), "payload": payload,
        "historical_version_source": TRAC_VERSION_SOURCE,
        "provenance_scope": "official tracker historical version, not a cryptographic or complete-history guarantee"}
    if save_root is not None:
        receipt["raw_html"] = _save_raw(Path(save_root).resolve(), raw)
    return receipt


def verify_trac_receipt(root: Path | str, receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Re-read immutable raw HTML and recompute parsed fields, not verified flags."""
    root = Path(root).resolve()
    payload = receipt["payload"]
    if receipt.get("source_url") != ticket_url(payload["ticket_number"], version=payload["version"]):
        raise ValueError("Trac receipt URL differs from ticket/version identity")
    descriptor = receipt["raw_html"]
    path = inside_root(root, root / descriptor["path"])
    if not path.is_relative_to(root / "data/annotations/trac_receipts"):
        raise ValueError("Trac raw HTML must remain in private receipt storage")
    if path.stat().st_size != descriptor["bytes"] or path.stat().st_size > 2_000_000:
        raise ValueError("Trac retained raw HTML size differs")
    if sha256_file(path) != descriptor["sha256"] or descriptor["sha256"] != receipt["response_sha256"]:
        raise ValueError("Trac retained raw HTML digest differs")
    parsed = parse_trac_ticket_html(path.read_bytes(), payload["ticket_number"], version=payload["version"])
    if parsed != payload:
        raise ValueError("Trac parsed payload does not replay from retained HTML")
    if datetime.fromisoformat(parsed["created_at"].replace("Z", "+00:00")) > datetime.fromisoformat(
            utc_timestamp(receipt["fetched_at"]).replace("Z", "+00:00")):
        raise ValueError("Trac creation time is after its receipt fetch")
    return parsed


def audit_issue_receipt(root: Path | str, receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Embed bounded retained initial HTML in a private, self-replaying audit bundle.

    The pure historical auditor cannot read arbitrary files. Keeping the matched
    HTML bytes in its private receipt lets it validate digest and extracted query
    without promoting a caller's boolean verification declaration.
    """
    root = Path(root).resolve()
    payload = verify_trac_receipt(root, receipt)
    if payload["version"] != 0:
        raise ValueError("Historical Trac query requires its initial version=0")
    path = inside_root(root, root / receipt["raw_html"]["path"])
    return {**dict(receipt), "raw_html_text": path.read_bytes().decode("utf-8"), "source_kind": "django_trac"}


def fetch_trac_history_pair(root: Path | str, number: int | str, *, expected_problem_statement: str,
                            timeout: int = 30, max_response_bytes: int = 2_000_000) -> dict[str, Any]:
    """Acquire current/initial once each; source-query differences stay pending."""
    if not isinstance(expected_problem_statement, str):
        raise ValueError("Expected SWE problem statement must be text")
    root = Path(root).resolve()
    current = fetch_trac_receipt(number, save_root=root, timeout=timeout, max_response_bytes=max_response_bytes)
    initial = fetch_trac_receipt(number, version=0, save_root=root, timeout=timeout, max_response_bytes=max_response_bytes)
    now, original = verify_trac_receipt(root, current), verify_trac_receipt(root, initial)
    if now["created_at"] != original["created_at"]:
        raise ValueError("Trac current and initial versions disagree on creation time")
    expected = _normalize_match(expected_problem_statement)
    original_matches = _normalize_match(original["problem_statement"]) == expected
    current_matches = _normalize_match(now["problem_statement"]) == expected
    report = {"source_kind": "django_trac", "repository": "django/django", "ticket_number": _number(number),
        "issue_created_at": original["created_at"], "initial_version_replayed": True,
        "current_source_query_matches": current_matches, "original_source_query_matches": original_matches,
        "status": "exact_initial_query_match" if original_matches else "pending_source_query_differs_from_initial_version",
        "source_query_sha256": hashlib.sha256(expected_problem_statement.encode("utf-8")).hexdigest(),
        "current_query_sha256": now["problem_statement_sha256"], "original_query_sha256": original["problem_statement_sha256"],
        "source_urls": [current["source_url"], initial["source_url"]],
        "raw_html_receipts": [current["raw_html"], initial["raw_html"]],
        "case_replaced": False, "source_human_label_scope": "original_SWE_query_only",
        "evaluation_performed": False,
        "limitations": ["Official Trac version history is an independent source, not a cryptographic guarantee against administrator edits.",
            "HTML rendering or template changes can prevent an exact collector-normalized query match.",
            "A matching initial query alone does not establish pre-issue code availability."]}
    return {"report": report, "current_receipt": current, "initial_receipt": initial}
