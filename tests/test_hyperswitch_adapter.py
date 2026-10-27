from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.hyperswitch import sign, verify
from app.main import create_app

SAMPLE = pathlib.Path(__file__).resolve().parent.parent / "docs" / "samples" / "hyperswitch-refund-succeeded.json"
SECRET = "test-signing-secret"
KEY = "test-integration-key"


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("INTEGRATION_API_KEY", KEY)
    app = create_app(Settings(db_path=tmp_path / "hs.db", git_sha="tests"))
    with TestClient(app) as client:
        yield client


def configure(monkeypatch, workspace_id):
    monkeypatch.setenv("HYPERSWITCH_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("HYPERSWITCH_WORKSPACE_ID", workspace_id)
    monkeypatch.setenv("HYPERSWITCH_MERCHANT_ID", "merchant_demo_1")
    monkeypatch.setenv("HYPERSWITCH_PAYMENT_ID", "pay_demo_1")


def prepare(client, monkeypatch, captured=100_000, refund=30_000):
    service = client.app.state.integration
    workspace = service.create_workspace("hyperswitch", "pay_demo_1", captured)
    service.register_refund(workspace.workspace_id, "ref_demo_1", refund)
    configure(monkeypatch, workspace.workspace_id)
    return workspace.workspace_id


def sample_bytes(**changes):
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    data.pop("_note", None)
    for path, value in changes.items():
        node = data
        keys = path.split(".")
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return json.dumps(data).encode("utf-8")


def post(client, raw, signature=None):
    return client.post(
        "/webhooks/hyperswitch",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature-512": sign(raw, SECRET) if signature is None else signature,
        },
    )


def status_of(client, workspace_id):
    return client.app.state.integration.status(workspace_id)


# -- the happy path ------------------------------------------------------


def test_valid_success_confirms_30000(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)

    response = post(app_client, sample_bytes())

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "accepted"
    assert body["refund"]["status"] == "succeeded"
    assert body["refund"]["confirmed_paise"] == 30_000
    assert body["event_id"] == "evt_hs_demo_refund_1"
    assert body["attempt_id"] == "hs:evt_hs_demo_refund_1:1"

    assert status_of(app_client, workspace_id).confirmed_paise == 30_000


def test_three_deliveries_of_one_event_confirm_once(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)
    raw = sample_bytes()

    outcomes = [post(app_client, raw).json()["outcome"] for _ in range(3)]

    assert outcomes == ["accepted", "duplicate_event", "duplicate_event"]
    assert status_of(app_client, workspace_id).confirmed_paise == 30_000


def test_each_delivery_gets_its_own_attempt_id(app_client, monkeypatch):
    prepare(app_client, monkeypatch)
    raw = sample_bytes()

    attempts = [post(app_client, raw).json()["attempt_id"] for _ in range(3)]

    assert attempts == [
        "hs:evt_hs_demo_refund_1:1",
        "hs:evt_hs_demo_refund_1:2",
        "hs:evt_hs_demo_refund_1:3",
    ]


# -- signature -----------------------------------------------------------


def test_invalid_signature_is_rejected_without_writes(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)

    response = post(app_client, sample_bytes(), signature="0" * 128)

    assert response.status_code == 401
    assert status_of(app_client, workspace_id).confirmed_paise == 0
    assert app_client.app.state.integration.store.findings(workspace_id) == ()


def test_tampered_body_is_rejected(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)
    original = sample_bytes()
    signature = sign(original, SECRET)
    tampered = sample_bytes(**{"content.object.amount": 90_000})

    response = post(app_client, tampered, signature=signature)

    assert response.status_code == 401
    assert status_of(app_client, workspace_id).confirmed_paise == 0


def test_missing_signature_header_is_rejected(app_client, monkeypatch):
    prepare(app_client, monkeypatch)

    response = app_client.post(
        "/webhooks/hyperswitch",
        content=sample_bytes(),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 401


# -- binding -------------------------------------------------------------


def test_wrong_merchant_cannot_reach_the_workspace(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)
    raw = sample_bytes(merchant_id="merchant_other")

    response = post(app_client, raw)

    assert response.status_code == 403
    assert status_of(app_client, workspace_id).confirmed_paise == 0


def test_wrong_payment_cannot_reach_the_workspace(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)
    raw = sample_bytes(**{"content.object.payment_id": "pay_other"})

    response = post(app_client, raw)

    assert response.status_code == 403
    assert status_of(app_client, workspace_id).confirmed_paise == 0


def test_the_payload_cannot_select_a_workspace(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)
    raw = sample_bytes(**{"workspace_id": "ws_someone_else"})

    # Unknown top-level fields are ignored, and the configured workspace is used.
    response = post(app_client, raw)

    assert response.status_code == 200
    assert status_of(app_client, workspace_id).confirmed_paise == 30_000


# -- identity and money --------------------------------------------------


def test_unknown_refund_does_not_create_an_intent(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)
    raw = sample_bytes(**{"content.object.refund_id": "ref_unknown"})

    response = post(app_client, raw)

    assert response.status_code == 422
    assert "unknown refund" in response.json()["error"]
    assert app_client.app.state.integration.store.refund_count(workspace_id) == 1


def test_review_status_is_rejected_explicitly(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)
    raw = sample_bytes(
        **{"event_type": "refund_review", "content.object.status": "review"}
    )

    response = post(app_client, raw)

    assert response.status_code == 422
    assert "unsupported event_type" in response.json()["error"]
    assert status_of(app_client, workspace_id).confirmed_paise == 0


def test_non_inr_currency_is_rejected(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)
    raw = sample_bytes(**{"content.object.currency": "USD"})

    response = post(app_client, raw)

    assert response.status_code == 422
    assert "unsupported currency" in response.json()["error"]
    assert status_of(app_client, workspace_id).confirmed_paise == 0


def test_event_type_must_agree_with_status(app_client, monkeypatch):
    prepare(app_client, monkeypatch)
    raw = sample_bytes(**{"content.object.status": "failed"})

    response = post(app_client, raw)

    assert response.status_code == 422
    assert "disagrees" in response.json()["error"]


def test_inr_amount_mismatch_reaches_the_processor_as_review(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)
    raw = sample_bytes(**{"content.object.amount": 35_000})

    response = post(app_client, raw)

    assert response.status_code == 200
    assert response.json()["outcome"] == "accepted"
    state = status_of(app_client, workspace_id)
    assert state.confirmed_paise == 0
    assert state.review_paise == 30_000
    assert state.refunds[0]["reasons"] == ["amount_mismatch"]


def test_a_notification_never_changes_the_intended_amount(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)

    post(app_client, sample_bytes(**{"content.object.amount": 35_000}))

    # The intent amount is what the status view reports, not the evidenced amount.
    assert status_of(app_client, workspace_id).refunds[0]["amount_paise"] == 30_000


# -- availability --------------------------------------------------------


def test_route_is_absent_without_configuration(app_client, monkeypatch):
    for name in (
        "HYPERSWITCH_WEBHOOK_SECRET",
        "HYPERSWITCH_WORKSPACE_ID",
        "HYPERSWITCH_MERCHANT_ID",
        "HYPERSWITCH_PAYMENT_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    response = post(app_client, sample_bytes())

    assert response.status_code == 404


def test_route_is_absent_when_only_the_secret_is_set(app_client, monkeypatch):
    monkeypatch.setenv("HYPERSWITCH_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("HYPERSWITCH_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("HYPERSWITCH_MERCHANT_ID", raising=False)
    monkeypatch.delenv("HYPERSWITCH_PAYMENT_ID", raising=False)

    assert post(app_client, sample_bytes()).status_code == 404


def test_v1_bearer_auth_is_unchanged(app_client, monkeypatch):
    prepare(app_client, monkeypatch)

    assert app_client.get("/v1/workspaces/ws_missing").status_code == 401
    assert (
        app_client.get(
            "/v1/workspaces/ws_missing", headers={"Authorization": "Bearer %s" % KEY}
        ).status_code
        == 404
    )


# -- hostile headers and bodies ------------------------------------------


def test_non_ascii_signature_is_rejected_without_raising():
    # http clients refuse to send this header as ASCII, but a raw request can carry
    # it, and uvicorn decodes header bytes as latin-1. verify must not raise.
    signature = "\u00e9" * 16

    assert verify(b"{}", signature, SECRET) is False


def test_invalid_utf8_body_is_rejected_with_400(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)
    raw = b'{"merchant_id":"merchant_demo_1","event_id":"evt\xff\xfe"}'

    response = post(app_client, raw)

    assert response.status_code == 400
    assert "UTF-8" in response.json()["error"]
    assert status_of(app_client, workspace_id).confirmed_paise == 0


def test_amount_beyond_the_supported_maximum_is_rejected(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)
    raw = sample_bytes(**{"content.object.amount": 2 ** 80})

    response = post(app_client, raw)

    assert response.status_code == 422
    assert "maximum" in response.json()["error"]
    state = status_of(app_client, workspace_id)
    assert state.confirmed_paise == 0
    assert app_client.app.state.integration.store.findings(workspace_id) == ()
    assert app_client.app.state.integration.store.refund_count(workspace_id) == 1


def test_rejections_leave_deliveries_and_state_untouched(app_client, monkeypatch):
    workspace_id = prepare(app_client, monkeypatch)
    store = app_client.app.state.integration.store
    before = store.event_delivery_count(workspace_id, "evt_hs_demo_refund_1")

    post(app_client, sample_bytes(), signature="0" * 128)
    post(app_client, b"\xff\xfe not json")

    assert store.event_delivery_count(workspace_id, "evt_hs_demo_refund_1") == before
    assert status_of(app_client, workspace_id).confirmed_paise == 0
    assert store.findings(workspace_id) == ()
