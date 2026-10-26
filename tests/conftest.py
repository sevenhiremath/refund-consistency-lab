from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "lab.db"


@pytest.fixture()
def client(db_path):
    app = create_app(Settings(db_path=db_path, git_sha="tests-sha"))
    with TestClient(app) as test_client:
        yield test_client