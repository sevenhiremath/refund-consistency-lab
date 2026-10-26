from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.sim.fixture import FixtureError, load_fixture, parse_fixture
from app.sim.provider import ProviderView
from app.sim.runner import Runner
from app.store import Store

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture()
def store(tmp_path):
    instance = Store(tmp_path / "lab.db")
    instance.initialize()
    return instance


def start(store, name):
    runner = Runner(store)
    run_id = runner.start(load_fixture(FIXTURES / ("%s.json" % name)))
    return runner, run_id


def refund_of(report, refund_id="R1"):
    for row in report.body["comparison"]["refunds"]:
        if row["refund_id"] == refund_id:
            return row
    raise AssertionError("no row for %s" % refund_id)


# -- recovery ------------------------------------------------------------


def test_lookup_recovers_a_missing_success(store):
    runner, run_id = start(store, "lookup_recovers_success")

    before = runner.report(run_id)
    assert before.body["comparison"]["protected_confirmed_paise"] == 0

    record = runner.reconcile(run_id, "R1", 3)
    report = runner.report(run_id)

    assert record.outcome == "succeeded"
    assert record.recorded is True
    assert record.observation is not None
    assert record.observation.amount_paise == 30_000
    assert report.body["comparison"]["protected_confirmed_paise"] == 30_000
    assert refund_of(report)["protected_status"] == "succeeded"

    # The naive side is webhook-only, so it never learns about the lookup.
    assert report.body["comparison"]["naive_total_paise"] == 0

    lookup_actions = [
        action for action in report.body["actions"] if action["kind"] == "lookup"
    ]
    assert len(lookup_actions) == 1
    assert lookup_actions[0]["tick"] == 3
    assert lookup_actions[0]["outcome"] == "succeeded"


def test_unavailable_lookup_is_not_treated_as_failure(store):
    runner, run_id = start(store, "lookup_unavailable")

    record = runner.reconcile(run_id, "R1", 3)
    report = runner.report(run_id)

    assert record.outcome == "unavailable"
    assert record.observation is None
    assert refund_of(report)["protected_status"] == "pending"
    assert refund_of(report)["protected_confirmed_paise"] == 0
    assert "lookup_unavailable:R1@3" in report.body["evidence"]["findings"]
    assert report.body["comparison"]["naive_total_paise"] == 0


def test_not_found_is_not_treated_as_failure(store):
    runner, run_id = start(store, "lookup_recovers_success")

    record = runner.reconcile(run_id, "R1", 1)
    report = runner.report(run_id)

    assert record.outcome == "not_found"
    assert record.observation is None
    assert refund_of(report)["protected_status"] == "pending"
    assert "lookup_not_found:R1@1" in report.body["evidence"]["findings"]


def test_wrong_amount_from_a_lookup_is_quarantined(store):
    runner, run_id = start(store, "lookup_wrong_amount")

    record = runner.reconcile(run_id, "R1", 3)
    report = runner.report(run_id)

    assert record.outcome == "succeeded"
    row = refund_of(report)
    assert row["protected_status"] == "review"
    assert row["reasons"] == ["amount_mismatch"]
    assert row["protected_confirmed_paise"] == 0
    assert row["protected_review_paise"] == 30_000


def test_conflicting_lookup_keeps_both_terminal_results_visible(store):
    runner, run_id = start(store, "lookup_conflict")
    runner.run_to_end(run_id, 0)

    before = refund_of(runner.report(run_id))
    assert before["protected_status"] == "failed"

    record = runner.reconcile(run_id, "R1", 4)
    report = runner.report(run_id)

    assert record.outcome == "succeeded"
    row = refund_of(report)
    assert row["protected_status"] == "review"
    assert row["reasons"] == ["terminal_conflict"]
    assert row["protected_confirmed_paise"] == 0

    outcomes = [action["outcome"] for action in report.body["actions"]]
    assert outcomes == ["accepted", "succeeded"]


# -- reconciliation safety ----------------------------------------------


def test_repeated_reconciliation_adds_nothing(store):
    runner, run_id = start(store, "lookup_recovers_success")

    first = runner.reconcile(run_id, "R1", 3)
    second = runner.reconcile(run_id, "R1", 3)

    report = runner.report(run_id)
    lookups = [a for a in report.body["actions"] if a["kind"] == "lookup"]

    assert first.recorded is True
    assert second.recorded is False
    assert len(lookups) == 1
    assert report.body["comparison"]["protected_confirmed_paise"] == 30_000


def test_lookup_does_not_create_a_refund_or_change_provider_truth(store):
    fixture = load_fixture(FIXTURES / "lookup_recovers_success.json")
    runner, run_id = start(store, "lookup_recovers_success")

    before_refunds = store.refund_count(run_id)
    before_truth = ProviderView(fixture).lookup("R1", 3)

    runner.reconcile(run_id, "R1", 3)

    assert store.refund_count(run_id) == before_refunds
    after_truth = ProviderView(fixture).lookup("R1", 3)
    assert after_truth == before_truth


def test_lookup_cannot_query_the_past(store):
    runner, run_id = start(store, "lookup_conflict")
    runner.run_to_end(run_id, 0)

    with pytest.raises(ValueError, match="cannot look up"):
        runner.reconcile(run_id, "R1", 1)


# -- replay and digest ---------------------------------------------------


def test_replay_reproduces_lookups(store):
    runner, run_id = start(store, "lookup_recovers_success")
    runner.reconcile(run_id, "R1", 3)
    original = runner.report(run_id)

    replayed_run = runner.replay(run_id)
    replayed = runner.report(replayed_run)

    assert replayed_run != run_id
    assert replayed.digest == original.digest
    assert replayed.body["actions"] == original.body["actions"]


def test_digest_depends_on_lookup_tick(store):
    runner_a, run_a = start(store, "lookup_recovers_success")
    runner_a.reconcile(run_a, "R1", 1)
    early = runner_a.report(run_a)

    runner_b, run_b = start(store, "lookup_recovers_success")
    runner_b.reconcile(run_b, "R1", 3)
    late = runner_b.report(run_b)

    assert early.body["actions"][0]["tick"] == 1
    assert late.body["actions"][0]["tick"] == 3
    assert early.digest != late.digest


def test_actions_record_delivery_then_lookup_in_order(store):
    runner, run_id = start(store, "lookup_conflict")
    runner.run_to_end(run_id, 0)
    runner.reconcile(run_id, "R1", 4)

    actions = store.actions(run_id)

    assert [action["kind"] for action in actions] == ["delivery", "lookup"]
    assert [action["seq"] for action in actions] == [0, 1]
    assert [action["tick"] for action in actions] == [2, 4]
    assert actions[0]["subject"] == "a1"
    assert actions[1]["subject"] == "R1"


# -- fixture validation --------------------------------------------------


def test_delivery_claim_must_match_its_event():
    data = json.loads((FIXTURES / "healthy.json").read_text(encoding="utf-8"))
    data["deliveries"][0]["payload"] = {"status": "failed"}

    with pytest.raises(FixtureError, match="does not match its provider event"):
        parse_fixture(json.dumps(data))


def test_marked_intentional_conflict_is_allowed():
    fixture = load_fixture(FIXTURES / "identity_conflict_refunds.json")

    claims = [item.delivery.observation for item in fixture.schedule]

    assert claims[0].refund_id == "R1"
    assert claims[1].refund_id == "R2"
    assert claims[1].event_id == "E8"


def test_intentional_conflict_reaches_the_reducers(store):
    runner, run_id = start(store, "identity_conflict_refunds")
    report = runner.run_to_end(run_id, 0)

    assert refund_of(report, "R1")["protected_status"] == "review"
    assert refund_of(report, "R2")["protected_status"] == "review"
    assert report.body["comparison"]["protected_confirmed_paise"] == 0
    assert "event_conflict:E8" in report.body["evidence"]["findings"]


# -- D1: a lookup may not move the clock past an unprocessed delivery -------


def test_future_lookup_is_rejected_and_the_run_still_completes(store):
    runner, run_id = start(store, "pending_after_success")

    # Next scheduled delivery is at tick 2. A query at tick 10 would move the clock
    # past it, so it is refused before anything is written.
    with pytest.raises(ValueError, match="past the next scheduled delivery"):
        runner.reconcile(run_id, "R1", 10)

    report = runner.report(run_id)
    assert report.body["actions"] == []
    assert report.body["evidence"]["findings"] == []
    assert report.body["progress"]["tick"] == 0
    assert report.body["progress"]["cursor"] == 0

    # The run is still usable.
    assert runner.step(run_id, 0).event_id == "E8"
    done = runner.run_to_end(run_id, 1)
    assert done.body["progress"]["finished"] is True
    assert done.body["comparison"]["refunds"][0]["protected_status"] == "succeeded"


def test_lookup_at_the_next_delivery_tick_is_allowed(store):
    runner, run_id = start(store, "pending_after_success")

    record = runner.reconcile(run_id, "R1", 2)
    assert record.recorded is True

    # The clock is now level with the delivery, which is not backwards.
    assert runner.report(run_id).body["progress"]["tick"] == 2
    assert runner.step(run_id, 0).event_id == "E8"
    assert runner.run_to_end(run_id, 1).body["progress"]["finished"] is True


def test_future_lookup_is_allowed_once_nothing_is_left_to_deliver(store):
    runner, run_id = start(store, "pending_after_success")
    runner.run_to_end(run_id, 0)

    record = runner.reconcile(run_id, "R1", 99)

    assert record.recorded is True
    assert runner.report(run_id).body["progress"]["tick"] == 99


def test_api_rejects_a_future_lookup_then_steps(client):
    run_id = client.post("/api/runs", json={"scenario": "pending_after_success"}).json()["run_id"]

    rejected = client.post(
        "/api/runs/%s/reconcile" % run_id, json={"refund_id": "R1", "tick": 10}
    )
    assert rejected.status_code == 400
    body = rejected.json()
    assert "next scheduled delivery" in body["error"]
    assert body["hint"]

    report = client.get("/api/runs/%s" % run_id).json()
    assert report["actions"] == []
    assert report["progress"]["tick"] == 0

    stepped = client.post("/api/runs/%s/step" % run_id, json={"expected_cursor": 0})
    assert stepped.status_code == 200
    assert stepped.json()["event_id"] == "E8"

    finished = client.post("/api/runs/%s/run" % run_id, json={"expected_cursor": 1})
    assert finished.status_code == 200
    assert finished.json()["progress"]["finished"] is True


def test_page_rejects_a_future_lookup_then_steps(client):
    location = client.post(
        "/runs", data={"scenario": "pending_after_success"}, follow_redirects=False
    ).headers["location"]

    rejected = client.post(
        location + "/reconcile", data={"refund_id": "R1", "tick": "10"}
    )
    assert rejected.status_code == 400
    assert "next scheduled delivery" in rejected.text

    stepped = client.post(
        location + "/step", data={"expected_cursor": "0"}, follow_redirects=False
    )
    assert stepped.status_code == 303

    finished = client.post(
        location + "/run", data={"expected_cursor": "1"}, follow_redirects=True
    )
    assert finished.status_code == 200
    assert "succeeded" in finished.text


# -- D3: the digest carries the build identity ----------------------------


def test_report_carries_the_build_commit(store):
    runner = Runner(store, "abc123")
    run_id = runner.start(load_fixture(FIXTURES / "healthy.json"))

    engine = runner.report(run_id).body["engine"]
    assert engine["commit"] == "abc123"
    assert engine["commit_known"] is True


def test_unknown_commit_is_marked_unknown(store):
    runner = Runner(store, "unknown")
    run_id = runner.start(load_fixture(FIXTURES / "healthy.json"))

    engine = runner.report(run_id).body["engine"]
    assert engine["commit"] == "unknown"
    assert engine["commit_known"] is False


def test_same_build_and_actions_give_the_same_digest(store):
    first = Runner(store, "build-a").start(load_fixture(FIXTURES / "duplicate_success.json"))
    second = Runner(store, "build-a").start(load_fixture(FIXTURES / "duplicate_success.json"))

    a = Runner(store, "build-a").report(first)
    b = Runner(store, "build-a").report(second)
    assert a.digest == b.digest


def test_a_different_build_changes_the_digest(store):
    run_id = Runner(store, "build-a").start(load_fixture(FIXTURES / "duplicate_success.json"))

    before = Runner(store, "build-a").report(run_id)
    after = Runner(store, "build-b").report(run_id)

    assert before.body["comparison"] == after.body["comparison"]
    assert before.digest != after.digest
