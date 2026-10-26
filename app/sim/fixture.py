from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.model import (
    PROVIDER_STATUSES,
    Delivery,
    Observation,
    Payment,
    RefundIntent,
)


class FixtureError(ValueError):
    """A fixture is malformed or internally inconsistent."""


@dataclass(frozen=True)
class ProviderEvent:
    """Something the provider knows. Not necessarily something it sent."""

    event_id: str
    refund_id: str
    status: str
    amount_paise: int
    at_tick: int
    delivered: bool


@dataclass(frozen=True)
class ScheduledDelivery:
    at_tick: int
    attempt_id: str
    delivery: Delivery


@dataclass(frozen=True)
class Fixture:
    name: str
    version: int
    provider: str
    notes: str
    payment: Payment
    refunds: tuple[RefundIntent, ...]
    events: tuple[ProviderEvent, ...]
    schedule: tuple[ScheduledDelivery, ...]
    end_tick: int
    sha256: str
    text: str

    @property
    def dropped_event_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(event.event_id for event in self.events if not event.delivered)
        )


def load_fixture(path: Path | str) -> Fixture:
    # utf-8-sig tolerates an editor that saves a byte order mark.
    text = Path(path).read_text(encoding="utf-8-sig")
    return parse_fixture(text)


def parse_fixture(text: str) -> Fixture:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FixtureError("fixture is not valid JSON: %s" % exc) from exc
    if not isinstance(data, dict):
        raise FixtureError("fixture must be a JSON object")

    name = _text(data, "fixture")
    version = _int(data, "version", minimum=1)
    provider = _text(data, "provider")
    notes = data.get("notes", "")
    if not isinstance(notes, str):
        raise FixtureError("notes must be a string")
    end_tick = _int(data, "end_tick", minimum=0)

    payment_data = _object(data, "payment")
    payment = Payment(
        _text(payment_data, "payment_id"),
        _int(payment_data, "captured_paise", minimum=1),
    )

    refunds = []
    for index, entry in enumerate(_list(data, "refunds")):
        item = _object_at(entry, "refunds", index)
        refunds.append(
            RefundIntent(
                _text(item, "refund_id"),
                payment.payment_id,
                _int(item, "amount_paise", minimum=1),
            )
        )
    if not refunds:
        raise FixtureError("fixture must declare at least one refund")
    if len({refund.refund_id for refund in refunds}) != len(refunds):
        raise FixtureError("refund ids must be unique")
    if sum(refund.amount_paise for refund in refunds) > payment.captured_paise:
        raise FixtureError("declared refunds exceed the captured amount")

    events = []
    for index, entry in enumerate(_list(data, "events")):
        item = _object_at(entry, "events", index)
        status = _text(item, "status")
        if status not in PROVIDER_STATUSES:
            raise FixtureError("unknown provider status %r" % (status,))
        events.append(
            ProviderEvent(
                event_id=_text(item, "event_id"),
                refund_id=_text(item, "refund_id"),
                status=status,
                amount_paise=_int(item, "amount_paise", minimum=1),
                at_tick=_int(item, "at_tick", minimum=0),
                delivered=_bool(item, "delivered"),
            )
        )
    if len({event.event_id for event in events}) != len(events):
        raise FixtureError("event ids must be unique")
    if not {event.refund_id for event in events} <= {
        refund.refund_id for refund in refunds
    }:
        raise FixtureError("every event must reference a declared refund")

    by_event = {event.event_id: event for event in events}

    scheduled = []
    for index, entry in enumerate(_list(data, "deliveries")):
        item = _object_at(entry, "deliveries", index)
        event_id = _text(item, "event_id")
        event = by_event.get(event_id)
        if event is None:
            raise FixtureError("delivery for unknown event %r" % (event_id,))
        at_tick = _int(item, "at_tick", minimum=0)
        if at_tick < event.at_tick:
            raise FixtureError(
                "event %s cannot be delivered at tick %d before it happens at %d"
                % (event_id, at_tick, event.at_tick)
            )
        scheduled.append(
            ScheduledDelivery(
                at_tick=at_tick,
                attempt_id=_text(item, "attempt_id"),
                delivery=Delivery(
                    _text(item, "attempt_id"),
                    Observation(
                        event.event_id,
                        event.refund_id,
                        event.status,
                        event.amount_paise,
                    ),
                ),
            )
        )

    if len({item.attempt_id for item in scheduled}) != len(scheduled):
        raise FixtureError("attempt ids must be unique")

    delivered_ids = {item.delivery.observation.event_id for item in scheduled}
    for event in events:
        if event.delivered and event.event_id not in delivered_ids:
            raise FixtureError(
                "event %s is marked delivered but has no scheduled delivery"
                % (event.event_id,)
            )
        if not event.delivered and event.event_id in delivered_ids:
            raise FixtureError(
                "event %s is marked dropped but is scheduled for delivery"
                % (event.event_id,)
            )

    # Ties keep the order the author wrote them in, not an alphabetical guess.
    schedule = tuple(
        item
        for _, item in sorted(
            enumerate(scheduled), key=lambda pair: (pair[1].at_tick, pair[0])
        )
    )
    if schedule and end_tick < max(item.at_tick for item in schedule):
        raise FixtureError("end_tick is before the last scheduled delivery")

    return Fixture(
        name=name,
        version=version,
        provider=provider,
        notes=notes,
        payment=payment,
        refunds=tuple(refunds),
        events=tuple(events),
        schedule=schedule,
        end_tick=end_tick,
        sha256=digest,
        text=text,
    )


def _text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise FixtureError("%s must be a non-empty string" % key)
    return value


def _int(data: dict[str, Any], key: str, *, minimum: int) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise FixtureError("%s must be an integer" % key)
    if value < minimum:
        raise FixtureError("%s must be at least %d" % (key, minimum))
    return value


def _bool(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise FixtureError("%s must be a boolean" % key)
    return value


def _object(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise FixtureError("%s must be an object" % key)
    return value


def _object_at(value: Any, key: str, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FixtureError("%s[%d] must be an object" % (key, index))
    return value


def _list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    if not isinstance(value, list):
        raise FixtureError("%s must be a list" % key)
    return value