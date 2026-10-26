from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from app.sim.fixture import load_fixture
from app.sim.runner import Runner
from app.store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.sim",
        description="Run one fixture to the end and print its normalized report.",
    )
    parser.add_argument("fixture", help="path to a fixture JSON file")
    parser.add_argument(
        "--db",
        default=None,
        help="database path, defaults to a temporary file",
    )
    args = parser.parse_args(argv)

    fixture = load_fixture(Path(args.fixture))

    temporary = None
    if args.db:
        db_path = Path(args.db)
    else:
        temporary = tempfile.TemporaryDirectory()
        db_path = Path(temporary.name) / "lab.db"

    try:
        store = Store(db_path)
        store.initialize()
        runner = Runner(store)
        run_id = runner.start(fixture)
        report = runner.run_to_end(run_id, 0)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0
    finally:
        if temporary is not None:
            temporary.cleanup()