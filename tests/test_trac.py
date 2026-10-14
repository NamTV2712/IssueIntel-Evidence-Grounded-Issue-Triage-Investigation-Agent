"""Django ticket source/history receipts with bounded synthetic HTML transport."""
import io
import json
from pathlib import Path

import pytest

from issueintel.benchmarks import trac


def html(number=7, *, initial=False, summary="Bug &amp; symptom", body="<p>Original <code>error</code>.</p>", created="2023-01-01T02%3A03%3A04%2B02%3A00"):
    marker = '&mdash; at <a href="#comment:description">Initial Version</a>' if initial else ""
    return f'''<html><body><div id="ticket"><div class="date"><p>Opened
    <a class="timeline" href="/timeline?from={created}&amp;precision=second">years ago</a></p>
    <p>Last modified <a class="timeline" href="/timeline?from=2026-01-01T00%3A00%3A00Z&amp;precision=second">recently</a></p></div>
    <h2><a class="trac-id" href="/ticket/{number}">#{number}</a></h2>
    <h1 class="searchable"><span class="summary">{summary}</span>{marker}</h1>
    <div class="description"><h2>Description</h2><script>laterEditorSecret()</script>{body}</div>
    </div><div id="changelog"><p>Later fix patch and human rationale must stay out</p></div></body></html>'''.encode()


def transport(monkeypatch, raw_current, raw_initial=None):
    calls = []
    class Response(io.BytesIO):
        headers = {"Date": "Mon, 12 Oct 2026 00:00:00 GMT"}
        def __init__(self, raw, url): super().__init__(raw); self.url = url
        def geturl(self): return self.url
    class Opener:
        def open(self, request, **kwargs):
            calls.append(request.full_url)
            assert not request.has_header("Authorization")
            return Response(raw_initial if request.full_url.endswith("?version=0") else raw_current, request.full_url)
    monkeypatch.setattr(trac.urllib.request, "build_opener", lambda *handlers: Opener())
    return calls


def test_initial_version_extracts_precise_timezone_and_excludes_current_comments():
    parsed = trac.parse_trac_ticket_html(html(initial=True), 7, version=0)
    assert parsed["created_at"] == "2023-01-01T00:03:04Z"
    assert parsed["problem_statement"].startswith("Bug & symptom\n")
    assert "Initial Version" not in parsed["problem_statement"]
    assert "laterEditorSecret" not in parsed["problem_statement"]
    assert "Later fix patch" not in parsed["problem_statement"]
    assert parsed["comments_included"] is False


def test_current_parser_normalizes_as_official_collector():
    parsed = trac.parse_trac_ticket_html(html(body="<p>Two  spaces here.</p>\n\n<p>    Next line.</p>"), 7)
    assert parsed["problem_statement"] == "Bug & symptom\nDescriptionTwo spaces here.\n\tNext line.\n"


@pytest.mark.parametrize("raw,number,version", [
    (html(number=8), 7, None), (html(), 7, 0),
    (html(initial=True), 7, None), (html(created="2023-01-01T02%3A03%3A04"), 7, None),
    (html().replace(b'precision=second', b'precision=day'), 7, None),
])
def test_wrong_ticket_version_or_imprecise_time_is_rejected(raw, number, version):
    with pytest.raises(ValueError): trac.parse_trac_ticket_html(raw, number, version=version)


def test_fetch_retains_raw_private_and_replays_without_trusting_flags(tmp_path, monkeypatch):
    raw = html(initial=True)
    calls = transport(monkeypatch, html(), raw)
    receipt = trac.fetch_trac_receipt(7, version=0, save_root=tmp_path)
    assert calls == ["https://code.djangoproject.com/ticket/7?version=0"]
    assert trac.verify_trac_receipt(tmp_path, receipt) == receipt["payload"]
    receipt["payload"]["problem_statement"] = "Invented original description"
    with pytest.raises(ValueError, match="does not replay"): trac.verify_trac_receipt(tmp_path, receipt)


def test_original_mismatch_pending_and_pair_summary_contains_no_issue_text(tmp_path, monkeypatch):
    calls = transport(monkeypatch, html(summary="Later title"), html(initial=True))
    result = trac.fetch_trac_history_pair(tmp_path, 7, expected_problem_statement="Later source task")
    report = result["report"]
    assert len(calls) == 2
    assert report["status"] == "pending_source_query_differs_from_initial_version"
    assert report["case_replaced"] is False
    assert report["evaluation_performed"] is False
    assert not {"problem_statement", "title", "description", "body", "summary"} & report.keys()
    assert report["source_human_label_scope"] == "original_SWE_query_only"
    assert all((tmp_path / descriptor["path"]).is_file() for descriptor in report["raw_html_receipts"])


def test_matching_initial_receipt_does_not_claim_code_publication(tmp_path, monkeypatch):
    raw = html(initial=True)
    original = trac.parse_trac_ticket_html(raw, 7, version=0)["problem_statement"]
    transport(monkeypatch, html(), raw)
    report = trac.fetch_trac_history_pair(tmp_path, 7, expected_problem_statement=original)["report"]
    assert report["status"] == "exact_initial_query_match"
    assert any("code availability" in item for item in report["limitations"])


def test_retained_html_tamper_is_rejected(tmp_path, monkeypatch):
    transport(monkeypatch, html(), html(initial=True))
    receipt = trac.fetch_trac_receipt(7, version=0, save_root=tmp_path)
    path = tmp_path / receipt["raw_html"]["path"]
    path.write_bytes(path.read_bytes().replace(b'Original', b'Modified'))
    with pytest.raises(ValueError, match="size differs|digest differs"): trac.verify_trac_receipt(tmp_path, receipt)


def test_redirect_cannot_leave_official_https_host():
    request = trac.urllib.request.Request(trac.ticket_url(7))
    handler = trac._OfficialRedirect()
    for target in ("https://other.example/ticket/7", "http://code.djangoproject.com/ticket/7", "https://user@code.djangoproject.com/ticket/7"):
        with pytest.raises(ValueError, match="official HTTPS"): handler.redirect_request(request, None, 302, "Found", {}, target)


def test_size_caps_and_unsupported_ticket_versions_fail_before_parsing(monkeypatch):
    with pytest.raises(ValueError): trac.ticket_url(True)
    with pytest.raises(ValueError): trac.ticket_url(7, version=1)
    with pytest.raises(ValueError): trac.parse_trac_ticket_html(b"x" * 2_000_001, 7)
    transport(monkeypatch, html())
    with pytest.raises(ValueError, match="size cap"): trac.fetch_trac_receipt(7, max_response_bytes=8)
    with pytest.raises(ValueError, match="limits"): trac.fetch_trac_receipt(7, timeout=61)
