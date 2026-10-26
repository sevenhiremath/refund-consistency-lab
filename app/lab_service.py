from __future__ import annotations

from pathlib import Path

from app.errors import LabError
from app.sim.fixture import Fixture, load_fixture
from app.sim.runner import Runner, RunReport
from app.store import LookupRecord, StepConflict, StepOutcome, Store


def default_fixtures_dir() -> Path:
    packaged = Path(__file__).resolve().parent.parent / "fixtures"
    if packaged.is_dir():
        return packaged
    return Path.cwd() / "fixtures"


class LabService:
    """The only place routes get lab state from.

    Every number the page shows comes out of a RunReport built here. Templates
    format, they do not calculate.
    """

    def __init__(
        self, store: Store, fixtures_dir: Path | None = None, commit: str | None = None
    ) -> None:
        self.store = store
        self.runner = Runner(store, commit)
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else default_fixtures_dir()
        self._fixtures: dict[str, Fixture] = {}
        for path in sorted(self.fixtures_dir.glob("*.json")):
            fixture = load_fixture(path)
            if fixture.name in self._fixtures:
                raise ValueError("duplicate fixture name %r" % (fixture.name,))
            self._fixtures[fixture.name] = fixture

    def scenarios(self) -> list[dict]:
        return [
            {
                "name": fixture.name,
                "notes": fixture.notes,
                "provider": fixture.provider,
                "refunds": len(fixture.refunds),
                "deliveries": len(fixture.schedule),
                "dropped": len(fixture.dropped_event_ids),
            }
            for fixture in sorted(self._fixtures.values(), key=lambda item: item.name)
        ]

    def start(self, scenario: str) -> str:
        return self.runner.start(self._fixture(scenario))

    def report(self, run_id: str) -> RunReport:
        self._require_run(run_id)
        return self.runner.report(run_id)

    def step(self, run_id: str, expected_cursor: int) -> StepOutcome:
        self._require_run(run_id)
        try:
            return self.runner.step(run_id, expected_cursor)
        except StepConflict as exc:
            raise LabError(
                409,
                str(exc),
                "The run moved on. Reload the page and use the current position.",
            ) from exc

    def run_to_end(self, run_id: str, expected_cursor: int) -> RunReport:
        self._require_run(run_id)
        try:
            return self.runner.run_to_end(run_id, expected_cursor)
        except StepConflict as exc:
            raise LabError(
                409,
                str(exc),
                "The run moved on. Reload the page and use the current position.",
            ) from exc

    def reconcile(
        self, run_id: str, refund_id: str, tick: int | None = None
    ) -> LookupRecord:
        self._require_run(run_id)
        known = {
            row["refund_id"] for row in self.runner.report(run_id).body["comparison"]["refunds"]
        }
        if refund_id not in known:
            raise LabError(
                400,
                "unknown refund %r" % (refund_id,),
                "Choose one of the refunds in this run.",
            )
        try:
            return self.runner.reconcile(run_id, refund_id, tick)
        except ValueError as exc:
            raise LabError(
                400,
                str(exc),
                "A lookup can ask about the current tick or later, but not past the "
                "next scheduled delivery. Step the run first, or use a smaller tick.",
            ) from exc

    def replay(self, run_id: str) -> str:
        self._require_run(run_id)
        try:
            return self.runner.replay(run_id)
        except ValueError as exc:
            raise LabError(
                400,
                "this run cannot be replayed: %s" % exc,
                "Runs created before the lookup tick rule was tightened may not "
                "replay. Start the scenario again.",
            ) from exc

    def _fixture(self, name: str) -> Fixture:
        fixture = self._fixtures.get(name)
        if fixture is None:
            raise LabError(
                404,
                "unknown scenario %r" % (name,),
                "Pick a scenario from the list.",
            )
        return fixture

    def _require_run(self, run_id: str) -> None:
        try:
            self.store.run_state(run_id)
        except ValueError as exc:
            raise LabError(
                404,
                "unknown run %r" % (run_id,),
                "Start a scenario from the home page.",
            ) from exc