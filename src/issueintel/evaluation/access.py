"""Explicit, revocable holdout access bound to a verified dataset release."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import uuid

from issueintel.runtime.io import digest, read_jsonl, write_json


@dataclass
class ReleaseTestCapability:
    root: Path
    split: str
    release_sha256: str
    purpose: str
    active: bool = True

    def _verified(self):
        if not self.active:
            raise PermissionError("Holdout capability has expired")
        from issueintel.benchmarks.releases import verify_dataset_release
        verified = verify_dataset_release(self.root, self.release_sha256)
        if self.split not in verified["manifest"]["splits"]:
            raise PermissionError("The release does not contain the authorized split")
        return verified

    def cases(self):
        return [row for row in self._verified()["inference_cases"] if row["split"] == self.split]

    def gold(self):
        verified = self._verified()
        ids = {row["case_id"] for row in verified["inference_cases"] if row["split"] == self.split}
        descriptor = verified["manifest"]["source"]["files"]["gold"]
        path = (self.root / descriptor["path"]).resolve()
        if not path.is_relative_to(self.root / "data/benchmark"):
            raise ValueError("Release gold projection escapes its private directory")
        return {row["case_id"]: row for row in read_jsonl(path) if row["case_id"] in ids}

    def labels(self):
        verified = self._verified()
        ids = {row["case_id"] for row in verified["inference_cases"] if row["split"] == self.split}
        return {case_id: paths for case_id, paths in verified["label_paths"].items() if case_id in ids}


@contextmanager
def open_test_split(root, *, split, authorization, purpose, release_sha256=None):
    """Release access still requires the user's fresh evaluation authorization.

    Without a release, delegate to the original source-cohort gate. The gate
    records authorization and access scope; it does not run predictions, metrics
    or model selection and is not OS-level isolation.
    """
    if (split not in {"main_test", "temporal_candidate"}
            or authorization != "evaluate held-out test"
            or not isinstance(purpose, str) or not purpose.strip()):
        raise PermissionError("Explicit holdout evaluation authorization and purpose required")
    root = Path(root).resolve()
    if release_sha256 is None:
        from issueintel.benchmarks.store import open_test_split as source_gate
        with source_gate(root, split=split, authorization=authorization, purpose=purpose) as capability:
            yield capability
        return
    capability = ReleaseTestCapability(root, split, release_sha256, purpose)
    verified = capability._verified()
    selected = [row for row in verified["manifest"]["cases"] if row["split"] == split]
    if not selected:
        raise PermissionError("The release has no eligible complete cases in this split")
    log = root / "data/runtime/evaluation_authorizations" / (uuid.uuid4().hex + ".json")
    write_json(log, {"created_at": datetime.now(timezone.utc).isoformat(), "split": split,
        "authorization": authorization, "purpose": purpose, "release_sha256": release_sha256,
        "manifest_sha256": verified["manifest"]["source"]["manifest"]["sha256"],
        "recipe_sha256": verified["manifest"]["recipe"]["recipe_sha256"],
        "selected_case_count": len(selected), "cohort_sha256": digest(selected),
        "authorization_scope": "release_eligible_complete_cases_only"})
    try:
        yield capability
    finally:
        capability.active = False
