from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from app.config import VERSION
from app.domain.model import Delivery
from app.sim.fixture import Fixture, FixtureError, parse_fixture
from app.store import StepOutcome, Store


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

    def __init__(self, store: Store) -> None:
        self.store = store

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

    def replay(self, run_id: str) -> str:
        """Start a new run from the fixture text stored with an existing run."""
        fixture, _ = self._load(run_id)
        return self.start(fixture)

    def report(self, run_id: str) -> RunReport:
        fixture, schedule = self._load(run_id)
        state = self.store.run_state(run_id)
        comparison = self.store.report(run_id, fixture.payment.payment_id)
        steps = self.store.steps(run_id)

        body = {
            "engine": {"version": VERSION},
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
            "steps": [dict(step) for step in steps],
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