"""HTTP route for Hyperswitch refund webhooks.

Separate from `/v1`: this route is unauthenticated by design, because the caller is
Hyperswitch and the only credential is the HMAC signature. It exists only when the
signing secret and workspace binding are configured. It never accepts a workspace id
from the payload.

Not a `/v1` route and not a public demo route. Nothing here is reachable unless
`HYPERSWITCH_WEBHOOK_SECRET`, `HYPERSWITCH_WORKSPACE_ID`, `HYPERSWITCH_MERCHANT_ID`
and `HYPERSWITCH_PAYMENT_ID` are all set.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.hyperswitch import (
    SIGNATURE_HEADER,
    AdapterConfig,
    AdapterError,
    parse_notification,
    verify,
)
from app.integration import IntegrationError, IntegrationService

router = APIRouter()


def _service(request: Request) -> IntegrationService:
    service = getattr(request.app.state, "integration", None)
    if service is None:
        raise AdapterError(503, "storage unavailable")
    return service


@router.post("/webhooks/hyperswitch")
async def hyperswitch_webhook(request: Request) -> JSONResponse:
    config = AdapterConfig.from_env()
    if config is None:
        # The route is mounted but inert without configuration. 404 matches how the
        # integration API behaves with no key: nothing to discover.
        raise AdapterError(404, "not found")

    raw = await request.body()
    signature = request.headers.get(SIGNATURE_HEADER, "")
    if not verify(raw, signature, config.secret):
        # Rejected before parsing, so a tampered body cannot be partially applied.
        raise AdapterError(401, "invalid signature")

    notification = parse_notification(raw, config)
    service = _service(request)

    try:
        workspace = service.workspace(config.workspace_id)
    except IntegrationError as exc:
        raise AdapterError(503, "configured workspace is unavailable") from exc

    # The payment and refund must already exist. A notification never creates either,
    # and never changes an intended amount.
    if not service.store.refund_exists(workspace.workspace_id, notification.refund_id):
        raise AdapterError(
            422,
            "unknown refund %r in the configured workspace; register it before "
            "delivering notifications" % (notification.refund_id,),
        )

    # One attempt id per received delivery. Repeats of one event still dedupe on the
    # event id, which is what makes the protected side count once.
    seen = service.store.event_delivery_count(
        workspace.workspace_id, notification.event_id
    )
    attempt_id = "hs:%s:%d" % (notification.event_id, seen + 1)

    outcome = service.submit_delivery(
        workspace_id=workspace.workspace_id,
        attempt_id=attempt_id,
        event_id=notification.event_id,
        refund_id=notification.refund_id,
        status=notification.status,
        amount_paise=notification.amount_paise,
    )

    return JSONResponse(
        {
            "event_id": notification.event_id,
            "attempt_id": attempt_id,
            "outcome": outcome.outcome,
            "recorded": outcome.recorded,
            "refund": outcome.refund,
        }
    )