"""Campaign Window / Campaign Run / audience API (v2.0.0 foundation).

Thin read + lifecycle surface over app.services.windows so the domain is
reachable and inspectable. Deliberately minimal: no reporting aggregations and
no SMS execution (both arrive in later tasks).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db.windows import WindowConfigError, WindowStateError
from app.services import eligibility, windows as svc

router = APIRouter(tags=["campaign-windows"])


class CreateWindowRequest(BaseModel):
    name: str | None = Field(None, max_length=200)
    start_time: datetime | None = None
    end_time: datetime | None = None
    finalization_deadline: datetime | None = None
    segments: list[str] | None = None
    assignment_method: str = "deterministic"
    control_override: float | None = None


class ExtendEndRequest(BaseModel):
    new_end: datetime


class EligibleCountRequest(BaseModel):
    eligible_count: int = Field(gt=0)
    segment_counts: dict[str, int] | None = None


class ControlOverrideRequest(BaseModel):
    percentage: float


class StartRunRequest(BaseModel):
    note: str | None = Field(None, max_length=500)


class StopRunRequest(BaseModel):
    stop_reason: str = "operator"


class AudienceMember(BaseModel):
    user_id: str
    segment_id: str
    phone: str | None = None
    phone_valid: bool | None = None
    eligibility_state: dict | None = None


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, WindowStateError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, WindowConfigError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Campaign Windows
# ---------------------------------------------------------------------------

@router.post("/windows")
def create_window(req: CreateWindowRequest):
    try:
        return svc.create_window(
            name=req.name,
            start_time=req.start_time,
            end_time=req.end_time,
            finalization_deadline=req.finalization_deadline,
            segments=req.segments,
            assignment_method=req.assignment_method,
            control_override=req.control_override,
        )
    except (WindowConfigError, WindowStateError) as exc:
        raise _http(exc)


@router.get("/windows")
def list_windows(limit: int = 50):
    return svc.list_windows(limit)


@router.get("/windows/{window_id}")
def window_detail(window_id: int):
    try:
        return svc.get_window_detail(window_id)
    except WindowStateError as exc:
        raise _http(exc)


@router.post("/windows/{window_id}/end")
def end_window(window_id: int):
    try:
        return svc.end_window(window_id)
    except WindowStateError as exc:
        raise _http(exc)


@router.post("/windows/{window_id}/finalize")
def finalize_window(window_id: int):
    try:
        return svc.finalize_window(window_id)
    except WindowStateError as exc:
        raise _http(exc)


@router.post("/windows/{window_id}/extend-end")
def extend_end(window_id: int, req: ExtendEndRequest):
    try:
        return svc.extend_window_end(window_id, req.new_end)
    except (WindowConfigError, WindowStateError) as exc:
        raise _http(exc)


@router.post("/windows/{window_id}/eligible-count")
def set_eligible_count(window_id: int, req: EligibleCountRequest):
    try:
        return svc.set_eligible_count(window_id, req.eligible_count, req.segment_counts)
    except (WindowConfigError, WindowStateError) as exc:
        raise _http(exc)


@router.post("/windows/{window_id}/control-override")
def set_control_override(window_id: int, req: ControlOverrideRequest):
    try:
        return svc.set_control_override(window_id, req.percentage)
    except (WindowConfigError, WindowStateError) as exc:
        raise _http(exc)


# ---------------------------------------------------------------------------
# Campaign Runs
# ---------------------------------------------------------------------------

@router.post("/windows/{window_id}/runs")
def start_run(window_id: int, req: StartRunRequest | None = None):
    try:
        return svc.start_run(window_id, note=req.note if req else None)
    except (WindowConfigError, WindowStateError) as exc:
        raise _http(exc)


@router.get("/windows/{window_id}/runs")
def list_runs(window_id: int):
    return svc.list_runs(window_id)


@router.get("/runs/{run_id}")
def get_run(run_id: int):
    try:
        return svc.get_run(run_id)
    except WindowStateError as exc:
        raise _http(exc)


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: int, req: StopRunRequest | None = None):
    try:
        return svc.stop_run(run_id, stop_reason=req.stop_reason if req else "operator")
    except WindowStateError as exc:
        raise _http(exc)


@router.post("/runs/{run_id}/complete")
def complete_run(run_id: int):
    try:
        return svc.complete_run(run_id)
    except WindowStateError as exc:
        raise _http(exc)


@router.post("/runs/{run_id}/evaluate")
def evaluate_run(run_id: int):
    """Evaluate current-welcome eligibility for a running Run.

    Reads exactly the Run's frozen snapshot, computes the segment membership +
    eligibility batch (login band, play-after-login, cooldown, phone validity),
    adds eligible users to the window audience, and refreshes N. Never
    completes/stops the Run.
    """
    try:
        return eligibility.evaluate_run(run_id)
    except (WindowConfigError, WindowStateError) as exc:
        raise _http(exc)


@router.get("/runs/{run_id}/snapshot")
def run_snapshot(run_id: int):
    try:
        return svc.get_run_snapshot(run_id)
    except WindowStateError as exc:
        raise _http(exc)


# ---------------------------------------------------------------------------
# Audience + assignment
# ---------------------------------------------------------------------------

@router.post("/windows/{window_id}/audience")
def add_audience(window_id: int, members: list[AudienceMember]):
    try:
        return svc.add_eligible_users(window_id, [m.model_dump() for m in members])
    except (WindowConfigError, WindowStateError) as exc:
        raise _http(exc)


@router.get("/windows/{window_id}/audience")
def get_audience(window_id: int):
    return svc.get_audience(window_id)


@router.get("/windows/{window_id}/audience/count")
def audience_count(window_id: int):
    return svc.count_audience(window_id)


@router.get("/windows/{window_id}/recipients")
def campaign_recipients(window_id: int):
    return svc.list_campaign_recipients(window_id)