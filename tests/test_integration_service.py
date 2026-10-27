from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from app.errors import LabError
from app.integration import IntegrationError, IntegrationService
from app.lab_service import LabService
from app.sim.fixture import load_fixture
from app.sim.runner import Runner
from app.store import Store

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture()
def store(tmp_path):
    instance = Store(tmp_path / "integration.db")
    instance.initialize()
    return instance


@pytest.fixture()
def service(store):
    return IntegrationService(store)


def workspace(service, captured=100_000):
    return service.create_workspace("psp_x", "pay_1", captured)


def count_rows(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    finally:
        conn.close()


# -- workspace -----------------------------------------------------------


def test_workspace_starts_without_a_fixture(store, service):
    created = workspace(service)

    state = store.run_state(created.workspace_id)
    assert state["fixture_text"] == ""
    assert state["fixture_name"] == ""
    assert store.run_kind(created.workspace_id) == "integration"

    status = service.status(created.workspace_id)
    assert status.refunds == ()
    assert status.admitted_paise == 0
    assert status.confirmed_paise == 0
    assert status.review_paise == 0
    assert status.captured_paise == 100_000


def test_workspace_and_payment_are_created_together(store, service):
    created = workspace(service)

    assert store.run_kind(created.workspace_id) == "integration"
    payments = store.run_payments(created.workspace_id)
    assert len(payments) == 1
    assert payments[0].payment_id == "pay_1"
    assert payments[0].captured_paise == 100_000


def test_creation_failure_leaves_no_partial_workspace(store, service, monkeypatch):
    def boom(conn, run_id, payment):
        raise RuntimeError("injected failure before the payment row")

    monkeypatch.setattr(store, "_insert_payment", boom)

    with pytest.raises(RuntimeError):
        workspace(service)

    assert count_rows(store.db_path, "runs") == 0
    assert count_rows(store.db_path, "run_state") == 0
    assert count_rows(store.db_path, "payments") == 0


def test_existing_database_gets_the_additive_column(tmp_path):
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    try:
        conn.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO runs VALUES ('old_run', 'psp_a', '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    store = Store(legacy)
    store.initialize()

    assert store.run_kind("old_run") == "demo"
    conn = sqlite3.connect(legacy)
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(runs)")]
    finally:
        conn.close()
    assert "kind" in columns


# -- admission -----------------------------------------------------------


def test_two_partials_fit_and_a_third_exceeding_the_capture_is_refused(service):
    created = workspace(service)

    first = service.register_refund(created.workspace_id, "R1", 30_000)
    second = service.register_refund(created.workspace_id, "R2", 20_000)
    third = service.register_refund(created.workspace_id, "R3", 60_000)

    assert first.accepted is True and first.reason == "accepted"
    assert second.accepted is True
    assert second.admitted_paise == 50_000
    assert third.accepted is False
    assert third.reason == "over_refund"
    assert third.admitted_paise == 50_000

    status = service.status(created.workspace_id)
    assert status.admitted_paise == 50_000
    assert [row["refund_id"] for row in status.refunds] == ["R1", "R2"]


def test_registering_the_same_refund_twice_is_idempotent(service):
    created = workspace(service)

    service.register_refund(created.workspace_id, "R1", 30_000)
    again = service.register_refund(created.workspace_id, "R1", 30_000)

    assert again.accepted is True
    assert again.reason == "already_admitted"
    assert again.admitted_paise == 30_000


# -- ingest --------------------------------------------------------------


def test_repeated_success_notifications_confirm_one_amount(service):
    created = workspace(service)
    ws = created.workspace_id
    service.register_refund(ws, "R1", 30_000)

    first = service.submit_delivery(ws, "a1", "E8", "R1", "succeeded", 30_000)
    second = service.submit_delivery(ws, "a2", "E8", "R1", "succeeded", 30_000)
    third = service.submit_delivery(ws, "a3", "E8", "R1", "succeeded", 30_000)

    assert first.outcome == "accepted"
    assert second.outcome == "duplicate_event"
    assert third.outcome == "duplicate_event"

    status = service.status(ws)
    assert status.confirmed_paise == 30_000
    refund = status.refunds[0]
    assert refund["status"] == "succeeded"
    assert refund["confirmed_paise"] == 30_000


def test_identical_attempt_retry_is_harmless(service):
    created = workspace(service)
    ws = created.workspace_id
    service.register_refund(ws, "R1", 30_000)

    service.submit_delivery(ws, "a1", "E8", "R1", "succeeded", 30_000)
    again = service.submit_delivery(ws, "a1", "E8", "R1", "succeeded", 30_000)

    assert again.recorded is False
    assert service.status(ws).confirmed_paise == 30_000


def test_changed_claim_under_an_existing_identity_keeps_conflict_evidence(service):
    created = workspace(service)
    ws = created.workspace_id
    service.register_refund(ws, "R1", 30_000)
    service.submit_delivery(ws, "a1", "E1", "R1", "succeeded", 30_000)

    changed = service.submit_delivery(ws, "a1", "E2", "R1", "failed", 31_000)

    assert changed.outcome == "attempt_conflict"
    status = service.status(ws)
    assert status.confirmed_paise == 30_000
    assert len(status.findings) == 1
    finding = status.findings[0]
    assert finding["kind"] == "attempt_identity_conflict"
    assert "E2" in finding["detail"]
    assert "31000" in finding["detail"]

    # The original delivery still decides the refund.
    assert status.refunds[0]["status"] == "succeeded"


def test_notification_never_creates_a_refund_intent(service, store):
    created = workspace(service)
    ws = created.workspace_id

    result = service.submit_delivery(ws, "a1", "E9", "R9", "succeeded", 30_000)

    assert result.outcome == "unknown_refund"
    assert result.refund is None
    status = service.status(ws)
    assert status.refunds == ()
    assert status.admitted_paise == 0
    assert [row["refund_id"] for row in status.unmatched] == ["R9"]
    assert count_rows(store.db_path, "refund_intents") == 0


def test_amount_mismatch_is_quarantined(service):
    created = workspace(service)
    ws = created.workspace_id
    service.register_refund(ws, "R1", 30_000)

    service.submit_delivery(ws, "a1", "E8", "R1", "succeeded", 35_000)

    status = service.status(ws)
    assert status.confirmed_paise == 0
    assert status.review_paise == 30_000
    assert status.refunds[0]["status"] == "review"
    assert status.refunds[0]["reasons"] == ["amount_mismatch"]


def test_conflicting_event_payloads_stay_under_review(service):
    created = workspace(service)
    ws = created.workspace_id
    service.register_refund(ws, "R1", 30_000)

    service.submit_delivery(ws, "a1", "E8", "R1", "succeeded", 30_000)
    service.submit_delivery(ws, "a2", "E8", "R1", "failed", 30_000)

    status = service.status(ws)
    assert status.refunds[0]["status"] == "review"
    assert status.refunds[0]["reasons"] == ["event_identity_conflict"]
    assert status.confirmed_paise == 0


def test_pending_stays_unresolved(service):
    created = workspace(service)
    ws = created.workspace_id
    service.register_refund(ws, "R1", 30_000)

    service.submit_delivery(ws, "a1", "E7", "R1", "pending", 30_000)

    status = service.status(ws)
    assert status.refunds[0]["status"] == "pending"
    assert status.confirmed_paise == 0
    assert status.review_paise == 0


# -- response shape ------------------------------------------------------


def test_status_excludes_naive_results_and_simulator_clocks(service):
    created = workspace(service)
    ws = created.workspace_id
    service.register_refund(ws, "R1", 30_000)
    service.submit_delivery(ws, "a1", "E8", "R1", "succeeded", 30_000)

    payload = service.status(ws).to_dict()

    assert "naive_total_paise" not in payload["totals"]
    assert "naive" not in str(payload).lower()
    assert "cursor" not in payload
    assert "tick" not in payload
    assert payload["totals"] == {"confirmed_paise": 30_000, "review_paise": 0}
    assert payload["refunds"][0]["confirmed_paise"] == 30_000


# -- surface separation --------------------------------------------------


def test_demo_service_refuses_an_integration_workspace(store, service):
    created = workspace(service)
    lab = LabService(store)

    for call in (
        lambda: lab.report(created.workspace_id),
        lambda: lab.step(created.workspace_id, 0),
        lambda: lab.run_to_end(created.workspace_id, 0),
        lambda: lab.reconcile(created.workspace_id, "R1", 0),
        lambda: lab.replay(created.workspace_id),
    ):
        with pytest.raises(LabError) as exc:
            call()
        assert exc.value.status_code == 404


def test_integration_service_refuses_a_demo_run(store, service):
    runner = Runner(store)
    demo = runner.start(load_fixture(FIXTURES / "healthy.json"))

    with pytest.raises(IntegrationError) as status_error:
        service.status(demo)
    assert status_error.value.status == 404

    with pytest.raises(IntegrationError):
        service.register_refund(demo, "R1", 10_000)

    with pytest.raises(IntegrationError):
        service.submit_delivery(demo, "a9", "E9", "R1", "succeeded", 10_000)


def test_demo_runs_still_work(store):
    lab = LabService(store)
    runner = Runner(store)
    demo = runner.start(load_fixture(FIXTURES / "duplicate_success.json"))
    lab.run_to_end(demo, 0)

    report = lab.report(demo)
    assert report.body["comparison"]["protected_confirmed_paise"] == 30_000
    assert report.body["comparison"]["naive_total_paise"] == 90_000


def test_refund_limit_holds_under_concurrent_registration(store, service):
    """49 refunds, then two concurrent registrations. Exactly one may land."""
    created = workspace(service, captured=1_000_000)
    ws = created.workspace_id
    for index in range(49):
        result = service.register_refund(ws, "R%d" % index, 100)
        assert result.accepted is True

    barrier = threading.Barrier(2)
    results = []
    errors = []

    def register(refund_id):
        try:
            barrier.wait(timeout=10)
            results.append(service.register_refund(ws, refund_id, 100))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=register, args=("RA",)),
               threading.Thread(target=register, args=("RB",))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors, errors
    assert len(results) == 2
    accepted = [result for result in results if result.accepted]
    rejected = [result for result in results if not result.accepted]
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert rejected[0].reason == "refund_limit_reached"
    assert store.refund_count(ws) == 50


def test_identical_retry_still_works_at_the_limit(store, service):
    created = workspace(service, captured=1_000_000)
    ws = created.workspace_id
    for index in range(50):
        service.register_refund(ws, "R%d" % index, 100)

    again = service.register_refund(ws, "R0", 100)

    assert again.accepted is True
    assert again.reason == "already_admitted"
    assert store.refund_count(ws) == 50
