from __future__ import annotations

import pytest

from app.domain.model import Payment, RefundIntent
from app.domain.rules import admit

PAYMENT = Payment("pay_A", 100_000)
R1 = RefundIntent("R1", "pay_A", 30_000)
R2 = RefundIntent("R2", "pay_A", 20_000)


def test_two_partial_refunds_are_admitted_separately():
    first = admit(PAYMENT, (), R1)
    second = admit(PAYMENT, first.intents, R2)

    assert first.accepted is True
    assert first.reason == "accepted"
    assert second.accepted is True
    assert second.admitted_paise == 50_000
    assert [intent.refund_id for intent in second.intents] == ["R1", "R2"]


def test_admission_exactly_at_the_captured_amount_is_allowed():
    result = admit(PAYMENT, (R1,), RefundIntent("R2", "pay_A", 70_000))

    assert result.accepted is True
    assert result.admitted_paise == 100_000


def test_one_paise_over_the_captured_amount_is_refused():
    result = admit(PAYMENT, (R1,), RefundIntent("R2", "pay_A", 70_001))

    assert result.accepted is False
    assert result.reason == "over_refund"
    assert result.admitted_paise == 30_000
    assert [intent.refund_id for intent in result.intents] == ["R1"]


def test_third_refund_beyond_the_total_is_refused():
    after_two = admit(PAYMENT, (R1,), R2)

    result = admit(PAYMENT, after_two.intents, RefundIntent("R3", "pay_A", 60_000))

    assert result.accepted is False
    assert result.reason == "over_refund"
    assert result.admitted_paise == 50_000


def test_repeating_an_identical_request_returns_the_original():
    result = admit(PAYMENT, (R1,), RefundIntent("R1", "pay_A", 30_000))

    assert result.accepted is True
    assert result.reason == "already_admitted"
    assert len(result.intents) == 1


def test_same_refund_id_with_a_different_amount_is_a_conflict():
    result = admit(PAYMENT, (R1,), RefundIntent("R1", "pay_A", 35_000))

    assert result.accepted is False
    assert result.reason == "refund_id_conflict"
    assert result.admitted_paise == 30_000


def test_refund_for_an_unknown_payment_is_refused():
    result = admit(PAYMENT, (), RefundIntent("R1", "pay_other", 30_000))

    assert result.accepted is False
    assert result.reason == "unknown_payment"
    assert result.intents == ()


@pytest.mark.parametrize("amount", [0, -1])
def test_non_positive_refund_cannot_be_constructed(amount):
    with pytest.raises(ValueError):
        RefundIntent("R1", "pay_A", amount)