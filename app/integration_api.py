"""HTTP surface for an application's trusted backend.

This is not a payment provider webhook endpoint. It accepts already normalized
observations from a caller we trust and does not verify provider signatures. Nothing
here performs a refund, sends a notification, or asks a provider for the truth.
"""

from __future__ import annotations

import hmac
import os
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domain.money import MoneyError
from app.integration import IntegrationError, IntegrationService

INTEGRATION_KEY_ENV = "INTEGRATION_API_KEY"

# Bounded identifiers. No spaces, no slashes, no control characters.
Identifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]
ProviderName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]
# Strict: a string, a float or a bool is not accepted as money.
Paise = Annotated[int, Field(strict=True, gt=0, le=1_000_000_000_000)]

# Fixed by the server. A caller cannot claim the observation came from a lookup.
DeliveryStatus = Literal["pending", "succeeded", "failed"]


class CreateWorkspaceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ProviderName
    payment_id: Identifier
    captured_paise: Paise


class RegisterRefundBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refund_id: Identifier
    amount_paise: Paise


class SubmitDeliveryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: Identifier
    event_id: Identifier
    refund_id: Identifier
    status: DeliveryStatus
    amount_paise: Paise


def _configured_key() -> str:
    return os.environ.get(INTEGRATION_KEY_ENV, "").strip()


def require_api_key(request: Request) -> None:
    """Every integration route is gated here.

    With no configured key the whole surface answers 404, so it is indistinguishable
    from a path that does not exist. The key is read from the Authorization header
    only, never from a query string, and is never echoed in a response.
    """
    expected = _configured_key()
    if not expected:
        raise IntegrationError(404, "not found")

    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if (
        scheme.lower() != "bearer"
        or not token
        or not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
    ):
        raise IntegrationError(
            401, "unauthorized", headers={"WWW-Authenticate": "Bearer"}
        )


router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])


def _service(request: Request) -> IntegrationService:
    service = getattr(request.app.state, "integration", None)
    if service is None:
        raise IntegrationError(503, "storage unavailable")
    return service


@router.post("/workspaces", status_code=201)
def create_workspace(body: CreateWorkspaceBody, request: Request) -> JSONResponse:
    service = _service(request)
    try:
        workspace = service.create_workspace(
            body.provider, body.payment_id, body.captured_paise
        )
    except MoneyError as exc:
        raise IntegrationError(422, str(exc)) from exc

    return JSONResponse(
        {
            "workspace_id": workspace.workspace_id,
            "provider": workspace.provider,
            "payment": {
                "payment_id": workspace.payment_id,
                "captured_paise": workspace.captured_paise,
                "currency": workspace.currency,
            },
            "status_url": "/v1/workspaces/%s" % workspace.workspace_id,
        },
        status_code=201,
    )


@router.post("/workspaces/{workspace_id}/refunds")
def register_refund(
    workspace_id: str, body: RegisterRefundBody, request: Request
) -> JSONResponse:
    service = _service(request)
    workspace = service.workspace(workspace_id)

    try:
        result = service.register_refund(
            workspace_id, body.refund_id, body.amount_paise
        )
    except MoneyError as exc:
        raise IntegrationError(422, str(exc)) from exc

    payload = {
        "refund_id": body.refund_id,
        "amount_paise": body.amount_paise,
        "admitted_paise": result.admitted_paise,
        "captured_paise": workspace.captured_paise,
        "accepted": result.accepted,
        "reason": result.reason,
    }

    if result.accepted and result.reason == "accepted":
        return JSONResponse(payload, status_code=201)
    if result.accepted:
        return JSONResponse(payload, status_code=200)
    if result.reason == "over_refund":
        return JSONResponse(payload, status_code=422)
    if result.reason in ("refund_id_conflict", "unknown_payment"):
        return JSONResponse(payload, status_code=409)
    return JSONResponse(payload, status_code=400)


@router.post("/workspaces/{workspace_id}/deliveries")
def submit_delivery(
    workspace_id: str, body: SubmitDeliveryBody, request: Request
) -> JSONResponse:
    service = _service(request)

    outcome = service.submit_delivery(
        workspace_id=workspace_id,
        attempt_id=body.attempt_id,
        event_id=body.event_id,
        refund_id=body.refund_id,
        status=body.status,
        amount_paise=body.amount_paise,
    )

    # The ingestion outcome and the refund's resulting status are separate facts.
    # A recorded delivery does not mean the refund succeeded.
    return JSONResponse(
        {
            "outcome": outcome.outcome,
            "recorded": outcome.recorded,
            "refund": outcome.refund,
        }
    )


@router.get("/workspaces/{workspace_id}")
def workspace_status(workspace_id: str, request: Request) -> JSONResponse:
    return JSONResponse(_service(request).status(workspace_id).to_dict())