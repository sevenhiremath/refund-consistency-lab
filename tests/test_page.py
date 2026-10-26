from __future__ import annotations

from app.main import PLANNED_SCENARIOS


def test_index_renders_skeleton_state(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "Refund Consistency Lab" in body
    assert "Skeleton checkpoint" in body
    assert "tests-sha" in body
    assert "wal" in body


def test_index_does_not_claim_working_scenarios(client):
    body = client.get("/").text

    assert "None of these run in this build." in body
    for scenario in PLANNED_SCENARIOS:
        assert scenario in body