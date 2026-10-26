from __future__ import annotations

from itertools import permutations

import pytest

from app.domain.model import Delivery, Observation, Payment, RefundIntent
from app.domain.rules import evaluate

# \u20b91,000 payment. R1 is \u20b9300, R2 is \u20b9200. All amounts are paise.
PAYMENT = Payment("pay_A", 100_000)
R1 = RefundIntent("R1", "pay_A", 30_000)
R2 = RefundIntent("R2", "pay_A", 20_000)


def delivery(
    attempt_id: str,
    event_id: str,
    refund_id: str,
    status: str,
    amount_paise: int = 30_000,
    currency: str = "INR",
) -> Delivery:
    return Delivery(
        attempt_id,
        Observation(event_id, refund_id, status, amount_paise, currency),
    )


# --------------------------------------------------------------------------
# Healthy control: both processors agree when nothing goes wrong.
# --------------------------------------------------------------------------


def test_healthy_control_agrees():
    # A status transition is two events: E7 says pending, E8 says succeeded.
    # Reusing one event id for both would be an identity conflict, not a transition.
    deliveries = [
        delivery("a1", "E7", "R1", "pending"),
        delivery("a2", "E8", "R1", "succeeded"),
    ]

    report = evaluate(PAYMENT, [R1], deliveries)

    assert report.row("R1").protected_status == "succeeded"
    assert report.row("R1").protected_confirmed_paise == 30_000
    assert report.row("R1").naive_status == "succeeded"
    assert report.row("R1").naive_total_paise == 30_000
    assert report.protected_confirmed_paise == 30_000
    assert report.naive_total_paise == 30_000


# --------------------------------------------------------------------------
# Duplicates: three deliveries of one event, then two events saying one thing.
# --------------------------------------------------------------------------


def test_three_deliveries_of_one_event_count_once():
    deliveries = [
        delivery("a1", "E7", "R1", "succeeded"),
        delivery("a2", "E7", "R1", "succeeded"),
        delivery("a3", "E7", "R1", "succeeded"),
    ]

    report = evaluate(PAYMENT, [R1], deliveries)

    assert report.row("R1").protected_status == "succeeded"
    assert report.row("R1").protected_confirmed_paise == 30_000
    assert report.row("R1").naive_total_paise == 90_000
    assert report.protected_confirmed_paise == 30_000
    assert report.naive_total_paise == 90_000


def test_two_event_ids_claiming_the_same_success_count_once():
    deliveries = [
        delivery("a1", "E7", "R1", "succeeded"),
        delivery("a2", "E9", "R1", "succeeded"),
    ]

    report = evaluate(PAYMENT, [R1], deliveries)

    assert report.row("R1").protected_confirmed_paise == 30_000
    assert report.row("R1").naive_total_paise == 60_000


# --------------------------------------------------------------------------
# Reordering: a stale pending message must not move a refund backwards.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("order", "expected_naive_status", "expected_naive_total"),
    [
        (("pending", "succeeded"), "succeeded", 30_000),
        (("succeeded", "pending"), "pending", 30_000),
    ],
)
def test_pending_never_overrides_success(order, expected_naive_status, expected_naive_total):
    deliveries = [
        delivery("a%d" % index, "E%d" % (7 + index), "R1", status)
        for index, status in enumerate(order)
    ]

    report = evaluate(PAYMENT, [R1], deliveries)

    assert report.row("R1").protected_status == "succeeded"
    assert report.row("R1").protected_confirmed_paise == 30_000
    assert report.row("R1").naive_status == expected_naive_status
    assert report.row("R1").naive_total_paise == expected_naive_total


# --------------------------------------------------------------------------
# Terminal conflict: success and failure for the same refund.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("order", "expected_naive_status"),
    [
        (("succeeded", "failed"), "failed"),
        (("failed", "succeeded"), "succeeded"),
    ],
)
def test_success_and_failure_stay_under_review_in_both_orders(order, expected_naive_status):
    deliveries = [
        delivery("a%d" % index, "E%d" % (7 + index), "R1", status)
        for index, status in enumerate(order)
    ]

    report = evaluate(PAYMENT, [R1], deliveries)

    row = report.row("R1")
    assert row.protected_status == "review"
    assert row.protected_confirmed_paise == 0
    assert row.protected_review_paise == 30_000
    assert row.reasons == ("terminal_conflict",)

    # The naive side settles it, and its own status and total then disagree:
    # it reports "failed" while still holding \u20b9300.
    assert row.naive_status == expected_naive_status
    assert row.naive_total_paise == 30_000


# --------------------------------------------------------------------------
# Incorrect amounts and currencies reported by the provider.
# --------------------------------------------------------------------------


def test_amount_larger_than_the_intent_is_quarantined():
    deliveries = [delivery("a1", "E7", "R1", "succeeded", amount_paise=35_000)]

    report = evaluate(PAYMENT, [R1], deliveries)

    row = report.row("R1")
    assert row.protected_status == "review"
    assert row.protected_confirmed_paise == 0
    assert row.protected_review_paise == 30_000
    assert row.reasons == ("amount_mismatch",)
    assert row.naive_status == "succeeded"
    assert row.naive_total_paise == 35_000


def test_amount_smaller_than_the_intent_is_quarantined():
    deliveries = [delivery("a1", "E7", "R1", "succeeded", amount_paise=25_000)]

    report = evaluate(PAYMENT, [R1], deliveries)

    row = report.row("R1")
    assert row.protected_status == "review"
    assert row.reasons == ("amount_mismatch",)
    assert row.naive_total_paise == 25_000


def test_foreign_currency_is_quarantined():
    deliveries = [delivery("a1", "E7", "R1", "succeeded", currency="EUR")]

    report = evaluate(PAYMENT, [R1], deliveries)

    row = report.row("R1")
    assert row.protected_status == "review"
    assert row.protected_confirmed_paise == 0
    assert row.reasons == ("currency_mismatch",)


def test_one_event_id_with_two_different_payloads_is_an_identity_conflict():
    deliveries = [
        delivery("a1", "E7", "R1", "succeeded"),
        delivery("a2", "E7", "R1", "failed"),
    ]

    report = evaluate(PAYMENT, [R1], deliveries)

    row = report.row("R1")
    assert row.protected_status == "review"
    assert row.protected_confirmed_paise == 0
    assert row.reasons == ("event_identity_conflict",)


def test_one_event_id_used_for_two_refunds_is_an_identity_conflict():
    # One provider event id cannot describe two different refunds. Both refunds
    # are identifiable, so both go under review rather than being counted.
    deliveries = [
        delivery("a1", "E7", "R1", "succeeded", amount_paise=30_000),
        delivery("a2", "E7", "R2", "succeeded", amount_paise=20_000),
    ]

    report = evaluate(PAYMENT, [R1, R2], deliveries)

    assert report.row("R1").protected_status == "review"
    assert report.row("R2").protected_status == "review"
    assert report.row("R1").reasons == ("event_identity_conflict",)
    assert report.row("R2").reasons == ("event_identity_conflict",)
    assert report.protected_confirmed_paise == 0
    assert report.admitted_paise == 50_000
    # The naive side has no identity rule, so it counts both.
    assert report.naive_total_paise == 50_000


# --------------------------------------------------------------------------
# Separate partial refunds.
# --------------------------------------------------------------------------


def test_partial_refunds_stay_separate_and_a_duplicate_only_doubles_naive():
    deliveries = [
        delivery("a1", "E7", "R1", "succeeded", amount_paise=30_000),
        delivery("a2", "E7", "R1", "succeeded", amount_paise=30_000),
        delivery("a3", "E10", "R2", "succeeded", amount_paise=20_000),
    ]

    report = evaluate(PAYMENT, [R1, R2], deliveries)

    assert report.row("R1").protected_confirmed_paise == 30_000
    assert report.row("R2").protected_confirmed_paise == 20_000
    assert report.protected_confirmed_paise == 50_000
    assert report.admitted_paise == 50_000

    assert report.row("R1").naive_total_paise == 60_000
    assert report.row("R2").naive_total_paise == 20_000
    assert report.naive_total_paise == 80_000


def test_a_refund_that_failed_contributes_nothing_to_either_side():
    deliveries = [delivery("a1", "E7", "R1", "failed")]

    report = evaluate(PAYMENT, [R1, R2], deliveries)

    assert report.row("R1").protected_status == "failed"
    assert report.row("R1").protected_confirmed_paise == 0
    assert report.row("R1").naive_total_paise == 0
    # R2 was admitted but never observed.
    assert report.row("R2").protected_status == "pending"
    assert report.admitted_paise == 50_000
    assert report.protected_confirmed_paise == 0


# --------------------------------------------------------------------------
# Arrival order exhaustion.
# --------------------------------------------------------------------------


def test_pending_and_success_are_order_independent():
    for order in permutations(["pending", "succeeded"]):
        deliveries = [
            delivery("a%d" % index, "E%d" % (7 + index), "R1", status)
            for index, status in enumerate(order)
        ]

        report = evaluate(PAYMENT, [R1], deliveries)

        assert report.row("R1").protected_status == "succeeded", order
        assert report.row("R1").protected_confirmed_paise == 30_000, order
        assert report.row("R1").reasons == (), order


def test_three_way_conflict_is_review_in_all_six_orders():
    naive_statuses = set()

    for order in permutations(["pending", "succeeded", "failed"]):
        deliveries = [
            delivery("a%d" % index, "E%d" % (7 + index), "R1", status)
            for index, status in enumerate(order)
        ]

        report = evaluate(PAYMENT, [R1], deliveries)
        row = report.row("R1")

        assert row.protected_status == "review", order
        assert row.protected_confirmed_paise == 0, order
        assert row.protected_review_paise == 30_000, order
        assert row.reasons == ("terminal_conflict",), order
        naive_statuses.add(row.naive_status)

    # The naive answer moves with arrival order; the protected answer does not.
    assert len(naive_statuses) == 3


def test_protected_total_is_invariant_under_every_arrival_order():
    base = [
        delivery("a1", "E7", "R1", "pending"),
        delivery("a2", "E8", "R1", "succeeded"),
        delivery("a3", "E9", "R1", "succeeded"),
        delivery("a4", "E10", "R2", "succeeded", amount_paise=20_000),
    ]

    seen = set()
    for order in permutations(base):
        report = evaluate(PAYMENT, [R1, R2], order)
        seen.add(
            (
                report.protected_confirmed_paise,
                tuple(
                    (row.refund_id, row.protected_status, row.protected_confirmed_paise)
                    for row in report.rows
                ),
            )
        )

    assert seen == {
        (
            50_000,
            (
                ("R1", "succeeded", 30_000),
                ("R2", "succeeded", 20_000),
            ),
        )
    }


def test_unknown_refund_evidence_is_visible_but_changes_no_totals():
    deliveries = [
        delivery("a1", "E8", "R1", "succeeded"),
        delivery("a2", "E99", "R9", "succeeded", amount_paise=99_000),
    ]

    report = evaluate(PAYMENT, [R1], deliveries)

    assert [row.refund_id for row in report.rows] == ["R1"]
    assert report.protected_confirmed_paise == 30_000
    assert report.naive_total_paise == 30_000
    # The evidence is not dropped, it is reported separately.
    assert [observation.refund_id for observation in report.unmatched] == ["R9"]
    assert report.unmatched[0].event_id == "E99"


def test_unknown_refund_cannot_change_a_valid_refund():
    # Even when the unknown delivery reuses an event id, it must not turn the
    # admitted refund into a review. Unknown evidence is ingest's problem.
    deliveries = [
        delivery("a1", "E7", "R1", "succeeded"),
        delivery("a2", "E7", "R9", "succeeded", amount_paise=99_000),
    ]

    report = evaluate(PAYMENT, [R1], deliveries)

    assert report.row("R1").protected_status == "succeeded"
    assert report.row("R1").protected_confirmed_paise == 30_000
    assert report.protected_confirmed_paise == 30_000
    assert [observation.refund_id for observation in report.unmatched] == ["R9"]


def test_duplicate_attempt_ids_are_not_merged_by_the_reducer():
    """Attempt identity is the transport's problem; the reducer dedupes by event."""
    deliveries = [
        delivery("a1", "E7", "R1", "succeeded"),
        delivery("a1", "E7", "R1", "succeeded"),
    ]

    report = evaluate(PAYMENT, [R1], deliveries)

    assert report.row("R1").protected_confirmed_paise == 30_000
    assert report.row("R1").naive_total_paise == 60_000