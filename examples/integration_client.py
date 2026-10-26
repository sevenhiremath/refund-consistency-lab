#!/usr/bin/env python3
"""Example client for the Refund Consistency Lab integration API.

This is a stand-in for another application using the service. It imports nothing
from the lab itself: only ``httpx``, ``os`` and ``sys``. Every notification below is
synthetic, but it travels through the real ``/v1`` integration API, so the refund
rules, admission cap, duplicate handling and stored evidence are the ones the service
actually applies.

The API key is read from the environment and sent only in the Authorization header.
It is never printed, never placed in a URL and never echoed from an error.

Run it with:

    python -m pip install httpx
    set LAB_BASE_URL=http://127.0.0.1:8031
    set INTEGRATION_API_KEY=your-key
    python examples/integration_client.py

Windows Command Prompt syntax is used above. In PowerShell use
``$env:LAB_BASE_URL = "..."`` instead.

Exit codes: 0 on success, 1 when a request or an assertion fails, 2 when the
environment is incomplete.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import httpx

BASE_URL_ENV = "LAB_BASE_URL"
API_KEY_ENV = "INTEGRATION_API_KEY"

CAPTURED_PAISE = 100_000
REFUND_PAISE = 30_000


class ClientError(RuntimeError):
    """A request the service refused, or could not be reached."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class LabClient:
    """Minimal wrapper over the four /v1 routes."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        http: httpx.Client | None = None,
    ) -> None:
        self._owns_http = http is None
        self._http = http or httpx.Client(base_url=base_url, timeout=30.0)
        self._http.headers["Authorization"] = "Bearer %s" % api_key

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # -- transport -------------------------------------------------------

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        try:
            response = self._http.request(method, path, json=body)
        except httpx.HTTPError as exc:
            # Deliberately does not include headers or the request URL query string.
            raise ClientError(
                "%s %s could not be reached (%s)" % (method, path, type(exc).__name__)
            ) from None

        if response.status_code >= 400:
            detail = ""
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if isinstance(payload, dict):
                value = payload.get("error") or payload.get("detail")
                if isinstance(value, str):
                    detail = ": " + value
            raise ClientError(
                "%s %s returned %d%s" % (method, path, response.status_code, detail)
            )
        return response.json()

    # -- the four routes -------------------------------------------------

    def create_workspace(
        self, provider: str, payment_id: str, captured_paise: int
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            "/v1/workspaces",
            {
                "provider": provider,
                "payment_id": payment_id,
                "captured_paise": captured_paise,
            },
        )

    def register_refund(
        self, workspace_id: str, refund_id: str, amount_paise: int
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            "/v1/workspaces/%s/refunds" % workspace_id,
            {"refund_id": refund_id, "amount_paise": amount_paise},
        )

    def submit_delivery(
        self,
        workspace_id: str,
        attempt_id: str,
        event_id: str,
        refund_id: str,
        status: str,
        amount_paise: int,
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            "/v1/workspaces/%s/deliveries" % workspace_id,
            {
                "attempt_id": attempt_id,
                "event_id": event_id,
                "refund_id": refund_id,
                "status": status,
                "amount_paise": amount_paise,
            },
        )

    def status(self, workspace_id: str) -> dict[str, Any]:
        return self._call("GET", "/v1/workspaces/%s" % workspace_id)


def run(client: LabClient) -> None:
    """The demonstration. Raises on the first failed expectation."""

    # Creating a workspace is not retried: the endpoint makes a new workspace each
    # time it succeeds, so a blind retry would leave duplicates behind.
    workspace = client.create_workspace("psp_example", "pay_example_1", CAPTURED_PAISE)
    workspace_id = workspace["workspace_id"]
    print("workspace  %s captured %d" % (workspace_id, CAPTURED_PAISE))

    admitted = client.register_refund(workspace_id, "R1", REFUND_PAISE)
    check(
        admitted["accepted"] is True,
        "refund was not admitted: %s" % admitted.get("reason"),
    )
    print(
        "refund     R1 %d admitted, total %d"
        % (REFUND_PAISE, admitted["admitted_paise"])
    )

    # One provider event, delivered three times with three different attempts.
    for attempt in ("a1", "a2", "a3"):
        result = client.submit_delivery(
            workspace_id, attempt, "E8", "R1", "succeeded", REFUND_PAISE
        )
        state = client.status(workspace_id)
        confirmed = state["totals"]["confirmed_paise"]
        check(
            confirmed == REFUND_PAISE,
            "confirmed is %d after attempt %s, expected %d"
            % (confirmed, attempt, REFUND_PAISE),
        )
        print("delivery   %s %-15s confirmed %d" % (attempt, result["outcome"], confirmed))

    # The same attempt id with identical content is a retry, not a new delivery.
    retry = client.submit_delivery(
        workspace_id, "a1", "E8", "R1", "succeeded", REFUND_PAISE
    )
    check(retry["recorded"] is False, "an identical attempt id was recorded again")
    state = client.status(workspace_id)
    check(
        state["totals"]["confirmed_paise"] == REFUND_PAISE,
        "a retry changed the confirmed amount to %d"
        % state["totals"]["confirmed_paise"],
    )
    print(
        "retry      a1 recorded=%s confirmed %d"
        % (retry["recorded"], state["totals"]["confirmed_paise"])
    )

    # A later, different event must not drag a settled refund backwards.
    pending = client.submit_delivery(
        workspace_id, "a4", "E9", "R1", "pending", REFUND_PAISE
    )
    state = client.status(workspace_id)
    refund = state["refunds"][0]
    check(
        refund["status"] == "succeeded",
        "a pending event changed the settled status to %s" % refund["status"],
    )
    check(
        state["totals"]["confirmed_paise"] == REFUND_PAISE,
        "a pending event changed the confirmed amount to %d"
        % state["totals"]["confirmed_paise"],
    )
    print(
        "pending    E9 %-15s status %s, confirmed %d"
        % (pending["outcome"], refund["status"], state["totals"]["confirmed_paise"])
    )

    print(
        "done       %d refunds, %d confirmed, %d under review"
        % (
            len(state["refunds"]),
            state["totals"]["confirmed_paise"],
            state["totals"]["review_paise"],
        )
    )


def main() -> int:
    base_url = os.environ.get(BASE_URL_ENV, "").strip()
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not base_url or not api_key:
        print(
            "set %s and %s before running this example" % (BASE_URL_ENV, API_KEY_ENV),
            file=sys.stderr,
        )
        return 2

    client = LabClient(base_url, api_key)
    try:
        run(client)
    except (ClientError, AssertionError) as exc:
        print("FAILED: %s" % exc, file=sys.stderr)
        return 1
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())