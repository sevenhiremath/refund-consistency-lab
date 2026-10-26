from __future__ import annotations

from dataclasses import dataclass

from app.domain.model import Observation
from app.sim.fixture import Fixture


@dataclass(frozen=True)
class LookupResult:
    outcome: str
    observation: Observation | None
    detail: str


class ProviderView:
    """What the provider knows at a logical tick.

    Read-only and derived from the fixture. A lookup never creates a refund and
    never changes provider truth. Only the tick decides what is visible, so a
    lookup cannot see an event that has not happened yet.
    """

    def __init__(self, fixture: Fixture) -> None:
        self.fixture = fixture
        self._order = {
            event.event_id: index for index, event in enumerate(fixture.events)
        }
        self._unavailable = set(fixture.lookup_unavailable_ticks)

    def lookup(self, refund_id: str, tick: int) -> LookupResult:
        if tick in self._unavailable:
            return LookupResult(
                "unavailable",
                None,
                "provider did not answer at tick %d" % tick,
            )

        visible = [
            event
            for event in self.fixture.events
            if event.refund_id == refund_id and event.at_tick <= tick
        ]
        if not visible:
            return LookupResult(
                "not_found",
                None,
                "provider has no record at tick %d" % tick,
            )

        latest = max(
            visible, key=lambda event: (event.at_tick, self._order[event.event_id])
        )
        observation = Observation(
            "lookup:%s:%d" % (refund_id, tick),
            refund_id,
            latest.status,
            latest.amount_paise,
        )
        return LookupResult(
            latest.status,
            observation,
            "provider reports %s at tick %d" % (latest.status, tick),
        )