from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

KEY = "test-integration-key"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("INTEGRATION_API_KEY", KEY)
    app = create_app(Settings(db_path=tmp_path / "api.db", git_sha="tests"))
    with TestClient(app) as test_client:
        yield test_client


def auth():
    return {"Authorization": "Bearer %s" % KEY}


def workspace(client, captured=100_000):
    response = client.post(
        "/v1/workspaces",
        json={"provider": "psp_x", "payment_id": "pay_1", "captured_paise": captured},
        headers=auth(),
    )
    assert response.status_code == 201
    return response.json()


# -- the normal flow -----------------------------------------------------


def test_full_flow_workspace_refund_delivery_status(client):
    created = workspace(client)
    ws = created["workspace_id"]

    registered = client.post(
        "/v1/workspaces/%s/refunds" % ws,
        json={"refund_id": "R1", "amount_paise": 30_000},
        headers=auth(),
    )
    assert registered.status_code == 201
    assert registered.json()["accepted"] is True

    delivered = client.post(
        "/v1/workspaces/%s/deliveries" % ws,
        json={
            "attempt_id": "a1",
            "event_id": "E8",
            "refund_id": "R1",
            "status": "succeeded",
            "amount_paise": 30_000,
        },
        headers=auth(),
    )
    assert delivered.status_code == 200
    body = delivered.json()
    assert body["outcome"] == "accepted"
    assert body["recorded"] is True
    # The recorded outcome and the refund's status are separate facts.
    assert body["refund"]["status"] == "succeeded"
    assert body["refund"]["confirmed_paise"] == 30_000

    status = client.get("/v1/workspaces/%s" % ws, headers=auth())
    assert status.status_code == 200
    payload = status.json()
    assert payload["totals"]["confirmed_paise"] == 30_000
    assert payload["payment"]["admitted_paise"] == 30_000
    assert payload["refunds"][0]["refund_id"] == "R1"


def test_two_partials_fit_and_the_third_is_refused(client):
    ws = workspace(client)["workspace_id"]

    first = client.post(
        "/v1/workspaces/%s/refunds" % ws,
        json={"refund_id": "R1", "amount_paise": 30_000},
        headers=auth(),
    )
    second = client.post(
        "/v1/workspaces/%s/refunds" % ws,
        json={"refund_id": "R2", "amount_paise": 20_000},
        headers=auth(),
    )
    third = client.post(
        "/v1/workspaces/%s/refunds" % ws,
        json={"refund_id": "R3", "amount_paise": 60_000},
        headers=auth(),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["admitted_paise"] == 50_000
    assert third.status_code == 422
    assert third.json()["accepted"] is False
    assert third.json()["reason"] == "over_refund"
    assert third.json()["admitted_paise"] == 50_000


# -- retries -------------------------------------------------------------


def test_identical_registration_is_idempotent(client):
    ws = workspace(client)["workspace_id"]
    body = {"refund_id": "R1", "amount_paise": 30_000}

    first = client.post("/v1/workspaces/%s/refunds" % ws, json=body, headers=auth())
    again = client.post("/v1/workspaces/%s/refunds" % ws, json=body, headers=auth())

    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["reason"] == "already_admitted"

    status = client.get("/v1/workspaces/%s" % ws, headers=auth()).json()
    assert status["payment"]["admitted_paise"] == 30_000
    assert len(status["refunds"]) == 1


def test_changed_immutable_data_gives_a_conflict(client):
    ws = workspace(client)["workspace_id"]
    client.post(
        "/v1/workspaces/%s/refunds" % ws,
        json={"refund_id": "R1", "amount_paise": 30_000},
        headers=auth(),
    )

    changed = client.post(
        "/v1/workspaces/%s/refunds" % ws,
        json={"refund_id": "R1", "amount_paise": 35_000},
        headers=auth(),
    )

    assert changed.status_code == 409
    assert changed.json()["accepted"] is False
    assert changed.json()["reason"] == "refund_id_conflict"

    status = client.get("/v1/workspaces/%s" % ws, headers=auth()).json()
    assert status["payment"]["admitted_paise"] == 30_000


# -- duplicates and conflicts -------------------------------------------


def test_repeated_success_notifications_confirm_one_amount(client):
    ws = workspace(client)["workspace_id"]
    client.post(
        "/v1/workspaces/%s/refunds" % ws,
        json={"refund_id": "R1", "amount_paise": 30_000},
        headers=auth(),
    )
    payload = {
        "event_id": "E8",
        "refund_id": "R1",
        "status": "succeeded",
        "amount_paise": 30_000,
    }

    first = client.post(
        "/v1/workspaces/%s/deliveries" % ws,
        json=dict(payload, attempt_id="a1"),
        headers=auth(),
    )
    second = client.post(
        "/v1/workspaces/%s/deliveries" % ws,
        json=dict(payload, attempt_id="a2"),
        headers=auth(),
    )

    assert first.json()["outcome"] == "accepted"
    assert second.json()["outcome"] == "duplicate_event"

    status = client.get("/v1/workspaces/%s" % ws, headers=auth()).json()
    assert status["totals"]["confirmed_paise"] == 30_000


def test_changed_claim_under_the_same_attempt_is_reported_as_a_conflict(client):
    ws = workspace(client)["workspace_id"]
    client.post(
        "/v1/workspaces/%s/refunds" % ws,
        json={"refund_id": "R1", "amount_paise": 30_000},
        headers=auth(),
    )
    client.post(
        "/v1/workspaces/%s/deliveries" % ws,
        json={
            "attempt_id": "a1",
            "event_id": "E1",
            "refund_id": "R1",
            "status": "succeeded",
            "amount_paise": 30_000,
        },
        headers=auth(),
    )

    changed = client.post(
        "/v1/workspaces/%s/deliveries" % ws,
        json={
            "attempt_id": "a1",
            "event_id": "E2",
            "refund_id": "R1",
            "status": "failed",
            "amount_paise": 31_000,
        },
        headers=auth(),
    )

    assert changed.status_code == 200
    assert changed.json()["outcome"] == "attempt_conflict"

    status = client.get("/v1/workspaces/%s" % ws, headers=auth()).json()
    assert status["totals"]["confirmed_paise"] == 30_000
    assert len(status["findings"]) == 1
    assert status["findings"][0]["kind"] == "attempt_identity_conflict"
    assert "E2" in status["findings"][0]["detail"]


def test_a_notification_never_creates_a_refund(client):
    ws = workspace(client)["workspace_id"]

    response = client.post(
        "/v1/workspaces/%s/deliveries" % ws,
        json={
            "attempt_id": "a1",
            "event_id": "E9",
            "refund_id": "R9",
            "status": "succeeded",
            "amount_paise": 30_000,
        },
        headers=auth(),
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "unknown_refund"
    assert response.json()["refund"] is None

    status = client.get("/v1/workspaces/%s" % ws, headers=auth()).json()
    assert status["refunds"] == []
    assert status["payment"]["admitted_paise"] == 0
    assert [row["refund_id"] for row in status["unmatched"]] == ["R9"]


def test_amount_mismatch_stays_unresolved(client):
    ws = workspace(client)["workspace_id"]
    client.post(
        "/v1/workspaces/%s/refunds" % ws,
        json={"refund_id": "R1", "amount_paise": 30_000},
        headers=auth(),
    )
    client.post(
        "/v1/workspaces/%s/deliveries" % ws,
        json={
            "attempt_id": "a1",
            "event_id": "E8",
            "refund_id": "R1",
            "status": "succeeded",
            "amount_paise": 35_000,
        },
        headers=auth(),
    )

    status = client.get("/v1/workspaces/%s" % ws, headers=auth()).json()
    assert status["totals"]["confirmed_paise"] == 0
    assert status["totals"]["review_paise"] == 30_000
    assert status["refunds"][0]["status"] == "review"
    assert status["refunds"][0]["reasons"] == ["amount_mismatch"]


# -- validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"provider": "psp_x", "payment_id": "pay_1", "captured_paise": "100000"},
        {"provider": "psp_x", "payment_id": "pay_1", "captured_paise": 100000.0},
        {"provider": "psp_x", "payment_id": "pay_1", "captured_paise": True},
        {"provider": "psp_x", "payment_id": "pay_1", "captured_paise": 0},
        {"provider": "psp_x", "payment_id": "pay_1", "captured_paise": -5},
        {"provider": "psp_x", "payment_id": "", "captured_paise": 100000},
        {"provider": "psp_x", "payment_id": "pay_1", "captured_paise": 100000, "extra": 1},
        {"provider": "", "payment_id": "pay_1", "captured_paise": 100000},
        {"payment_id": "pay_1", "captured_paise": 100000},
    ],
)
def test_invalid_workspace_bodies_are_rejected(client, body):
    response = client.post("/v1/workspaces", json=body, headers=auth())

    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"refund_id": "R1", "amount_paise": "30000"},
        {"refund_id": "R1", "amount_paise": 30000.0},
        {"refund_id": "R1", "amount_paise": True},
        {"refund_id": "R1", "amount_paise": 0},
        {"refund_id": "", "amount_paise": 30000},
        {"refund_id": "R1", "amount_paise": 30000, "status": "succeeded"},
    ],
)
def test_invalid_refund_bodies_are_rejected(client, body):
    ws = workspace(client)["workspace_id"]

    response = client.post("/v1/workspaces/%s/refunds" % ws, json=body, headers=auth())

    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {
            "attempt_id": "a1",
            "event_id": "E8",
            "refund_id": "R1",
            "status": "refunded",
            "amount_paise": 30000,
        },
        {
            "attempt_id": "a1",
            "event_id": "E8",
            "refund_id": "R1",
            "status": "succeeded",
            "amount_paise": "30000",
        },
        {
            "attempt_id": "a1",
            "event_id": "E8",
            "refund_id": "R1",
            "status": "succeeded",
            "amount_paise": 30000,
            "origin": "lookup",
        },
        {
            "attempt_id": "a1",
            "event_id": "E8",
            "refund_id": "R1",
            "status": "succeeded",
        },
    ],
)
def test_invalid_delivery_bodies_are_rejected(client, body):
    ws = workspace(client)["workspace_id"]

    response = client.post("/v1/workspaces/%s/deliveries" % ws, json=body, headers=auth())

    assert response.status_code == 422


def test_identifiers_are_bounded(client):
    body = {
        "provider": "psp_x",
        "payment_id": "p" * 200,
        "captured_paise": 100000,
    }

    assert client.post("/v1/workspaces", json=body, headers=auth()).status_code == 422


def test_a_workspace_cannot_grow_without_limit(client):
    ws = workspace(client)["workspace_id"]
    for index in range(50):
        response = client.post(
            "/v1/workspaces/%s/refunds" % ws,
            json={"refund_id": "R%d" % index, "amount_paise": 100},
            headers=auth(),
        )
        assert response.status_code == 201

    over = client.post(
        "/v1/workspaces/%s/refunds" % ws,
        json={"refund_id": "R999", "amount_paise": 100},
        headers=auth(),
    )
    assert over.status_code == 409


# -- authentication ------------------------------------------------------


def test_missing_key_is_unauthorized(client):
    response = client.post(
        "/v1/workspaces",
        json={"provider": "psp_x", "payment_id": "pay_1", "captured_paise": 100000},
    )

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_wrong_key_is_unauthorized(client):
    response = client.post(
        "/v1/workspaces",
        json={"provider": "psp_x", "payment_id": "pay_1", "captured_paise": 100000},
        headers={"Authorization": "Bearer wrong-key"},
    )

    assert response.status_code == 401


def test_the_key_is_not_accepted_in_a_url(client):
    response = client.post(
        "/v1/workspaces?api_key=%s" % KEY,
        json={"provider": "psp_x", "payment_id": "pay_1", "captured_paise": 100000},
    )

    assert response.status_code == 401


def test_no_configured_key_means_the_surface_is_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("INTEGRATION_API_KEY", raising=False)
    app = create_app(Settings(db_path=tmp_path / "nokey.db", git_sha="tests"))
    with TestClient(app) as client:
        response = client.post(
            "/v1/workspaces",
            json={"provider": "psp_x", "payment_id": "pay_1", "captured_paise": 100000},
            headers=auth(),
        )
        assert response.status_code == 404

        # The public demonstration still works without a key.
        assert client.get("/").status_code == 200
        assert client.get("/walkthrough").status_code == 200
        assert client.get("/api/scenarios").status_code == 200


def test_no_response_ever_contains_the_key(client):
    response = client.get("/v1/workspaces/ws_missing", headers=auth())

    assert response.status_code == 404
    assert KEY not in response.text


# -- separation from the demo -------------------------------------------


def test_demo_endpoints_cannot_reach_integration_data(client):
    ws = workspace(client)["workspace_id"]
    client.post(
        "/v1/workspaces/%s/refunds" % ws,
        json={"refund_id": "R1", "amount_paise": 30_000},
        headers=auth(),
    )

    page = client.get("/runs/%s" % ws)
    assert page.status_code == 404

    step = client.post("/runs/%s/step" % ws, data={"expected_cursor": "0"})
    assert step.status_code == 404

    run = client.post("/runs/%s/run" % ws, data={"expected_cursor": "0"})
    assert run.status_code == 404

    reconcile = client.post(
        "/runs/%s/reconcile" % ws, data={"refund_id": "R1", "tick": "1"}
    )
    assert reconcile.status_code == 404

    replay = client.post("/runs/%s/replay" % ws)
    assert replay.status_code == 404

    evidence = client.get("/runs/%s/evidence" % ws)
    assert evidence.status_code == 404

    # The integration data is untouched by those attempts.
    status = client.get("/v1/workspaces/%s" % ws, headers=auth()).json()
    assert status["payment"]["admitted_paise"] == 30_000
    assert status["totals"]["confirmed_paise"] == 0


def test_the_integration_api_has_no_lookup_route(client):
    ws = workspace(client)["workspace_id"]

    for path in (
        "/v1/workspaces/%s/lookup" % ws,
        "/v1/workspaces/%s/reconcile" % ws,
        "/v1/workspaces/%s/provider" % ws,
    ):
        assert client.get(path, headers=auth()).status_code == 404
        assert client.post(path, headers=auth()).status_code == 404


def test_unknown_workspace_is_404(client):
    response = client.get("/v1/workspaces/ws_missing", headers=auth())

    assert response.status_code == 404


def test_authorization_runs_before_body_validation(client):
    # An unauthorized caller learns nothing about the schema.
    invalid = {"provider": "psp_x", "payment_id": "pay_1", "captured_paise": "nope"}

    assert client.post("/v1/workspaces", json=invalid).status_code == 401
    assert (
        client.post(
            "/v1/workspaces", json=invalid, headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    # Only an authorized caller sees a validation failure.
    assert client.post("/v1/workspaces", json=invalid, headers=auth()).status_code == 422
