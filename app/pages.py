from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.config import VERSION
from app.errors import LabError
from app.guided import GUIDED_STEPS, guided_step
from app.lab_service import LabService

router = APIRouter()


def _lab(request: Request) -> LabService:
    lab = getattr(request.app.state, "lab", None)
    if lab is None:
        raise LabError(
            503,
            "the database is not available",
            "The server started without a usable database. Check /health.",
        )
    return lab


def _templates(request: Request):
    return request.app.state.templates


def _guided_suffix(request: Request) -> str:
    """Keep the guided flag across a redirect."""
    return "?guided=1" if request.query_params.get("guided") else ""


def _int_field(form, name: str, default: int) -> int:
    raw = form.get(name, "")
    if raw in (None, ""):
        return default
    try:
        return int(str(raw))
    except ValueError as exc:
        raise LabError(
            400,
            "%s must be a whole number" % name,
            "Reload the page and try again.",
        ) from exc


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return _templates(request).TemplateResponse(
        request=request,
        name="index.html",
        context={
            "version": VERSION,
            "scenarios": _lab(request).scenarios(),
            "guided": GUIDED_STEPS,
        },
    )


@router.get("/integrate", response_class=HTMLResponse)
async def integrate(request: Request) -> HTMLResponse:
    """Documentation for the integration API. Never contains a key."""
    return _templates(request).TemplateResponse(
        request=request,
        name="integrate.html",
        context={"version": VERSION},
    )


@router.get("/walkthrough", response_class=HTMLResponse)
async def walkthrough(request: Request) -> HTMLResponse:
    """Entry point for the guided path. The full list stays at /."""
    return _templates(request).TemplateResponse(
        request=request,
        name="walkthrough.html",
        context={
            "version": VERSION,
            "scenarios": _lab(request).scenarios(),
            "guided": GUIDED_STEPS,
        },
    )


@router.post("/runs")
async def create_run(request: Request) -> RedirectResponse:
    form = await request.form()
    scenario = str(form.get("scenario", ""))
    run_id = _lab(request).start(scenario)
    suffix = "?guided=1" if guided_step(scenario) else ""
    return RedirectResponse("/runs/%s%s" % (run_id, suffix), status_code=303)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_page(request: Request, run_id: str) -> HTMLResponse:
    report = _lab(request).report(run_id)
    body = report.body
    # A pending refund at the end of the schedule is unresolved, not failed. The
    # page says so instead of implying the run settled the refund.
    pending_refunds = [
        row["refund_id"]
        for row in body["comparison"]["refunds"]
        if row["protected_status"] == "pending"
    ]
    return _templates(request).TemplateResponse(
        request=request,
        name="run.html",
        context={
            "run_id": run_id,
            "body": body,
            "finished": body["progress"]["finished"],
            "digest": report.digest,
            "pending_refunds": pending_refunds,
            "guided": guided_step(str(body["fixture"]["name"])),
        },
    )


@router.post("/runs/{run_id}/step")
async def step(request: Request, run_id: str) -> RedirectResponse:
    form = await request.form()
    expected = _int_field(form, "expected_cursor", 0)
    _lab(request).step(run_id, expected)
    return RedirectResponse("/runs/%s%s" % (run_id, _guided_suffix(request)), status_code=303)


@router.post("/runs/{run_id}/run")
async def run_to_end(request: Request, run_id: str) -> RedirectResponse:
    form = await request.form()
    expected = _int_field(form, "expected_cursor", 0)
    _lab(request).run_to_end(run_id, expected)
    return RedirectResponse("/runs/%s%s" % (run_id, _guided_suffix(request)), status_code=303)


@router.post("/runs/{run_id}/reconcile")
async def reconcile(request: Request, run_id: str) -> RedirectResponse:
    form = await request.form()
    current = _lab(request).report(run_id).body["progress"]["tick"]
    tick = _int_field(form, "tick", current)
    _lab(request).reconcile(run_id, str(form.get("refund_id", "")), tick)
    return RedirectResponse("/runs/%s%s" % (run_id, _guided_suffix(request)), status_code=303)


@router.post("/runs/{run_id}/replay")
async def replay(request: Request, run_id: str) -> RedirectResponse:
    new_run = _lab(request).replay(run_id)
    return RedirectResponse(
        "/runs/%s%s" % (new_run, _guided_suffix(request)), status_code=303
    )


@router.get("/runs/{run_id}/evidence")
async def evidence(request: Request, run_id: str) -> JSONResponse:
    report = _lab(request).report(run_id)
    filename = "%s-%s.json" % (report.body["fixture"]["name"], run_id)
    return JSONResponse(
        report.to_dict(),
        headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
    )