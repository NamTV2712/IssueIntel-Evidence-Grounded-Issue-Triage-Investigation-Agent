"""Typed routing; remote decision models cannot directly execute tools."""
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class Action(str, Enum):
    RETRIEVE = "retrieve"
    ASK = "ask"
    VERIFY = "verify"
    STOP = "stop"


@dataclass(frozen=True)
class DecisionState:
    evidence_count: int
    hypotheses_count: int
    missing_information: tuple[str, ...] = ()
    rounds_remaining: int = 1


class RuleController:
    name = "rules"

    def decide(self, state: DecisionState, allowed: tuple[Action, ...]) -> Action:
        if state.evidence_count and state.hypotheses_count and state.rounds_remaining>0 and Action.RETRIEVE in allowed:
            return Action.RETRIEVE
        preferred = Action.ASK if state.evidence_count == 0 else Action.VERIFY if state.hypotheses_count else Action.RETRIEVE
        return preferred if preferred in allowed else Action.STOP


class JevController:
    """Injected decision-model port. Integration requires separately configured transport.

    Reusing the Groq spending authorization for an unrelated paid service is forbidden.
    Invalid responses fall back to rules; no automatic remote call is configured here.
    """
    name = "jev"

    def __init__(self, decide: Callable, fallback=None):
        self.remote_decide = decide
        self.fallback = fallback or RuleController()
        self.last_fallback = False

    def decide(self, state: DecisionState, allowed: tuple[Action, ...]) -> Action:
        self.last_fallback = False
        if not allowed:
            return Action.STOP
        try:
            response = self.remote_decide({"state": state.__dict__, "allowed": [a.value for a in allowed],
                "rubric": "Prefer information that can falsify a current hypothesis; ask when missing information prevents a grounded answer."})
            action = Action(response["action"])
            if action not in allowed:
                raise ValueError("forbidden action")
            return action
        except (KeyError, ValueError, TypeError, RuntimeError):
            self.last_fallback = True
            return self.fallback.decide(state, allowed)
