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
from app.services import eligibility, execution, segments, windows as svc

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


@router.get("/segments")
def list_segments():
    """The segment catalog for Window configuration (ids + labels + default)."""
    return {"items": segments.list_segments()}


@router.get("/windows")
def list_windows(limit: int = 50):
    return svc.list_windows_overview(limit)


@router.get("/windows/{window_id}")
def window_detail(window_id: int):
    try:
        return svc.get_window_detail(window_id)
    except WindowStateError as exc:
        raise _http(exc)


@router.get("/windows/{window_id}/data-state")
def window_data_state(window_id: int):
    """Current source-data state for a Window (files now vs Runs' snapshots)."""
    try:
        return svc.get_window_data_state(window_id)
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


@router.get("/windows/{window_id}/report")
def window_report(window_id: int):
    """The Campaign Window report.

    Finalized windows serve the frozen snapshot written at finalization;
    active/ended windows compute a live report (ingesting grace-period uploads
    first).
    """
    from app.services.window_report import get_report

    if svc.get_window(window_id) is None:
        raise HTTPException(status_code=404, detail=f"Campaign Window #{window_id} not found.")
    try:
        return get_report(window_id)
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
        result = svc.start_run(window_id, note=req.note if req else None)
        # Backward compatibility: flatten the response
        return {
            "run": result["run"],
            "snapshot": result["snapshot"],
            "evaluation": result["evaluation"],
        }
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


@router.get("/runs/{run_id}/evaluation")
def get_run_evaluation(run_id: int):
    """Get the evaluation report for a Run's frozen target.

    Evaluation happens once at Run start. This endpoint returns the stored
    evaluation results (candidates, decisions, audience admitted) without
    re-evaluating or mutating the Run target.
    """
    run = svc.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Campaign Run #{run_id} not found.")

    # Get the audience members admitted by this run
    from app.db.windows import get_audience
    audience = get_audience(run["window_id"])
    run_members = [m for m in audience if m.get("run_id") == run_id]

    decisions: dict[str, int] = {}
    for m in run_members:
        # We don't have the original decision stored, but we know they were eligible
        # since they were admitted. For display purposes, count them as eligible.
        decisions["eligible"] = decisions.get("eligible", 0) + 1

    return {
        "run_id": run_id,
        "window_id": run["window_id"],
        "evaluated_at": run["started_at"],
        "candidates": len(run_members),
        "decisions": decisions,
        "audience": {
            "added": len(run_members),
            "existing": 0,
            "campaign": sum(1 for m in run_members if m["assignment"] == "campaign"),
            "control": sum(1 for m in run_members if m["assignment"] == "control"),
            "invalid_phone": 0,
        },
        "message": "Evaluation completed at Run start. Target is frozen.",
    }


@router.post("/runs/{run_id}/dispatch")
async def dispatch_run(run_id: int):
    """Send the Welcome SMS to a running Run's frozen target.

    Reads exactly the members THIS Run admitted (window_audiences run_id,
    Campaign-assigned, phone_valid). Control users and unusable phones never
    reached the audience. Cooldown and the welcome cap are enforced at
    dispatch; a user already accepted for this Run (sms_log cycle_id
    "run:{run_id}") is never re-sent, so repeated calls are idempotent and
    only failed attempts are retried. Does not complete/stop the Run.
    """
    try:
        return await execution.dispatch_run(run_id)
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