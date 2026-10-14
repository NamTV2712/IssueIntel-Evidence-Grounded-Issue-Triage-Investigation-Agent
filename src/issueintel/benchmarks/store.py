"""Manifest-checked projections and an explicit holdout capability gate."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from issueintel.runtime.io import read_jsonl, write_json


def verify_manifest(root: Path | str) -> dict:
    root = Path(root).resolve()
    manifest_path = root / "data/manifests/dataset.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key, descriptor in manifest["files"].items():
        path = (root / descriptor["path"]).resolve()
        if not path.is_relative_to(root / "data"):
            raise ValueError("Manifest path escapes data")
        content = path.read_bytes()
        if len(content) != descriptor["bytes"] or hashlib.sha256(content).hexdigest() != descriptor["sha256"]:
            raise ValueError(f"Manifest integrity failure: {key}")
    sources = json.loads((root / manifest["files"]["sources"]["path"]).read_text(encoding="utf-8"))
    for source in sources["sources"]:
        for descriptor in source["files"]:
            path = (root / descriptor["path"]).resolve()
            if not path.is_relative_to(root / "data"):
                raise ValueError("Upstream source is outside private data")
            if path.stat().st_size != descriptor["bytes"]:
                raise ValueError("Upstream source size mismatch")
            hasher = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(block)
            if hasher.hexdigest() != descriptor["sha256"]:
                raise ValueError("Upstream source digest mismatch")
    return manifest


def load_dev_cases(root: Path | str) -> list[dict]:
    manifest = verify_manifest(root)
    rows = read_jsonl(Path(root) / manifest["files"]["cases"]["path"])
    return [row for row in rows if row["split"] == "main_dev"]


def load_dev_gold(root: Path | str) -> dict[str, dict]:
    ids = {case["case_id"] for case in load_dev_cases(root)}
    return {row["case_id"]: row for row in read_jsonl(Path(root) / "data/benchmark/gold.jsonl") if row["case_id"] in ids}


@dataclass
class TestCapability:
    root: Path
    split: str
    manifest_sha256: str
    purpose: str
    active: bool = True

    def cases(self) -> list[dict]:
        if not self.active:
            raise PermissionError("Holdout capability has expired")
        path = self.root / "data/manifests/dataset.json"
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.manifest_sha256:
            raise PermissionError("Dataset changed after capability was granted")
        verify_manifest(self.root)
        return [row for row in read_jsonl(self.root / "data/benchmark/cases.jsonl") if row["split"] == self.split]

    def gold(self) -> dict[str, dict]:
        ids = {case["case_id"] for case in self.cases()}
        return {row["case_id"]: row for row in read_jsonl(self.root / "data/benchmark/gold.jsonl") if row["case_id"] in ids}


@contextmanager
def open_test_split(root: Path | str, *, split: str, authorization: str, purpose: str):
    """Caller must pass the user's explicit evaluation authorization, not a freeze.

    This audit gate is not OS isolation. Local file access remains possible; the
    product/development interfaces do not call it or expose gold by default.
    """
    if split not in {"main_test", "temporal_candidate"} or authorization != "evaluate held-out test" or not purpose.strip():
        raise PermissionError("Explicit holdout evaluation authorization and purpose required")
    root = Path(root).resolve()
    manifest = verify_manifest(root)
    if split == "temporal_candidate" and not manifest["temporal_validation"]["strict_issue_time_validated"]:
        raise PermissionError("Temporal candidates have not passed issue-time audit")
    sha = hashlib.sha256((root / "data/manifests/dataset.json").read_bytes()).hexdigest()
    capability = TestCapability(root, split, sha, purpose)
    log = root / "data/runtime/evaluation_authorizations" / (uuid.uuid4().hex + ".json")
    write_json(log, {"created_at": datetime.now(timezone.utc).isoformat(), "split": split,
                    "manifest_sha256": sha, "purpose": purpose, "authorization": authorization})
    try:
        yield capability
    finally:
        capability.active = False
