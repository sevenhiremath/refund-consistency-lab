"""Adapter for Hyperswitch refund webhooks.

Hyperswitch posts its own envelope, signed with HMAC-SHA512, and expects a 2xx only
after the event is accepted. This module verifies that signature over the raw bytes and
translates supported refund events into the delivery model the service already uses.

It does not implement refund rules. Everything after translation goes through
``IntegrationService.submit_delivery``, so duplicates, amount mismatches and conflicts
behave exactly as they do for any other delivery.

Verified against a documented payload, not against a live Hyperswitch instance.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any

SIGNATURE_HEADER = "X-Webhook-Signature-512"
SECRET_ENV = "HYPERSWITCH_WEBHOOK_SECRET"
WORKSPACE_ENV = "HYPERSWITCH_WORKSPACE_ID"
MERCHANT_ENV = "HYPERSWITCH_MERCHANT_ID"
PAYMENT_ENV = "HYPERSWITCH_PAYMENT_ID"

SUPPORTED_CURRENCY = "INR"

# Same ceiling the integration API applies to a delivery amount, and well inside the
# range SQLite can store as an integer. Anything larger is refused before it reaches
# the processor.
MAX_AMOUNT_PAISE = 1_000_000_000_000

# Event type to the refund status it must agree with. `refund_review` is deliberately
# absent: our status vocabulary has no review state, so that event is refused rather
# than guessed into success or failure.
EVENT_STATUS = {
    "refund_succeeded": "succeeded",
    "refund_failed": "failed",
}

# Statuses our delivery model accepts. `review` exists in Hyperswitch but not here.
ACCEPTED_STATUSES = ("pending", "succeeded", "failed")


class AdapterError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class AdapterConfig:
    secret: str
    workspace_id: str
    merchant_id: str
    payment_id: str

    @classmethod
    def from_env(cls) -> "AdapterConfig | None":
        """Return the configuration, or None when the route should not exist.

        Every value is read from the process environment. Nothing is defaulted, so a
        missing secret cannot silently enable an unauthenticated route.
        """
        secret = os.environ.get(SECRET_ENV, "").strip()
        workspace_id = os.environ.get(WORKSPACE_ENV, "").strip()
        merchant_id = os.environ.get(MERCHANT_ENV, "").strip()
        payment_id = os.environ.get(PAYMENT_ENV, "").strip()
        if not all((secret, workspace_id, merchant_id, payment_id)):
            return None
        return cls(
            secret=secret,
            workspace_id=workspace_id,
            merchant_id=merchant_id,
            payment_id=payment_id,
        )


@dataclass(frozen=True)
class RefundNotification:
    event_id: str
    event_type: str
    merchant_id: str
    refund_id: str
    payment_id: str
    amount_paise: int
    currency: str
    status: str


def sign(raw_body: bytes, secret: str) -> str:
    """The digest Hyperswitch puts in X-Webhook-Signature-512: hex HMAC-SHA512."""
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()


def verify(raw_body: bytes, signature: str, secret: str) -> bool:
    """Constant-time check over the exact bytes received, before any parsing.

    The header is compared as ASCII bytes. A header with non ASCII characters is not
    a digest we could have produced, so it fails the check instead of raising.
    """
    if not signature:
        return False
    try:
        provided = signature.strip().lower().encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(sign(raw_body, secret).encode("ascii"), provided)


def _text(data: dict[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AdapterError(422, "%s.%s must be a non-empty string" % (where, key))
    return value


def parse_notification(raw_body: bytes, config: AdapterConfig) -> RefundNotification:
    """Validate the envelope and translate it, without touching the database."""
    try:
        text = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdapterError(400, "body is not valid UTF-8") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AdapterError(400, "body is not valid JSON") from exc
    if not isinstance(data, dict):
        raise AdapterError(422, "body must be a JSON object")

    merchant_id = _text(data, "merchant_id", "envelope")
    event_id = _text(data, "event_id", "envelope")
    event_type = _text(data, "event_type", "envelope")

    # Bind the payload to the configured workspace. An unauthenticated body must not
    # be able to choose which workspace it lands in.
    if merchant_id != config.merchant_id:
        raise AdapterError(403, "merchant_id does not match the configured workspace")

    content = data.get("content")
    if not isinstance(content, dict):
        raise AdapterError(422, "content must be an object")
    if content.get("type") != "refund_details":
        raise AdapterError(
            422, "content.type must be refund_details, got %r" % (content.get("type"),)
        )
    obj = content.get("object")
    if not isinstance(obj, dict):
        raise AdapterError(422, "content.object must be an object")

    refund_id = _text(obj, "refund_id", "content.object")
    payment_id = _text(obj, "payment_id", "content.object")
    if payment_id != config.payment_id:
        raise AdapterError(403, "payment_id does not match the configured workspace")

    currency = _text(obj, "currency", "content.object")
    if currency != SUPPORTED_CURRENCY:
        # Never relabel another currency as INR.
        raise AdapterError(
            422, "unsupported currency %r; this service accepts %s only"
            % (currency, SUPPORTED_CURRENCY)
        )

    amount = obj.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise AdapterError(422, "content.object.amount must be a positive integer")
    if amount > MAX_AMOUNT_PAISE:
        raise AdapterError(
            422,
            "content.object.amount exceeds the supported maximum of %d paise"
            % MAX_AMOUNT_PAISE,
        )

    if event_type not in EVENT_STATUS:
        raise AdapterError(
            422,
            "unsupported event_type %r; supported: %s"
            % (event_type, ", ".join(sorted(EVENT_STATUS))),
        )

    object_status = _text(obj, "status", "content.object")
    if object_status not in ACCEPTED_STATUSES:
        raise AdapterError(
            422,
            "unsupported refund status %r; this service accepts %s"
            % (object_status, ", ".join(ACCEPTED_STATUSES)),
        )

    expected = EVENT_STATUS[event_type]
    if object_status != expected:
        raise AdapterError(
            422,
            "event_type %s disagrees with the refund status %r"
            % (event_type, object_status),
        )

    return RefundNotification(
        event_id=event_id,
        event_type=event_type,
        merchant_id=merchant_id,
        refund_id=refund_id,
        payment_id=payment_id,
        # Hyperswitch amounts are in minor units, so INR maps directly to paise.
        amount_paise=amount,
        currency=currency,
        status=object_status,
    )