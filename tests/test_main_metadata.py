"""Synthetic PR/issue mapping checks; no live requests or model calls."""

from __future__ import annotations

import json
import io
import hashlib
import urllib.error
from pathlib import Path

import pytest

from issueintel.benchmarks import import_benchmark
from issueintel.benchmarks.main_metadata import (
    BODY_REFERENCE_POLICY, COMMIT_REFERENCE_POLICY, TITLE_BODY_REFERENCE_POLICY,
    append_main_mapping, derive_pr_identity, enrich_main_metadata,
    extract_closing_references, map_verified_case, resolve_main_case,
    verify_main_mapping_record, fetch_api_receipt,
    map_trac_verified_case, map_declared_verified_case, resolve_trac_main_case,
    enrich_trac_main_metadata, upgrade_declared_main_relations, TRAC_MAPPING_METHOD,
    DECLARED_GITHUB_MAPPING_METHOD,
)


def case(statement="Bug\nOriginal symptom\n"):
    return {"case_id": "verified:example__project-99", "instance_id": "example__project-99",
            "repository": "example/project", "revision": "a" * 40, "split": "main_dev",
            "problem_statement": statement}


def pull(body="Fixes #7\nPRIVATE FIX SECRET", *, number=99, title=""):
    url = f"https://api.github.com/repos/example/project/pulls/{number}"
    return {"source_url": url, "fetched_at": "2026-10-12T00:00:00Z",
            "payload": {"url": url, "number": number, "body": body, "title": title,
                        "created_at": "2025-01-01T00:00:00Z",
                        "base": {"repo": {"full_name": "example/project"}}}}


def issue(number=7, *, repository="example/project", title="Bug", body="Original symptom", is_pr=False):
    url = f"https://api.github.com/repos/{repository}/issues/{number}"
    payload = {"number": number, "url": url, "title": title, "body": body,
               "created_at": "2024-08-01T01:02:03Z"}
    if is_pr:
        payload["pull_request"] = {"url": url.replace("issues", "pulls")}
    return {"source_url": url, "fetched_at": "2026-10-12T00:00:00Z", "payload": payload}


def commits(messages=("Fixes #7",), *, number=99, complete=True):
    return {"source_url": f"https://api.github.com/repos/example/project/pulls/{number}/commits?per_page=100&page=1",
            "payload": [{"sha": f"{index+1:040x}", "commit": {"message": message}}
                        for index, message in enumerate(messages)], "pagination_complete": complete}


def test_closing_keywords_colons_global_ids_urls_links_and_deduplication():
    body = """CLOSES: #7, resolves Other/Repo#8.
fixed https://github.com/third/repo/issues/9#issuecomment-123
Fixes [a report](https://github.com/fourth/repo/issues/10)
Closed #7. Mentions #11. Notfixes #12. Fixes #13word.
`Fixes #14`
<!-- Fixes #15 -->
```markdown
Fixes #16
```
"""
    references = extract_closing_references(body, "example/project")
    assert [reference.global_id for reference in references] == [
        "example/project#7", "other/repo#8", "third/repo#9", "fourth/repo#10"
    ]


def test_pr_suffix_is_never_the_issue_number():
    assert derive_pr_identity(case()) == ("example/project", 99)
    report = map_verified_case(case(), pull(), [issue()])
    assert report["status"] == "exact_issue_match"
    assert report["pull_request"]["number"] == 99
    assert report["selected_issues"][0]["number"] == 7
    assert report["issue_created_at"] == "2024-08-01T01:02:03Z"
    assert report["pull_request"]["pull_created_at"] == "2025-01-01T00:00:00Z"
    assert report["original_text_at_issue_creation_verified"] is False
    assert "SECRET" not in json.dumps(report)


def test_mismatched_source_instance_or_pr_receipt_is_rejected():
    bad = case()
    bad["instance_id"] = "other__repository-99"
    with pytest.raises(ValueError):
        derive_pr_identity(bad)
    with pytest.raises(ValueError, match="PR receipt"):
        map_verified_case(case(), pull(number=98), [issue()])


def test_cross_repository_reference_preserves_its_global_identity():
    report = map_verified_case(case(), pull("Fixes Other/Repo#7"), [issue(repository="Other/Repo")])
    assert report["selected_issues"][0]["global_id"] == "other/repo#7"
    assert report["selected_issues"][0]["repository"] == "Other/Repo"
    assert report["pull_request"]["repository"] == "example/project"


def test_pr_references_are_not_mistaken_for_issues():
    report = map_verified_case(case(), pull(), [issue(is_pr=True)])
    assert report["status"] == "no_exact_current_issue_text_match"
    assert report["selected_issues"] == []
    assert report["rejected_candidates"][0]["reason"] == "reference_is_a_pull_request"


def test_identical_descriptions_in_two_distinct_issues_stay_ambiguous():
    report = map_verified_case(case(), pull("Fixes #7, fixes #8"), [issue(7), issue(8)])
    assert report["status"] == "ambiguous_exact_matches"
    assert report["selected_issues"] == []


def test_explicit_multi_issue_concat_and_subset_match():
    receipts = [issue(), issue(8, title="Second bug", body="Second symptom")]
    description = "Bug\nOriginal symptom\nSecond bug\nSecond symptom\n"
    report = map_verified_case(case(description), pull("Fixes #7, resolves #8"), receipts)
    assert report["status"] == "exact_multi_issue_match"
    assert [item["number"] for item in report["selected_issues"]] == [7, 8]
    single = map_verified_case(case(), pull("Fixes #7, resolves #8"), receipts)
    assert single["status"] == "exact_issue_match"
    assert [item["number"] for item in single["selected_issues"]] == [7]


def test_current_text_difference_is_not_hidden_by_whitespace_normalization():
    report = map_verified_case(case(), pull(), [issue(body="Original  symptom")])
    assert report["status"] == "no_exact_current_issue_text_match"
    assert report["selected_issues"] == []


def test_resolver_fetches_only_closing_candidates_and_skips_missing_candidate():
    calls = []

    def fetch(url):
        calls.append(url)
        if "/pulls/" in url:
            return pull("Fixes #7, fixes #8; mention #999")
        if url.endswith("/8"):
            raise urllib.error.HTTPError(url, 404, "missing", {}, None)
        return issue()

    report, bundle = resolve_main_case(case(), fetcher=fetch)
    assert report["status"] == "exact_issue_match"
    assert len(calls) == 3
    assert all("999" not in url for url in calls)
    assert bundle["fetch_failures"][0]["http_status"] == 404


def test_reference_cap_prevents_overfetch_and_does_not_guess_unseen_candidates():
    calls = []

    def fetch(url):
        calls.append(url)
        return pull("Fixes #7, fixes #8") if "/pulls/" in url else issue()

    report, _ = resolve_main_case(case(), fetcher=fetch, max_references=1)
    assert len(calls) == 2
    assert report["status"] == "reference_bound_exceeded"
    assert report["selected_issues"] == []


def test_append_mapping_preserves_manifests_and_keeps_fix_text_private(tmp_path):
    upstream_path = tmp_path / "data/fixtures/main.jsonl"
    upstream_path.parent.mkdir(parents=True)
    upstream_path.write_text(json.dumps({"instance_id": "example__project-99", "repo": "example/project",
        "base_commit": "a" * 40, "created_at": "2025-01-01T00:00:00Z",
        "problem_statement": case()["problem_statement"]}) + "\n")
    import_benchmark(tmp_path, verified_path=upstream_path, target_live=0)
    original_case = json.loads((tmp_path / "data/benchmark/cases.jsonl").read_text())
    report = map_verified_case(original_case, pull(), [issue()])
    manifest_path = tmp_path / "data/manifests/dataset.json"
    before = manifest_path.read_bytes()
    bundle = {"pull_receipt": pull(), "issue_receipts": [issue()]}
    event = append_main_mapping(tmp_path, original_case, report, bundle)
    assert append_main_mapping(tmp_path, original_case, report, bundle) == event
    overlay = tmp_path / "data/annotations/main_metadata.jsonl"
    assert len(overlay.read_text().splitlines()) == 1
    assert "SECRET" not in overlay.read_text()
    assert "SECRET" in (tmp_path / event["receipt_bundle"]["path"]).read_text()
    assert manifest_path.read_bytes() == before
    assert not overlay.with_suffix(".lock").exists()
    result = enrich_main_metadata(tmp_path, limit=1, fetcher=lambda url: pytest.fail("completed mapping must resume"))
    assert result["already_mapped"] == 1
    assert result["mapped"] == 0


def test_django_trac_source_never_requests_github_issue_with_ticket_number():
    django_case = {**case(), "repository": "django/django",
                   "instance_id": "django__django-99", "case_id": "verified:django__django-99"}
    calls = []

    def fetch(url):
        calls.append(url)
        assert url == "https://api.github.com/repos/django/django/pulls/99"
        return {"source_url": url, "payload": {"url": url, "number": 99,
            "body": "No closing reference in this body", "title": "Fixed #31234 -- A Trac ticket",
            "created_at": "2025-01-01T00:00:00Z",
            "base": {"repo": {"full_name": "django/django"}}}}

    report, bundle = resolve_main_case(django_case, fetcher=fetch)
    assert len(calls) == 1
    assert bundle["issue_receipts"] == []
    assert report["status"] == "unsupported_django_trac_source"
    assert report["source_issue_tracker"] == "django_trac"
    assert report["selected_issues"] == []
    assert report["reference_policy"] == TITLE_BODY_REFERENCE_POLICY
    assert report["closing_candidates"][0]["global_id"] == "django_trac#31234"
    assert report["closing_candidates"][0]["ticket_url"] == "https://code.djangoproject.com/ticket/31234"
    assert report["candidates_fetched"] == 0
    assert report["strict_issue_time_snapshot_verified"] is False


def test_explicit_all_splits_builds_only_metadata_and_resumes_pending_outcomes(tmp_path):
    upstream_path = tmp_path / "data/fixtures/main.jsonl"
    upstream_path.parent.mkdir(parents=True)
    rows = [{"instance_id": f"example__project-{number}", "repo": "example/project",
             "base_commit": digit * 40, "created_at": "2025-01-01T00:00:00Z",
             "problem_statement": case()["problem_statement"]}
            for number, digit in ((99, "a"), (100, "b"))]
    upstream_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    import_benchmark(tmp_path, verified_path=upstream_path, target_live=0, main_dev_count=1)
    cases = [json.loads(line) for line in (tmp_path / "data/benchmark/cases.jsonl").read_text().splitlines()]
    manifest_path = tmp_path / "data/manifests/dataset.json"
    original_manifest = manifest_path.read_bytes()
    calls = []

    def fetch(url):
        calls.append(url)
        assert "/pulls/" in url, "No candidate means no issue API requests"
        if "/commits?" in url:
            number = int(url.split("/pulls/", 1)[1].split("/", 1)[0])
            return commits((), number=number)
        number = int(url.rsplit("/", 1)[1])
        return pull("This PR has no closing keyword", number=number)

    first = enrich_main_metadata(tmp_path, limit=500, fetcher=fetch)
    assert first["mapped"] == 1
    assert first["all_splits_dataset_construction"] is False
    assert first["evaluation_performed"] is False
    default_row = json.loads((tmp_path / "data/annotations/main_metadata.jsonl").read_text())
    assert next(row for row in cases if row["case_id"] == default_row["case_id"])["split"] == "main_dev"

    second = enrich_main_metadata(tmp_path, limit=500, fetcher=fetch, all_splits=True)
    assert second["mapped"] == 1
    assert second["already_mapped"] == 1
    assert second["all_splits_dataset_construction"] is True
    assert second["evaluation_performed"] is False
    assert len(calls) == 4
    third = enrich_main_metadata(tmp_path, limit=500, fetcher=lambda url: pytest.fail("pending results resume"),
                                 all_splits=True)
    assert third["already_mapped"] == 2
    assert third["mapped"] == 0
    assert third["failures"] == []
    assert manifest_path.read_bytes() == original_manifest


def test_resume_rejects_tampered_private_mapping_receipt_before_network(tmp_path):
    upstream_path = tmp_path / "data/fixtures/main.jsonl"
    upstream_path.parent.mkdir(parents=True)
    upstream_path.write_text(json.dumps({"instance_id": "example__project-99", "repo": "example/project",
        "base_commit": "a" * 40, "created_at": "2025-01-01T00:00:00Z",
        "problem_statement": case()["problem_statement"]}) + "\n")
    import_benchmark(tmp_path, verified_path=upstream_path, target_live=0)
    original_case = json.loads((tmp_path / "data/benchmark/cases.jsonl").read_text())
    bundle = {"pull_receipt": pull(), "issue_receipts": [issue()]}
    event = append_main_mapping(tmp_path, original_case, map_verified_case(original_case, pull(), [issue()]), bundle)
    receipt_path = tmp_path / event["receipt_bundle"]["path"]
    receipt_path.write_bytes(receipt_path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="private receipt changed"):
        enrich_main_metadata(tmp_path, limit=1, fetcher=lambda url: pytest.fail("must reject before network"))


def test_title_reference_resolves_without_commit_request():
    calls = []

    def fetch(url):
        calls.append(url)
        if "/pulls/" in url:
            assert "/commits" not in url
            return pull("No body reference", title="Fixed #7 -- Fix the symptom")
        return issue()

    report, bundle = resolve_main_case(case(), fetcher=fetch)
    assert report["reference_policy"] == TITLE_BODY_REFERENCE_POLICY
    assert report["status"] == "exact_issue_match"
    assert report["schema_version"] == 2
    assert len(calls) == 2
    assert "commit_receipt" not in bundle
    assert report["commits_inspected"] == 0


def test_unresolved_title_body_uses_one_bounded_commit_page_then_exact_issue_match():
    calls = []

    def fetch(url):
        calls.append(url)
        if "/commits?" in url:
            return commits(("Resolve #8\nPRIVATE COMMIT FIX SECRET",))
        if "/pulls/" in url:
            return pull("Fixes #7")
        return issue(7, is_pr=True) if url.endswith("/7") else issue(8)

    report, bundle = resolve_main_case(case(), fetcher=fetch)
    assert report["reference_policy"] == COMMIT_REFERENCE_POLICY
    assert report["status"] == "exact_issue_match"
    assert report["selected_issues"][0]["number"] == 8
    assert report["commits_inspected"] == 1
    assert report["commit_list_complete"] is True
    assert len(calls) == 4
    assert sum("/commits?" in url for url in calls) == 1
    assert "SECRET" not in json.dumps(report)
    assert "SECRET" in json.dumps(bundle)


def test_partial_commit_page_does_not_accept_observed_exact_match_or_follow_next_page():
    calls = []

    def fetch(url):
        calls.append(url)
        if "/commits?" in url:
            return commits(("Fixes #7",), complete=False)
        assert url.endswith("/pulls/99")
        return pull("No references")

    report, bundle = resolve_main_case(case(), fetcher=fetch)
    assert report["status"] == "commit_reference_bound_exceeded"
    assert report["selected_issues"] == []
    assert report["commit_list_complete"] is False
    assert len(calls) == 2
    assert bundle["issue_receipts"] == []


def test_commit_receipt_wrong_pr_missing_page_status_or_too_many_commits_rejects():
    for receipt in (commits(number=100), {key: value for key, value in commits().items() if key != "pagination_complete"},
                    commits(tuple("Fixes #7" for _ in range(101)))):
        with pytest.raises(ValueError):
            map_verified_case(case(), pull(), [issue()], reference_policy=COMMIT_REFERENCE_POLICY,
                              commit_receipt=receipt)


def test_versioned_title_mapping_does_not_reinterpret_or_overwrite_body_only_receipt(tmp_path):
    upstream_path = tmp_path / "data/fixtures/main.jsonl"
    upstream_path.parent.mkdir(parents=True)
    upstream_path.write_text(json.dumps({"instance_id": "example__project-99", "repo": "example/project",
        "base_commit": "a" * 40, "created_at": "2025-01-01T00:00:00Z",
        "problem_statement": case()["problem_statement"]}) + "\n")
    import_benchmark(tmp_path, verified_path=upstream_path, target_live=0)
    original_case = json.loads((tmp_path / "data/benchmark/cases.jsonl").read_text())
    receipt = pull("No body reference", title="Fixed #7 -- Correct this error")
    legacy = append_main_mapping(tmp_path, original_case, map_verified_case(original_case, receipt, []),
                                 {"pull_receipt": receipt, "issue_receipts": []})
    old_bytes = (tmp_path / legacy["receipt_bundle"]["path"]).read_bytes()
    calls = []

    def fetch(url):
        calls.append(url)
        assert url.endswith("/issues/7"), "Remap reuses the immutable PR receipt"
        return issue()

    result = enrich_main_metadata(tmp_path, limit=1, fetcher=fetch, remap_pending=True)
    assert result["mapped"] == 1
    assert result["statuses"] == {"exact_issue_match": 1}
    assert calls == ["https://api.github.com/repos/example/project/issues/7"]
    rows = [json.loads(line) for line in (tmp_path / "data/annotations/main_metadata.jsonl").read_text().splitlines()]
    assert rows[0] == legacy
    assert rows[0].get("reference_policy", BODY_REFERENCE_POLICY) == BODY_REFERENCE_POLICY
    assert rows[1]["reference_policy"] == TITLE_BODY_REFERENCE_POLICY
    assert (tmp_path / legacy["receipt_bundle"]["path"]).read_bytes() == old_bytes
    for row in rows:
        verify_main_mapping_record(tmp_path, original_case, row)
    resumed = enrich_main_metadata(tmp_path, limit=1, remap_pending=True,
        fetcher=lambda url: pytest.fail("exact latest mapping must resume"))
    assert resumed["already_mapped"] == 1
    assert resumed["mapped"] == 0


def test_explicit_pending_remap_skips_existing_trac_receipt_without_http(tmp_path):
    upstream_path = tmp_path / "data/fixtures/main.jsonl"
    upstream_path.parent.mkdir(parents=True)
    upstream_path.write_text(json.dumps({"instance_id": "django__django-99", "repo": "django/django",
        "base_commit": "a" * 40, "created_at": "2025-01-01T00:00:00Z",
        "problem_statement": case()["problem_statement"]}) + "\n")
    import_benchmark(tmp_path, verified_path=upstream_path, target_live=0)
    original_case = json.loads((tmp_path / "data/benchmark/cases.jsonl").read_text())
    url = "https://api.github.com/repos/django/django/pulls/99"
    receipt = {"source_url": url, "payload": {"url": url, "number": 99,
        "body": "Fixed #31234", "created_at": "2025-01-01T00:00:00Z",
        "base": {"repo": {"full_name": "django/django"}}}}
    append_main_mapping(tmp_path, original_case, map_verified_case(original_case, receipt, []),
                        {"pull_receipt": receipt, "issue_receipts": []})
    result = enrich_main_metadata(tmp_path, limit=1, all_splits=True, remap_pending=True,
        fetcher=lambda url: pytest.fail("Trac history needs a separate mapper"))
    assert result["already_mapped"] == 1
    assert result["mapped"] == 0


def test_commit_http_receipt_records_pagination_and_enforces_one_page_and_size(monkeypatch):
    calls = []
    payload = commits()["payload"]

    class Response(io.BytesIO):
        headers = {"Link": '<https://api.github.com/repos/example/project/pulls/99/commits?per_page=100&page=2>; rel="next"'}

    def transport(request, *, timeout):
        calls.append(request.full_url)
        return Response(json.dumps(payload).encode())

    monkeypatch.setattr("issueintel.benchmarks.main_metadata.open_github_request", transport)
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-test-token")
    url = commits()["source_url"]
    receipt = fetch_api_receipt(url)
    assert receipt["pagination_complete"] is False
    assert receipt["maximum_pages"] == 1
    assert receipt["maximum_commits"] == 100
    assert calls == [url]
    with pytest.raises(ValueError, match="response size cap"):
        fetch_api_receipt(url, max_response_bytes=1)
    for invalid_url in (url.replace("per_page=100", "per_page=101"), url.replace("page=1", "page=2"),
                        url.replace("/pulls/", "/issues/")):
        with pytest.raises(ValueError):
            fetch_api_receipt(invalid_url)
    assert len(calls) == 2


def django_case(statement="Bug\nOriginal symptom\n"):
    return {**case(statement), "repository": "django/django", "instance_id": "django__django-99",
            "case_id": "verified:django__django-99"}


def django_pull(body="", *, title="Fixed #7 -- Correct the behavior"):
    url = "https://api.github.com/repos/django/django/pulls/99"
    return {"source_url": url, "payload": {"url": url, "number": 99, "title": title, "body": body,
        "created_at": "2025-01-01T00:00:00Z", "base": {"repo": {"full_name": "django/django"}}}}


def trac_pair(root, number=7, *, current_body="Original symptom", initial_body="Original symptom"):
    from issueintel.benchmarks import trac

    def receipt(version):
        initial = version == 0
        marker = '<a href="#comment:description">Initial Version</a>' if initial else ""
        body = initial_body if initial else current_body
        raw = f'''<div id="ticket"><div class="date"><p>Opened
          <a class="timeline" href="/timeline?from=2024-08-01T01%3A02%3A03Z&amp;precision=second">old</a></p></div>
          <a class="trac-id" href="/ticket/{number}">#{number}</a>
          <h1 class="searchable"><span class="summary">Bug</span>{marker}</h1>
          <div class="description">{body}</div></div>'''.encode()
        return {"source_url": trac.ticket_url(number, version=version), "fetched_at": "2026-10-12T00:00:00Z",
                "payload": trac.parse_trac_ticket_html(raw, number, version=version),
                "response_sha256": hashlib.sha256(raw).hexdigest(), "raw_html": trac._save_raw(root, raw)}

    return {"report": {"untrusted_flags": "not used as proof"},
            "current_receipt": receipt(None), "initial_receipt": receipt(0)}


def import_one_main(root, repository="django/django", *, statement="Bug\nOriginal symptom\n"):
    path = root / "data/fixtures/main.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"instance_id": repository.replace("/", "__") + "-99", "repo": repository,
        "base_commit": "a" * 40, "created_at": "2025-01-01T00:00:00Z", "problem_statement": statement}) + "\n")
    import_benchmark(root, verified_path=path, target_live=0)
    return json.loads((root / "data/benchmark/cases.jsonl").read_text())


def test_trac_exact_query_mapping_uses_canonical_ticket_identity_and_replayed_initial_html(tmp_path):
    pair = trac_pair(tmp_path)
    mapped = map_trac_verified_case(tmp_path, django_case(), django_pull(), [pair])
    assert mapped["mapping_method"] == TRAC_MAPPING_METHOD
    assert mapped["status"] == "exact_issue_match"
    selected = mapped["selected_issues"][0]
    assert selected["source_kind"] == "django_trac"
    assert selected["global_id"] == "django_trac#7"
    assert selected["source_url"] == "https://code.djangoproject.com/ticket/7?version=0"
    assert mapped["original_text_at_issue_creation_verified"] is True
    assert mapped["strict_issue_time_snapshot_verified"] is False
    candidate = mapped["original_query_candidate"]
    assert candidate["basis"] == "pr_declared_closing_reference"
    assert candidate["human_validated"] is False
    assert candidate["independent_causal_validation"] is False
    assert "Original symptom" not in json.dumps(mapped)


def test_trac_source_mismatch_stays_pending_but_declared_original_lineage_is_separate(tmp_path):
    pair = trac_pair(tmp_path, current_body="Later edited description", initial_body="True opening text")
    mapped = map_trac_verified_case(tmp_path, django_case(), django_pull(), [pair])
    assert mapped["status"] == "no_exact_current_trac_text_match"
    assert mapped["selected_issues"] == []
    assert mapped["current_text_matches_source_task"] is False
    assert mapped["original_text_at_issue_creation_verified"] is False
    candidate = mapped["original_query_candidate"]
    assert candidate["original_issue_text_verified"] is True
    assert candidate["exact_source_query_match"] is False
    assert candidate["original_source_query_match"] is False
    assert candidate["parent_case_id"] == django_case()["case_id"]
    assert candidate["source_query_sha256"] == hashlib.sha256(django_case()["problem_statement"].encode()).hexdigest()
    assert candidate["original_query_sha256"] == pair["initial_receipt"]["payload"]["problem_statement_sha256"]
    assert candidate["source_human_label_scope"] == "original_SWE_query_only"


def test_trac_ambiguous_refs_and_missing_pairs_never_force_original_candidate(tmp_path):
    pairs = [trac_pair(tmp_path, 7), trac_pair(tmp_path, 8)]
    pull_receipt = django_pull("Fixes #8")
    ambiguous = map_trac_verified_case(tmp_path, django_case(), pull_receipt, pairs)
    assert ambiguous["status"] == "ambiguous_exact_trac_matches"
    assert ambiguous["original_query_candidate"] is None
    assert ambiguous["selected_issues"] == []
    missing = map_trac_verified_case(tmp_path, django_case(), pull_receipt, pairs[:1])
    assert missing["original_query_candidate"] is None
    assert missing["rejected_candidates"][0]["reason"] == "missing_trac_history_pair"
    with pytest.raises(ValueError, match="duplicate Trac"):
        map_trac_verified_case(tmp_path, django_case(), django_pull(), [pairs[0], pairs[0]])


def test_trac_raw_tampering_denies_mapping_even_when_payload_claims_correctness(tmp_path):
    pair = trac_pair(tmp_path)
    path = tmp_path / pair["initial_receipt"]["raw_html"]["path"]
    path.write_bytes(path.read_bytes().replace(b"Original", b"Modified"))
    with pytest.raises(ValueError, match="size differs|digest differs"):
        map_trac_verified_case(tmp_path, django_case(), django_pull(), [pair])


def test_trac_enrichment_reuses_private_pr_and_pair_then_resumes_pending_without_requests(tmp_path):
    original = import_one_main(tmp_path)
    receipt = django_pull()
    old_event = append_main_mapping(tmp_path, original, map_verified_case(original, receipt, []),
                                   {"pull_receipt": receipt, "issue_receipts": []})
    manifest_before = (tmp_path / "data/manifests/dataset.json").read_bytes()
    old_bytes = (tmp_path / old_event["receipt_bundle"]["path"]).read_bytes()
    calls = []

    def fetch_pair(root, number, *, expected_problem_statement):
        calls.append(number)
        assert expected_problem_statement == original["problem_statement"]
        return trac_pair(root, number, current_body="Later description", initial_body="Initial description")

    result = enrich_trac_main_metadata(tmp_path, limit=1, all_splits=True, pair_fetcher=fetch_pair,
        fetcher=lambda url: pytest.fail("PR comes from the earlier immutable receipt"))
    assert result["mapped"] == 1
    assert result["original_query_candidates"] == 1
    assert result["statuses"] == {"no_exact_current_trac_text_match": 1}
    assert calls == [7]
    rows = [json.loads(line) for line in (tmp_path / "data/annotations/main_metadata.jsonl").read_text().splitlines()]
    assert rows[0] == old_event
    assert rows[1]["mapping_method"] == TRAC_MAPPING_METHOD
    assert (tmp_path / old_event["receipt_bundle"]["path"]).read_bytes() == old_bytes
    for row in rows:
        verify_main_mapping_record(tmp_path, original, row)
    resumed = enrich_trac_main_metadata(tmp_path, limit=1, all_splits=True,
        pair_fetcher=lambda *args, **kwargs: pytest.fail("pending schema3 resumes"),
        fetcher=lambda url: pytest.fail("pending schema3 resumes"))
    assert resumed["already_mapped"] == 1
    assert resumed["mapped"] == 0
    assert (tmp_path / "data/manifests/dataset.json").read_bytes() == manifest_before


def test_declared_github_lineage_requires_unique_real_issue_and_keeps_original_text_unverified():
    mismatch = issue(body="Later edited text")
    mapped = map_declared_verified_case(case(), pull(), [mismatch])
    assert mapped["mapping_method"] == DECLARED_GITHUB_MAPPING_METHOD
    assert mapped["status"] == "no_exact_current_issue_text_match"
    assert mapped["selected_issues"] == []
    candidate = mapped["original_query_candidate"]
    assert candidate["basis"] == "pr_declared_closing_reference"
    assert candidate["exact_source_query_match"] is False
    assert candidate["original_issue_text_verified"] is False
    assert candidate["original_query_sha256"] is None
    assert candidate["human_validated"] is False
    multiple = map_declared_verified_case(case(), pull("Fixes #7, fixes #8"), [mismatch, issue(8, body="Another")])
    assert multiple["original_query_candidate"] is None
    unseen = map_declared_verified_case(case(), pull("Fixes #7, fixes #8"), [mismatch])
    assert unseen["original_query_candidate"] is None
    referenced_pr = map_declared_verified_case(case(), pull(), [issue(is_pr=True)])
    assert referenced_pr["original_query_candidate"] is None


def test_declared_github_upgrade_uses_existing_receipts_only_and_preserves_exact_match_policy(tmp_path):
    original = import_one_main(tmp_path, "example/project")
    receipt = pull()
    mismatch = issue(body="Later edited text")
    old_event = append_main_mapping(tmp_path, original, map_verified_case(original, receipt, [mismatch]),
                                   {"pull_receipt": receipt, "issue_receipts": [mismatch]})
    result = upgrade_declared_main_relations(tmp_path, all_splits=True)
    assert result["network_calls"] == 0
    assert result["mapped"] == 1
    assert result["original_query_candidates"] == 1
    rows = [json.loads(line) for line in (tmp_path / "data/annotations/main_metadata.jsonl").read_text().splitlines()]
    assert rows[0] == old_event
    assert rows[1]["selected_issues"] == []
    assert rows[1]["status"] == old_event["status"]
    assert rows[1]["original_query_candidate"]["parent_case_id"] == original["case_id"]
    for row in rows:
        verify_main_mapping_record(tmp_path, original, row)
    assert upgrade_declared_main_relations(tmp_path, all_splits=True)["mapped"] == 0
