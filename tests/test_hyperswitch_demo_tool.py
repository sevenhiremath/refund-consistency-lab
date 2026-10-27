from __future__ import annotations

import pathlib
import sys

TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

from hyperswitch_demo import (  # noqa: E402
    REFUND,
    check_expectations,
    check_totals,
)


# -- outcome expectations ------------------------------------------------


def test_fresh_run_expectations_are_exact():
    assert check_expectations(
        "deliver", ["accepted", "duplicate_event", "duplicate_event"]
    ) == []


def test_fresh_run_rejects_all_duplicates():
    # The real case from the second run: a repeat delivered against a settled
    # workspace is not a fresh run, and must not be accepted as one.
    problems = check_expectations(
        "deliver", ["duplicate_event", "duplicate_event", "duplicate_event"]
    )
    assert len(problems) == 1
    assert "expected" in problems[0]


def test_fresh_run_rejects_a_mixed_sequence():
    problems = check_expectations(
        "deliver", ["accepted", "accepted", "duplicate_event"]
    )
    assert problems


def test_repeat_run_expects_all_duplicates():
    assert check_expectations(
        "repeat", ["duplicate_event", "duplicate_event", "duplicate_event"]
    ) == []


def test_repeat_run_rejects_a_new_accept():
    problems = check_expectations(
        "repeat", ["accepted", "duplicate_event", "duplicate_event"]
    )
    assert problems


# -- totals --------------------------------------------------------------


def test_fresh_run_must_gain_the_refund():
    assert check_totals("deliver", 0, REFUND) == []
    assert check_totals("deliver", 0, 0)


def test_fresh_run_refuses_a_workspace_that_is_already_settled():
    problems = check_totals("deliver", REFUND, REFUND)
    assert len(problems) == 1
    assert "not a fresh run" in problems[0]


def test_repeat_run_must_leave_the_total_unchanged():
    assert check_totals("repeat", REFUND, REFUND) == []


def test_repeat_run_detects_a_changed_total():
    problems = check_totals("repeat", REFUND, 60_000)
    assert problems


def test_repeat_run_refuses_a_workspace_that_was_never_delivered():
    problems = check_totals("repeat", 0, 0)
    assert len(problems) == 1
    assert "run `deliver` first" in problems[0]