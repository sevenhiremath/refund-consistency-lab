from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.sim.cli import main
from app.sim.fixture import FixtureError, load_fixture, parse_fixture
from app.sim.runner import Runner
from app.store import StepConflict, Store

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture()
def store(tmp_path):
    instance = Store(tmp_path / "lab.db")
    instance.initialize()
    return instance


def run_fixture(store, name):
    runner = Runner(store)
    run_id = runner.start(load_fixture(FIXTURES / ("%s.json" % name)))
    report = runner.run_to_end(run_id, 0)
    return runner, run_id, report


@pytest.mark.parametrize(
    ("name", "protected", "naive", "status", "review"),
    [
        ("healthy", 30_000, 30_000, "succeeded", 0),
        ("duplicate_success", 30_000, 90_000, "succeeded", 0),
        ("pending_after_success", 30_000, 30_000, "succeeded", 0),
        ("dropped_success", 0, 0, "pending", 0),
        ("partials_duplicate", 50_000, 80_000, "succeeded", 0),
        ("conflict_success_first", 0, 30_000, "review", 30_000),
        ("conflict_failure_first", 0, 30_000, "review", 30_000),
    ],
)
def test_expected_outcomes(store, name, protected, naive, status, review):
    _, _, report = run_fixture(store, name)
    comparison = report.body["comparison"]

    assert comparison["protected_confirmed_paise"] == protected
    assert comparison["naive_total_paise"] == naive
    assert comparison["protected_review_paise"] == review
    assert comparison["refunds"][0]["protected_status"] == status


def test_all_fixtures_load_and_hash():
    for path in sorted(FIXTURES.glob("*.json")):
        fixture = load_fixture(path)
        assert len(fixture.sha256) == 64
        assert fixture.payment.captured_paise > 0


def test_dropped_event_never_reaches_a_processor(store):
    _, run_id, report = run_fixture(store, "dropped_success")

    assert report.body["provider"]["dropped"] == ["E9"]
    assert report.body["progress"]["finished"] is True
    assert report.body["actions"] == []
    assert report.body["comparison"]["protected_confirmed_paise"] == 0
    assert report.body["comparison"]["naive_total_paise"] == 0

    state = store.refund_states(run_id)["R1"]
    assert state["status"] == "pending"
    assert state["confirmed_paise"] == 0


def test_future_delivery_is_not_applied_early(store):
    runner = Runner(store)
    run_id = runner.start(load_fixture(FIXTURES / "healthy.json"))

    first = runner.step(run_id, 0)
    assert first.event_id == "E7"
    assert store.refund_states(run_id)["R1"]["status"] == "pending"

    second = runner.step(run_id, 1)
    assert second.event_id == "E8"
    assert store.refund_states(run_id)["R1"]["status"] == "succeeded"


def test_repeating_a_step_returns_the_recorded_result(store):
    runner = Runner(store)
    run_id = runner.start(load_fixture(FIXTURES / "duplicate_success.json"))

    first = runner.step(run_id, 0)
    again = runner.step(run_id, 0)

    assert first.applied is True
    assert again.applied is False
    assert again.outcome == first.outcome
    assert again.next_cursor == 1
    assert store.run_state(run_id)["cursor"] == 1
    assert len(store.steps(run_id)) == 1


def test_step_ahead_of_the_run_is_refused(store):
    runner = Runner(store)
    run_id = runner.start(load_fixture(FIXTURES / "healthy.json"))

    with pytest.raises(StepConflict):
        runner.step(run_id, 5)


def test_same_fixture_gives_the_same_digest(store):
    _, first_run, first = run_fixture(store, "duplicate_success")
    _, second_run, second = run_fixture(store, "duplicate_success")

    assert first_run != second_run
    assert first.digest == second.digest
    assert first.body == second.body


def test_replay_makes_a_new_run_with_the_same_digest(store):
    runner, run_id, original = run_fixture(store, "partials_duplicate")

    replayed_run = runner.replay(run_id)
    replayed = runner.report(replayed_run)

    assert replayed_run != run_id
    assert replayed.digest == original.digest
    assert replayed.body["actions"] == original.body["actions"]
    assert len(store.actions(replayed_run)) == len(store.actions(run_id))


def test_stepping_and_running_to_end_give_the_same_digest(store):
    runner = Runner(store)
    fixture = load_fixture(FIXTURES / "duplicate_success.json")

    stepped = runner.start(fixture)
    for cursor in range(3):
        runner.step(stepped, cursor)

    together = runner.start(fixture)
    runner.run_to_end(together, 0)

    assert runner.report(stepped).digest == runner.report(together).digest
    assert runner.report(stepped).body["actions"] == runner.report(together).body["actions"]


def test_report_contains_inputs_and_decisions(store):
    _, _, report = run_fixture(store, "pending_after_success")
    body = report.body

    assert body["fixture"]["name"] == "pending_after_success"
    assert len(body["fixture"]["sha256"]) == 64
    assert body["inputs"]["payment"]["captured_paise"] == 100_000
    assert body["inputs"]["scheduled_deliveries"] == 2
    assert [action["event_id"] for action in body["actions"]] == ["E8", "E7"]
    assert [action["outcome"] for action in body["actions"]] == ["accepted", "accepted"]
    assert [action["tick"] for action in body["actions"]] == [2, 3]
    assert body["comparison"]["refunds"][0]["naive_status"] == "pending"
    assert body["comparison"]["refunds"][0]["naive_total_paise"] == 30_000


def test_conflict_orders_agree_for_protected_and_differ_for_naive(store):
    _, _, first = run_fixture(store, "conflict_success_first")
    _, _, second = run_fixture(store, "conflict_failure_first")

    a = first.body["comparison"]["refunds"][0]
    b = second.body["comparison"]["refunds"][0]

    assert a["protected_status"] == b["protected_status"] == "review"
    assert a["protected_confirmed_paise"] == b["protected_confirmed_paise"] == 0
    assert a["protected_review_paise"] == b["protected_review_paise"] == 30_000
    assert a["naive_status"] == "failed"
    assert b["naive_status"] == "succeeded"
    assert a["naive_total_paise"] == b["naive_total_paise"] == 30_000


def test_delivered_event_without_a_delivery_is_rejected():
    data = json.loads((FIXTURES / "healthy.json").read_text(encoding="utf-8"))
    data["deliveries"] = data["deliveries"][:1]

    with pytest.raises(FixtureError, match="marked delivered"):
        parse_fixture(json.dumps(data))


def test_dropped_event_with_a_delivery_is_rejected():
    data = json.loads((FIXTURES / "healthy.json").read_text(encoding="utf-8"))
    data["events"][1]["delivered"] = False

    with pytest.raises(FixtureError, match="marked dropped"):
        parse_fixture(json.dumps(data))


def test_delivery_before_its_event_is_rejected():
    data = json.loads((FIXTURES / "healthy.json").read_text(encoding="utf-8"))
    data["deliveries"][1]["at_tick"] = 1

    with pytest.raises(FixtureError, match="before it happens"):
        parse_fixture(json.dumps(data))


def test_cli_prints_the_report(capsys, tmp_path):
    code = main(
        [str(FIXTURES / "duplicate_success.json"), "--db", str(tmp_path / "cli.db")]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["comparison"]["protected_confirmed_paise"] == 30_000
    assert payload["comparison"]["naive_total_paise"] == 90_000
    assert payload["digest"]