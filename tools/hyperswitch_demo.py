#!/usr/bin/env python3
"""Drive the Hyperswitch adapter over HTTP against a running server.

Two phases, because the adapter binds to a workspace id that only exists after the
workspace is created:

    set LAB_BASE_URL=http://127.0.0.1:8031
    set INTEGRATION_API_KEY=...        (same value the server uses)
    python tools/hyperswitch_demo.py prepare        -> prints the workspace id

Restart the server with that id in HYPERSWITCH_WORKSPACE_ID, then:

    set HYPERSWITCH_WEBHOOK_SECRET=...  (same value the server uses)
    python tools/hyperswitch_demo.py deliver

The payload is the documented sample in docs/samples/. It is constructed from the
official schema, not captured from a live Hyperswitch instance. Nothing here prints a
secret.
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

PAYMENT_ID = "pay_demo_1"
REFUND_ID = "ref_demo_1"
CAPTURED = 100_000
REFUND = 30_000

SAMPLE = pathlib.Path(__file__).resolve().parent.parent / "docs" / "samples" / "hyperswitch-refund-succeeded.json"


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
    return {"Authorization": "Bearer %s" % env(KEY_ENV), "Content-Type": "application/json"}


def sign(raw: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()


def sample() -> bytes:
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data.pop("_note", None)
    return json.dumps(data).encode("utf-8")


def prepare() -> int:
    status, text = request(
        "POST",
        "/v1/workspaces",
        json.dumps(
            {"provider": "hyperswitch", "payment_id": PAYMENT_ID, "captured_paise": CAPTURED}
        ).encode(),
        auth(),
    )
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
    return 0


def deliver() -> int:
    secret = env(SECRET_ENV)
    raw = sample()
    signature = sign(raw, secret)

    print("delivering the documented sample three times")
    outcomes = []
    for index in range(3):
        status, text = request(
            "POST",
            "/webhooks/hyperswitch",
            raw,
            {"Content-Type": "application/json", "X-Webhook-Signature-512": signature},
        )
        if status != 200:
            print("delivery %d failed: %s %s" % (index + 1, status, text))
            return 1
        body = json.loads(text)
        outcomes.append(body["outcome"])
        print(
            "  delivery %d  attempt=%s  outcome=%-15s confirmed=%s"
            % (
                index + 1,
                body["attempt_id"],
                body["outcome"],
                body["refund"]["confirmed_paise"] if body["refund"] else "-",
            )
        )

    if outcomes != ["accepted", "duplicate_event", "duplicate_event"]:
        print("unexpected outcomes: %s" % outcomes)
        return 1

    # Rejections must not write.
    status, _ = request(
        "POST",
        "/webhooks/hyperswitch",
        raw,
        {"Content-Type": "application/json", "X-Webhook-Signature-512": "0" * 128},
    )
    print("  bad signature   -> %s (expected 401)" % status)
    if status != 401:
        return 1

    tampered = raw.replace(b"30000", b"90000")
    status, _ = request(
        "POST",
        "/webhooks/hyperswitch",
        tampered,
        {"Content-Type": "application/json", "X-Webhook-Signature-512": signature},
    )
    print("  tampered body   -> %s (expected 401)" % status)
    if status != 401:
        return 1

    # Read the protected status back through the authenticated API.
    workspace_id = os.environ.get("HYPERSWITCH_WORKSPACE_ID", "").strip()
    if not workspace_id:
        raise SystemExit("set HYPERSWITCH_WORKSPACE_ID to read the status back")
    status, text = request("GET", "/v1/workspaces/%s" % workspace_id, None, auth())
    if status != 200:
        print("status read failed: %s %s" % (status, text))
        return 1
    state = json.loads(text)
    refund = state["refunds"][0]
    print(
        "  final status    %s confirmed=%d review=%d"
        % (refund["status"], refund["confirmed_paise"], refund["review_paise"])
    )
    if refund["confirmed_paise"] != REFUND or refund["status"] != "succeeded":
        print("expected one confirmed refund of %d" % REFUND)
        return 1

    print("ok: adapter confirmed %d once across three deliveries" % REFUND)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("prepare", "deliver"):
        print("usage: python tools/hyperswitch_demo.py prepare|deliver")
        return 2
    return prepare() if argv[1] == "prepare" else deliver()


if __name__ == "__main__":
    sys.exit(main(sys.argv))