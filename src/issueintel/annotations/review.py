"""Blind human review sheets and declared-independent agreement summaries.

No source-human task-quality labels or automatic candidate correctness labels
are prefilled. Agreement is computed only from the new reviewers' actual rows.
"""
from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Mapping, Sequence
import io
import json
from pathlib import Path

from .models import Annotation, AnnotationError, require_text


REVIEW_FIELDS = [
    "case_id", "hypothesis_id", "statement", "support_ids", "counter_ids",
    "falsification_test", "reviewer_id", "reviewer_kind", "correctness",
    "abstention", "notes",
]


def export_review(
    annotations: Sequence[Annotation | Mapping],
    output: str | Path | None = None,
    *,
    reviewer_id: str = "",
) -> str:
    """Return/write a blind CSV. Reviewer fills labels and declares human status.

    The associated public evidence packet must be supplied separately. A blind
    sheet shows hypotheses and citations to judge, not their automatic labels.
    """
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=REVIEW_FIELDS)
    writer.writeheader()
    seen = set()
    for annotation in annotations:
        if not isinstance(annotation, Annotation):
            annotation = Annotation.from_dict(annotation)
        if annotation.case_id in seen:
            raise AnnotationError("Duplicate case IDs in review export")
        seen.add(annotation.case_id)
        for hypothesis in annotation.hypotheses:
            writer.writerow({
                "case_id": annotation.case_id, "hypothesis_id": hypothesis.id,
                "statement": hypothesis.statement,
                "support_ids": json.dumps(hypothesis.support_ids, ensure_ascii=False),
                "counter_ids": json.dumps(hypothesis.counter_ids, ensure_ascii=False),
                "falsification_test": hypothesis.falsification_test,
                "reviewer_id": reviewer_id, "reviewer_kind": "",
                "correctness": "", "abstention": "", "notes": "",
            })
    value = stream.getvalue()
    if output is not None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(value, encoding="utf-8", newline="")
    return value


def _read_rows(value: str | Path | Sequence[Mapping]) -> list[dict]:
    if isinstance(value, Path):
        value = value.read_text(encoding="utf-8-sig")
    elif isinstance(value, str) and "\n" not in value and "\r" not in value:
        value = Path(value).read_text(encoding="utf-8-sig")
    if isinstance(value, str):
        return list(csv.DictReader(io.StringIO(value)))
    if isinstance(value, Sequence):
        if any(not isinstance(row, Mapping) for row in value):
            raise AnnotationError("Review rows must be objects")
        return [dict(row) for row in value]
    raise AnnotationError("Review input must be CSV or a sequence of rows")


def _collect(value) -> tuple[dict, dict, str | None, int]:
    correctness, abstention = {}, {}
    all_rows = set()
    reviewer = None
    pending = 0
    for row in _read_rows(value):
        case = require_text(row.get("case_id"), "review case_id")
        hypothesis = require_text(row.get("hypothesis_id"), "review hypothesis_id")
        key = (case, hypothesis)
        if key in all_rows:
            raise AnnotationError("Duplicate case/hypothesis row in review")
        all_rows.add(key)
        correct = str(row.get("correctness") or "").strip()
        abstain = str(row.get("abstention") or "").strip()
        if not correct and not abstain:
            pending += 1
            continue
        identity = require_text(row.get("reviewer_id"), "reviewer_id")
        if row.get("reviewer_kind") != "human":
            raise AnnotationError("Independent human agreement requires reviewer_kind=human")
        if reviewer is not None and reviewer != identity:
            raise AnnotationError("One review input must have one reviewer identity")
        reviewer = identity
        if correct:
            if correct not in {"supported", "refuted", "uncertain"}:
                raise AnnotationError("Unknown human correctness label")
            correctness[key] = correct
        if abstain:
            if abstain not in {"answerable", "abstain", "uncertain"}:
                raise AnnotationError("Unknown human abstention label")
            if case in abstention and abstention[case] != abstain:
                raise AnnotationError("Conflicting case abstention labels within a review")
            abstention[case] = abstain
    return correctness, abstention, reviewer, pending


def _nominal(left: dict, right: dict, *, dimension: str) -> dict:
    common = sorted(set(left) & set(right))
    disagreements = []
    for key in common:
        if left[key] != right[key]:
            disagreements.append({
                "case_id": key[0] if isinstance(key, tuple) else key,
                **({"hypothesis_id": key[1]} if isinstance(key, tuple) else {}),
                "left": left[key], "right": right[key],
            })
    count = len(common)
    matches = count - len(disagreements)
    kappa = None
    status = "no_paired_labels"
    if count:
        a = Counter(left[key] for key in common)
        b = Counter(right[key] for key in common)
        expected = sum(a[label] * b[label] for label in set(a) | set(b)) / (count * count)
        if expected < 1:
            kappa = ((matches / count) - expected) / (1 - expected)
            status = "defined"
        else:
            status = "undefined_constant_labels"
    return {
        "dimension": dimension, "paired": count, "agreed": matches,
        "agreement": matches / count if count else None,
        "cohen_kappa": kappa, "kappa_status": status,
        "left_unpaired": len(set(left) - set(right)),
        "right_unpaired": len(set(right) - set(left)),
        "disagreements": disagreements,
    }


def score_agreement(left, right) -> dict:
    """Cohen's nominal kappa for two *declared* distinct human reviewers.

    Correctness is scored per hypothesis. Abstention is scored per case, so
    repeating the same case label over 3–5 hypotheses never inflates its count.
    Missing rows are reported, not silently turned into disagreements.
    """
    left_correct, left_abstain, left_id, left_pending = _collect(left)
    right_correct, right_abstain, right_id, right_pending = _collect(right)
    if left_id is not None and left_id == right_id:
        raise AnnotationError("Independent agreement requires distinct reviewer IDs")
    correctness = _nominal(left_correct, right_correct, dimension="correctness")
    abstention = _nominal(left_abstain, right_abstain, dimension="abstention")
    paired = correctness["paired"] + abstention["paired"]
    return {
        "schema_version": "issueintel.agreement.v1",
        "status": "scored_declared_human_reviews" if paired else "pending_no_independent_pairs",
        "reviewers": [left_id, right_id],
        "independence": "distinct reviewer identities declared; identity and independence are not externally verified",
        "correctness": correctness, "abstention": abstention,
        "pending_rows": {"left": left_pending, "right": right_pending},
        "limitation": "Source SWE-bench human screening does not validate these new hypotheses or abstention labels.",
    }
