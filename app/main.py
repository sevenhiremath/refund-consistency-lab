from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from app import db
from app.api import router as api_router
from app.config import VERSION, Settings
from app.domain.money import format_paise
from app.errors import LabError
from app.lab_service import LabService
from app.pages import router as pages_router
from app.store import Store

TEMPLATES_DIR = Path(__file__).parent / "templates"

ERROR_TITLES = {
    400: "Bad request",
    404: "Not found",
    409: "The run moved on",
    503: "Unavailable",
}


def create_app(settings: Settings | None = None, fixtures_dir: Path | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            store = Store(resolved.db_path)
            store.initialize()
            application.state.lab = LabService(store, fixtures_dir)
            application.state.lab_error = None
        except (sqlite3.Error, OSError, ValueError) as exc:
            # /health still reports the database problem, and pages explain it,
            # instead of the process failing to start with no diagnosis.
            application.state.lab = None
            application.state.lab_error = "%s: %s" % (type(exc).__name__, exc)
        yield

    app = FastAPI(title="Refund Consistency Lab", version=VERSION, lifespan=lifespan)
    app.state.settings = resolved

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["paise"] = format_paise
    app.state.templates = templates

    def health_payload() -> tuple[dict[str, Any], int]:
        database = db.probe(resolved.db_path)
        if not database["writable"]:
            status = "error"
        elif database["journal_mode"] != "wal":
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

    @app.exception_handler(LabError)
    async def lab_error(request: Request, exc: LabError):
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                {"error": exc.message, "hint": exc.hint},
                status_code=exc.status_code,
            )
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "status": exc.status_code,
                "title": ERROR_TITLES.get(exc.status_code, "Something went wrong"),
                "message": exc.message,
                "hint": exc.hint,
            },
            status_code=exc.status_code,
        )

    app.include_router(pages_router)
    app.include_router(api_router)
    return app


app = create_app()