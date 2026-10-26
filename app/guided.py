from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    slug: str
    scenario: str
    title: str
    subtitle: str
    claim: str
    watch: str
    side_note: str


# Presentation only. The scenario ids are the internal fixture names, the rest is
# copy for a guided walkthrough. Nothing here carries an expected total: every
# number on the page still comes from the run report.
GUIDED_STEPS: tuple[Step, ...] = (
    Step(
        slug="duplicate",
        scenario="duplicate_success",
        title="The same notification, three times",
        subtitle="₹300 refund notified three times",
        claim=(
            "The provider sent one success notice for a ₹300 refund, and it arrived "
            "three times. Each arrival is a separate delivery of the same event."
        ),
        watch=(
            "Apply the deliveries and the two local records drift apart: the naive "
            "side counts every arrival, the protected side counts the event once. "
            "These are records in this app, not three payouts. The provider sent ₹300 "
            "once."
        ),
        side_note=(
            "No reconciliation step is needed here. Repeated notifications are handled "
            "by the duplicate rule, which is why the protected side never moves."
        ),
    ),
    Step(
        slug="missing",
        scenario="lookup_recovers_success",
        title="The notification that never arrived",
        subtitle="₹300 refund, success notice dropped",
        claim=(
            "The provider completed the refund, but the success notification never "
            "reached this app. No processor has evidence of it."
        ),
        watch=(
            "Nothing can be recovered by rereading what we have, because the message "
            "was never delivered. Run the provider lookup and watch the refund move "
            "from unknown to confirmed."
        ),
        side_note=(
            "The lookup is an explicit request. The outcome stays unknown until it "
            "returns evidence, which is why a timeout or a not-found answer does not "
            "become a failure."
        ),
    ),
    Step(
        slug="conflict",
        scenario="lookup_conflict",
        title="When the provider contradicts itself",
        subtitle="₹300 refund, failure notice then a later success",
        claim=(
            "A failure notification arrived, and the provider later reported success "
            "for the same refund. Both are real observations."
        ),
        watch=(
            "After the failure arrives the naive side settles on failed. Add the "
            "provider lookup and the protected side moves to review instead of "
            "picking a winner, keeping the amount visible as unresolved."
        ),
        side_note=(
            "Reconciling does not resolve this one. The lookup adds the second claim, "
            "and the refund stays under review because the evidence disagrees and "
            "recency is not proof."
        ),
    ),
)

GUIDED_BY_SLUG = {step.slug: step for step in GUIDED_STEPS}
GUIDED_BY_SCENARIO = {step.scenario: step for step in GUIDED_STEPS}

# Friendly names for the full list. Internal ids stay the keys everywhere else.
SCENARIO_LABELS = {
    "healthy": "Healthy control",
    "duplicate_success": "Duplicate notifications",
    "pending_after_success": "Stale pending after success",
    "dropped_success": "Success notice never arrives",
    "lookup_recovers_success": "Success recovered by lookup",
    "lookup_unavailable": "Lookup times out",
    "lookup_wrong_amount": "Lookup reports the wrong amount",
    "lookup_conflict": "Conflicting results",
    "conflict_success_first": "Conflict, success first",
    "conflict_failure_first": "Conflict, failure first",
    "partials_duplicate": "Two partial refunds, one duplicated",
    "identity_conflict_refunds": "One event id for two refunds",
}


def label_for(scenario: str) -> str:
    return SCENARIO_LABELS.get(scenario, scenario)


def guided_step(scenario: str) -> Step | None:
    return GUIDED_BY_SCENARIO.get(scenario)