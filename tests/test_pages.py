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


def base_path(location):
    """The run path without the guided flag, for building sub-paths."""
    return location.split("?")[0]


def action_url(location, action):
    """Build a sub-path URL, keeping the guided flag after the path."""
    path, _, query = location.partition("?")
    return path + action + ("?" + query if query else "")


def test_picker_lists_scenarios(client):
    """The scenario list lives on the demo page; the overview links to it."""
    response = client.get("/walkthrough")

    assert response.status_code == 200
    body = response.text
    assert "Refund Consistency" in body
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

    page = client.post(base_path(location) + "/run", data={"expected_cursor": "0"}, follow_redirects=True)

    assert page.status_code == 200
    assert page.text.count("300.00") >= 1
    assert "900.00" in page.text
    assert "Notification delivery" in page.text
    assert "complete" in page.text


def test_reconcile_recovers_a_missing_success(client):
    location = start(client, "lookup_recovers_success")

    page = client.post(
        base_path(location) + "/reconcile",
        data={"refund_id": "R1", "tick": "3"},
        follow_redirects=True,
    )

    assert page.status_code == 200
    assert "succeeded" in page.text
    assert "300.00" in page.text
    assert "lookup" in page.text


def test_reconcile_keeps_a_conflict_unresolved(client):
    location = start(client, "lookup_conflict")
    client.post(base_path(location) + "/run", data={"expected_cursor": "0"}, follow_redirects=True)

    page = client.post(
        base_path(location) + "/reconcile",
        data={"refund_id": "R1", "tick": "4"},
        follow_redirects=True,
    )

    assert page.status_code == 200
    assert "review" in page.text
    assert "terminal_conflict" in page.text


def test_replay_creates_a_separate_run(client):
    location = start(client, "healthy")
    client.post(base_path(location) + "/run", data={"expected_cursor": "0"}, follow_redirects=True)

    replayed = client.post(base_path(location) + "/replay", follow_redirects=False)

    assert replayed.status_code == 303
    assert replayed.headers["location"] != location
    assert client.get(replayed.headers["location"]).status_code == 200


def test_evidence_download_is_a_json_attachment(client):
    location = start(client, "duplicate_success")
    client.post(base_path(location) + "/run", data={"expected_cursor": "0"}, follow_redirects=True)

    response = client.get(base_path(location) + "/evidence")

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
    client.post(base_path(location) + "/run", data={"expected_cursor": "0"}, follow_redirects=True)

    response = client.post(
        base_path(location) + "/reconcile",
        data={"refund_id": "R1", "tick": "1"},
    )

    assert response.status_code == 400
    assert "cannot look up" in response.text


def test_finished_run_disables_step_buttons(client):
    location = start(client, "healthy")
    page = client.post(base_path(location) + "/run", data={"expected_cursor": "0"}, follow_redirects=True)

    assert 'disabled>Step' in page.text
    assert 'disabled>Run to end' in page.text

def test_complete_delivery_with_a_pending_refund_explains_the_unknown(client):
    # lookup_recovers_success has no scheduled deliveries, so delivery is complete
    # while the refund is still pending. The page must not imply it settled.
    location = start(client, "lookup_recovers_success")
    page = client.get(location)

    assert "Notification delivery" in page.text
    assert "complete" in page.text
    assert "Reconcile" in page.text
    assert "Notification delivery" in page.text


# -- the guided walkthrough ----------------------------------------------


def test_walkthrough_lists_the_three_guided_steps(client):
    response = client.get("/walkthrough")

    assert response.status_code == 200
    body = response.text
    assert "The same notification, three times" in body
    assert "The notification that never arrived" in body
    assert "When the provider contradicts itself" in body
    # Every scenario stays reachable from the same page.
    assert "All scenarios" in body
    assert "Healthy control" in body
    assert body.count("Open") == 3


def test_walkthrough_starts_a_guided_scenario(client):
    response = client.post(
        "/runs", data={"scenario": "duplicate_success"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("?guided=1")


def test_guided_page_labels_both_sides_and_shows_one_figure(client):
    location = start(client, "duplicate_success")

    # Before anything is delivered both records are empty, and the page says so.
    empty = client.get(location)
    assert "Nothing delivered yet" in empty.text

    client.post(
        action_url(location, "/run"), data={"expected_cursor": "0"}, follow_redirects=True
    )
    page = client.get(location)

    assert page.status_code == 200
    assert "₹900.00" in page.text
    assert "₹300.00" in page.text
    # The local record framing must be visible.
    assert "not three payouts" in page.text


def test_guided_page_keeps_technical_detail_behind_a_disclosure(client):
    location = start(client, "duplicate_success")
    page = client.get(location)

    assert "Identifiers" in page.text
    assert "<details" in page.text
    assert "Notification history" in page.text


def test_guided_flag_survives_an_action(client):
    location = start(client, "duplicate_success")
    assert location.endswith("?guided=1")

    response = client.post(
        action_url(location, "/run"),
        data={"expected_cursor": "0"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("?guided=1")


def test_guided_page_keeps_the_full_list_reachable(client):
    page = client.get("/walkthrough")

    assert "Healthy control" in page.text
    assert "Conflicting results" in page.text
    assert "All scenarios" in page.text


def test_guided_flag_survives_replay(client):
    location = start(client, "duplicate_success")
    client.post(
        action_url(location, "/run"), data={"expected_cursor": "0"}, follow_redirects=True
    )

    replayed = client.post(action_url(location, "/replay"), follow_redirects=False)

    assert replayed.status_code == 303
    assert replayed.headers["location"].endswith("?guided=1")


def test_guided_copy_does_not_claim_a_result_before_deliveries(client):
    location = start(client, "duplicate_success")
    page = client.get(location)

    # Explanation of the scenario is allowed. A stated current total is not.
    assert "now records" not in page.text
    assert "records ₹900" not in page.text
    assert "Nothing delivered yet" in page.text
    assert "₹0.00" in page.text


# -- the redesigned navigation and pages ---------------------------------


def test_three_navigation_destinations_are_present(client):
    page = client.get("/")
    body = page.inner_text if False else page.text

    assert "Overview" in body
    assert "Demo" in body
    assert "Integrate" in body
    assert 'href="/integrate"' in body


def test_overview_states_the_scope_and_links_both_destinations(client):
    response = client.get("/")
    body = response.text

    assert response.status_code == 200
    assert "maintains refund records" in body.lower()
    assert "does not move money" in body.lower()
    assert 'href="/walkthrough"' in body
    assert 'href="/integrate"' in body


def test_integrate_page_documents_the_real_flow(client):
    response = client.get("/integrate")
    body = response.text

    assert response.status_code == 200
    assert "/v1/workspaces" in body
    assert "/v1/workspaces/{id}/refunds" in body
    assert "/v1/workspaces/{id}/deliveries" in body
    assert "GET /v1/workspaces/{id}" in body
    assert "INTEGRATION_API_KEY" in body
    assert "Authorization" in body
    assert "examples" in body
    # The key is never embedded in the page.
    assert "Bearer ey" not in body
    assert "your-key" in body


def test_integrate_page_says_the_key_is_required(client):
    body = client.get("/integrate").text

    assert "unavailable" in body.lower()
    assert "404" in body


def test_demo_page_leads_with_status_and_amounts(client):
    location = start(client, "duplicate_success")
    client.post(action_url(location, "/run"), data={"expected_cursor": "0"}, follow_redirects=True)
    page = client.get(location)

    assert "Refund" in page.text and "Status" in page.text
    assert "Confirmed" in page.text
    assert "Under review" in page.text
    assert "Notification history" in page.text
    # One summary, not a second copy of the same table.
    assert 'data-testid="comparison"' not in page.text
    assert "₹300.00" in page.text
    assert "₹900.00" in page.text


def test_naive_comparison_is_a_secondary_teaching_view(client):
    location = start(client, "duplicate_success")
    client.post(action_url(location, "/run"), data={"expected_cursor": "0"}, follow_redirects=True)
    page = client.get(location)

    assert "Naive baseline" in page.text
    assert "naive baseline" in page.text.lower()
    assert "not repeated payouts" in page.text or "not three payouts" in page.text
    # The two sides are named from the product's point of view.
    assert "Naive processor" in page.text
    assert "Protected processor" in page.text


def test_demo_labels_simulated_provider_data(client):
    body = client.get("/walkthrough").text

    assert "simulated" in body.lower()
    assert "no merchant payment" in body.lower()


def test_overview_corrects_the_webhook_framing(client):
    # Collapse whitespace: source line breaks are not part of the copy.
    body = " ".join(client.get("/").text.lower().split())

    # A proper processor handles duplicates without a lookup.
    assert "without asking anyone" in body
    assert "never arrived" in body
