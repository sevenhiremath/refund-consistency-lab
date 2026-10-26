from __future__ import annotations

from typing import Sequence

from app.domain.model import (
    STATUS_PENDING,
    STATUS_SUCCEEDED,
    Delivery,
    NaiveRefund,
    RefundIntent,
)


def run_naive(
    intents: Sequence[RefundIntent],
    deliveries: Sequence[Delivery],
) -> dict[str, NaiveRefund]:
    """The deliberately naive baseline.

    Two mistakes, both of which a competent first implementation makes:

    1. Add the refund amount on every success notification, instead of deriving
       the total from the refund. Three deliveries of one success become 3x.
    2. Let the most recent arrival decide the status, so a stale pending message
       moves a refund backwards.

    Nothing here is scenario-specific sabotage. It is the obvious implementation.
    """
    status = {intent.refund_id: STATUS_PENDING for intent in intents}
    totals = {intent.refund_id: 0 for intent in intents}

    for delivery in deliveries:
        observation = delivery.observation
        if observation.refund_id not in status:
            # No local refund row to update; a webhook-only baseline drops it.
            continue
        if observation.status == STATUS_SUCCEEDED:
            totals[observation.refund_id] += observation.amount_paise
        status[observation.refund_id] = observation.status

    return {
        refund_id: NaiveRefund(refund_id, status[refund_id], totals[refund_id])
        for refund_id in status
    }