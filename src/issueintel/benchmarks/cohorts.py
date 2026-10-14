"""Immutable, explicitly selected construction cohorts derived from pinned Live.

The bootstrap dataset and its default loader are untouched. A cohort view is a
construction input namespace, never a holdout evaluation capability.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import itertools
import json
from pathlib import Path
import re
from typing import Any, Mapping

from issueintel.runtime.io import digest
from .importer import _json_bytes, _jsonl_bytes, _normalized, _publish
from .main_metadata import extract_closing_references, verify_main_mapping_record
from .models import UpstreamRecord, utc_timestamp
from .sources import LIVE_DATASET, LIVE_REVISION, SOURCE_FILES, inside_root, sha256_file
from .store import verify_manifest
from .temporal import _issue_receipt

SELECTION_RULE = "latest_pull_created_at_at_or_after_cutoff_then_instance_id; exclude current800 and conflicting IDs"
COHORT_SCHEMA = "issueintel.live_extension_cohort.v1"


def _sha(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Require a full lowercase SHA256")
    return value


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Require a JSON object")
    return value


def _data_path(root: Path, relative: Any, *, within: Path | None = None) -> Path:
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Require a contained relative data path")
    path = inside_root(root, root / relative)
    if not path.is_relative_to((within or root / "data").resolve()):
        raise ValueError("Cohort input escapes its private namespace")
    return path


def _descriptor(root: Path, path: Path) -> dict:
    path = inside_root(root, path)
    return {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path), "bytes": path.stat().st_size}


def _checked(root: Path, descriptor: Mapping[str, Any], *, within: Path | None = None, size_required: bool = True) -> Path:
    path = _data_path(root, descriptor.get("path"), within=within)
    size = descriptor.get("bytes")
    if ((size_required and size is None) or (size is not None and (type(size) is not int or size < 0 or path.stat().st_size != size))
            or sha256_file(path) != _sha(descriptor.get("sha256"))):
        raise ValueError("Cohort artifact integrity failure")
    return path


def _rows(path: Path) -> list[dict]:
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError("Incomplete JSONL artifact")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Require JSONL objects")
    return rows


def _partial_projection(record: UpstreamRecord) -> dict:
    case = record.public_projection("temporal_candidate")
    # The metadata registration predates the source projection's URL field.
    return {key: value for key, value in case.items() if key != "issue_url"}


def _namespace(root: Path, cohort_sha256: str) -> Path:
    expected = root / "data/benchmark/cohorts" / _sha(cohort_sha256)
    actual = inside_root(root, expected)
    if actual != expected:
        raise ValueError("Cohort namespace escapes through a junction or symlink")
    return actual


def _source_state(root: Path, selection_sha256: str) -> tuple[dict, dict, list[UpstreamRecord], dict]:
    parent = verify_manifest(root)
    parent_path = root / "data/manifests/dataset.json"
    source_path = _data_path(root, parent["files"]["sources"]["path"])
    sources = _json(source_path)
    live_groups = [item for item in sources["sources"] if item["source"] == "live"]
    if len(live_groups) != 1:
        raise ValueError("Require exactly one pinned Live source group")
    live = live_groups[0]
    if (live["dataset_id"] != LIVE_DATASET or live["revision"] != LIVE_REVISION
            or live.get("revision_validated") is not True):
        raise ValueError("Extension cohorts require the pinned official Live source")
    expected = {item.sha256: item for item in SOURCE_FILES if item.source == "live"}
    if len(live["files"]) != len(expected) or {item["sha256"] for item in live["files"]} != set(expected):
        raise ValueError("Live source pins differ from the supported source")
    paths = []
    for descriptor in live["files"]:
        source = expected[descriptor["sha256"]]
        if (descriptor.get("official_hash_verified") is not True or descriptor["bytes"] != source.size
                or descriptor.get("url") != source.url):
            raise ValueError("Live source descriptor is not its official pin")
        paths.append(_checked(root, descriptor, within=root / "data/upstream"))
    records, normalization = _normalized(paths, "live", LIVE_REVISION, duplicate_policy="exclude_conflicts")
    if normalization != parent["normalization"]["live"]:
        raise ValueError("Live normalization differs from the parent source")
    base = root / "data/annotations/live_extension" / _sha(selection_sha256)
    registration_path = _data_path(root, (base / "registration.json").relative_to(root).as_posix(), within=base)
    registration = _json(registration_path)
    if digest(registration) != selection_sha256:
        raise ValueError("Selection registration digest differs")
    if (registration.get("schema_version") != 1
            or registration.get("kind") != "live_extension_metadata_preregistration"
            or registration.get("parent_manifest_sha256") != sha256_file(parent_path)
            or registration.get("source_manifest_sha256") != sha256_file(source_path)
            or registration.get("live_source_revision") != LIVE_REVISION
            or registration.get("source_files") != live["files"]
            or registration.get("rule") != SELECTION_RULE
            or registration.get("model_calls") != 0 or registration.get("evaluation_performed") is not False
            or registration.get("annotation_contents_read") is not False
            or registration.get("source_manifest_mutation") is not False):
        raise ValueError("Selection source, rule or construction boundary differs")
    cutoff = utc_timestamp(registration.get("cutoff"))
    if cutoff != parent["selection"]["cutoff"]:
        raise ValueError("Selection cutoff differs from its parent")
    limit = registration.get("limit")
    if type(limit) is not int or not 1 <= limit <= 300:
        raise ValueError("Require a bounded pre-registered extension size")
    parent_cases = _rows(_data_path(root, parent["files"]["cases"]["path"]))
    parent_ids = {case["instance_id"] for case in parent_cases}
    fingerprints = {(case["repository"], case["revision"], case["problem_statement"]) for case in parent_cases}
    eligible = [record for record in records if record.instance_id not in parent_ids
                and (record.repository, record.revision, record.problem_statement) not in fingerprints
                and record.pull_created_at >= cutoff]
    eligible.sort(key=lambda record: record.instance_id)
    eligible.sort(key=lambda record: record.pull_created_at, reverse=True)
    selected = eligible[:limit]
    counts = {"raw": normalization["raw_rows"], "usable": normalization["usable_unique_instances"],
              "not_in_current_cohort_eligible": len(eligible)}
    if (registration.get("candidate_cases") != [_partial_projection(record) for record in selected]
            or registration.get("source_counts") != counts):
        raise ValueError("Pre-registered candidate selection does not reproduce from source")
    return parent, registration, selected, {"parent_manifest": _descriptor(root, parent_path),
        "source_manifest": _descriptor(root, source_path), "registration": _descriptor(root, registration_path),
        "source_files": live["files"], "normalization": normalization}


def _acquisition_report(root: Path, base: Path, report_sha256: str | None) -> tuple[dict, dict]:
    if report_sha256 is None:
        pointer = _json(root / "artifacts/live_extension_metadata_report.json")
        path = _checked(root, pointer["immutable_report"], within=base / "reports")
        report = _json(path)
        if report != {key: value for key, value in pointer.items() if key != "immutable_report"}:
            raise ValueError("Acquisition report copy differs from immutable report")
    else:
        path = _data_path(root, (base / "reports" / (_sha(report_sha256) + ".json")).relative_to(root).as_posix(), within=base / "reports")
        report = _json(path)
    if path.stem != digest(report):
        raise ValueError("Acquisition report content address differs")
    return report, _descriptor(root, path)


def _known_identities(root: Path, parent: dict, descriptor: dict) -> dict[tuple[str, int], set[str]]:
    snapshot_path = _checked(root, descriptor, within=root / "data/annotations/live_extension")
    snapshot = _json(snapshot_path)
    if snapshot_path.stem != digest(snapshot) or snapshot.get("unknowns_are_not_absence") is not True:
        raise ValueError("Known identity snapshot digest or uncertainty policy differs")
    parent_cases = {case["case_id"]: case for case in _rows(_data_path(root, parent["files"]["cases"]["path"]))}
    parent_sha = sha256_file(root / "data/manifests/dataset.json")
    known = defaultdict(set)
    expected_names = {"main_metadata", "temporal_audit", "historical_audit"}
    if set(snapshot.get("inputs", {})) != expected_names:
        raise ValueError("Known identity snapshot must bind all parent metadata overlays")
    for name, entry in snapshot["inputs"].items():
        if entry["path"] != "data/annotations/" + name + ".jsonl":
            raise ValueError("Known identity input is not its declared overlay")
        path = _data_path(root, entry["path"], within=root / "data/annotations")
        count = entry.get("bytes")
        if type(count) is not int or count < 0:
            raise ValueError("Invalid overlay snapshot length")
        if not path.exists() and count == 0:
            raw = b""
        else:
            with path.open("rb") as stream:
                raw = stream.read(count)
        # Later append-only events may exist; this receipt binds the recorded prefix.
        if (len(raw) != count or hashlib.sha256(raw).hexdigest() != _sha(entry.get("sha256"))
                or (raw and not raw.endswith(b"\n"))):
            raise ValueError("Recorded metadata overlay prefix differs")
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if len(rows) != entry.get("events"):
            raise ValueError("Recorded metadata event count differs")
        for row in rows:
            case = parent_cases.get(row.get("case_id"))
            if case is None:
                raise ValueError("Known identity references a case outside the parent")
            if name == "main_metadata":
                bundle = verify_main_mapping_record(root, case, row)
            else:
                if (row.get("benchmark_manifest_sha256") != parent_sha or row.get("case_sha256") != digest(case)
                        or row.get("audit_id") != digest({key: value for key, value in row.items() if key != "audit_id"})):
                    raise ValueError("Known historical metadata event binding differs")
                bundle = _json(_checked(root, row["receipt_bundle"], within=root / "data/annotations", size_required=False))
            for receipt in bundle.get("issue_receipts", []):
                match = re.fullmatch(r"https://api\.github\.com/repos/([^/]+/[^/]+)/issues/([1-9][0-9]*)", str(receipt.get("source_url", "")))
                if not match:
                    continue
                item = _issue_receipt(receipt, match[1], match[2])
                if item["identity_valid"] and item["created_before_fetch"]:
                    known[(match[1].lower(), int(match[2]))].add(case["case_id"])
    expected = [{"repository": repo, "number": number, "case_ids": sorted(ids)} for (repo, number), ids in sorted(known.items())]
    if snapshot.get("identities") != expected:
        raise ValueError("Known identities do not replay from the captured metadata")
    return known


def _receipt(root: Path, descriptor: dict, base: Path) -> dict:
    path = _checked(root, descriptor, within=base / "receipts")
    receipt = _json(path)
    if path.stem != digest(receipt):
        raise ValueError("API receipt content address differs")
    if receipt.get("payload_sha256") is not None and receipt["payload_sha256"] != digest(receipt.get("payload")):
        raise ValueError("API receipt payload digest differs")
    _sha(receipt.get("response_sha256"))
    return receipt


def _metadata(root: Path, selection_sha: str, parent: dict, registration: dict,
              records: list[UpstreamRecord], report_sha: str | None,
              alias_pull_receipts: dict | None = None) -> tuple[list[dict], dict]:
    base = root / "data/annotations/live_extension" / selection_sha
    report, report_descriptor = _acquisition_report(root, base, report_sha)
    if (report.get("kind") != "live_extension_metadata_acquisition" or report.get("schema_version") != 1
            or report.get("selection_sha256") != selection_sha or report.get("selected") != len(records)
            or report.get("source_counts") != registration["source_counts"]
            or report.get("model_calls") != 0 or report.get("evaluation_performed") is not False
            or report.get("annotation_contents_read") is not False or report.get("strict_issue_time_cases") != 0
            or any(report.get(key) is not False for key in ("parent_manifest_modified", "source_modified", "current_recipe_modified"))):
        raise ValueError("Acquisition report construction/source boundary differs")
    if _json(_checked(root, report["registration"], within=base)) != registration:
        raise ValueError("Acquisition report refers to another registration")
    known = _known_identities(root, parent, report["known_identity_snapshot"])
    entries = report.get("case_reports", [])
    failures = report.get("failures", [])
    by_id = {entry["case_id"]: entry for entry in entries}
    by_failure = {entry["case_id"]: entry for entry in failures}
    ids = {record.case_id for record in records}
    if (len(by_id) != len(entries) or len(by_failure) != len(failures) or set(by_id) & set(by_failure)
            or set(by_id) | set(by_failure) != ids or report.get("audited") != len(entries)):
        raise ValueError("Acquisition case coverage differs from the selection")
    receipt_by_url = defaultdict(list)
    if alias_pull_receipts is None:
        # Only registration discovers the alias receipts. Verification reads the
        # explicitly frozen descriptors, unaffected by unrelated later files.
        for path in sorted((base / "receipts").glob("*.json")):
            receipt = _receipt(root, _descriptor(root, path), base)
            if "/pulls/" in str(receipt.get("source_url", "")):
                receipt_by_url[receipt["source_url"]].append((receipt, _descriptor(root, path)))
    elif set(alias_pull_receipts) != set(by_failure):
        raise ValueError("Frozen alias receipt coverage differs")
    bound_aliases = {}
    output, bound_case_reports, used = [], [], set()
    issues_to_cases = defaultdict(set)
    temporal_statuses, mapping_statuses = Counter(), Counter()
    for index, record in enumerate(records):
        case = _partial_projection(record)
        prefix = record.repository.replace("/", "__") + "-"
        suffix = record.instance_id[len(prefix):] if record.instance_id.startswith(prefix) else ""
        if not re.fullmatch(r"[1-9][0-9]*", suffix):
            raise ValueError("Source instance does not specify a PR identity")
        pull_url = f"https://api.github.com/repos/{record.repository}/pulls/{suffix}"
        audit = None
        if record.case_id in by_failure:
            if alias_pull_receipts is None:
                choices = receipt_by_url[pull_url]
                if len(choices) != 1:
                    raise ValueError("Require one unambiguous alias PR receipt at registration")
                pull_receipt, pull_descriptor = choices[0]
            else:
                pull_descriptor = alias_pull_receipts[record.case_id]
                pull_receipt = _receipt(root, pull_descriptor, base)
            bound_aliases[record.case_id] = pull_descriptor
        else:
            entry = by_id[record.case_id]
            expected_path = (base / "cases" / (hashlib.sha256(record.case_id.encode()).hexdigest() + ".json")).relative_to(root).as_posix()
            if entry["path"] != expected_path:
                raise ValueError("Mapped case report is outside its case namespace")
            path = _data_path(root, entry["path"], within=base / "cases")
            audit = _json(path)
            pull_descriptor = audit["pull_receipt"]
            pull_receipt = _receipt(root, pull_descriptor, base)
        if pull_receipt.get("source_url") != pull_url:
            raise ValueError("Captured PR receipt is not the selected source endpoint")
        pull = pull_receipt.get("payload", {})
        if (not isinstance(pull, dict) or type(pull.get("number")) is not int or pull.get("number") != int(suffix)
                or utc_timestamp(pull.get("created_at")) != record.pull_created_at):
            raise ValueError("Captured PR date/number differs from its source")
        base_repo = pull.get("base", {}).get("repo", {})
        exact_pull = pull.get("url") == pull_url and str(base_repo.get("full_name", "")).lower() == record.repository.lower()
        item = {"case_id": record.case_id, "selection_case_sha256": digest(case),
                "case_sha256": digest(record.public_projection("temporal_candidate")),
                "query_sha256": hashlib.sha256(record.problem_statement.encode()).hexdigest(),
                "gold_sha256": digest(record.gold_projection()), "pull_receipt": pull_descriptor,
                "strict_issue_time_verified": False, "opening_query_verified": False,
                "evaluation_authorized": False, "annotation_status": "not_constructed"}
        if record.case_id in by_failure:
            if exact_pull or by_failure[record.case_id].get("error") != "Live pull metadata identity differs":
                raise ValueError("Only recorded repository identity mismatches may remain alias pending")
            canonical = base_repo.get("full_name")
            if (not isinstance(canonical, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", canonical)
                    or pull.get("url") != f"https://api.github.com/repos/{canonical}/pulls/{suffix}"):
                raise ValueError("Unknown PR failure lacks a bounded current alias candidate")
            item.update({"eligibility_status": "pending_repository_identity", "selected_issues": [],
                         "current_alias_candidate": {"upstream_repository": record.repository, "current_repository": canonical,
                              "repository_id": base_repo.get("id"), "repository_node_id": base_repo.get("node_id"),
                              "historical_identity_verified": False}, "metadata_failure": by_failure[record.case_id]})
        else:
            if not exact_pull:
                raise ValueError("Mapped extension PR has an unverified repository alias")
            if (audit.get("case_id") != record.case_id or audit.get("case_sha256") != digest(case)
                    or audit.get("selection_sha256") != selection_sha
                    or audit.get("parent_manifest_sha256") != sha256_file(root / "data/manifests/dataset.json")
                    or audit.get("cutoff") != registration["cutoff"] or audit.get("model_calls") != 0
                    or audit.get("evaluation_performed") is not False or audit.get("strict_issue_time") is not False
                    or audit.get("pull_created_at_source") != record.pull_created_at
                    or audit.get("pull_created_at_api") != record.pull_created_at
                    or audit.get("candidate_rank") != parent["counts"]["temporal_candidate"] + index + 1
                    or audit.get("metadata_uses_future_PR_relations_for_lineage_only") is not True
                    or _receipt(root, audit["pull_receipt"], base) != pull_receipt
                    or audit.get("mapping_status") != "exact_selected_receipt_text_match"):
                raise ValueError("Mapped metadata case/source/receipt binding differs")
            issue_receipts = {_receipt(root, descriptor, base)["source_url"]: _receipt(root, descriptor, base)
                              for descriptor in audit["issue_receipts"]}
            if len(issue_receipts) != len(audit["issue_receipts"]):
                raise ValueError("Duplicate issue receipt descriptor")
            refs = [(record.repository, int(number), "upstream_issue_numbers") for number in record.issue_numbers]
            refs.extend((ref.repository, ref.number, "pr_title_body_closing_reference") for ref in
                        extract_closing_references((pull.get("title") or "") + "\n" + (pull.get("body") or ""), record.repository))
            candidates = {}
            for repo, number, basis in refs:
                candidates.setdefault((repo.lower(), number), {"repository": repo, "number": number, "bases": []})["bases"].append(basis)
            if len(candidates) > 10:
                raise ValueError("Candidate universe exceeds the registered reference bound")
            valid_candidates, rejected = [], []
            for identity, ref in candidates.items():
                url = f"https://api.github.com/repos/{ref['repository']}/issues/{ref['number']}"
                receipt = issue_receipts.get(url)
                if receipt is None:
                    missing = [value for value in audit.get("rejected_references", [])
                               if value == {**ref, "reason": "http_404"} or value == {**ref, "reason": "http_410"}]
                    if len(missing) != 1:
                        raise ValueError("Candidate universe lacks its receipt or declared negative lookup")
                    rejected.extend(missing)
                    continue
                parsed = _issue_receipt(receipt, ref["repository"], str(ref["number"]))
                if not parsed["identity_valid"] or not parsed["created_before_fetch"]:
                    rejected.append({**ref, "reason": "PR_or_invalid_actual_issue_identity_or_date"})
                    continue
                valid_candidates.append({**ref, "created_at": parsed["created_at"],
                    "issue_url": f"https://github.com/{ref['repository']}/issues/{ref['number']}",
                    "receipt_sha256": digest(receipt), "query_text_sha256": parsed["content_sha256"],
                    "opening_text_supported_by_receipt": parsed["original_text_supported"],
                    "known_current_case_overlap": sorted(known.get(identity, [])), "text": parsed["rendered_issue_text"]})
            if (audit.get("valid_candidate_issues") != [{k: v for k, v in value.items() if k != "text"} for value in valid_candidates]
                    or audit.get("rejected_references") != rejected
                    or set(issue_receipts) - {f"https://api.github.com/repos/{ref['repository']}/issues/{ref['number']}" for ref in candidates.values()}):
                raise ValueError("Candidate receipt/rejection universe does not replay")
            matching_groups = [group for size in range(1, len(valid_candidates) + 1)
                for group in itertools.combinations(valid_candidates, size)
                if "".join(value["text"] for value in group).replace("\r\n", "\n").strip()
                == record.problem_statement.replace("\r\n", "\n").strip()]
            if (len(matching_groups) != 1 or audit.get("selected_issues") !=
                    [{k: v for k, v in value.items() if k != "text"} for value in matching_groups[0]]):
                raise ValueError("Exact source query mapping is absent or ambiguous")
            rendered, chosen = [], []
            for ref in audit["selected_issues"]:
                repository, number = ref["repository"], ref["number"]
                if type(number) is not int or number <= 0:
                    raise ValueError("Selected issue number is invalid")
                url = f"https://api.github.com/repos/{repository}/issues/{number}"
                receipt = issue_receipts.get(url)
                if receipt is None:
                    raise ValueError("Selected issue lacks its exact captured receipt")
                parsed = _issue_receipt(receipt, repository, str(number))
                if (not parsed["identity_valid"] or not parsed["created_before_fetch"]
                        or ref.get("created_at") != parsed["created_at"] or ref.get("receipt_sha256") != digest(receipt)
                        or ref.get("query_text_sha256") != parsed["content_sha256"]
                        or ref.get("opening_text_supported_by_receipt") != parsed["original_text_supported"]
                        or ref.get("issue_url") != f"https://github.com/{repository}/issues/{number}"):
                    raise ValueError("Selected issue identity, query or date does not replay")
                identity = (repository.lower(), number)
                if known.get(identity) or ref.get("known_current_case_overlap") != []:
                    raise ValueError("Selected issue overlaps a known parent identity")
                if identity in used:
                    raise ValueError("Selected issue overlaps another extension case")
                used.add(identity)
                issues_to_cases[identity].add(record.case_id)
                rendered.append(parsed["rendered_issue_text"])
                chosen.append({"repository": repository, "number": number, "created_at": parsed["created_at"],
                               "issue_url": ref["issue_url"], "receipt_sha256": digest(receipt)})
            source_text = record.problem_statement.replace("\r\n", "\n").strip()
            if not chosen or "".join(rendered).replace("\r\n", "\n").strip() != source_text:
                raise ValueError("Selected issue text does not exactly reproduce the source query")
            post_cutoff = all(ref["created_at"] > registration["cutoff"] for ref in chosen)
            status = "post_cutoff_dates_confirmed_historical_proofs_pending" if post_cutoff else "excluded_issue_not_after_cutoff"
            if (audit.get("all_actual_issue_dates_after_cutoff") != post_cutoff or audit.get("temporal_status") != status
                    or entry.get("temporal_status") != status or entry.get("mapping_status") != audit["mapping_status"]):
                raise ValueError("Metadata eligibility does not replay from actual issue dates")
            temporal_statuses[status] += 1
            mapping_statuses[audit["mapping_status"]] += 1
            bound_case_reports.append(_descriptor(root, path))
            item.update({"eligibility_status": status, "selected_issues": chosen,
                         "metadata_case_report": _descriptor(root, path)})
        output.append(item)
    if (dict(temporal_statuses) != report.get("statuses") or dict(mapping_statuses) != report.get("mapping_statuses")
            or len(issues_to_cases) != report.get("unique_selected_issue_identities")
            or report.get("known_current_issue_overlap") != [] or report.get("within_extension_issue_overlap") != []):
        raise ValueError("Acquisition aggregate does not replay from its case receipts")
    return output, {"acquisition_report": report_descriptor, "known_identity_snapshot": report["known_identity_snapshot"],
                    "metadata_case_reports": bound_case_reports, "alias_pull_receipts": bound_aliases,
                    "acquisition_report_sha256": digest(report)}


def register_extension_cohort(root: Path | str, selection_sha256: str, *,
                              acquisition_report_sha256: str | None = None) -> dict:
    """Register every selected candidate, preserving source cases/gold and exclusions."""
    root = Path(root).resolve()
    parent, registration, records, bindings = _source_state(root, _sha(selection_sha256))
    metadata, acquisition = _metadata(root, selection_sha256, parent, registration, records, acquisition_report_sha256)
    cases, gold = [record.public_projection("temporal_candidate") for record in records], [record.gold_projection() for record in records]
    payloads = {"cases": _jsonl_bytes(cases), "restricted_gold": _jsonl_bytes(gold), "eligibility": _jsonl_bytes(metadata)}
    filenames = {"cases": "cases.jsonl", "restricted_gold": "restricted_gold.jsonl", "eligibility": "eligibility.jsonl"}
    manifest = _manifest(selection_sha256, registration, records, metadata, bindings, acquisition, payloads, filenames)
    cohort_sha = digest(manifest)
    namespace = _namespace(root, cohort_sha)
    _publish(root, [(namespace / filenames[key], raw) for key, raw in payloads.items()] + [(namespace / "manifest.json", _json_bytes(manifest))])
    verify_cohort(root, cohort_sha)
    return {"cohort_sha256": cohort_sha, "manifest_path": (namespace / "manifest.json").relative_to(root).as_posix(),
            "selection_sha256": selection_sha256, "counts": manifest["counts"], "evaluation_authorized": False}


def _manifest(selection_sha256, registration, records, metadata, bindings, acquisition, payloads, filenames):
    return {"schema": COHORT_SCHEMA, "dataset_name": "IssueIntel-Bench", "kind": "live_extension_candidate_cohort",
        "status": "historical_proofs_and_annotations_pending", "selection_sha256": selection_sha256,
        "source_revision": LIVE_REVISION, "source_dataset": LIVE_DATASET, "bindings": {**bindings, **acquisition},
        "files": {key: {"path": filenames[key], "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)} for key, raw in payloads.items()},
        "file_path_base": "cohort_directory", "split": "temporal_candidate", "case_ids": [record.case_id for record in records],
        "counts": {"candidate_cases": len(records), "eligibility": dict(Counter(item["eligibility_status"] for item in metadata)),
                   "strict_issue_time_cases": 0, "complete_annotations": 0},
        "model_cutoff": registration["cutoff"], "query_policy": "upstream_source_unchanged; original_query_is_separate_child_overlay",
        "access_policy": {"scope": "dataset_construction_only", "inference": "cases.jsonl only",
                          "restricted": ["restricted_gold.jsonl", "eligibility.jsonl", "bindings"],
                          "evaluation_authorized": False, "default_loader_modified": False},
        "quality": {"source_human_labels_validate_new_annotations": False, "issueintel_independent_human_checked": False,
                    "known_identity_overlap": False, "identity_overlap_scope": "recorded_parent_metadata_prefixes; replay again before release",
                    "unknown_identity_is_not_proof_of_absence": True},
        "source_and_parent_modified": False, "current_annotation_recipe_modified": False}


def verify_cohort(root: Path | str, cohort_sha256: str) -> dict:
    """Replay source, selection, identity and all file bindings without evaluation."""
    root = Path(root).resolve()
    namespace = _namespace(root, cohort_sha256)
    manifest = _json(namespace / "manifest.json")
    if digest(manifest) != cohort_sha256 or manifest.get("schema") != COHORT_SCHEMA:
        raise ValueError("Cohort manifest content address or schema differs")
    parent, registration, records, bindings = _source_state(root, manifest["selection_sha256"])
    metadata, acquisition = _metadata(root, manifest["selection_sha256"], parent, registration, records,
                                     manifest["bindings"]["acquisition_report_sha256"], manifest["bindings"]["alias_pull_receipts"])
    if manifest["bindings"] != {**bindings, **acquisition}:
        raise ValueError("Cohort acquisition/source bindings differ")
    expected = {"cases": [record.public_projection("temporal_candidate") for record in records],
                "restricted_gold": [record.gold_projection() for record in records], "eligibility": metadata}
    if set(manifest.get("files", {})) != set(expected):
        raise ValueError("Cohort must separate cases, restricted gold and eligibility")
    for key, rows in expected.items():
        descriptor = manifest["files"][key]
        name = {"cases": "cases.jsonl", "restricted_gold": "restricted_gold.jsonl", "eligibility": "eligibility.jsonl"}[key]
        if descriptor["path"] != name:
            raise ValueError("Cohort file path differs from its role")
        path = inside_root(root, namespace / name)
        if not path.is_relative_to(namespace):
            raise ValueError("Cohort file escapes through a junction")
        raw = path.read_bytes()
        if (len(raw) != descriptor["bytes"] or hashlib.sha256(raw).hexdigest() != _sha(descriptor["sha256"])
                or raw != _jsonl_bytes(rows)):
            raise ValueError("Cohort projection or file digest differs from source")
    payloads = {key: _jsonl_bytes(rows) for key, rows in expected.items()}
    filenames = {"cases": "cases.jsonl", "restricted_gold": "restricted_gold.jsonl", "eligibility": "eligibility.jsonl"}
    if manifest != _manifest(manifest["selection_sha256"], registration, records, metadata, bindings, acquisition, payloads, filenames):
        raise ValueError("Cohort identity/count/policy/quality boundary differs")
    return manifest


@dataclass(frozen=True)
class CohortView:
    """An explicit construction view. It does not authorize holdout evaluation."""
    root: Path
    cohort_sha256: str

    @property
    def namespace(self) -> Path:
        return _namespace(self.root.resolve(), self.cohort_sha256)

    def manifest(self) -> dict:
        return verify_cohort(self.root, self.cohort_sha256)

    def cases(self) -> list[dict]:
        self.manifest()
        return _rows(self.namespace / "cases.jsonl")

    def eligibility(self) -> dict[str, dict]:
        self.manifest()
        return {row["case_id"]: row for row in _rows(self.namespace / "eligibility.jsonl")}

    def gold_for_construction(self) -> dict[str, dict]:
        self.manifest()
        return {row["case_id"]: row for row in _rows(self.namespace / "restricted_gold.jsonl")}


def load_cohort_view(root: Path | str, cohort_sha256: str) -> CohortView:
    """Select a verified cohort explicitly; the default dev/test loaders are unchanged."""
    root = Path(root).resolve()
    verify_cohort(root, _sha(cohort_sha256))
    return CohortView(root, cohort_sha256)
