from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app import db
from app.config import VERSION, Settings

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Page content only. Nothing here is wired to a processor; the list exists so the
# skeleton states what it will cover instead of implying it already does.
PLANNED_SCENARIOS = (
    "Healthy control",
    "Duplicate delivery",
    "Pending after success",
    "Dropped success, recovered by lookup",
    "Dropped success, lookup times out",
    "Partial refunds with a duplicate",
    "Success and failure reported for the same refund",
)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    app = FastAPI(title="Refund Consistency Lab", version=VERSION)
    app.state.settings = resolved
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    def health_payload() -> tuple[dict[str, Any], int]:
        database = db.probe(resolved.db_path)
        if not database["writable"]:
            status = "error"
        elif database["journal_mode"] != "wal":
            # A mounted network volume can refuse WAL and fall back to a rollback
            # journal. The lab still works; concurrent writers get slower, so this
            # is reported rather than treated as an outage.
            status = "degraded"
        else:
            status = "ok"
        payload = {
            "status": status,
            "service": "refund-consistency-lab",
            "version": VERSION,
            "git_sha": resolved.git_sha,
            "database": database,
        }
        return payload, 503 if status == "error" else 200

    @app.get("/health")
    def health() -> JSONResponse:
        payload, status_code = health_payload()
        return JSONResponse(payload, status_code=status_code)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        payload, _ = health_payload()
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "version": VERSION,
                "git_sha": resolved.git_sha,
                "database": payload["database"],
                "status": payload["status"],
                "scenarios": PLANNED_SCENARIOS,
            },
        )

    return app


app = create_app()