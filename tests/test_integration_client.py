from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"
sys.path.insert(0, str(EXAMPLES))

from integration_client import API_KEY_ENV, ClientError, LabClient, main, run  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

KEY = "client-test-key"


def build(tmp_path, monkeypatch, key=KEY):
    monkeypatch.setenv("INTEGRATION_API_KEY", key)
    app = create_app(Settings(db_path=tmp_path / "client.db", git_sha="tests"))
    return TestClient(app)


def test_example_flow_runs_against_the_real_api(tmp_path, monkeypatch, capsys):
    with build(tmp_path, monkeypatch) as http:
        client = LabClient("http://testserver", KEY, http=http)
        run(client)

    out = capsys.readouterr().out
    assert "workspace  ws_" in out
    assert "delivery   a1 accepted" in out
    assert "delivery   a2 duplicate_event" in out
    assert "delivery   a3 duplicate_event" in out
    assert "retry      a1 recorded=False" in out
    assert "pending    E9 accepted" in out
    assert "status succeeded" in out
    assert out.count("confirmed 30000") >= 3


def test_the_key_is_never_printed(tmp_path, monkeypatch, capsys):
    with build(tmp_path, monkeypatch) as http:
        client = LabClient("http://testserver", KEY, http=http)
        run(client)

    captured = capsys.readouterr()
    assert KEY not in captured.out
    assert KEY not in captured.err


def test_a_wrong_key_fails_with_a_clear_message_without_leaking_the_key(
    tmp_path, monkeypatch
):
    with build(tmp_path, monkeypatch) as http:
        client = LabClient("http://testserver", "wrong-key", http=http)
        with pytest.raises(ClientError) as excinfo:
            client.status("ws_missing")

    message = str(excinfo.value)
    assert "401" in message
    assert "wrong-key" not in message


def test_a_missing_workspace_fails_instead_of_retrying(tmp_path, monkeypatch):
    with build(tmp_path, monkeypatch) as http:
        client = LabClient("http://testserver", KEY, http=http)
        with pytest.raises(ClientError) as excinfo:
            client.status("ws_missing")

    assert "404" in str(excinfo.value)


def test_missing_environment_exits_without_calling_anything(monkeypatch, capsys):
    monkeypatch.delenv("LAB_BASE_URL", raising=False)
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    assert main() == 2
    assert "set LAB_BASE_URL" in capsys.readouterr().err


def test_the_client_imports_nothing_from_the_lab():
    source = (EXAMPLES / "integration_client.py").read_text(encoding="utf-8")

    assert "from app" not in source
    assert "import app" not in source