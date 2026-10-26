from __future__ import annotations

import sqlite3
import threading

import pytest

from app.domain.model import Delivery, Observation, Payment, RefundIntent
from app.store import Store

RUN = "run_1"
PROVIDER = "psp_a"


@pytest.fixture()
def store(tmp_path):
    instance = Store(tmp_path / "lab.db")
    instance.initialize()
    instance.create_run(RUN, PROVIDER)
    instance.add_payment(RUN, Payment("pay_A", 100_000))
    return instance


def delivery(
    attempt_id: str,
    event_id: str,
    refund_id: str,
    status: str,
    amount_paise: int = 30_000,
) -> Delivery:
    return Delivery(
        attempt_id,
        Observation(event_id, refund_id, status, amount_paise),
    )


def counts(db_path):
    conn = sqlite3.connect(db_path)
    try:
        deliveries = conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0]
        states = conn.execute("SELECT COUNT(*) FROM refund_states").fetchone()[0]
        return deliveries, states
    finally:
        conn.close()


def accepted_count(db_path, event_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM deliveries WHERE event_id = ? AND accepted = 1",
            (event_id,),
        ).fetchone()[0]
    finally:
        conn.close()


def run_together(calls):
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)
    errors = []

    def worker(index, call):
        try:
            barrier.wait(timeout=10)
            results[index] = call()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(index, call))
        for index, call in enumerate(calls)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors, errors
    return results


# -- admission -----------------------------------------------------------


def test_admission_is_persisted_and_capped(store):
    assert store.admit(RUN, RefundIntent("R1", "pay_A", 30_000)).accepted is True
    assert store.admit(RUN, RefundIntent("R2", "pay_A", 20_000)).accepted is True

    refused = store.admit(RUN, RefundIntent("R3", "pay_A", 60_000))

    assert refused.accepted is False
    assert refused.reason == "over_refund"
    assert store.refund_count(RUN) == 2
    assert store.report(RUN, "pay_A").admitted_paise == 50_000


def test_two_concurrent_over_cap_requests_admit_only_one(store):
    results = run_together(
        [
            lambda: store.admit(RUN, RefundIntent("R3", "pay_A", 60_000)),
            lambda: store.admit(RUN, RefundIntent("R4", "pay_A", 60_000)),
        ]
    )

    accepted = [result for result in results if result.accepted]
    refused = [result for result in results if not result.accepted]

    assert len(accepted) == 1
    assert len(refused) == 1
    assert refused[0].reason == "over_refund"
    assert store.refund_count(RUN) == 1
    assert store.report(RUN, "pay_A").admitted_paise == 60_000


def test_refund_for_an_unknown_payment_is_refused(store):
    result = store.admit(RUN, RefundIntent("R1", "pay_missing", 30_000))

    assert result.accepted is False
    assert result.reason == "unknown_payment"
    assert store.refund_count(RUN) == 0


# -- delivery processing -------------------------------------------------


def test_duplicate_delivery_is_kept_and_counted_once(store):
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))

    first = store.ingest(RUN, PROVIDER, delivery("a1", "E8", "R1", "succeeded"))
    second = store.ingest(RUN, PROVIDER, delivery("a2", "E8", "R1", "succeeded"))

    assert first.outcome == "accepted"
    assert second.outcome == "duplicate_event"
    assert counts(store.db_path) == (2, 1)
    assert accepted_count(store.db_path, "E8") == 1

    state = store.refund_states(RUN)["R1"]
    assert state["status"] == "succeeded"
    assert state["confirmed_paise"] == 30_000
    # The naive baseline counts per delivery attempt, which is the point of it.
    assert store.report(RUN, "pay_A").naive_total_paise == 60_000


def test_two_concurrent_deliveries_of_one_event(store):
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))

    results = run_together(
        [
            lambda: store.ingest(RUN, PROVIDER, delivery("a1", "E8", "R1", "succeeded")),
            lambda: store.ingest(RUN, PROVIDER, delivery("a2", "E8", "R1", "succeeded")),
        ]
    )

    assert sorted(result.outcome for result in results) == ["accepted", "duplicate_event"]
    assert accepted_count(store.db_path, "E8") == 1
    assert counts(store.db_path)[0] == 2
    assert store.refund_states(RUN)["R1"]["confirmed_paise"] == 30_000


def test_repeating_a_coordinator_request_adds_no_delivery(store):
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))
    first = store.ingest(RUN, PROVIDER, delivery("a1", "E8", "R1", "succeeded"))

    again = store.ingest(RUN, PROVIDER, delivery("a1", "E8", "R1", "succeeded"))

    assert first.recorded is True
    assert again.recorded is False
    assert again.outcome == "accepted"
    assert counts(store.db_path)[0] == 1
    assert store.report(RUN, "pay_A").naive_total_paise == 30_000


def test_conflicting_payload_is_kept_and_forces_review(store):
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))

    first = store.ingest(RUN, PROVIDER, delivery("a1", "E8", "R1", "succeeded"))
    second = store.ingest(RUN, PROVIDER, delivery("a2", "E8", "R1", "failed"))

    assert first.outcome == "accepted"
    assert second.outcome == "conflicting_payload"
    assert counts(store.db_path)[0] == 2
    assert accepted_count(store.db_path, "E8") == 1

    state = store.refund_states(RUN)["R1"]
    assert state["status"] == "review"
    assert state["confirmed_paise"] == 0
    assert state["reasons"] == "event_identity_conflict"
    assert "event_conflict:E8" in store.finding_keys(RUN)


def test_event_id_reused_for_two_refunds_marks_both_under_review(store):
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))
    store.admit(RUN, RefundIntent("R2", "pay_A", 20_000))

    store.ingest(RUN, PROVIDER, delivery("a1", "E8", "R1", "succeeded", 30_000))
    store.ingest(RUN, PROVIDER, delivery("a2", "E8", "R2", "succeeded", 20_000))

    states = store.refund_states(RUN)
    assert states["R1"]["status"] == "review"
    assert states["R2"]["status"] == "review"
    assert states["R1"]["confirmed_paise"] == 0
    assert states["R2"]["confirmed_paise"] == 0
    assert "event_conflict:E8" in store.finding_keys(RUN)


def test_unknown_refund_creates_a_finding_and_no_refund(store):
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))
    store.ingest(RUN, PROVIDER, delivery("a1", "E8", "R1", "succeeded"))

    result = store.ingest(
        RUN, PROVIDER, delivery("a2", "E99", "R9", "succeeded", 99_000)
    )

    assert result.outcome == "unknown_refund"
    assert store.refund_count(RUN) == 1
    assert "unknown_refund:R9" in store.finding_keys(RUN)

    report = store.report(RUN, "pay_A")
    assert report.protected_confirmed_paise == 30_000
    assert report.naive_total_paise == 30_000
    assert [observation.refund_id for observation in report.unmatched] == ["R9"]
    assert report.admitted_paise == 30_000


def test_unique_index_rejects_two_accepted_claims(store):
    """The database refuses a second accepted claim even if code skips the check."""
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))
    conn = sqlite3.connect(store.db_path, isolation_level=None)
    insert = """
        INSERT INTO deliveries (
            run_id, provider, seq, attempt_id, event_id, refund_id,
            status, amount_paise, currency, accepted, conflict, outcome, received_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            insert,
            (RUN, PROVIDER, 1, "x1", "E8", "R1", "succeeded", 30_000, "INR", 1, 0, "accepted", "t"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                insert,
                (RUN, PROVIDER, 2, "x2", "E8", "R1", "succeeded", 30_000, "INR", 1, 0, "accepted", "t"),
            )
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.close()


def test_provider_mismatch_is_refused(store):
    with pytest.raises(ValueError):
        store.ingest(RUN, "psp_other", delivery("a1", "E8", "R1", "succeeded"))


# -- atomicity and durability -------------------------------------------


def test_failure_between_delivery_and_projection_rolls_back(store, monkeypatch):
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))

    def boom(conn, run_id):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(store, "_apply_projections", boom)
    with pytest.raises(RuntimeError):
        store.ingest(RUN, PROVIDER, delivery("a1", "E8", "R1", "succeeded"))

    # No delivery survived and the refund is still pending. The one state row comes
    # from admission, not from the failed ingest.
    assert counts(store.db_path) == (0, 1)
    pending = store.refund_states(RUN)["R1"]
    assert pending["status"] == "pending"
    assert pending["confirmed_paise"] == 0

    monkeypatch.undo()
    result = store.ingest(RUN, PROVIDER, delivery("a1", "E8", "R1", "succeeded"))

    assert result.recorded is True
    assert counts(store.db_path) == (1, 1)
    state = store.refund_states(RUN)["R1"]
    assert state["status"] == "succeeded"
    assert state["confirmed_paise"] == 30_000
    assert store.report(RUN, "pay_A").naive_total_paise == 30_000


def test_saved_state_survives_a_reopened_database(store):
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))
    store.ingest(RUN, PROVIDER, delivery("a1", "E8", "R1", "succeeded"))

    reopened = Store(store.db_path)

    state = reopened.refund_states(RUN)["R1"]
    assert state["status"] == "succeeded"
    assert state["confirmed_paise"] == 30_000
    assert reopened.report(RUN, "pay_A").protected_confirmed_paise == 30_000


# -- D2: an attempt id reused with different content ----------------------


@pytest.mark.parametrize(
    "changed",
    [
        {"event_id": "E9"},
        {"refund_id": "R2"},
        {"status": "failed"},
        {"amount_paise": 35_000},
        {"currency": "EUR"},
    ],
)
def test_attempt_id_reused_with_different_content_is_quarantined(store, changed):
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))
    store.admit(RUN, RefundIntent("R2", "pay_A", 20_000))
    store.ingest(RUN, PROVIDER, delivery("a1", "E8", "R1", "succeeded"))

    original = {"event_id": "E8", "refund_id": "R1", "status": "succeeded", "amount_paise": 30_000}
    claim = {**original, **changed}
    reused = store.ingest(
        RUN,
        PROVIDER,
        Delivery(
            "a1",
            Observation(
                claim["event_id"],
                claim["refund_id"],
                claim["status"],
                claim["amount_paise"],
                claim.get("currency", "INR"),
            ),
        ),
    )

    assert reused.outcome == "attempt_conflict"
    assert reused.recorded is True

    # The original row is untouched and the conflicting claim is not a delivery.
    assert counts(store.db_path)[0] == 1
    conn = sqlite3.connect(store.db_path)
    try:
        row = conn.execute(
            "SELECT event_id, refund_id, status, amount_paise, currency FROM deliveries"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("E8", "R1", "succeeded", 30_000, "INR")

    # No total moved, and the attempted payload is retained as evidence.
    report = store.report(RUN, "pay_A")
    assert report.naive_total_paise == 30_000
    assert store.refund_states(RUN)["R2"]["status"] == "pending"
    finding = store.findings(RUN)[-1]
    assert finding["finding_key"].startswith("attempt_conflict:a1:")
    assert finding["kind"] == "attempt_identity_conflict"
    for value in changed.values():
        assert str(value) in finding["detail"]


def test_identical_retry_stays_harmless(store):
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))
    first = store.ingest(RUN, PROVIDER, delivery("a1", "E8", "R1", "succeeded"))

    again = store.ingest(RUN, PROVIDER, delivery("a1", "E8", "R1", "succeeded"))

    assert again.recorded is False
    assert again.outcome == first.outcome
    assert counts(store.db_path)[0] == 1
    assert store.findings(RUN) == ()
    assert store.report(RUN, "pay_A").naive_total_paise == 30_000


def test_attempt_conflict_evidence_survives_reopening(store):
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))
    store.ingest(RUN, PROVIDER, delivery("a1", "E8", "R1", "succeeded"))
    store.ingest(RUN, PROVIDER, delivery("a1", "E99", "R1", "failed", 35_000))

    reopened = Store(store.db_path)

    findings = reopened.findings(RUN)
    assert len(findings) == 1
    assert findings[0]["finding_key"].startswith("attempt_conflict:a1:")
    detail = findings[0]["detail"]
    assert "E99" in detail and "failed" in detail and "35000" in detail
    assert "E8" in detail and "succeeded" in detail
    assert counts(store.db_path)[0] == 1
    assert reopened.refund_states(RUN)["R1"]["status"] == "succeeded"


# -- D4: repeated conflicts under one attempt id keep separate evidence -----


def test_repeated_attempt_conflicts_keep_each_claim(store):
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))
    store.ingest(RUN, PROVIDER, delivery("a1", "E1", "R1", "succeeded"))

    store.ingest(RUN, PROVIDER, delivery("a1", "E2", "R1", "failed", 31_000))
    store.ingest(RUN, PROVIDER, delivery("a1", "E3", "R1", "pending", 32_000))

    findings = store.findings(RUN)
    assert len(findings) == 2
    assert all(row["kind"] == "attempt_identity_conflict" for row in findings)

    by_event = {row["event_id"]: row for row in findings}
    assert set(by_event) == {"E2", "E3"}
    # Each finding describes the claim its own event id names.
    assert "E2" in by_event["E2"]["detail"]
    assert "E3" in by_event["E3"]["detail"]
    assert "E3" not in by_event["E2"]["detail"]
    assert "E2" not in by_event["E3"]["detail"]

    # The original delivery is untouched and no conflicting claim got in.
    assert counts(store.db_path)[0] == 1
    assert store.refund_states(RUN)["R1"]["status"] == "succeeded"
    assert store.refund_states(RUN)["R1"]["confirmed_paise"] == 30_000
    assert store.report(RUN, "pay_A").naive_total_paise == 30_000


def test_repeating_an_identical_conflict_is_idempotent(store):
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))
    store.ingest(RUN, PROVIDER, delivery("a1", "E1", "R1", "succeeded"))

    first = store.ingest(RUN, PROVIDER, delivery("a1", "E2", "R1", "failed", 31_000))
    second = store.ingest(RUN, PROVIDER, delivery("a1", "E2", "R1", "failed", 31_000))

    assert first.outcome == second.outcome == "attempt_conflict"
    assert len(store.findings(RUN)) == 1
    assert counts(store.db_path)[0] == 1


def test_repeated_attempt_conflicts_survive_reopening(store):
    store.admit(RUN, RefundIntent("R1", "pay_A", 30_000))
    store.ingest(RUN, PROVIDER, delivery("a1", "E1", "R1", "succeeded"))
    store.ingest(RUN, PROVIDER, delivery("a1", "E2", "R1", "failed", 31_000))
    store.ingest(RUN, PROVIDER, delivery("a1", "E3", "R1", "pending", 32_000))

    reopened = Store(store.db_path)

    keys = [row["finding_key"] for row in reopened.findings(RUN)]
    assert len(keys) == 2
    assert len(set(keys)) == 2
    assert counts(store.db_path)[0] == 1
