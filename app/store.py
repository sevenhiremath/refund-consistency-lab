from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

from app.db import connect
from app.domain.model import (
    Delivery,
    Observation,
    Payment,
    PaymentReport,
    RefundIntent,
)
from app.domain.rules import AdmissionResult, admit, evaluate

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        created_at TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'demo'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS payments (
        run_id TEXT NOT NULL,
        payment_id TEXT NOT NULL,
        captured_paise INTEGER NOT NULL CHECK (captured_paise > 0),
        currency TEXT NOT NULL,
        PRIMARY KEY (run_id, payment_id),
        FOREIGN KEY (run_id) REFERENCES runs (run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS refund_intents (
        run_id TEXT NOT NULL,
        refund_id TEXT NOT NULL,
        payment_id TEXT NOT NULL,
        amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
        currency TEXT NOT NULL,
        PRIMARY KEY (run_id, refund_id),
        FOREIGN KEY (run_id) REFERENCES runs (run_id),
        FOREIGN KEY (run_id, payment_id) REFERENCES payments (run_id, payment_id)
    )
    """,
    # One row per delivery attempt. Duplicates and conflicting payloads stay.
    """
    CREATE TABLE IF NOT EXISTS deliveries (
        run_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        seq INTEGER NOT NULL,
        attempt_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        refund_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
        amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
        currency TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'webhook' CHECK (source IN ('webhook', 'lookup')),
        accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)),
        conflict INTEGER NOT NULL DEFAULT 0 CHECK (conflict IN (0, 1)),
        outcome TEXT NOT NULL,
        received_at TEXT NOT NULL,
        PRIMARY KEY (run_id, provider, attempt_id),
        UNIQUE (run_id, provider, seq),
        FOREIGN KEY (run_id) REFERENCES runs (run_id)
    )
    """,
    # At most one accepted claim per provider event in a run.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_deliveries_accepted_event
        ON deliveries (run_id, provider, event_id)
        WHERE accepted = 1
    """,
    """
    CREATE TABLE IF NOT EXISTS findings (
        run_id TEXT NOT NULL,
        finding_key TEXT NOT NULL,
        kind TEXT NOT NULL,
        refund_id TEXT,
        event_id TEXT,
        detail TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, finding_key),
        FOREIGN KEY (run_id) REFERENCES runs (run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS refund_states (
        run_id TEXT NOT NULL,
        refund_id TEXT NOT NULL,
        status TEXT NOT NULL,
        confirmed_paise INTEGER NOT NULL,
        review_paise INTEGER NOT NULL,
        reasons TEXT NOT NULL,
        PRIMARY KEY (run_id, refund_id),
        FOREIGN KEY (run_id, refund_id) REFERENCES refund_intents (run_id, refund_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS naive_states (
        run_id TEXT NOT NULL,
        refund_id TEXT NOT NULL,
        status TEXT NOT NULL,
        total_paise INTEGER NOT NULL,
        PRIMARY KEY (run_id, refund_id),
        FOREIGN KEY (run_id, refund_id) REFERENCES refund_intents (run_id, refund_id)
    )
    """,
    # Scheduler bookkeeping. The cursor advances in the same transaction as the
    # delivery it consumed, so a failed step does not eat a scheduled delivery.
    """
    CREATE TABLE IF NOT EXISTS run_state (
        run_id TEXT PRIMARY KEY,
        cursor INTEGER NOT NULL DEFAULT 0,
        tick INTEGER NOT NULL DEFAULT 0,
        action_seq INTEGER NOT NULL DEFAULT 0,
        fixture_name TEXT NOT NULL DEFAULT '',
        fixture_sha TEXT NOT NULL DEFAULT '',
        fixture_text TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (run_id) REFERENCES runs (run_id)
    )
    """,
    # One ordered log for every logical action: a delivery or a provider lookup.
    # Replay follows this order, so timing and sequence are both reproducible.
    """
    CREATE TABLE IF NOT EXISTS run_actions (
        run_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('delivery', 'lookup')),
        tick INTEGER NOT NULL,
        cursor INTEGER,
        subject TEXT NOT NULL,
        event_id TEXT,
        outcome TEXT NOT NULL,
        PRIMARY KEY (run_id, seq),
        FOREIGN KEY (run_id) REFERENCES runs (run_id)
    )
    """,
)

UPSERT_REFUND_STATE = """
    INSERT INTO refund_states (run_id, refund_id, status, confirmed_paise, review_paise, reasons)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT (run_id, refund_id) DO UPDATE SET
        status = excluded.status,
        confirmed_paise = excluded.confirmed_paise,
        review_paise = excluded.review_paise,
        reasons = excluded.reasons
"""

UPSERT_NAIVE_STATE = """
    INSERT INTO naive_states (run_id, refund_id, status, total_paise)
    VALUES (?, ?, ?, ?)
    ON CONFLICT (run_id, refund_id) DO UPDATE SET
        status = excluded.status,
        total_paise = excluded.total_paise
"""


@dataclass(frozen=True)
class IngestResult:
    event_id: str
    outcome: str
    recorded: bool


class StepConflict(Exception):
    """The caller's expected cursor is ahead of the stored cursor."""


class LookupStale(Exception):
    """A lookup result arrived after the run moved past the tick it describes.

    Raised inside the write transaction, so nothing is recorded. The provider query
    it came from is discarded rather than stored against the wrong moment.
    """


@dataclass(frozen=True)
class StepOutcome:
    cursor: int
    tick: int
    attempt_id: str | None
    event_id: str | None
    outcome: str
    applied: bool
    next_cursor: int


@dataclass(frozen=True)
class LookupRecord:
    refund_id: str
    tick: int
    outcome: str
    recorded: bool
    observation: Observation | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """File-backed SQLite store for one or more lab runs.

    Every method opens its own connection, so concurrent callers cannot share a
    transaction or a cursor. Writes go through BEGIN IMMEDIATE: the write lock is
    taken before the first read, which is what makes check-then-insert safe here.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA:
                conn.execute(statement)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
            if "kind" not in columns:
                # Additive only: existing rows become demo runs and keep working.
                conn.execute(
                    "ALTER TABLE runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'demo'"
                )
            conn.execute("PRAGMA user_version = 2")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    # -- setup -----------------------------------------------------------

    def _insert_run(
        self, conn: sqlite3.Connection, run_id: str, provider: str, kind: str
    ) -> None:
        conn.execute(
            """
            INSERT INTO runs (run_id, provider, created_at, kind)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, provider, _now(), kind),
        )

    def _insert_run_state(self, conn: sqlite3.Connection, run_id: str, **fixture) -> None:
        conn.execute(
            """
            INSERT INTO run_state
                (run_id, cursor, tick, fixture_name, fixture_sha, fixture_text)
            VALUES (?, 0, 0, ?, ?, ?)
            """,
            (
                run_id,
                fixture.get("fixture_name", ""),
                fixture.get("fixture_sha", ""),
                fixture.get("fixture_text", ""),
            ),
        )

    def _insert_payment(
        self, conn: sqlite3.Connection, run_id: str, payment: Payment
    ) -> None:
        conn.execute(
            """
            INSERT INTO payments (run_id, payment_id, captured_paise, currency)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, payment.payment_id, payment.captured_paise, payment.currency),
        )

    def create_run(
        self,
        run_id: str,
        provider: str,
        *,
        fixture_name: str = "",
        fixture_sha: str = "",
        fixture_text: str = "",
        kind: str = "demo",
    ) -> None:
        with self._write() as conn:
            self._insert_run(conn, run_id, provider, kind)
            self._insert_run_state(
                conn,
                run_id,
                fixture_name=fixture_name,
                fixture_sha=fixture_sha,
                fixture_text=fixture_text,
            )

    def add_payment(self, run_id: str, payment: Payment) -> None:
        with self._write() as conn:
            self._insert_payment(conn, run_id, payment)

    def create_workspace(
        self, run_id: str, provider: str, payment: Payment
    ) -> None:
        """A fixture free workspace and its payment in one transaction.

        create_run and add_payment each open their own transaction, which is fine for
        a demo that only ever adds fixture data. An integration workspace must never
        exist without its payment, so both rows commit together or neither does.
        """
        with self._write() as conn:
            self._insert_run(conn, run_id, provider, "integration")
            self._insert_run_state(conn, run_id)
            self._insert_payment(conn, run_id, payment)

    def run_info(self, run_id: str) -> dict:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT run_id, provider, kind FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise ValueError("unknown run %r" % (run_id,))
        return dict(row)

    def run_kind(self, run_id: str) -> str:
        return self.run_info(run_id)["kind"]

    def event_delivery_count(self, run_id: str, event_id: str) -> int:
        """How many deliveries of one provider event have been recorded."""
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM deliveries WHERE run_id = ? AND event_id = ?",
                (run_id, event_id),
            ).fetchone()[0]
        finally:
            conn.close()

    def refund_exists(self, run_id: str, refund_id: str) -> bool:
        conn = self._connect()
        try:
            return (
                conn.execute(
                    "SELECT 1 FROM refund_intents WHERE run_id = ? AND refund_id = ?",
                    (run_id, refund_id),
                ).fetchone()
                is not None
            )
        finally:
            conn.close()

    def run_payments(self, run_id: str) -> tuple[Payment, ...]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT payment_id, captured_paise, currency
                FROM payments WHERE run_id = ? ORDER BY payment_id
                """,
                (run_id,),
            ).fetchall()
            return tuple(
                Payment(row["payment_id"], row["captured_paise"], row["currency"])
                for row in rows
            )
        finally:
            conn.close()

    # -- admission -------------------------------------------------------

    def admit(
        self,
        run_id: str,
        refund: RefundIntent,
        max_refunds: int | None = None,
    ) -> AdmissionResult:
        """Admit a refund inside one write transaction.

        The cap check and the insert share the transaction and the transaction holds
        the write lock first, so two concurrent requests cannot both read 400 of
        1000 and both admit 600.

        The refund count limit is checked here too, inside the same transaction and
        before the insert. Checking it in the caller would let two concurrent
        registrations both pass and leave the workspace one over the limit. An
        identical retry is still admitted, because the domain returns
        ``already_admitted`` before the count is considered.
        """
        with self._write() as conn:
            row = conn.execute(
                "SELECT captured_paise, currency FROM payments WHERE run_id = ? AND payment_id = ?",
                (run_id, refund.payment_id),
            ).fetchone()
            if row is None:
                return AdmissionResult(False, "unknown_payment", ())

            payment = Payment(refund.payment_id, row["captured_paise"], row["currency"])
            intents = self._intents(conn, run_id)
            result = admit(payment, intents, refund)

            if result.accepted and result.reason == "accepted":
                existing = len(
                    [i for i in intents if i.payment_id == payment.payment_id]
                )
                if max_refunds is not None and existing >= max_refunds:
                    return AdmissionResult(
                        False, "refund_limit_reached", tuple(intents)
                    )
                conn.execute(
                    """
                    INSERT INTO refund_intents
                        (run_id, refund_id, payment_id, amount_paise, currency)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        refund.refund_id,
                        refund.payment_id,
                        refund.amount_paise,
                        refund.currency,
                    ),
                )
                # An admitted refund has a result from the moment it exists, so a
                # refund with no notifications still reads as pending rather than
                # missing.
                self._apply_projections(conn, run_id)
            return result

    # -- delivery processing ---------------------------------------------

    def ingest(self, run_id: str, provider: str, delivery: Delivery) -> IngestResult:
        """Record one delivery and its result in a single transaction.

        Either the delivery row, its findings and both projections commit, or none
        of them do and the caller can retry with the same identities.
        """
        with self._write() as conn:
            return self._ingest(conn, run_id, provider, delivery)

    def _ingest(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        provider: str,
        delivery: Delivery,
    ) -> IngestResult:
        run = conn.execute(
            "SELECT provider FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise ValueError("unknown run %r" % (run_id,))
        if run["provider"] != provider:
            raise ValueError(
                "provider %r does not belong to run %r" % (provider, run_id)
            )

        observation = delivery.observation

        # Transport retry: the same attempt id is the same delivery. Return what was
        # recorded and change nothing, so the naive side does not count it twice.
        # The claim is compared first: an attempt id reused with different content is
        # not a retry, and treating it as one would drop evidence silently.
        recorded = conn.execute(
            """
            SELECT attempt_id, event_id, refund_id, status, amount_paise, currency,
                   outcome
            FROM deliveries
            WHERE run_id = ? AND provider = ? AND attempt_id = ?
            """,
            (run_id, provider, delivery.attempt_id),
        ).fetchone()
        if recorded is not None:
            stored_claim = (
                recorded["event_id"],
                recorded["refund_id"],
                recorded["status"],
                recorded["amount_paise"],
                recorded["currency"],
            )
            incoming_claim = (
                observation.event_id,
                observation.refund_id,
                observation.status,
                observation.amount_paise,
                observation.currency,
            )
            if incoming_claim == stored_claim:
                return IngestResult(
                    observation.event_id, recorded["outcome"], recorded=False
                )
            # Same attempt id, different claim. The original row is left untouched,
            # the incoming claim is kept as finding evidence, and neither projection
            # sees it, so no total moves.
            #
            # The key carries the claim itself, so a second, different conflict gets
            # its own finding instead of overwriting the first. Repeating an
            # identical claim produces the same key and stays idempotent.
            claim_digest = hashlib.sha256(repr(incoming_claim).encode("utf-8")).hexdigest()[:12]
            self._add_finding(
                conn,
                run_id,
                "attempt_conflict:%s:%s" % (delivery.attempt_id, claim_digest),
                "attempt_identity_conflict",
                refund_id=observation.refund_id,
                event_id=observation.event_id,
                detail="attempt id %s reused with a different claim: stored %r, received %r"
                % (delivery.attempt_id, stored_claim, incoming_claim),
            )
            return IngestResult(
                observation.event_id, "attempt_conflict", recorded=True
            )

        accepted_row = conn.execute(
            """
            SELECT attempt_id, refund_id, status, amount_paise, currency
            FROM deliveries
            WHERE run_id = ? AND provider = ? AND event_id = ? AND accepted = 1
            """,
            (run_id, provider, observation.event_id),
        ).fetchone()

        conflict = 0
        if accepted_row is None:
            accepted = 1
            event_outcome = "accepted"
        else:
            same_claim = (
                accepted_row["refund_id"] == observation.refund_id
                and accepted_row["status"] == observation.status
                and accepted_row["amount_paise"] == observation.amount_paise
                and accepted_row["currency"] == observation.currency
            )
            accepted = 0
            if same_claim:
                event_outcome = "duplicate_event"
            else:
                # Keep both payloads: this row holds the variant, the accepted row
                # keeps the original, and both are marked conflicted.
                event_outcome = "conflicting_payload"
                conflict = 1
                conn.execute(
                    """
                    UPDATE deliveries SET conflict = 1
                    WHERE run_id = ? AND provider = ? AND event_id = ? AND accepted = 1
                    """,
                    (run_id, provider, observation.event_id),
                )

        refund_known = (
            conn.execute(
                "SELECT 1 FROM refund_intents WHERE run_id = ? AND refund_id = ?",
                (run_id, observation.refund_id),
            ).fetchone()
            is not None
        )
        outcome = event_outcome if refund_known else "unknown_refund"

        seq = conn.execute(
            """
            SELECT COALESCE(MAX(seq), 0) + 1 AS next
            FROM deliveries WHERE run_id = ? AND provider = ?
            """,
            (run_id, provider),
        ).fetchone()["next"]

        conn.execute(
            """
            INSERT INTO deliveries (
                run_id, provider, seq, attempt_id, event_id, refund_id,
                status, amount_paise, currency, source, accepted, conflict, outcome, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                provider,
                seq,
                delivery.attempt_id,
                observation.event_id,
                observation.refund_id,
                observation.status,
                observation.amount_paise,
                observation.currency,
                delivery.origin,
                accepted,
                conflict,
                outcome,
                _now(),
            ),
        )

        if not refund_known:
            self._add_finding(
                conn,
                run_id,
                "unknown_refund:%s" % observation.refund_id,
                "unknown_refund",
                refund_id=observation.refund_id,
                event_id=observation.event_id,
                detail="delivery for a refund that was never admitted",
            )
        if conflict:
            self._add_finding(
                conn,
                run_id,
                "event_conflict:%s" % observation.event_id,
                "event_identity_conflict",
                refund_id=None,
                event_id=observation.event_id,
                detail="one event id carried different claims",
            )

        self._apply_projections(conn, run_id)
        return IngestResult(observation.event_id, outcome, recorded=True)

    def _add_finding(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        key: str,
        kind: str,
        *,
        refund_id: str | None,
        event_id: str | None,
        detail: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO findings
                (run_id, finding_key, kind, refund_id, event_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, finding_key) DO UPDATE SET
                kind = excluded.kind,
                detail = excluded.detail
            """,
            (run_id, key, kind, refund_id, event_id, detail, _now()),
        )

    def _apply_projections(self, conn: sqlite3.Connection, run_id: str) -> None:
        """Recompute persisted results from stored evidence inside the caller's txn."""
        payments = self._payments(conn, run_id)
        intents = self._intents(conn, run_id)
        deliveries = self._deliveries(conn, run_id)

        for payment in payments:
            report = evaluate(payment, intents, deliveries)
            for row in report.rows:
                conn.execute(
                    UPSERT_REFUND_STATE,
                    (
                        run_id,
                        row.refund_id,
                        row.protected_status,
                        row.protected_confirmed_paise,
                        row.protected_review_paise,
                        ",".join(row.reasons),
                    ),
                )
                conn.execute(
                    UPSERT_NAIVE_STATE,
                    (
                        run_id,
                        row.refund_id,
                        row.naive_status,
                        row.naive_total_paise,
                    ),
                )

    # -- scheduled steps ------------------------------------------------

    def apply_step(
        self,
        run_id: str,
        provider: str,
        expected_cursor: int,
        schedule: Sequence[tuple[int, Delivery]],
    ) -> StepOutcome:
        """Apply exactly one scheduled delivery.

        A repeat of an already applied step returns the recorded outcome and changes
        nothing. The cursor and the delivery it consumed commit together, so a
        failure leaves the schedule where it was.
        """
        with self._write() as conn:
            cursor = self._cursor(conn, run_id)
            if expected_cursor > cursor:
                raise StepConflict(
                    "expected cursor %d is ahead of the run at %d"
                    % (expected_cursor, cursor)
                )
            if expected_cursor < cursor:
                row = conn.execute(
                    """
                    SELECT subject, tick, event_id, outcome FROM run_actions
                    WHERE run_id = ? AND kind = 'delivery' AND cursor = ?
                    """,
                    (run_id, expected_cursor),
                ).fetchone()
                if row is None:
                    raise StepConflict("no recorded step at cursor %d" % expected_cursor)
                return StepOutcome(
                    cursor=expected_cursor,
                    tick=row["tick"],
                    attempt_id=row["subject"],
                    event_id=row["event_id"],
                    outcome=row["outcome"],
                    applied=False,
                    next_cursor=cursor,
                )
            if cursor >= len(schedule):
                state = conn.execute(
                    "SELECT tick FROM run_state WHERE run_id = ?", (run_id,)
                ).fetchone()
                return StepOutcome(cursor, state["tick"], None, None, "finished", False, cursor)

            step = self._apply_one(conn, run_id, provider, cursor, schedule)
            return StepOutcome(
                cursor=step["cursor"],
                tick=step["tick"],
                attempt_id=step["attempt_id"],
                event_id=step["event_id"],
                outcome=step["outcome"],
                applied=True,
                next_cursor=cursor + 1,
            )

    def apply_remaining(
        self,
        run_id: str,
        provider: str,
        expected_cursor: int,
        schedule: Sequence[tuple[int, Delivery]],
    ) -> int:
        """Apply every remaining delivery in one transaction. Returns the cursor."""
        with self._write() as conn:
            cursor = self._cursor(conn, run_id)
            if expected_cursor > cursor:
                raise StepConflict(
                    "expected cursor %d is ahead of the run at %d"
                    % (expected_cursor, cursor)
                )
            if expected_cursor < cursor:
                return cursor
            while cursor < len(schedule):
                self._apply_one(conn, run_id, provider, cursor, schedule)
                cursor += 1
            return cursor

    def _cursor(self, conn: sqlite3.Connection, run_id: str) -> int:
        row = conn.execute(
            "SELECT cursor FROM run_state WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown run %r" % (run_id,))
        return row["cursor"]

    def _apply_one(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        provider: str,
        index: int,
        schedule: Sequence[tuple[int, Delivery]],
    ) -> dict:
        tick, delivery = schedule[index]
        state = conn.execute(
            "SELECT tick FROM run_state WHERE run_id = ?", (run_id,)
        ).fetchone()
        if tick < state["tick"]:
            raise ValueError(
                "delivery at tick %d is before the run clock %d" % (tick, state["tick"])
            )
        result = self._ingest(conn, run_id, provider, delivery)
        self._add_action(
            conn,
            run_id,
            kind="delivery",
            tick=tick,
            cursor=index,
            subject=delivery.attempt_id,
            event_id=delivery.observation.event_id,
            outcome=result.outcome,
        )
        conn.execute(
            "UPDATE run_state SET cursor = ?, tick = ? WHERE run_id = ?",
            (index + 1, tick, run_id),
        )
        return {
            "cursor": index,
            "tick": tick,
            "attempt_id": delivery.attempt_id,
            "event_id": delivery.observation.event_id,
            "outcome": result.outcome,
        }

    def _add_action(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        *,
        kind: str,
        tick: int,
        cursor: int | None,
        subject: str,
        event_id: str | None,
        outcome: str,
    ) -> None:
        seq = conn.execute(
            "SELECT action_seq FROM run_state WHERE run_id = ?", (run_id,)
        ).fetchone()["action_seq"]
        conn.execute(
            """
            INSERT INTO run_actions
                (run_id, seq, kind, tick, cursor, subject, event_id, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, seq, kind, tick, cursor, subject, event_id, outcome),
        )
        conn.execute(
            "UPDATE run_state SET action_seq = ? WHERE run_id = ?",
            (seq + 1, run_id),
        )

    def record_lookup(
        self,
        run_id: str,
        provider: str,
        refund_id: str,
        tick: int,
        outcome: str,
        observation: Observation | None,
        schedule_ticks: Sequence[int] = (),
    ) -> LookupRecord:
        """Record one provider query and its evidence.

        A query at the same refund and tick is the same query. Repeating it returns
        the recorded outcome and adds nothing, so pressing reconcile twice cannot
        manufacture evidence.

        The clock is re-checked here, inside the write transaction. Callers validate
        before querying the provider, which happens outside the lock, so the run can
        move on in between. Applying a delivery between that check and this write
        used to leave an action whose tick was behind the run, which broke replay.
        A stale result is rejected before anything is written.
        """
        with self._write() as conn:
            state = conn.execute(
                "SELECT tick, cursor FROM run_state WHERE run_id = ?", (run_id,)
            ).fetchone()
            if state is None:
                raise ValueError("unknown run %r" % (run_id,))

            clock = state["tick"]
            cursor = state["cursor"]
            if tick < clock:
                raise LookupStale(
                    "lookup at tick %d is stale: the run reached tick %d before it "
                    "could be recorded" % (tick, clock)
                )
            if cursor < len(schedule_ticks):
                next_tick = schedule_ticks[cursor]
                if tick > next_tick:
                    raise LookupStale(
                        "lookup at tick %d is stale: the next unapplied delivery moved "
                        "to tick %d" % (tick, next_tick)
                    )

            existing = conn.execute(
                """
                SELECT outcome FROM run_actions
                WHERE run_id = ? AND kind = 'lookup' AND subject = ? AND tick = ?
                """,
                (run_id, refund_id, tick),
            ).fetchone()
            if existing is not None:
                return LookupRecord(
                    refund_id, tick, existing["outcome"], False, observation
                )

            if observation is not None:
                self._ingest(
                    conn,
                    run_id,
                    provider,
                    Delivery(
                        "lookup:%s:%d" % (refund_id, tick),
                        observation,
                        origin="lookup",
                    ),
                )
            else:
                # No evidence, so no observation. The refund keeps its last known
                # state and the failed query stays visible as a finding.
                self._add_finding(
                    conn,
                    run_id,
                    "lookup_%s:%s@%d" % (outcome, refund_id, tick),
                    "lookup_%s" % outcome,
                    refund_id=refund_id,
                    event_id=None,
                    detail="provider lookup returned %s at tick %d" % (outcome, tick),
                )

            self._add_action(
                conn,
                run_id,
                kind="lookup",
                tick=tick,
                cursor=None,
                subject=refund_id,
                event_id=observation.event_id if observation else None,
                outcome=outcome,
            )
            # A query is also a clock read, so it moves the logical clock forward.
            conn.execute(
                "UPDATE run_state SET tick = ? WHERE run_id = ? AND tick < ?",
                (tick, run_id, tick),
            )
            return LookupRecord(refund_id, tick, outcome, True, observation)

    def run_state(self, run_id: str) -> dict:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM run_state WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown run %r" % (run_id,))
            return dict(row)
        finally:
            conn.close()

    def steps(self, run_id: str) -> tuple[dict, ...]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT cursor, tick, subject AS attempt_id, event_id, outcome
                FROM run_actions
                WHERE run_id = ? AND kind = 'delivery'
                ORDER BY cursor
                """,
                (run_id,),
            ).fetchall()
            return tuple(dict(row) for row in rows)
        finally:
            conn.close()

    def actions(self, run_id: str) -> tuple[dict, ...]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT seq, kind, tick, cursor, subject, event_id, outcome
                FROM run_actions
                WHERE run_id = ?
                ORDER BY seq
                """,
                (run_id,),
            ).fetchall()
            return tuple(dict(row) for row in rows)
        finally:
            conn.close()

    # -- reads -----------------------------------------------------------

    def report(self, run_id: str, payment_id: str) -> PaymentReport:
        conn = self._connect()
        try:
            return self._report(conn, run_id, payment_id)
        finally:
            conn.close()

    def _report(
        self, conn: sqlite3.Connection, run_id: str, payment_id: str
    ) -> PaymentReport:
        row = conn.execute(
            """
            SELECT captured_paise, currency FROM payments
            WHERE run_id = ? AND payment_id = ?
            """,
            (run_id, payment_id),
        ).fetchone()
        if row is None:
            raise ValueError("unknown payment %r" % (payment_id,))
        payment = Payment(payment_id, row["captured_paise"], row["currency"])
        return evaluate(payment, self._intents(conn, run_id), self._deliveries(conn, run_id))

    def refund_states(self, run_id: str) -> dict[str, dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM refund_states WHERE run_id = ? ORDER BY refund_id",
                (run_id,),
            ).fetchall()
            return {row["refund_id"]: dict(row) for row in rows}
        finally:
            conn.close()

    def finding_keys(self, run_id: str) -> tuple[str, ...]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT finding_key FROM findings WHERE run_id = ? ORDER BY finding_key",
                (run_id,),
            ).fetchall()
            return tuple(row["finding_key"] for row in rows)
        finally:
            conn.close()

    def findings(self, run_id: str) -> tuple[dict, ...]:
        """Full finding rows, including the retained evidence in detail."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT finding_key, kind, refund_id, event_id, detail, created_at
                FROM findings WHERE run_id = ? ORDER BY finding_key
                """,
                (run_id,),
            ).fetchall()
            return tuple(dict(row) for row in rows)
        finally:
            conn.close()

    def refund_count(self, run_id: str) -> int:
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT COUNT(*) AS n FROM refund_intents WHERE run_id = ?", (run_id,)
            ).fetchone()["n"]
        finally:
            conn.close()

    # -- row mapping -----------------------------------------------------

    def _payments(self, conn: sqlite3.Connection, run_id: str) -> tuple[Payment, ...]:
        rows = conn.execute(
            "SELECT payment_id, captured_paise, currency FROM payments WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        return tuple(
            Payment(row["payment_id"], row["captured_paise"], row["currency"])
            for row in rows
        )

    def _intents(
        self, conn: sqlite3.Connection, run_id: str
    ) -> tuple[RefundIntent, ...]:
        rows = conn.execute(
            """
            SELECT refund_id, payment_id, amount_paise, currency
            FROM refund_intents WHERE run_id = ? ORDER BY refund_id
            """,
            (run_id,),
        ).fetchall()
        return tuple(
            RefundIntent(
                row["refund_id"],
                row["payment_id"],
                row["amount_paise"],
                row["currency"],
            )
            for row in rows
        )

    def _deliveries(
        self, conn: sqlite3.Connection, run_id: str
    ) -> tuple[Delivery, ...]:
        rows = conn.execute(
            """
            SELECT attempt_id, event_id, refund_id, status, amount_paise, currency, source
            FROM deliveries WHERE run_id = ? ORDER BY seq
            """,
            (run_id,),
        ).fetchall()
        return tuple(
            Delivery(
                row["attempt_id"],
                Observation(
                    row["event_id"],
                    row["refund_id"],
                    row["status"],
                    row["amount_paise"],
                    row["currency"],
                ),
                row["source"],
            )
            for row in rows
        )