from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.lab_service import LabService

router = APIRouter(prefix="/api")


class CreateRunBody(BaseModel):
    scenario: str


class CursorBody(BaseModel):
    expected_cursor: int


class ReconcileBody(BaseModel):
    refund_id: str
    tick: int | None = None


def _lab(request: Request) -> LabService:
    return request.app.state.lab


@router.get("/scenarios")
def scenarios(request: Request) -> JSONResponse:
    return JSONResponse({"scenarios": _lab(request).scenarios()})


@router.post("/runs")
def create_run(request: Request, body: CreateRunBody) -> JSONResponse:
    run_id = _lab(request).start(body.scenario)
    return JSONResponse(
        {"run_id": run_id, "url": "/runs/%s" % run_id}, status_code=201
    )


@router.get("/runs/{run_id}")
def run_report(request: Request, run_id: str) -> JSONResponse:
    return JSONResponse(_lab(request).report(run_id).to_dict())


@router.post("/runs/{run_id}/step")
def step(request: Request, run_id: str, body: CursorBody) -> JSONResponse:
    outcome = _lab(request).step(run_id, body.expected_cursor)
    return JSONResponse(
        {
            "cursor": outcome.cursor,
            "tick": outcome.tick,
            "attempt_id": outcome.attempt_id,
            "event_id": outcome.event_id,
            "outcome": outcome.outcome,
            "applied": outcome.applied,
            "next_cursor": outcome.next_cursor,
        }
    )


@router.post("/runs/{run_id}/run")
def run_to_end(request: Request, run_id: str, body: CursorBody) -> JSONResponse:
    report = _lab(request).run_to_end(run_id, body.expected_cursor)
    return JSONResponse(report.to_dict())


@router.post("/runs/{run_id}/reconcile")
def reconcile(request: Request, run_id: str, body: ReconcileBody) -> JSONResponse:
    record = _lab(request).reconcile(run_id, body.refund_id, body.tick)
    observation = record.observation
    return JSONResponse(
        {
            "refund_id": record.refund_id,
            "tick": record.tick,
            "outcome": record.outcome,
            "recorded": record.recorded,
            "observation": None
            if observation is None
            else {
                "event_id": observation.event_id,
                "refund_id": observation.refund_id,
                "status": observation.status,
                "amount_paise": observation.amount_paise,
            },
        }
    )


@router.post("/runs/{run_id}/replay")
def replay(request: Request, run_id: str) -> JSONResponse:
    new_run = _lab(request).replay(run_id)
    return JSONResponse(
        {"run_id": new_run, "url": "/runs/%s" % new_run}, status_code=201
    )


@router.get("/runs/{run_id}/evidence")
def evidence(request: Request, run_id: str) -> JSONResponse:
    return JSONResponse(_lab(request).report(run_id).to_dict())