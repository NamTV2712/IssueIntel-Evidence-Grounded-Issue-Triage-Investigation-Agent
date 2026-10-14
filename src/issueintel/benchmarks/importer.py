"""Build reproducible public cases and restricted gold from pinned SWE-bench data."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import random
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import UpstreamRecord, utc_timestamp
from .sources import (
    LIVE_DATASET, LIVE_REVISION, SOURCE_FILES, VERIFIED_DATASET, VERIFIED_REVISION,
    download_sources, inside_root, sha256_file,
)

DEFAULT_MODEL_CUTOFF = "2024-07-01T00:00:00Z"


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".parquet":
        try:
            import pyarrow.parquet as parquet
        except ImportError as error:
            raise RuntimeError("Parquet imports require: pip install -e '.[data]'") from error
        return parquet.read_table(path).to_pylist()
    if path.suffix.lower() == ".jsonl":
        with path.open(encoding="utf-8-sig") as stream:
            return [json.loads(line) for line in stream if line.strip()]
    if path.suffix.lower() == ".json":
        content = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(content, list):
            return content
        if isinstance(content, dict) and isinstance(content.get("rows"), list):
            return content["rows"]
    raise ValueError(f"Unsupported upstream file format: {path}")


def _normalized(
    paths: Iterable[Path], source: str, revision: str, *, duplicate_policy: str = "reject"
) -> tuple[list[UpstreamRecord], dict[str, Any]]:
    records = [
        UpstreamRecord.from_mapping(row, source=source, source_revision=revision)
        for path in paths for row in _read_rows(path)
    ]
    groups: dict[str, list[UpstreamRecord]] = defaultdict(list)
    for record in records:
        groups[record.instance_id].append(record)
    duplicates = {key: group for key, group in groups.items() if len(group) > 1}
    if duplicates and duplicate_policy == "reject":
        raise ValueError(f"Duplicate upstream instance IDs in {source}")
    usable = []
    decisions = []
    for instance_id, group in sorted(groups.items()):
        identical = all(record == group[0] for record in group[1:])
        if len(group) == 1 or identical:
            usable.append(group[0])
        if len(group) > 1:
            decisions.append({
                "instance_id": instance_id,
                "occurrences": len(group),
                "policy": "collapsed_identical" if identical else "excluded_conflicting_id",
                "reason": "Reference/evaluation variants must not be resolved by arbitrary row order.",
            })
    return usable, {
        "raw_rows": len(records),
        "unique_upstream_ids": len(groups),
        "usable_unique_instances": len(usable),
        "duplicate_policy": duplicate_policy,
        "duplicate_decisions": decisions,
    }


def _main_splits(records: list[UpstreamRecord], dev_count: int, seed: int) -> dict[str, str]:
    if not 0 <= dev_count <= len(records):
        raise ValueError("main_dev_count must be between zero and the Main size")
    # Union shared base snapshots and explicitly supplied parent-instance relations.
    parent = {record.instance_id: record.instance_id for record in records}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        left, right = find(left), find(right)
        parent[max(left, right)] = min(left, right)

    same_base: dict[tuple[str, str], str] = {}
    for record in sorted(records, key=lambda item: item.instance_id):
        key = (record.repository, record.revision)
        if key in same_base:
            union(record.instance_id, same_base[key])
        else:
            same_base[key] = record.instance_id
        if record.parent_instance_id in parent:
            union(record.instance_id, record.parent_instance_id)
    groups: dict[str, list[str]] = defaultdict(list)
    for record in records:
        groups[find(record.instance_id)].append(record.case_id)
    ordered = sorted(groups)
    random.Random(seed).shuffle(ordered)
    # Closest achievable target without breaking a shared snapshot/parent group.
    possible: dict[int, tuple[str, ...]] = {0: ()}
    for group_id in ordered:
        size = len(groups[group_id])
        for current, chosen in list(possible.items()):
            possible.setdefault(current + size, (*chosen, group_id))
    selected_size = min(possible, key=lambda count: (abs(count - dev_count), count))
    selected = set(possible[selected_size])
    return {
        case_id: "main_dev" if group_id in selected else "main_test"
        for group_id, case_ids in groups.items() for case_id in case_ids
    }


def _date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _temporal_candidates(
    live: list[UpstreamRecord], main: list[UpstreamRecord], target: int, cutoff: str
) -> tuple[list[UpstreamRecord], dict[str, Any]]:
    if target < 0:
        raise ValueError("target_live must be nonnegative")
    cutoff_time = _date(cutoff)
    main_ids = {record.instance_id for record in main}
    main_fingerprints = {
        (record.repository, record.revision, record.problem_statement) for record in main
    }
    eligible = [
        record for record in live
        if record.instance_id not in main_ids
        and (record.repository, record.revision, record.problem_statement) not in main_fingerprints
        and _date(record.pull_created_at) >= cutoff_time
    ]
    # Stable tie breaking before sorting dates descending.
    eligible.sort(key=lambda record: record.instance_id)
    eligible.sort(key=lambda record: _date(record.pull_created_at), reverse=True)
    selected = eligible[:target]
    return selected, {
        "rule": "latest_pull_created_at_at_or_after_cutoff_then_instance_id",
        "cutoff": cutoff,
        "date_field": "pull_created_at",
        "requested": target,
        "eligible": len(eligible),
        "selected": len(selected),
        "shortfall": max(0, target - len(selected)),
        "earliest_selected_pull_created_at": min(
            (record.pull_created_at for record in selected), default=None, key=_date
        ),
        "latest_selected_pull_created_at": max(
            (record.pull_created_at for record in selected), default=None, key=_date
        ),
        "issue_time_validated": False,
        "contamination_free_claim": False,
        "note": "PR dates select candidates; issue timestamps and model cutoff provenance need validation.",
    }


def _read_annotations(path: Path | None) -> list[Mapping[str, str]]:
    if path is None:
        return []
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            entry = archive.getinfo("ensembled_annotations_public.csv")
            if entry.file_size > 16 * 1024 * 1024:
                raise ValueError("Annotation CSV exceeds the allowed uncompressed size")
            text = archive.read(entry).decode("utf-8-sig")
    else:
        text = path.read_text(encoding="utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def _source_annotations(
    rows: Iterable[Mapping[str, str]], main: list[UpstreamRecord]
) -> list[dict[str, Any]]:
    by_id: dict[str, Mapping[str, str]] = {}
    for row in rows:
        instance_id = row.get("instance_id", "")
        if instance_id in by_id:
            raise ValueError(f"Duplicate source-human annotation for {instance_id}")
        by_id[instance_id] = row
    annotations = []
    for record in sorted(main, key=lambda item: item.case_id):
        row = by_id.get(record.instance_id)
        if row is None:
            continue
        raw_label = row.get("underspecified", "")
        label = float(raw_label)
        if label not in {0.0, 1.0, 2.0, 3.0}:
            raise ValueError(f"Invalid underspecification ordinal: {raw_label}")
        annotations.append({
            "case_id": record.case_id,
            "instance_id": record.instance_id,
            "source": "openai_swe_bench_ensemble_human_annotation",
            "underspecified_ordinal": int(label),
            "label_semantics": "issue_clarity_severity_0_to_3_not_an_abstention_label",
            "filter_out": row.get("filter_out", "").lower() == "true",
            "difficulty": row.get("difficulty") or None,
            "abstention_label": None,
            "issueintel_human_reviewed": False,
            # Source notes contain solution-derived details and stay in restricted upstream.
        })
    return annotations


def _json_bytes(content: Any) -> bytes:
    return (json.dumps(content, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode("utf-8")
        for row in rows
    )


def _file_descriptor(root: Path, path: Path, content: bytes) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def _publish(root: Path, files: list[tuple[Path, bytes]]) -> None:
    # Validate the whole transaction first: never overwrite another cohort or labels.
    for path, content in files:
        inside_root(root, path)
        if path.exists() and path.read_bytes() != content:
            raise FileExistsError(f"Benchmark artifact already exists with different content: {path}")
    for path, content in files:
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".import-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _sources_manifest(
    root: Path, verified_path: Path, live_paths: list[Path], annotations_path: Path | None
) -> dict[str, Any]:
    source_groups = [
        ("verified", VERIFIED_DATASET, VERIFIED_REVISION, [verified_path]),
        ("live", LIVE_DATASET, LIVE_REVISION, live_paths),
    ]
    if annotations_path:
        source_groups.append(("source_human", "OpenAI/SWE-bench-annotations", SOURCE_FILES[3].revision,
                              [annotations_path]))
    sources = []
    for source_name, dataset_id, revision, paths in source_groups:
        entries = []
        for path in paths:
            digest = sha256_file(path)
            official = next((item for item in SOURCE_FILES if item.source == source_name
                             and item.sha256 == digest), None)
            entries.append({
                "path": path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path),
                "sha256": digest,
                "bytes": path.stat().st_size,
                "url": official.url if official else None,
                "official_hash_verified": official is not None,
                "provenance": "pinned_official" if official else "user_supplied",
            })
        sources.append({
            "source": source_name, "dataset_id": dataset_id, "revision": revision,
            "revision_validated": bool(entries) and all(item["official_hash_verified"] for item in entries),
            "files": entries,
        })
    return {"schema_version": 1, "sources": sources}


def import_benchmark(
    root: str | Path,
    verified_path: str | Path | None = None,
    live_paths: Iterable[str | Path] | None = None,
    annotations_path: str | Path | None = None,
    target_live: int = 300,
    model_cutoff: str = DEFAULT_MODEL_CUTOFF,
    *,
    main_dev_count: int | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """Import without evaluating, annotating with an LLM, or opening held-out cases.

    Local JSON/JSONL fixtures are supported. Source provenance is declared verified
    only when its bytes match a pinned official source. Existing cohort files are
    immutable; an identical import is idempotent, a different import is rejected.
    """
    root = Path(root).resolve()
    if verified_path is None:
        downloaded = download_sources(root)
        verified_path = downloaded["verified_path"]
        if live_paths is None:
            live_paths = downloaded["live_paths"]
        if annotations_path is None:
            annotations_path = downloaded["annotations_path"]
    verified_path = Path(verified_path).resolve()
    live_paths = [Path(path).resolve() for path in (live_paths or [])]
    annotations_path = Path(annotations_path).resolve() if annotations_path else None
    cutoff = utc_timestamp(model_cutoff)
    main, main_normalization = _normalized([verified_path], "verified", VERIFIED_REVISION)
    if not main:
        raise ValueError("Main dataset must contain at least one instance")
    live, live_normalization = _normalized(
        live_paths, "live", LIVE_REVISION, duplicate_policy="exclude_conflicts"
    )
    dev_count = round(len(main) * 0.8) if main_dev_count is None else main_dev_count
    splits = _main_splits(main, dev_count, seed)
    temporal, selection = _temporal_candidates(live, main, target_live, cutoff)
    all_records = sorted([*main, *temporal], key=lambda item: item.case_id)
    public_cases = [
        record.public_projection(splits.get(record.case_id, "temporal_candidate"))
        for record in all_records
    ]
    gold = [record.gold_projection() for record in all_records]
    source_human = _source_annotations(_read_annotations(annotations_path), main)
    counts = {split: 0 for split in ("main_dev", "main_test", "temporal_candidate")}
    counts.update(Counter(case["split"] for case in public_cases))
    counts["total"] = len(public_cases)
    source_manifest = _sources_manifest(root, verified_path, live_paths, annotations_path)
    source_content = _json_bytes(source_manifest)
    outputs = [
        (root / "data/benchmark/cases.jsonl", _jsonl_bytes(public_cases)),
        (root / "data/benchmark/gold.jsonl", _jsonl_bytes(gold)),
        (root / "data/annotations/source_human.jsonl", _jsonl_bytes(source_human)),
        (root / "data/manifests/sources.json", source_content),
    ]
    descriptors = {
        key: _file_descriptor(root, path, content)
        for key, (path, content) in zip(("cases", "gold", "source_human", "sources"), outputs)
    }
    official_verified = source_manifest["sources"][0]["revision_validated"]
    if official_verified and len(main) != 500:
        raise ValueError("Pinned Verified source must contain all 500 instances")
    manifest = {
        "schema_version": 1,
        "dataset_name": "IssueIntel-Bench",
        "status": "bootstrap_unannotated",
        "counts": counts,
        "normalization": {"verified": main_normalization, "live": live_normalization},
        "files": descriptors,
        "main_selection": {
            "requested_dev_count": dev_count,
            "actual_dev_count": counts["main_dev"],
            "seed": seed,
            "grouping": "repository_and_base_commit_or_explicit_parent_instance",
            "scope": "IssueIntel-specific splits; upstream Verified remains the SWE-bench test dataset",
        },
        "selection": selection,
        "annotation_status": {
            "official_source_human_labels": len(source_human),
            "official_underspecified_ordinals": dict(Counter(
                str(row["underspecified_ordinal"]) for row in source_human
            )),
            "issueintel_human_reviewed": 0,
            "human_inter_annotator_agreement": None,
            "llm_hypotheses_generated": 0,
            "abstention_labels_generated": 0,
            "counter_evidence_annotations_generated": 0,
        },
        "temporal_validation": {
            "snapshot_mode": "benchmark_base",
            "strict_issue_time_validated": False,
            "base_commit_is_issue_time_claim": False,
            "known_issue_created_at": sum(record.issue_created_at is not None for record in all_records),
            "model_cutoff_provenance": "configuration_value_requires_model_card_validation",
            "contamination_free_claim": False,
        },
        "access_policy": {
            "agent_input": "data/benchmark/cases.jsonl plus explicitly selected base code snapshot",
            "restricted": ["data/benchmark/gold.jsonl", "data/annotations/", "data/upstream/"],
            "default_generation_split": "main_dev",
            "held_out": ["main_test", "temporal_candidate"],
            "evaluation_performed": False,
        },
    }
    outputs.append((root / "data/manifests/dataset.json", _json_bytes(manifest)))
    _publish(root, outputs)
    return manifest


def bootstrap(root: str | Path, **kwargs: Any) -> dict[str, Any]:
    """Download pinned sources and import their annotation-ready projections."""
    paths = download_sources(root)
    return import_benchmark(root, **paths, **kwargs)
