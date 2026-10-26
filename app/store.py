from __future__ import annotations

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
        created_at TEXT NOT NULL
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
            conn.execute("PRAGMA user_version = 1")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    # -- setup -----------------------------------------------------------

    def create_run(self, run_id: str, provider: str) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, provider, created_at) VALUES (?, ?, ?)",
                (run_id, provider, _now()),
            )

    def add_payment(self, run_id: str, payment: Payment) -> None:
        with self._write() as conn:
            conn.execute(
                """
                INSERT INTO payments (run_id, payment_id, captured_paise, currency)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, payment.payment_id, payment.captured_paise, payment.currency),
            )

    # -- admission -------------------------------------------------------

    def admit(self, run_id: str, refund: RefundIntent) -> AdmissionResult:
        """Admit a refund inside one write transaction.

        The cap check and the insert share the transaction and the transaction holds
        the write lock first, so two concurrent requests cannot both read 400 of
        1000 and both admit 600.
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
        recorded = conn.execute(
            """
            SELECT outcome FROM deliveries
            WHERE run_id = ? AND provider = ? AND attempt_id = ?
            """,
            (run_id, provider, delivery.attempt_id),
        ).fetchone()
        if recorded is not None:
            return IngestResult(observation.event_id, recorded["outcome"], recorded=False)

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
                status, amount_paise, currency, accepted, conflict, outcome, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            SELECT attempt_id, event_id, refund_id, status, amount_paise, currency
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
            )
            for row in rows
        )