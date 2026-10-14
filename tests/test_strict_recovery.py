import base64
import copy
import hashlib
import pytest
from test_strict_dev import pure_fixture
from issueintel.benchmarks.strict_recovery import verify_long_ancestry, check_recovered_proof
from issueintel.benchmarks.strict_recovery import archive_repo, push_revisions, opening_event_valid, event_replays
from issueintel.benchmarks.strict_recovery import parse_commit_batch
from issueintel.runtime.io import digest


def chain(count):
    objects = []
    parent = None
    for i in range(count):
        raw = (f"tree {'0'*40}\n" + (f"parent {parent}\n" if parent else "")
               + f"author Synthetic <test@example.invalid> {i} +0000\ncommitter Synthetic <test@example.invalid> {i} +0000\n\nfixture {i}\n").encode()
        revision = hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        objects.insert(0, {"revision": revision, "raw_commit_base64": base64.b64encode(raw).decode()})
        parent = revision
    return {"schema": "raw_git_first_parent_chain_v2", "base_revision": objects[0]["revision"],
            "selected_snapshot_revision": objects[-1]["revision"], "commit_parent_chain": objects, "parent_chain_sha256": digest(objects)}


def test_long_history_preserves_actual_parent_and_object_integrity():
    proof = chain(300)
    assert verify_long_ancestry(proof, proof["base_revision"], proof["selected_snapshot_revision"])
    changed = copy.deepcopy(proof)
    changed["commit_parent_chain"][2]["raw_commit_base64"] = base64.b64encode(b"fake commit").decode()
    changed["parent_chain_sha256"] = digest(changed["commit_parent_chain"])
    assert not verify_long_ancestry(changed, proof["base_revision"], proof["selected_snapshot_revision"])


def test_extended_search_never_accepts_git_clock_as_time_proof():
    case, bundle = pure_fixture()
    proof = chain(300)
    case["revision"] = proof["base_revision"]
    selected = proof["selected_snapshot_revision"]
    bundle.update(selected_snapshot_revision=selected, long_ancestry_receipt=proof,
                  revision_proof={"repository": case["repository"], "revision": selected,
                                  "basis": "git_commit_timestamp", "source_url": "https://api.github.com/commit"})
    report, reasons = check_recovered_proof(case, bundle)
    assert report["snapshot_ancestry_verified"]
    assert "independent_code_availability_missing" in reasons
    assert not report["strict_issue_time_receipt_checks_passed"]


def test_wrong_split_or_hidden_fields_cannot_use_extended_path():
    case, bundle = pure_fixture()
    bundle["long_ancestry_receipt"] = chain(300)
    assert check_recovered_proof(dict(case, split="main_test"), bundle)[1] == ["public_projection_or_dev_membership_invalid"]
    assert check_recovered_proof(dict(case, patch="hidden gold"), bundle)[1] == ["public_projection_or_dev_membership_invalid"]


def match(event):
    import json
    raw = json.dumps(event, ensure_ascii=False) + "\n"
    return {"event": event, "raw_event_line": raw, "raw_line_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "event_sha256": digest(event), "source_url": "https://data.gharchive.org/2014-10-31-22.json.gz"}


def test_legacy_transfer_keeps_original_bytes_and_requires_same_numeric_repo():
    event = {"type": "PushEvent", "public": True, "created_at": "2014-10-31T22:10:00Z",
             "repository": {"id": 123, "name": "project", "owner": "old-owner", "full_name": "old-owner/project"},
             "payload": {"shas": [["a"*40, "message", "actor"]], "head": "b"*40}}
    fact = {"requested_repository": "new-owner/project", "payload": {"id": 123}}
    recorded = match(event)
    assert archive_repo(event) == ("old-owner/project", 123)
    assert push_revisions(event) == {"a"*40, "b"*40}
    assert event_replays(recorded, fact, "PushEvent")
    assert not event_replays(recorded, {"payload": {"id": 124}}, "PushEvent")
    changed = copy.deepcopy(recorded)
    changed["event"]["repository"]["full_name"] = "new-owner/project"
    assert not event_replays(changed, fact, "PushEvent")


def test_opening_alias_still_requires_immutable_issue_id_and_initial_version():
    issue = {"id": 99, "number": 7, "created_at": "2014-10-31T22:10:00Z", "updated_at": "2014-10-31T22:10:00Z",
             "title": "Initial", "body": "Original body", "url": "https://api.github.com/repos/old-owner/project/issues/7"}
    event = {"type": "IssuesEvent", "public": True, "created_at": "2014-10-31T22:10:03Z",
             "repo": {"id": 123, "name": "old-owner/project"}, "payload": {"action": "opened", "issue": issue}}
    current_payload = dict(issue, url="https://api.github.com/repos/new-owner/project/issues/7")
    current = {"source_url": current_payload["url"], "payload": current_payload, "payload_sha256": digest(current_payload),
               "fetched_at": "2026-10-13T00:00:00Z"}
    fact = {"requested_repository": "new-owner/project", "payload": {"id": 123}}
    assert opening_event_valid(match(event), current, fact)
    edited = copy.deepcopy(event)
    edited["payload"]["issue"]["updated_at"] = "2014-10-31T22:10:01Z"
    assert not opening_event_valid(match(edited), current, fact)
    wrong = copy.deepcopy(current)
    wrong["payload"]["id"] = 98
    wrong["payload_sha256"] = digest(wrong["payload"])
    assert not opening_event_valid(match(event), wrong, fact)


def test_commit_batch_returns_identical_bytes_and_denies_bad_framing():
    proof = chain(3)
    objects = proof["commit_parent_chain"]
    output = b""
    for item in objects:
        raw = base64.b64decode(item["raw_commit_base64"])
        output += f"{item['revision']} commit {len(raw)}\n".encode() + raw + b"\n"
    requested = [item["revision"] for item in objects]
    assert parse_commit_batch(output, requested) == objects
    for corrupt in (output[:-1], output + b"extra", output.replace(b"commit", b"blob", 1), output.replace(b"fixture", b"fiction", 1)):
        with pytest.raises(ValueError):
            parse_commit_batch(corrupt, requested)


def test_extended_path_rejects_wrong_query_policy():
    case, bundle = pure_fixture()
    bundle.update(long_ancestry_receipt=chain(300), query_policy="current_query")
    assert check_recovered_proof(case, bundle)[1] == ["original_query_provenance_missing"]


def identity_fixture():
    case, _ = pure_fixture()
    ancestry = chain(300)
    case["revision"] = ancestry["base_revision"]
    issue = {"id": 99, "number": 7, "created_at": "2014-10-31T22:10:00Z", "updated_at": "2014-10-31T22:10:00Z",
             "title": "Original", "body": "Initial query", "url": "https://api.github.com/repos/old/project/issues/7"}
    event = {"type": "IssuesEvent", "public": True, "created_at": "2014-10-31T22:10:03Z",
             "repository": {"id": 123, "full_name": "old/project"}, "payload": {"action": "opened", "issue": issue}}
    current = dict(issue, url="https://api.github.com/repos/" + case["repository"] + "/issues/7")
    receipt = {"payload": current, "payload_sha256": digest(current), "source_url": current["url"], "fetched_at": "2026-10-13T00:00:00Z"}
    push = {"type": "PushEvent", "public": True, "created_at": "2014-10-31T22:09:00Z",
            "repository": {"id": 123, "full_name": "old/project"},
            "payload": {"shas": [[ancestry["selected_snapshot_revision"], "synthetic"]]}}
    bundle = {"event_schema_version": "raw_archive_identity_v2", "query_policy": "original_opening_query", "source_case_sha256": digest(case),
              "repository_identity_fact": {"requested_repository": case["repository"], "source_url": "https://api.github.com/repos/" + case["repository"],
                                           "payload": {"id": 123, "private": False}},
              "issue_references": [{"repository": case["repository"], "number": 7}], "current_issue_receipts": [receipt],
              "opening_matches": [match(event)], "push_match": match(push), "long_ancestry_receipt": ancestry,
              "selected_snapshot_revision": ancestry["selected_snapshot_revision"]}
    return case, bundle


def test_legacy_schema_support_preserves_strict_preissue_and_source_contract():
    case, bundle = identity_fixture()
    report, reasons = check_recovered_proof(case, bundle)
    assert reasons == [] and report["strict_issue_time_receipt_checks_passed"]
    assert report["source_case_was_rewritten"] is False and report["raw_events_rewritten"] is False
    later = copy.deepcopy(bundle)
    later["push_match"]["event"]["created_at"] = "2014-10-31T22:10:00Z"
    later["push_match"] = match(later["push_match"]["event"])
    assert "snapshot_not_pre_issue" in check_recovered_proof(case, later)[1]
    wrong = copy.deepcopy(bundle)
    wrong["source_case_sha256"] = "0" * 64
    assert check_recovered_proof(case, wrong)[1] == ["query_source_binding_invalid"]
    wrong = copy.deepcopy(bundle)
    wrong["opening_matches"][0]["event"]["repository"]["id"] = 124
    wrong["opening_matches"][0] = match(wrong["opening_matches"][0]["event"])
    assert "opening_version_or_identity_unverified" in check_recovered_proof(case, wrong)[1]


def test_git_timeout_stops_child_process_tree_instead_of_waiting_on_pipes():
    import os
    import sys
    import time
    from issueintel.benchmarks.strict_recovery import _bounded_git_runner
    script = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);time.sleep(30)"
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        _bounded_git_runner([sys.executable, "-c", script], capture_output=True, check=False,
                            timeout=0.5, env=os.environ.copy())
    assert time.monotonic() - started < 10


def test_merge_parent_path_is_a_real_ancestor_but_requires_every_raw_link():
    proof = chain(3)
    raw = (f"tree {'0'*40}\nparent {'f'*40}\nparent {proof['base_revision']}\n"
           "author Synthetic <test@example.invalid> 5 +0000\ncommitter Synthetic <test@example.invalid> 5 +0000\n\nmerge\n").encode()
    merged = hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    objects = [{"revision": merged, "raw_commit_base64": base64.b64encode(raw).decode()}, *proof["commit_parent_chain"]]
    receipt = dict(proof, schema="raw_git_ancestor_chain_v3", base_revision=merged,
                   commit_parent_chain=objects, parent_chain_sha256=digest(objects))
    assert verify_long_ancestry(receipt, merged, proof["selected_snapshot_revision"])
    wrong = copy.deepcopy(receipt)
    wrong["commit_parent_chain"][1] = chain(1)["commit_parent_chain"][0]
    wrong["parent_chain_sha256"] = digest(wrong["commit_parent_chain"])
    assert not verify_long_ancestry(wrong, merged, proof["selected_snapshot_revision"])


def test_legacy_hour_label_uses_declared_offset_but_creation_compares_actual_instant():
    issue = {"id": 99, "number": 7, "created_at": "2014-10-31T15:13:00-07:00", "updated_at": "2014-10-31T15:13:00-07:00",
             "title": "Initial", "body": "Full body"}
    event = {"type": "IssuesEvent", "public": True, "created_at": "2014-10-31T15:13:03-07:00",
             "repository": {"id": 123, "full_name": "old/project"}, "payload": {"action": "opened", "issue": issue}}
    recorded = match(event)
    recorded["source_url"] = "https://data.gharchive.org/2014-10-31-15.json.gz"
    fact = {"requested_repository": "new/project", "payload": {"id": 123}}
    payload = dict(issue, created_at="2014-10-31T22:13:00Z", url="https://api.github.com/repos/new/project/issues/7")
    current = {"source_url": payload["url"], "payload": payload, "payload_sha256": digest(payload), "fetched_at": "2026-10-13T00:00:00Z"}
    assert opening_event_valid(recorded, current, fact)
    wrong_hour = dict(recorded, source_url="https://data.gharchive.org/2014-10-31-16.json.gz")
    assert not event_replays(wrong_hour, fact, "IssuesEvent")
    wrong_time = copy.deepcopy(current)
    wrong_time["payload"]["created_at"] = "2014-10-31T15:13:00Z"
    wrong_time["payload_sha256"] = digest(wrong_time["payload"])
    assert not opening_event_valid(recorded, wrong_time, fact)
