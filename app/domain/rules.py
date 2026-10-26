from __future__ import annotations

from collections import defaultdict
from typing import Collection, Iterable, Sequence

from app.domain.model import (
    CANONICAL_FAILED,
    CANONICAL_PENDING,
    CANONICAL_REVIEW,
    CANONICAL_SUCCEEDED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUCCEEDED,
    AdmissionResult,
    Delivery,
    NaiveRefund,
    Observation,
    Payment,
    PaymentReport,
    RefundComparison,
    RefundIntent,
    RefundState,
)
from app.domain.naive import run_naive

REASON_TERMINAL_CONFLICT = "terminal_conflict"
REASON_AMOUNT_MISMATCH = "amount_mismatch"
REASON_CURRENCY_MISMATCH = "currency_mismatch"
REASON_EVENT_IDENTITY_CONFLICT = "event_identity_conflict"


def admit(
    payment: Payment,
    intents: Sequence[RefundIntent],
    candidate: RefundIntent,
) -> AdmissionResult:
    """Admit a refund intent, or refuse it before the provider is ever asked.

    The cap is checked here, at admission, not when a credit is posted. By the
    time a success arrives the provider has already moved the money, so a check
    at credit time can only report an over-refund, never prevent one.

    Reservations are never released during a run: a failed refund keeps consuming
    capacity. That is deliberately conservative and documented in the README.
    """
    if candidate.payment_id != payment.payment_id:
        return AdmissionResult(False, "unknown_payment", tuple(intents))

    for existing in intents:
        if existing.refund_id != candidate.refund_id:
            continue
        if existing == candidate:
            return AdmissionResult(True, "already_admitted", tuple(intents))
        return AdmissionResult(False, "refund_id_conflict", tuple(intents))

    admitted = sum(
        intent.amount_paise
        for intent in intents
        if intent.payment_id == payment.payment_id
    )
    if admitted + candidate.amount_paise > payment.captured_paise:
        return AdmissionResult(False, "over_refund", tuple(intents))

    return AdmissionResult(True, "accepted", tuple(intents) + (candidate,))


def _claims(observations: Iterable[Observation]) -> dict[str, set[tuple[str, str, int, str]]]:
    claims: dict[str, set[tuple[str, str, int, str]]] = defaultdict(set)
    for observation in observations:
        claims[observation.event_id].add(
            (
                observation.refund_id,
                observation.status,
                observation.amount_paise,
                observation.currency,
            )
        )
    return claims


def conflicted_event_ids(observations: Iterable[Observation]) -> frozenset[str]:
    """Event ids that carry more than one claim.

    A provider event id is one fact. If the same id describes two different refunds,
    or two different payloads for one refund, the identity cannot be trusted. Every
    refund it touches goes under review rather than being counted.
    """
    return frozenset(
        event_id
        for event_id, claims in _claims(observations).items()
        if len(claims) > 1
    )


def classify(
    intent: RefundIntent,
    observations: Iterable[Observation],
    run_conflicts: Collection[str] = (),
) -> RefundState:
    """Derive one refund's state from the *set* of evidence for it.

    Arrival order is never an input. Pending cannot supersede a terminal outcome,
    and two contradictory terminal outcomes stay unresolved rather than being
    settled by whichever arrived last.

    ``run_conflicts`` carries event ids already found to be conflicted elsewhere in
    the run. A collision that spans two refunds cannot be seen from one refund's
    evidence, so the caller supplies it.
    """
    by_event: dict[str, list[Observation]] = defaultdict(list)
    observed: list[Observation] = []
    for observation in observations:
        if observation.refund_id != intent.refund_id:
            raise ValueError(
                "observation for %s passed to %s"
                % (observation.refund_id, intent.refund_id)
            )
        by_event[observation.event_id].append(observation)
        observed.append(observation)

    # One event id carrying two different claims means the provider identity is
    # unreliable. Do not pick whichever variant arrived first.
    conflicts = set(run_conflicts) | conflicted_event_ids(observed)
    if any(observation.event_id in conflicts for observation in observed):
        return RefundState(
            refund_id=intent.refund_id,
            status=CANONICAL_REVIEW,
            confirmed_paise=0,
            review_paise=intent.amount_paise,
            reasons=(REASON_EVENT_IDENTITY_CONFLICT,),
        )

    unique = [variants[0] for variants in by_event.values()]

    mismatches = []
    if any(obs.amount_paise != intent.amount_paise for obs in unique):
        mismatches.append(REASON_AMOUNT_MISMATCH)
    if any(obs.currency != intent.currency for obs in unique):
        mismatches.append(REASON_CURRENCY_MISMATCH)
    if mismatches:
        return RefundState(
            refund_id=intent.refund_id,
            status=CANONICAL_REVIEW,
            confirmed_paise=0,
            review_paise=intent.amount_paise,
            reasons=tuple(sorted(mismatches)),
        )

    statuses = {obs.status for obs in unique}

    if STATUS_SUCCEEDED in statuses and STATUS_FAILED in statuses:
        return RefundState(
            refund_id=intent.refund_id,
            status=CANONICAL_REVIEW,
            confirmed_paise=0,
            review_paise=intent.amount_paise,
            reasons=(REASON_TERMINAL_CONFLICT,),
        )

    if STATUS_SUCCEEDED in statuses:
        return RefundState(
            refund_id=intent.refund_id,
            status=CANONICAL_SUCCEEDED,
            confirmed_paise=intent.amount_paise,
            review_paise=0,
        )

    if STATUS_FAILED in statuses:
        return RefundState(
            refund_id=intent.refund_id,
            status=CANONICAL_FAILED,
            confirmed_paise=0,
            review_paise=0,
        )

    return RefundState(
        refund_id=intent.refund_id,
        status=CANONICAL_PENDING,
        confirmed_paise=0,
        review_paise=0,
    )


def evaluate(
    payment: Payment,
    intents: Sequence[RefundIntent],
    deliveries: Sequence[Delivery],
) -> PaymentReport:
    """Run both processors over the same deliveries and return one report."""
    scoped = tuple(
        sorted(
            (intent for intent in intents if intent.payment_id == payment.payment_id),
            key=lambda intent: intent.refund_id,
        )
    )
    scoped_ids = {intent.refund_id for intent in scoped}

    observations: dict[str, list[Observation]] = defaultdict(list)
    all_observations: list[Observation] = []
    for delivery in deliveries:
        observations[delivery.refund_id].append(delivery.observation)
        all_observations.append(delivery.observation)

    # Event identity is a run-level property: the same id used for two refunds is
    # invisible from inside either refund, so it is computed once here. Evidence for
    # unadmitted refunds is excluded so it cannot move a valid total.
    admitted_observations = [
        observation
        for observation in all_observations
        if observation.refund_id in scoped_ids
    ]
    run_conflicts = conflicted_event_ids(admitted_observations)

    naive = run_naive(scoped, deliveries)

    rows = []
    for intent in scoped:
        state = classify(
            intent,
            observations.get(intent.refund_id, ()),
            run_conflicts,
        )
        naive_state = naive.get(
            intent.refund_id,
            NaiveRefund(intent.refund_id, STATUS_PENDING, 0),
        )
        rows.append(
            RefundComparison(
                refund_id=intent.refund_id,
                amount_paise=intent.amount_paise,
                protected_status=state.status,
                protected_confirmed_paise=state.confirmed_paise,
                protected_review_paise=state.review_paise,
                naive_status=naive_state.status,
                naive_total_paise=naive_state.total_paise,
                reasons=state.reasons,
            )
        )

    # Evidence for refunds that were never admitted is kept, not dropped. It must
    # not move any total; surfacing it is the report's job.
    unmatched: list[Observation] = []
    seen: set[tuple[str, str, str, int, str]] = set()
    for observation in all_observations:
        if observation.refund_id in scoped_ids:
            continue
        key = (
            observation.event_id,
            observation.refund_id,
            observation.status,
            observation.amount_paise,
            observation.currency,
        )
        if key in seen:
            continue
        seen.add(key)
        unmatched.append(observation)
    unmatched.sort(key=lambda observation: (observation.refund_id, observation.event_id))

    return PaymentReport(
        payment_id=payment.payment_id,
        captured_paise=payment.captured_paise,
        admitted_paise=sum(intent.amount_paise for intent in scoped),
        protected_confirmed_paise=sum(row.protected_confirmed_paise for row in rows),
        protected_review_paise=sum(row.protected_review_paise for row in rows),
        naive_total_paise=sum(row.naive_total_paise for row in rows),
        rows=tuple(rows),
        unmatched=tuple(unmatched),
    )