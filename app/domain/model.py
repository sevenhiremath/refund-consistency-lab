from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.domain.money import (
    CURRENCY,
    validate_currency,
    validate_currency_code,
    validate_paise,
)

STATUS_PENDING = "pending"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
PROVIDER_STATUSES = (STATUS_PENDING, STATUS_SUCCEEDED, STATUS_FAILED)

CANONICAL_PENDING = STATUS_PENDING
CANONICAL_SUCCEEDED = STATUS_SUCCEEDED
CANONICAL_FAILED = STATUS_FAILED
CANONICAL_REVIEW = "review"


@dataclass(frozen=True)
class Payment:
    payment_id: str
    captured_paise: int
    currency: str = CURRENCY

    def __post_init__(self) -> None:
        if not self.payment_id:
            raise ValueError("payment_id is required")
        validate_paise(self.captured_paise, field="captured_paise")
        validate_currency(self.currency)


@dataclass(frozen=True)
class RefundIntent:
    """The business operation. Immutable: the amount is never rewritten by an event."""

    refund_id: str
    payment_id: str
    amount_paise: int
    currency: str = CURRENCY

    def __post_init__(self) -> None:
        if not self.refund_id:
            raise ValueError("refund_id is required")
        if not self.payment_id:
            raise ValueError("payment_id is required")
        validate_paise(self.amount_paise, field="amount_paise")
        validate_currency(self.currency)


@dataclass(frozen=True)
class Observation:
    """One provider claim about a refund, identified by provider event id.

    Repeats of the same event id are the same claim. Two different event ids may
    carry equivalent claims; that is a semantic duplicate, not an error.
    """

    event_id: str
    refund_id: str
    status: str
    amount_paise: int
    currency: str = CURRENCY

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id is required")
        if not self.refund_id:
            raise ValueError("refund_id is required")
        if self.status not in PROVIDER_STATUSES:
            raise ValueError("unknown provider status %r" % (self.status,))
        validate_paise(self.amount_paise, field="amount_paise")
        validate_currency_code(self.currency)


@dataclass(frozen=True)
class Delivery:
    """One transport delivery of one event. Retries are new attempts of the same event.

    origin separates a notification the provider pushed from evidence we pulled with
    a lookup. The naive baseline is webhook-only, so it must never see the second.
    """

    attempt_id: str
    observation: Observation
    origin: str = "webhook"

    def __post_init__(self) -> None:
        if not self.attempt_id:
            raise ValueError("attempt_id is required")
        if self.origin not in ("webhook", "lookup"):
            raise ValueError("unknown delivery origin %r" % (self.origin,))

    @property
    def event_id(self) -> str:
        return self.observation.event_id

    @property
    def refund_id(self) -> str:
        return self.observation.refund_id


@dataclass(frozen=True)
class RefundState:
    refund_id: str
    status: str
    confirmed_paise: int
    review_paise: int
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class NaiveRefund:
    refund_id: str
    status: str
    total_paise: int


@dataclass(frozen=True)
class AdmissionResult:
    accepted: bool
    reason: str
    intents: tuple[RefundIntent, ...]

    @property
    def admitted_paise(self) -> int:
        return sum(intent.amount_paise for intent in self.intents)


@dataclass(frozen=True)
class RefundComparison:
    refund_id: str
    amount_paise: int
    protected_status: str
    protected_confirmed_paise: int
    protected_review_paise: int
    naive_status: str
    naive_total_paise: int
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class PaymentReport:
    payment_id: str
    captured_paise: int
    admitted_paise: int
    protected_confirmed_paise: int
    protected_review_paise: int
    naive_total_paise: int
    rows: tuple[RefundComparison, ...]
    unmatched: tuple[Observation, ...] = ()

    def row(self, refund_id: str) -> RefundComparison:
        for row in self.rows:
            if row.refund_id == refund_id:
                return row
        raise KeyError(refund_id)


def sort_intents(intents: Sequence[RefundIntent]) -> tuple[RefundIntent, ...]:
    return tuple(sorted(intents, key=lambda intent: intent.refund_id))