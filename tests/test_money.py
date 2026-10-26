from __future__ import annotations

import pytest

from app.domain.money import MoneyError, format_paise, validate_currency, validate_paise
from app.domain.model import Observation, Payment, RefundIntent


def test_positive_integer_paise_is_accepted():
    assert validate_paise(30_000) == 30_000


@pytest.mark.parametrize("bad", [True, False])
def test_boolean_is_not_money(bad):
    with pytest.raises(MoneyError):
        validate_paise(bad)


@pytest.mark.parametrize("bad", [300.0, "30000", None])
def test_non_integer_is_rejected(bad):
    with pytest.raises(MoneyError):
        validate_paise(bad)


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_is_rejected(bad):
    with pytest.raises(MoneyError):
        validate_paise(bad)


def test_currency_must_be_inr_for_intents():
    assert validate_currency("INR") == "INR"
    with pytest.raises(MoneyError):
        validate_currency("EUR")


def test_payment_rejects_float_capture():
    with pytest.raises(MoneyError):
        Payment("pay_A", 1000.0)


def test_observation_may_report_a_foreign_currency():
    """A currency we did not expect is a mismatch to flag, not a parse error."""
    observation = Observation("E7", "R1", "succeeded", 30_000, "EUR")

    assert observation.currency == "EUR"


def test_refund_intent_rejects_unknown_status_via_observation():
    with pytest.raises(ValueError):
        Observation("E7", "R1", "refunded", 30_000)


def test_intent_amount_is_positive_paise():
    intent = RefundIntent("R1", "pay_A", 30_000)

    assert intent.amount_paise == 30_000
    assert intent.currency == "INR"


@pytest.mark.parametrize(
    ("paise", "rendered"),
    [(30_000, "\u20b9300.00"), (20_000, "\u20b9200.00"), (35_050, "\u20b9350.50"), (1, "\u20b90.01")],
)
def test_rendering(paise, rendered):
    assert format_paise(paise) == rendered