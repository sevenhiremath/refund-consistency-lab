from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from app.config import VERSION, resolve_source_commit
from app.domain.model import Delivery
from app.sim.fixture import Fixture, FixtureError, parse_fixture
from app.sim.provider import ProviderView
from app.store import LookupRecord, StepOutcome, Store


@dataclass(frozen=True)
class RunReport:
    run_id: str
    body: dict
    digest: str

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "digest": self.digest, **self.body}


def _digest(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Runner:
    """Drives a fixture through the stored processors on a logical clock.

    The runner only ever hands scheduled deliveries to the store. Provider events
    that were not delivered exist in the fixture and in this report, and nowhere
    the processors can read them.
    """

    def __init__(self, store: Store, commit: str | None = None) -> None:
        self.store = store
        self.commit = commit

    def start(self, fixture: Fixture) -> str:
        run_id = "run_" + uuid.uuid4().hex[:12]
        self.store.create_run(
            run_id,
            fixture.provider,
            fixture_name=fixture.name,
            fixture_sha=fixture.sha256,
            fixture_text=fixture.text,
        )
        self.store.add_payment(run_id, fixture.payment)
        for refund in fixture.refunds:
            result = self.store.admit(run_id, refund)
            if not result.accepted:
                raise FixtureError(
                    "refund %s was not admitted: %s"
                    % (refund.refund_id, result.reason)
                )
        return run_id

    def step(self, run_id: str, expected_cursor: int) -> StepOutcome:
        fixture, schedule = self._load(run_id)
        return self.store.apply_step(run_id, fixture.provider, expected_cursor, schedule)

    def run_to_end(self, run_id: str, expected_cursor: int) -> RunReport:
        fixture, schedule = self._load(run_id)
        self.store.apply_remaining(run_id, fixture.provider, expected_cursor, schedule)
        return self.report(run_id)

    def reconcile(
        self, run_id: str, refund_id: str, at_tick: int | None = None
    ) -> LookupRecord:
        """Ask the provider about one refund at a logical tick.

        The tick defaults to the run clock. A lookup may not query the past, so the
        logical clock only moves forward.

        A lookup also may not move the clock past a scheduled delivery that has not
        been applied yet. Applying that delivery would then look like a step
        backwards in time and the run could never advance. Ticks up to and including
        the next delivery tick are fine, and once nothing is left to deliver any
        later tick works. The check runs before anything is written, so a rejected
        lookup leaves no observation, action, finding or clock change behind.
        """
        fixture, schedule = self._load(run_id)
        state = self.store.run_state(run_id)
        tick = state["tick"] if at_tick is None else at_tick
        if tick < state["tick"]:
            raise ValueError(
                "cannot look up tick %d from a run at tick %d" % (tick, state["tick"])
            )

        cursor = state["cursor"]
        if cursor < len(schedule):
            next_tick = schedule[cursor][0]
            if tick > next_tick:
                raise ValueError(
                    "a lookup at tick %d would move the clock past the next scheduled "
                    "delivery at tick %d; use a tick up to %d or step first"
                    % (tick, next_tick, next_tick)
                )

        result = ProviderView(fixture).lookup(refund_id, tick)
        return self.store.record_lookup(
            run_id, fixture.provider, refund_id, tick, result.outcome, result.observation
        )

    def replay(self, run_id: str) -> str:
        """Replay the stored fixture and action log into a new run.

        Deliveries and lookups are replayed in their original order with their
        original ticks, so the digest is comparable.
        """
        fixture, schedule = self._load(run_id)
        actions = self.store.actions(run_id)

        new_run = self.start(fixture)
        for action in actions:
            if action["kind"] == "delivery":
                self.store.apply_step(
                    new_run, fixture.provider, action["cursor"], schedule
                )
            else:
                self.reconcile(new_run, action["subject"], action["tick"])
        return new_run

    def report(self, run_id: str) -> RunReport:
        fixture, schedule = self._load(run_id)
        state = self.store.run_state(run_id)
        comparison = self.store.report(run_id, fixture.payment.payment_id)
        actions = self.store.actions(run_id)

        commit = self.commit if self.commit is not None else resolve_source_commit()
        body = {
            "engine": {
                "version": VERSION,
                "commit": commit,
                "commit_known": commit != "unknown",
            },
            "fixture": {
                "name": fixture.name,
                "version": fixture.version,
                "sha256": fixture.sha256,
                "provider": fixture.provider,
                "notes": fixture.notes,
            },
            "inputs": {
                "payment": {
                    "payment_id": fixture.payment.payment_id,
                    "captured_paise": fixture.payment.captured_paise,
                    "currency": fixture.payment.currency,
                },
                "refunds": [
                    {
                        "refund_id": refund.refund_id,
                        "amount_paise": refund.amount_paise,
                    }
                    for refund in fixture.refunds
                ],
                "scheduled_deliveries": len(schedule),
                "end_tick": fixture.end_tick,
            },
            "provider": {
                "events": len(fixture.events),
                "dropped": list(fixture.dropped_event_ids),
            },
            "progress": {
                "cursor": state["cursor"],
                "tick": state["tick"],
                "finished": state["cursor"] >= len(schedule),
            },
            "actions": [dict(action) for action in actions],
            "comparison": {
                "captured_paise": comparison.captured_paise,
                "admitted_paise": comparison.admitted_paise,
                "protected_confirmed_paise": comparison.protected_confirmed_paise,
                "protected_review_paise": comparison.protected_review_paise,
                "naive_total_paise": comparison.naive_total_paise,
                "refunds": [
                    {
                        "refund_id": row.refund_id,
                        "amount_paise": row.amount_paise,
                        "protected_status": row.protected_status,
                        "protected_confirmed_paise": row.protected_confirmed_paise,
                        "protected_review_paise": row.protected_review_paise,
                        "naive_status": row.naive_status,
                        "naive_total_paise": row.naive_total_paise,
                        "reasons": list(row.reasons),
                    }
                    for row in comparison.rows
                ],
            },
            "evidence": {
                "unmatched": [
                    {
                        "event_id": observation.event_id,
                        "refund_id": observation.refund_id,
                        "status": observation.status,
                        "amount_paise": observation.amount_paise,
                    }
                    for observation in comparison.unmatched
                ],
                "findings": list(self.store.finding_keys(run_id)),
            },
        }
        return RunReport(run_id=run_id, body=body, digest=_digest(body))

    def _load(self, run_id: str) -> tuple[Fixture, tuple[tuple[int, Delivery], ...]]:
        state = self.store.run_state(run_id)
        if not state["fixture_text"]:
            raise FixtureError("run %s has no stored fixture" % (run_id,))
        fixture = parse_fixture(state["fixture_text"])
        if fixture.sha256 != state["fixture_sha"]:
            raise FixtureError(
                "stored fixture text for run %s does not match its recorded hash"
                % (run_id,)
            )
        schedule = tuple((item.at_tick, item.delivery) for item in fixture.schedule)
        return fixture, schedule