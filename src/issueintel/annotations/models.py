"""Validated annotation overlays, separate from public investigation inputs.

Correctness is a label with a declared source, never an implication of a gold
patch or of SWE-bench's original human task-quality screening.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from collections.abc import Mapping, Sequence
from typing import Any


class AnnotationError(ValueError):
    """An annotation or its cited evidence does not satisfy the contract."""


def require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnnotationError(f"{name} must be a nonempty string")
    return value.strip()


def strict_fields(value: Any, allowed: set[str], name: str) -> dict:
    if not isinstance(value, Mapping):
        raise AnnotationError(f"{name} must be an object")
    extras = set(value) - allowed
    if extras:
        raise AnnotationError(f"Unexpected {name} fields: {sorted(extras)}")
    return dict(value)


def required_fields(value: Mapping, required: set[str], name: str) -> None:
    missing = required - set(value)
    if missing:
        raise AnnotationError(f"Missing {name} fields: {sorted(missing)}")


def id_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise AnnotationError(f"{name} must be a list")
    ids = [require_text(item, name) for item in value]
    if len(ids) != len(set(ids)):
        raise AnnotationError(f"{name} contains duplicate IDs")
    return ids


@dataclass(frozen=True)
class Provenance:
    kind: str
    method: str
    actor: str | None = None
    provider: str | None = None
    model: str | None = None
    source: str | None = None
    created_at: str | None = None
    repair_count: int = 0

    def __post_init__(self):
        if not isinstance(self.kind, str) or self.kind not in {"llm", "automatic", "synthetic", "human", "source_human"}:
            raise AnnotationError("Unknown provenance kind")
        require_text(self.method, "provenance.method")
        if type(self.repair_count) is not int or not 0 <= self.repair_count <= 1:
            raise AnnotationError("Provenance repair_count must be 0 or 1")
        for name in ("actor", "provider", "model", "source", "created_at"):
            value = getattr(self, name)
            if value is not None:
                require_text(value, f"provenance.{name}")
        if self.kind == "human" and not self.actor:
            raise AnnotationError("Human provenance requires an actor")
        if self.kind == "source_human" and not self.source:
            raise AnnotationError("Imported human provenance requires a source")
        if self.kind == "llm" and (not self.provider or not self.model):
            raise AnnotationError("LLM provenance requires provider and model")

    @classmethod
    def from_dict(cls, value: Mapping) -> "Provenance":
        value = strict_fields(value, set(cls.__dataclass_fields__), "provenance")
        required_fields(value, {"kind", "method"}, "provenance")
        return cls(**value)


@dataclass(frozen=True)
class Hypothesis:
    id: str
    statement: str
    support_ids: list[str]
    counter_ids: list[str]
    counter_status: str
    falsification_test: str
    correctness: str = "unverified"
    correctness_provenance: Provenance | None = None
    correctness_evidence_ids: list[str] = field(default_factory=list)

    def __post_init__(self):
        require_text(self.id, "hypothesis.id")
        require_text(self.statement, "hypothesis.statement")
        require_text(self.falsification_test, "hypothesis.falsification_test")
        support = id_list(self.support_ids, "support_ids")
        counter = id_list(self.counter_ids, "counter_ids")
        if not support:
            raise AnnotationError("Every hypothesis requires supporting evidence")
        if set(support) & set(counter):
            raise AnnotationError("A hypothesis cannot cite an ID as both support and counter-evidence")
        if not isinstance(self.counter_status, str) or self.counter_status not in {"found", "not_found"}:
            raise AnnotationError("counter_status must be found or not_found")
        if bool(counter) != (self.counter_status == "found"):
            raise AnnotationError("Counter IDs and counter_status disagree")
        if not isinstance(self.correctness, str) or self.correctness not in {"unverified", "supported", "refuted", "uncertain"}:
            raise AnnotationError("Unknown correctness label")
        if self.correctness == "unverified" and self.correctness_provenance is not None:
            raise AnnotationError("Unverified correctness has no judge provenance")
        if self.correctness != "unverified" and self.correctness_provenance is None:
            raise AnnotationError("Judged correctness requires provenance")
        if self.correctness_provenance is not None and not isinstance(self.correctness_provenance, Provenance):
            raise AnnotationError("Correctness provenance must be a Provenance object")
        judge_ids = id_list(self.correctness_evidence_ids, "correctness_evidence_ids")
        if self.correctness == "unverified" and judge_ids:
            raise AnnotationError("Unverified correctness may not have judge citations")
        if self.correctness in {"supported", "refuted"} and not judge_ids:
            raise AnnotationError("Definite correctness labels require judge evidence citations")

    @classmethod
    def from_dict(cls, value: Mapping) -> "Hypothesis":
        value = strict_fields(value, set(cls.__dataclass_fields__), "hypothesis")
        required_fields(value, {"id", "statement", "support_ids", "counter_ids", "counter_status", "falsification_test"}, "hypothesis")
        if value.get("correctness_provenance") is not None:
            value["correctness_provenance"] = Provenance.from_dict(value["correctness_provenance"])
        return cls(**value)


@dataclass(frozen=True)
class AbstentionLabel:
    label: str
    reason: str
    provenance: Provenance

    def __post_init__(self):
        if not isinstance(self.label, str) or self.label not in {"unverified", "answerable", "abstain", "uncertain"}:
            raise AnnotationError("Unknown abstention label")
        require_text(self.reason, "abstention.reason")
        if not isinstance(self.provenance, Provenance):
            raise AnnotationError("Abstention provenance must be a Provenance object")

    @classmethod
    def from_dict(cls, value: Mapping) -> "AbstentionLabel":
        value = strict_fields(value, set(cls.__dataclass_fields__), "abstention")
        required_fields(value, {"label", "reason", "provenance"}, "abstention")
        value["provenance"] = Provenance.from_dict(value["provenance"])
        return cls(**value)


@dataclass(frozen=True)
class Annotation:
    case_id: str
    hypotheses: list[Hypothesis]
    abstention: AbstentionLabel
    provenance: Provenance
    evidence_ids: list[str]
    schema_version: str = "issueintel.annotation.v1"
    source_quality: dict = field(default_factory=dict)
    judgment: dict = field(default_factory=dict)

    def __post_init__(self):
        require_text(self.case_id, "case_id")
        if self.schema_version != "issueintel.annotation.v1":
            raise AnnotationError("Unsupported annotation schema")
        if not isinstance(self.abstention, AbstentionLabel) or not isinstance(self.provenance, Provenance):
            raise AnnotationError("Annotation labels and provenance must use their typed schema")
        if not isinstance(self.hypotheses, (list, tuple)) or not 3 <= len(self.hypotheses) <= 5:
            raise AnnotationError("Annotations require 3–5 competing hypotheses")
        if any(not isinstance(item, Hypothesis) for item in self.hypotheses):
            raise AnnotationError("hypotheses must contain Hypothesis objects")
        ids = [item.id for item in self.hypotheses]
        claims = [" ".join(item.statement.casefold().split()) for item in self.hypotheses]
        if len(ids) != len(set(ids)) or len(claims) != len(set(claims)):
            raise AnnotationError("Hypothesis IDs and statements must be distinct")
        known = set(id_list(self.evidence_ids, "evidence_ids"))
        for hypothesis in self.hypotheses:
            unknown = (set(hypothesis.support_ids) | set(hypothesis.counter_ids)) - known
            if unknown:
                raise AnnotationError(f"Unknown cited evidence IDs: {sorted(unknown)}")
        if not isinstance(self.source_quality, dict):
            raise AnnotationError("source_quality must be an object")
        if not isinstance(self.judgment, dict):
            raise AnnotationError("judgment must be an object")

    def validate_evidence(self, evidence: Sequence[Mapping]) -> "Annotation":
        ids = evidence_ids(evidence)
        if set(ids) != set(self.evidence_ids):
            raise AnnotationError("Annotation evidence inventory differs from supplied evidence")
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping) -> "Annotation":
        value = strict_fields(value, set(cls.__dataclass_fields__), "annotation")
        required_fields(value, {"case_id", "hypotheses", "abstention", "provenance", "evidence_ids"}, "annotation")
        if not isinstance(value["hypotheses"], (list, tuple)):
            raise AnnotationError("hypotheses must be a list")
        value["hypotheses"] = [Hypothesis.from_dict(item) for item in value["hypotheses"]]
        value["abstention"] = AbstentionLabel.from_dict(value["abstention"])
        value["provenance"] = Provenance.from_dict(value["provenance"])
        return cls(**value)


def evidence_ids(evidence: Sequence[Mapping]) -> list[str]:
    if isinstance(evidence, (str, bytes)) or not isinstance(evidence, Sequence):
        raise AnnotationError("Evidence must be a sequence of objects")
    ids = []
    for item in evidence:
        if not isinstance(item, Mapping):
            raise AnnotationError("Evidence must contain objects")
        ids.append(require_text(item.get("id"), "evidence.id"))
    if len(ids) != len(set(ids)):
        raise AnnotationError("Duplicate evidence IDs")
    return ids
