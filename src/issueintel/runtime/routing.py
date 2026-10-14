"""Versioned DEV artifact routing; eligibility never constructs a provider."""
from dataclasses import dataclass

from .budget import BudgetDenied

SMALL = "openai/gpt-oss-20b"
LARGE = "openai/gpt-oss-120b"
ESCALATIONS = {"invalid_structured_output", "confidence_too_low", "verifier_disagrees",
              "evidence_conflicts", "retry_limit_reached"}
STAGES = {"simple_semantic", "generation", "critique", "judge", "variants"}


@dataclass(frozen=True)
class ModelRoute:
    model: str | None
    reasoning_effort: str | None
    category: str
    stage: str
    escalation_reason: str | None = None
    diagnostic: bool = False
    version: str = "main_dev_artifact_routing_v1"


def route(stage, *, frozen_dev_count=0, pilot_approved=False, escalation_reason=None,
          pilot_model=None):
    if stage == "eligibility":
        return ModelRoute(None, None, "DATA_ELIGIBILITY_COST", stage)
    if stage not in STAGES:
        raise BudgetDenied("Unknown stage; evaluation has no authorization in this phase")
    if not 1 <= frozen_dev_count <= 400:
        raise BudgetDenied("Artifact calls deferred until an original Main Dev strict cohort is frozen")
    if pilot_model is not None:
        if pilot_model not in {SMALL, LARGE}:
            raise BudgetDenied("Pilot model not authorized")
        return ModelRoute(pilot_model, "medium" if stage == "simple_semantic" else "high",
                          "ARTIFACT_GENERATION_COST", stage, diagnostic=True)
    if not pilot_approved:
        raise BudgetDenied("Stage-specific DEV pilot approval required before bulk routing")
    if escalation_reason is not None and escalation_reason not in ESCALATIONS:
        raise BudgetDenied("Escalation requires a concrete permitted reason")
    return ModelRoute(LARGE if escalation_reason else SMALL,
        "high" if escalation_reason or stage != "simple_semantic" else "medium",
        "ARTIFACT_GENERATION_COST", stage, escalation_reason)
