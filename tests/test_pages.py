from __future__ import annotations

SCENARIOS = (
    "healthy",
    "duplicate_success",
    "lookup_recovers_success",
    "lookup_conflict",
)


def start(client, scenario):
    response = client.post("/runs", data={"scenario": scenario}, follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"]


def test_picker_lists_scenarios(client):
    response = client.get("/")

    assert response.status_code == 200
    body = response.text
    assert "Refund Consistency Lab" in body
    for scenario in SCENARIOS:
        assert scenario in body
    assert "Start" in body


def test_start_creates_a_run_and_redirects(client):
    location = start(client, "healthy")

    assert location.startswith("/runs/run_")
    page = client.get(location)
    assert page.status_code == 200
    assert "healthy" in page.text
    assert "in progress" in page.text


def test_step_advances_and_renders_the_delivery(client):
    location = start(client, "healthy")

    stepped = client.post(
        location + "/step",
        data={"expected_cursor": "0"},
        follow_redirects=True,
    )

    assert stepped.status_code == 200
    assert "Deliveries applied" in stepped.text
    assert "1 of 2" in stepped.text
    assert "accepted" in stepped.text


def test_run_to_end_shows_the_naive_divergence(client):
    location = start(client, "duplicate_success")

    page = client.post(location + "/run", data={"expected_cursor": "0"}, follow_redirects=True)

    assert page.status_code == 200
    assert page.text.count("300.00") >= 1
    assert "900.00" in page.text
    assert "finished" in page.text


def test_reconcile_recovers_a_missing_success(client):
    location = start(client, "lookup_recovers_success")

    page = client.post(
        location + "/reconcile",
        data={"refund_id": "R1", "tick": "3"},
        follow_redirects=True,
    )

    assert page.status_code == 200
    assert "succeeded" in page.text
    assert "300.00" in page.text
    assert "lookup" in page.text


def test_reconcile_keeps_a_conflict_unresolved(client):
    location = start(client, "lookup_conflict")
    client.post(location + "/run", data={"expected_cursor": "0"}, follow_redirects=True)

    page = client.post(
        location + "/reconcile",
        data={"refund_id": "R1", "tick": "4"},
        follow_redirects=True,
    )

    assert page.status_code == 200
    assert "review" in page.text
    assert "terminal_conflict" in page.text


def test_replay_creates_a_separate_run(client):
    location = start(client, "healthy")
    client.post(location + "/run", data={"expected_cursor": "0"}, follow_redirects=True)

    replayed = client.post(location + "/replay", follow_redirects=False)

    assert replayed.status_code == 303
    assert replayed.headers["location"] != location
    assert client.get(replayed.headers["location"]).status_code == 200


def test_evidence_download_is_a_json_attachment(client):
    location = start(client, "duplicate_success")
    client.post(location + "/run", data={"expected_cursor": "0"}, follow_redirects=True)

    response = client.get(location + "/evidence")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    payload = response.json()
    assert payload["comparison"]["naive_total_paise"] == 90_000
    assert payload["comparison"]["protected_confirmed_paise"] == 30_000


def test_unknown_run_shows_a_404_page(client):
    response = client.get("/runs/run_missing")

    assert response.status_code == 404
    assert "unknown run" in response.text
    assert "Back to scenarios" in response.text


def test_unknown_scenario_shows_a_404_page(client):
    response = client.post("/runs", data={"scenario": "nope"})

    assert response.status_code == 404
    assert "unknown scenario" in response.text


def test_reconcile_in_the_past_shows_a_400_page(client):
    location = start(client, "lookup_conflict")
    client.post(location + "/run", data={"expected_cursor": "0"}, follow_redirects=True)

    response = client.post(
        location + "/reconcile",
        data={"refund_id": "R1", "tick": "1"},
    )

    assert response.status_code == 400
    assert "cannot look up" in response.text


def test_finished_run_disables_step_buttons(client):
    location = start(client, "healthy")
    page = client.post(location + "/run", data={"expected_cursor": "0"}, follow_redirects=True)

    assert 'disabled>Step' in page.text
    assert 'disabled>Run to end' in page.text
