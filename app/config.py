from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

VERSION = "0.1.0"

# Matches the plan's deployment shape: one writable directory, mounted where the
# host provides one. Locally it resolves inside the checkout.
DEFAULT_DB_PATH = Path(os.environ.get("LAB_DB_PATH", "var/lab.db"))


@dataclass(frozen=True)
class Settings:
    db_path: Path
    git_sha: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=Path(os.environ.get("LAB_DB_PATH", str(DEFAULT_DB_PATH))),
            git_sha=os.environ.get("LAB_GIT_SHA") or _git_sha_fallback(),
        )


def _git_sha_fallback() -> str:
    """Best-effort SHA for local runs.

    The container image bakes LAB_GIT_SHA at build time, so a missing git binary
    inside the image is expected and reported as "unknown" rather than crashing.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else "unknown"