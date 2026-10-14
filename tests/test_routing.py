import pytest
from issueintel.runtime.budget import BudgetDenied
from issueintel.runtime.routing import route, SMALL, LARGE, ESCALATIONS


def test_eligibility_never_uses_model_even_after_freeze():
    assert route("eligibility", frozen_dev_count=250).model is None


@pytest.mark.parametrize("count", [0, 401, 500])
def test_artifact_calls_denied_before_frozen_target(count):
    with pytest.raises(BudgetDenied, match="deferred"):
        route("generation", frozen_dev_count=count, pilot_approved=True)


def test_bulk_is_blocked_until_stage_pilot():
    with pytest.raises(BudgetDenied, match="pilot"):
        route("generation", frozen_dev_count=250)
    assert route("generation", frozen_dev_count=250, pilot_model=SMALL).diagnostic
    assert route("generation", frozen_dev_count=250, pilot_model=LARGE).diagnostic


def test_small_is_default_and_effort_is_stage_specific():
    assert route("generation", frozen_dev_count=250, pilot_approved=True).model == SMALL
    assert route("generation", frozen_dev_count=250, pilot_approved=True).reasoning_effort == "high"
    assert route("simple_semantic", frozen_dev_count=250, pilot_approved=True).reasoning_effort == "medium"


@pytest.mark.parametrize("reason", sorted(ESCALATIONS))
def test_failure_escalates_to_large_with_reason(reason):
    r = route("generation", frozen_dev_count=250, pilot_approved=True, escalation_reason=reason)
    assert r.model == LARGE and r.reasoning_effort == "high" and r.escalation_reason == reason


def test_no_cosmetic_escalation_and_no_evaluation_route():
    with pytest.raises(BudgetDenied, match="concrete"):
        route("critique", frozen_dev_count=250, pilot_approved=True, escalation_reason="make prettier")
    with pytest.raises(BudgetDenied, match="evaluation"):
        route("evaluation", frozen_dev_count=250, pilot_approved=True)

