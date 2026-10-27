#!/usr/bin/env python3
"""Drive the Hyperswitch adapter over HTTP against a running server.

Two phases, because the adapter binds to a workspace id that only exists after the
workspace is created.

Client environment variables, all set in the terminal running this tool:

    LAB_BASE_URL                 where the server is, e.g. http://127.0.0.1:8031
    INTEGRATION_API_KEY          same value the server was started with
    HYPERSWITCH_WORKSPACE_ID     printed by `prepare`, and configured on the server
    HYPERSWITCH_WEBHOOK_SECRET   same value the server was started with, for signing

`prepare` needs only the first two. `deliver` and `repeat` need all four: without the
workspace id there is no final status readback, so the tool refuses to start rather
than posting deliveries it cannot verify.

Modes:

    prepare   create the workspace, register the refund, print the workspace id
    deliver   a fresh event: expect accepted, duplicate_event, duplicate_event
    repeat    the same event again: expect duplicate_event three times, and an
              unchanged confirmed total

The payload is the documented sample in docs/samples/. It is built from the official
schema, not captured from a live Hyperswitch instance. Nothing here prints a secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

BASE_ENV = "LAB_BASE_URL"
KEY_ENV = "INTEGRATION_API_KEY"
SECRET_ENV = "HYPERSWITCH_WEBHOOK_SECRET"
WORKSPACE_ENV = "HYPERSWITCH_WORKSPACE_ID"

PAYMENT_ID = "pay_demo_1"
REFUND_ID = "ref_demo_1"
CAPTURED = 100_000
REFUND = 30_000

SAMPLE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "docs"
    / "samples"
    / "hyperswitch-refund-succeeded.json"
)

# What each mode must produce, in order. No mode accepts "any mixture".
EXPECTED_OUTCOMES = {
    "deliver": ["accepted", "duplicate_event", "duplicate_event"],
    "repeat": ["duplicate_event", "duplicate_event", "duplicate_event"],
}


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit("set %s first" % name)
    return value


def request(method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    base = env(BASE_ENV)
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def auth() -> dict:
    return {
        "Authorization": "Bearer %s" % env(KEY_ENV),
        "Content-Type": "application/json",
    }


def sign(raw: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()


def sample() -> bytes:
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data.pop("_note", None)
    return json.dumps(data).encode("utf-8")


def read_status(workspace_id: str) -> dict:
    status, text = request("GET", "/v1/workspaces/%s" % workspace_id, None, auth())
    if status != 200:
        raise SystemExit("status read failed: %s %s" % (status, text))
    return json.loads(text)


def check_expectations(mode: str, outcomes: list[str]) -> list[str]:
    """Compare what happened with what this mode promises. Pure, for testing."""
    expected = EXPECTED_OUTCOMES[mode]
    problems = []
    if outcomes != expected:
        problems.append("expected %s but got %s" % (expected, outcomes))
    return problems


def check_totals(mode: str, before: int, after: int) -> list[str]:
    """A fresh run must gain the refund once; a repeat must not move the total."""
    problems = []
    if mode == "deliver":
        if before != 0:
            problems.append(
                "workspace already has %d confirmed; this is not a fresh run, use "
                "`repeat` instead" % before
            )
        if after != REFUND:
            problems.append("expected %d confirmed after a fresh run, got %d" % (REFUND, after))
    else:
        if before != REFUND:
            problems.append(
                "expected %d already confirmed before a repeat, got %d; run `deliver` "
                "first" % (REFUND, before)
            )
        if after != before:
            problems.append(
                "repeat changed the confirmed total from %d to %d" % (before, after)
            )
    return problems


def prepare() -> int:
    body = json.dumps(
        {"provider": "hyperswitch", "payment_id": PAYMENT_ID, "captured_paise": CAPTURED}
    ).encode()
    status, text = request("POST", "/v1/workspaces", body, auth())
    if status != 201:
        print("workspace creation failed: %s %s" % (status, text))
        return 1
    workspace_id = json.loads(text)["workspace_id"]

    status, text = request(
        "POST",
        "/v1/workspaces/%s/refunds" % workspace_id,
        json.dumps({"refund_id": REFUND_ID, "amount_paise": REFUND}).encode(),
        auth(),
    )
    if status not in (200, 201):
        print("refund registration failed: %s %s" % (status, text))
        return 1

    print("workspace  %s" % workspace_id)
    print("payment    %s captured %d" % (PAYMENT_ID, CAPTURED))
    print("refund     %s %d admitted" % (REFUND_ID, REFUND))
    print()
    print("restart the server with these, keeping the same LAB_DB_PATH:")
    print("  set HYPERSWITCH_WORKSPACE_ID=%s" % workspace_id)
    print("  set HYPERSWITCH_MERCHANT_ID=merchant_demo_1")
    print("  set HYPERSWITCH_PAYMENT_ID=%s" % PAYMENT_ID)
    print("  set HYPERSWITCH_WEBHOOK_SECRET=<the same secret you use here>")
    print()
    print("and in this terminal also set:")
    print("  set HYPERSWITCH_WORKSPACE_ID=%s" % workspace_id)
    return 0


def deliver(mode: str) -> int:
    secret = env(SECRET_ENV)
    workspace_id = env(WORKSPACE_ENV)
    raw = sample()
    signature = sign(raw, secret)

    before = read_status(workspace_id)
    before_confirmed = before["totals"]["confirmed_paise"]

    print("%s run against %s" % (mode, workspace_id))
    print("  confirmed before: %d" % before_confirmed)
    outcomes = []
    for index in range(3):
        status, text = request(
            "POST",
            "/webhooks/hyperswitch",
            raw,
            {"Content-Type": "application/json", "X-Webhook-Signature-512": signature},
        )
        if status != 200:
            print("  delivery %d failed: %s %s" % (index + 1, status, text))
            return 1
        body = json.loads(text)
        outcomes.append(body["outcome"])
        print(
            "  delivery %d  attempt=%-30s outcome=%-15s confirmed=%s"
            % (index + 1, body["attempt_id"], body["outcome"],
               body["refund"]["confirmed_paise"] if body["refund"] else "-")
        )

    # Rejections must leave the record alone.
    status_bad, _ = request(
        "POST", "/webhooks/hyperswitch", raw,
        {"Content-Type": "application/json", "X-Webhook-Signature-512": "0" * 128},
    )
    print("  bad signature   -> %s (expected 401)" % status_bad)
    tampered = raw.replace(b"30000", b"90000")
    status_tampered, _ = request(
        "POST", "/webhooks/hyperswitch", tampered,
        {"Content-Type": "application/json", "X-Webhook-Signature-512": signature},
    )
    print("  tampered body   -> %s (expected 401)" % status_tampered)

    after = read_status(workspace_id)
    after_confirmed = after["totals"]["confirmed_paise"]
    refund = after["refunds"][0]
    print(
        "  confirmed after : %d  status=%s review=%d"
        % (after_confirmed, refund["status"], refund["review_paise"])
    )

    problems = []
    if status_bad != 401:
        problems.append("bad signature was not rejected with 401")
    if status_tampered != 401:
        problems.append("tampered body was not rejected with 401")
    problems += check_expectations(mode, outcomes)
    problems += check_totals(mode, before_confirmed, after_confirmed)

    if problems:
        for problem in problems:
            print("  FAILED: %s" % problem)
        return 1

    print("ok: %s expectations held at %d confirmed" % (mode, after_confirmed))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("prepare", "deliver", "repeat"):
        print("usage: python tools/hyperswitch_demo.py prepare|deliver|repeat")
        return 2
    if argv[1] == "prepare":
        return prepare()
    return deliver(argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv))