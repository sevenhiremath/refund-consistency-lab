from __future__ import annotations


def start(client, scenario):
    response = client.post("/api/runs", json={"scenario": scenario})
    assert response.status_code == 201
    return response.json()["run_id"]


def test_scenarios_are_listed(client):
    response = client.get("/api/scenarios")

    assert response.status_code == 200
    names = [item["name"] for item in response.json()["scenarios"]]
    assert "healthy" in names
    assert "lookup_conflict" in names
    assert len(names) == 12


def test_create_step_and_run(client):
    run_id = start(client, "duplicate_success")

    first = client.post("/api/runs/%s/step" % run_id, json={"expected_cursor": 0})
    assert first.status_code == 200
    assert first.json()["applied"] is True
    assert first.json()["event_id"] == "E8"

    again = client.post("/api/runs/%s/step" % run_id, json={"expected_cursor": 0})
    assert again.json()["applied"] is False
    assert again.json()["outcome"] == first.json()["outcome"]

    report = client.post("/api/runs/%s/run" % run_id, json={"expected_cursor": 1})
    body = report.json()
    assert body["comparison"]["protected_confirmed_paise"] == 30_000
    assert body["comparison"]["naive_total_paise"] == 90_000
    assert body["progress"]["finished"] is True


def test_reconcile_reports_the_evidence(client):
    run_id = start(client, "lookup_recovers_success")

    response = client.post(
        "/api/runs/%s/reconcile" % run_id, json={"refund_id": "R1", "tick": 3}
    )

    payload = response.json()
    assert payload["outcome"] == "succeeded"
    assert payload["recorded"] is True
    assert payload["observation"]["event_id"] == "lookup:R1:3"
    assert payload["observation"]["amount_paise"] == 30_000

    report = client.get("/api/runs/%s" % run_id).json()
    assert report["comparison"]["protected_confirmed_paise"] == 30_000
    assert report["comparison"]["naive_total_paise"] == 0


def test_replay_returns_a_new_run(client):
    run_id = start(client, "health" + "y")
    client.post("/api/runs/%s/run" % run_id, json={"expected_cursor": 0})

    response = client.post("/api/runs/%s/replay" % run_id)

    assert response.status_code == 201
    assert response.json()["run_id"] != run_id


def test_evidence_is_the_report(client):
    run_id = start(client, "partials_duplicate")
    client.post("/api/runs/%s/run" % run_id, json={"expected_cursor": 0})

    response = client.get("/api/runs/%s/evidence" % run_id)

    payload = response.json()
    assert payload["comparison"]["protected_confirmed_paise"] == 50_000
    assert payload["comparison"]["naive_total_paise"] == 80_000
    assert payload["digest"]


def test_unknown_scenario_is_json_404(client):
    response = client.post("/api/runs", json={"scenario": "nope"})

    assert response.status_code == 404
    assert "unknown scenario" in response.json()["error"]


def test_unknown_run_is_json_404(client):
    response = client.get("/api/runs/run_missing")

    assert response.status_code == 404
    assert "unknown run" in response.json()["error"]


def test_step_ahead_is_json_409(client):
    run_id = start(client, "healthy")

    response = client.post("/api/runs/%s/step" % run_id, json={"expected_cursor": 9})

    assert response.status_code == 409
    assert "ahead" in response.json()["error"]


def test_reconcile_in_the_past_is_json_400(client):
    run_id = start(client, "lookup_conflict")
    client.post("/api/runs/%s/run" % run_id, json={"expected_cursor": 0})

    response = client.post(
        "/api/runs/%s/reconcile" % run_id, json={"refund_id": "R1", "tick": 1}
    )

    assert response.status_code == 400
    assert "cannot look up" in response.json()["error"]