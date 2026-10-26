from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

# How long a writer waits for the single SQLite write lock before giving up.
# The ingest path turns an exhausted budget into a retryable 503.
BUSY_TIMEOUT_SECONDS = 5.0


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the lab database with the pragmas the plan depends on.

    isolation_level=None disables sqlite3's implicit transaction handling so that
    every write path must issue an explicit BEGIN IMMEDIATE. That is deliberate:
    statements outside an explicit transaction auto-commit, which is exactly the
    naive torn-write failure the lab exists to show.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=%d" % int(BUSY_TIMEOUT_SECONDS * 1000))
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def probe(db_path: Path) -> dict[str, Any]:
    """Report whether the deployment target can actually host this database.

    Checks the three things a host can silently take away: a writable directory,
    a real WAL journal (network filesystems fall back to "delete"), and the
    ability to take the write lock.
    """
    result: dict[str, Any] = {
        "path": str(db_path),
        "journal_mode": None,
        "writable": False,
        "error": None,
    }
    try:
        conn = connect(db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        return result

    result["journal_mode"] = str(mode).lower()
    result["writable"] = True
    return result