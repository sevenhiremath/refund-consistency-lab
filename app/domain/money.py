from __future__ import annotations

CURRENCY = "INR"


class MoneyError(ValueError):
    """Raised when an amount or currency is not acceptable lab input."""


def validate_paise(value: object, *, field: str = "amount_paise") -> int:
    """Accept only a positive int.

    bool is rejected explicitly because it is an int subclass in Python: without
    the check, True would silently become 1 paise.
    """
    if isinstance(value, bool):
        raise MoneyError("%s must be an integer, got bool" % field)
    if not isinstance(value, int):
        raise MoneyError("%s must be an integer, got %s" % (field, type(value).__name__))
    if value <= 0:
        raise MoneyError("%s must be positive, got %d" % (field, value))
    return value


def validate_currency(value: object) -> str:
    if value != CURRENCY:
        raise MoneyError("currency must be %s, got %r" % (CURRENCY, value))
    return CURRENCY


def validate_currency_code(value: object) -> str:
    """Looser check for provider-reported currency.

    Observations may legitimately report a currency we did not expect; that is a
    mismatch for the reducer to flag, not a construction error to hide.
    """
    if not isinstance(value, str) or not value.strip():
        raise MoneyError("currency must be a non-empty string, got %r" % (value,))
    return value


def format_paise(paise: int) -> str:
    return "\u20b9%d.%02d" % (paise // 100, paise % 100)