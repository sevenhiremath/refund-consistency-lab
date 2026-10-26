from __future__ import annotations


def test_health_reports_ready_database(client, db_path):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "refund-consistency-lab"
    assert body["git_sha"] == "tests-sha"
    assert body["database"]["journal_mode"] == "wal"
    assert body["database"]["writable"] is True
    assert db_path.exists()


def test_health_reports_degraded_when_wal_is_unavailable(client, monkeypatch):
    from app import db

    monkeypatch.setattr(
        db,
        "probe",
        lambda path: {
            "path": str(path),
            "journal_mode": "delete",
            "writable": True,
            "error": None,
        },
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_health_fails_when_database_cannot_be_opened(client, tmp_path):
    from app.config import Settings
    from app.main import create_app
    from fastapi.testclient import TestClient

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("occupied", encoding="utf-8")
    app = create_app(Settings(db_path=blocker / "lab.db", git_sha="tests-sha"))

    with TestClient(app) as broken_client:
        response = broken_client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["database"]["writable"] is False
    assert body["database"]["error"]