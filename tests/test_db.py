from __future__ import annotations

import sqlite3

from app import db


def test_connect_applies_lab_pragmas(tmp_path):
    conn = db.connect(tmp_path / "nested" / "lab.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        # isolation_level=None means no implicit transaction is ever open.
        assert conn.in_transaction is False
    finally:
        conn.close()


def test_probe_does_not_leave_a_write_transaction(tmp_path):
    path = tmp_path / "lab.db"

    result = db.probe(path)

    assert result["writable"] is True
    assert result["journal_mode"] == "wal"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        assert conn.in_transaction is False
    finally:
        conn.close()