from __future__ import annotations

from app.config import Settings


def test_platform_commit_is_used_when_the_image_has_none(monkeypatch):
    monkeypatch.setenv("LAB_DB_PATH", "var/test.db")
    monkeypatch.delenv("LAB_GIT_SHA", raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")

    assert Settings.from_env().git_sha == "abc123"


def test_build_time_sha_wins_over_the_platform(monkeypatch):
    monkeypatch.setenv("LAB_DB_PATH", "var/test.db")
    monkeypatch.setenv("LAB_GIT_SHA", "built")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "platform")

    assert Settings.from_env().git_sha == "built"


def test_placeholder_build_sha_does_not_mask_the_platform(monkeypatch):
    monkeypatch.setenv("LAB_DB_PATH", "var/test.db")
    monkeypatch.setenv("LAB_GIT_SHA", "unknown")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "platform")

    assert Settings.from_env().git_sha == "platform"