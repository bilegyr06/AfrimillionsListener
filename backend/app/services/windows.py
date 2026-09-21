"""Campaign Window domain service (v2.0.0 foundation).

Implements the business rules for the Campaign Window model:

  * Window lifecycle: active -> ended (grace period) -> finalized (immutable).
  * Calendar defaults: Monday 00:00 -> Saturday 23:59:59 Africa/Lagos; the
    finalization deadline defaults to Sunday 14:00 and may be configured no
    later than Sunday 17:00 (Africa/Lagos).
  * Segments are opaque identifiers selected on the Window (default:
    ['unsegmented']). The authoritative segment SQL is supplied separately and
    is deliberately NOT hard-coded here. Eligibility per segment produces the
    eligible audience; the window sums those per-segment counts into N.
  * Control calculation: C = 15,500 / N + 1,550; if C >= 0.5*N use 50%,
    otherwise Control% = C / N * 100, bucketed (<10% -> 10, <15% -> 15,
    <20% -> 20, else the calculated value). The operator may provide an
    override when creating the Window (or before the effective percentage is
    established); once the effective Control percentage is established it is
    fixed for the entire window and is never recomputed as N grows.
  * Campaign/Control assignment is Window-level and persistent per user.
    Deterministic assignment uses (user_id % 100) < campaign_percentage;
    random assignment uses a stable per-window/user seed so it never changes a
    member's assignment later.

Persistence is delegated to app.db.windows, which owns the transaction /
immutability guards; this module owns the decisions.
"""
from __future__ import annotations

import json
import random
import time as _timemod
from datetime import datetime
from pathlib import Path

from app.core import dates
from app.core.config import settings
from app.core.phones import gate_phone
from app.db import windows as db
from app.db.windows import WindowConfigError, WindowStateError
from app.services import eligibility

UNSEGMENTED = "unsegmented"

VALID_ASSIGNMENT_METHODS = ("deterministic", "random")
VALID_STATUSES = ("active", "ended", "finalized")
VALID_RUN_STATUSES = ("running", "completed", "stopped")


# ---------------------------------------------------------------------------
# Control calculation
# ---------------------------------------------------------------------------

def suggest_control_percentage(eligible_count: int) -> float:
    """Effective control percentage for N eligible users (formula + buckets).

    C = 15,500 / N + 1,550. When C >= 0.5*N the allocation is 50%. Otherwise
    Control% = C / N * 100, bucketed: below 10% -> 10%, 10-14.99% -> 15%,
    15-19.99% -> 20%, 20%+ -> the calculated percentage.
    """
    if eligible_count <= 0:
        raise WindowConfigError("Eligible audience (N) must be a positive integer.")
    c = 15500.0 / eligible_count + 1550.0
    if c >= 0.5 * eligible_count:
        raw = 50.0
    else:
        raw = c / eligible_count * 100.0
    if raw < 10:
        return 10.0
    if raw < 15:
        return 15.0
    if raw < 20:
        return 20.0
    return round(raw, 4)


def campaign_percentage_for(control_percentage: float) -> float:
    return round(100.0 - control_percentage, 4)


# ---------------------------------------------------------------------------
# Assignment primitives
# ---------------------------------------------------------------------------

def _numeric_user_id(user_id: str) -> int:
    try:
        return int(str(user_id))
    except (TypeError, ValueError):
        raise WindowConfigError(
            f"user_id {user_id!r} is not an integer; deterministic assignment "
            "(user_id % 100) < campaign_percentage requires a numeric user id."
        )


def deterministic_is_campaign(user_id: str, campaign_percentage: float) -> bool:
    """Deterministic rule: (user_id % 100) < campaign_percentage."""
    return _numeric_user_id(user_id) % 100 < campaign_percentage


def random_is_campaign(user_id: str, campaign_percentage: float, seed: int) -> bool:
    """Stable pseudo-random assignment seeded per (window, user).

    The seed makes the assignment reproducible and, more importantly, stable:
    re-evaluating the same user in the same window always yields the same
    assignment, so a member's assignment persists for the whole window.
    """
    rng = random.Random(f"{seed}|{user_id}")
    return rng.uniform(0, 100) < campaign_percentage


# ---------------------------------------------------------------------------
# Window lifecycle
# ---------------------------------------------------------------------------

def create_window(
    name: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    finalization_deadline: datetime | None = None,
    segments: list[str] | None = None,
    assignment_method: str = "deterministic",
    control_override: float | None = None,
    now: datetime | None = None,
) -> dict:
    """Create a new 'active' Camp Window with validated boundaries.

    Defaults (Africa/Lagos): start Monday 00:00, end Saturday 23:59:59,
    finalization deadline Sunday 14:00. The deadline may be configured no later
    than Sunday 17:00 for the corresponding window.

    Only one Campaign Window runs at a time: creating a window while another
    window is 'active' is rejected (the persistence layer enforces the same
    rule inside the insert transaction).
    """
    ref = dates.now_business() if now is None else dates.as_business(now)

    start = dates.default_window_start(ref) if start_time is None else dates.as_business(start_time)
    end = dates.default_window_end(start) if end_time is None else dates.as_business(end_time)
    deadline = (
        dates.default_finalization_deadline(start)
        if finalization_deadline is None
        else dates.as_business(finalization_deadline)
    )

    if end <= start:
        raise WindowConfigError("Window end_time must be after start_time.")
    if deadline < end:
        raise WindowConfigError(
            "The finalization deadline must be at or after the window end_time "
            "(the grace period starts when the window ends)."
        )
    if deadline > dates.max_finalization_deadline(start):
        raise WindowConfigError(
            "The finalization deadline may not be configured beyond Sunday 17:00 "
            f"(17:00) Africa/Lagos for this window (got {deadline.isoformat()})."
        )

    if segments is None:
        segment_list = [UNSEGMENTED]
    else:
        segment_list = list(segments)
        if not segment_list:
            raise WindowConfigError(
                "At least one segment must be selected (omit segments to use "
                "the default 'unsegmented' audience)."
            )
    if any(not isinstance(s, str) or not s for s in segment_list):
        raise WindowConfigError("At least one non-empty segment must be selected.")
    if len(set(segment_list)) != len(segment_list):
        raise WindowConfigError("Selected segments must be unique (segments are mutually exclusive).")
    if assignment_method not in VALID_ASSIGNMENT_METHODS:
        raise WindowConfigError(
            f"assignment_method must be one of {VALID_ASSIGNMENT_METHODS}; got {assignment_method!r}."
        )
    if control_override is not None and not (0 < control_override <= 50):
        raise WindowConfigError("Control override must be in the range (0, 50] percent.")

    active = db.get_active_window()
    if active is not None:
        raise WindowStateError(
            f"Only one Campaign Window can run at a time: Campaign Window "
            f"#{active['id']} is already active. End or finalize it before "
            "creating another window."
        )

    now_iso = dates.to_utc_iso(ref)
    return db.create_window(
        {
            "name": name,
            "start_time": dates.to_utc_iso(start),
            "end_time": dates.to_utc_iso(end),
            "finalization_deadline": dates.to_utc_iso(deadline),
            "business_timezone": dates.BUSINESS_TIMEZONE,
            "selected_segments": segment_list,
            "assignment_method": assignment_method,
            "control_override": control_override,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
    )


def get_window(window_id: int) -> dict | None:
    return db.get_window(window_id)


def _split_summary(window: dict, audience: dict) -> dict:
    """Recommended vs effective vs actual Campaign/Control split for a window.

    All percentages come from the backend formula: the recommmeded split uses
    the stored `suggested_control_percentage`, the effective split uses the
    stored `control_percentage` (suggestion or operator override), and the
    actual split derives from the assigned audience counts. The frontend only
    renders this; it never recomputes a percentage.
    """
    suggested = window.get("suggested_control_percentage")
    effective = window.get("control_percentage")
    total = audience.get("total", 0)
    campaign = audience.get("campaign", 0)
    control = audience.get("control", 0)
    actual_campaign_pct = round(campaign / total * 100.0, 2) if total else None
    actual_control_pct = round(control / total * 100.0, 2) if total else None
    return {
        "assignment_method": window.get("assignment_method"),
        "control_override": window.get("control_override"),
        "recommended": {
            "campaign_percentage": round(campaign_percentage_for(suggested), 2),
            "control_percentage": round(suggested, 2),
        }
        if suggested is not None
        else None,
        "effective": {
            "campaign_percentage": round(campaign_percentage_for(effective), 2),
            "control_percentage": round(effective, 2),
        }
        if effective is not None
        else None,
        "actual": {
            "campaign_users": campaign,
            "control_users": control,
            "total_users": total,
            "campaign_percentage": actual_campaign_pct,
            "control_percentage": actual_control_pct,
        },
    }


def list_windows(limit: int = 50) -> list[dict]:
    return db.list_windows(limit)


def list_windows_overview(limit: int = 50) -> list[dict]:
    """Window list rows enriched for the operator surface.

    Each row carries its audience split and Run activity so the Windows list
    can answer "what is this Window right now?" without a second round-trip per
    window: audience (campaign/control/total), runs_count, running run ids and
    the report state (live while not finalized, frozen once finalized).
    """
    windows = db.list_windows(limit)
    out = []
    for window in windows:
        runs = db.list_runs(window["id"])
        audience = db.count_audience(window["id"])
        running = [r for r in runs if r["status"] == "running"]
        out.append(
            {
                **window,
                "audience": audience,
                "split": _split_summary(window, audience),
                "runs_count": len(runs),
                "running_runs": [
                    {
                        "id": r["id"],
                        "status": r["status"],
                        "started_at": r["started_at"],
                        "note": r.get("note"),
                    }
                    for r in running
                ],
                "report_state": "frozen" if window["status"] == "finalized" else "live",
            }
        )
    return out


def get_window_data_state(window_id: int) -> dict:
    """Source-data state for a Window: what files exist now vs what its Runs froze.

    Combines the current data-folder capture (what a future Run would snapshot)
    with every Run's frozen snapshot (which files each Run is bound to), so the
    operator can see that a new upload is available to a future evaluation while
    existing Runs stay on their original snapshot.
    """
    window = db.get_window(window_id)
    if window is None:
        raise WindowStateError(f"Campaign Window #{window_id} not found.")
    current = capture_source_snapshot(window_id)
    runs = db.list_runs(window_id)
    run_states = []
    for run in runs:
        snapshot = db.get_run_snapshot(run["id"])
        run_states.append(
            {
                "id": run["id"],
                "status": run["status"],
                "started_at": run["started_at"],
                "ended_at": run.get("ended_at"),
                "note": run.get("note"),
                "snapshot_captured_at": snapshot["captured_at"] if snapshot else None,
                "snapshot_files": snapshot["files"] if snapshot else [],
            }
        )
    return {
        "window_id": window_id,
        "status": window["status"],
        "finalized_at": window.get("finalized_at"),
        "finalization_deadline": window["finalization_deadline"],
        "report_state": "frozen" if window["status"] == "finalized" else "live",
        "captured_at": dates.to_utc_iso(dates.now_business()),
        "current_files": current,
        "runs": run_states,
    }


def get_window_detail(window_id: int) -> dict:
    """Window plus audience counts, its runs and each run's frozen snapshot.

    Used for the operator workspace: the runs carry `snapshot` (captured_at +
    files) so the page can show the "this Run is bound to its snapshot" state
    without a per-run call.
    """
    window = db.get_window(window_id)
    if window is None:
        raise WindowStateError(f"Campaign Window #{window_id} not found.")
    runs = db.list_runs(window_id)
    for run in runs:
        snapshot = db.get_run_snapshot(run["id"])
        run["snapshot"] = {
            "captured_at": snapshot["captured_at"] if snapshot else None,
            "files": snapshot["files"] if snapshot else [],
        }
    audience = db.count_audience(window_id)
    return {
        **window,
        "audience": audience,
        "split": _split_summary(window, audience),
        "runs": runs,
        "report_state": "frozen" if window["status"] == "finalized" else "live",
    }


def set_eligible_count(
    window_id: int,
    eligible_count: int,
    segment_counts: dict[str, int] | None = None,
) -> dict:
    """Set N (the summed eligible audience across selected segments) and, the
    first time, the effective control percentage.

    If `segment_counts` is provided, its values must sum to `eligible_count`
    (each selected segment contributes its eligible users to N).

    The effective Control percentage is established from the first N that is
    configured: the formula suggestion is computed from that N, and the operator
    override (when present) wins over it. Once a value exists it is frozen -
    later calls only update N and the per-segment counts (growing N never
    re-derives or moves the split). This does NOT lock the configuration: the
    operator may still change the override until the window's first Campaign Run
    starts, at which point the percentage becomes immutable for the whole window.
    """
    if eligible_count <= 0:
        raise WindowConfigError("eligible_count (N) must be a positive integer.")
    if segment_counts and sum(segment_counts.values()) != eligible_count:
        raise WindowConfigError(
            "segment_counts must sum to eligible_count (N is the sum of the "
            "eligible users of all selected segments)."
        )
    window = db.get_window(window_id)
    if window is None:
        raise WindowStateError(f"Campaign Window #{window_id} not found.")
    override = window["control_override"]
    suggested = suggest_control_percentage(eligible_count)
    effective = override if override is not None else suggested
    return db.update_window_control(
        window_id,
        eligible_count=eligible_count,
        suggested_control_percentage=suggested,
        control_percentage=effective,
        segment_eligible_counts=segment_counts if segment_counts else None,
    )


def set_control_override(window_id: int, percentage: float) -> dict:
    """Persist an operator override of the effective control percentage.

    Allowed up to 50%. Valid only BEFORE the window's first Campaign Run starts
    (an override provided at window creation, or this endpoint before the first
    Run). Starting the first Run locks the Control percentage for the entire
    window: a later call raises WindowStateError even while the window is
    active, and the lock holds even when that first Run admits no eligible
    users. Before the lock, the override immediately becomes the effective
    percentage when N is already known; otherwise it stays pending until N (or
    the first Run) establishes it. Existing audience assignments are never
    rewritten (each member records the config that produced it).
    """
    if not (0 < percentage <= 50):
        raise WindowConfigError("Control override must be in the range (0, 50] percent.")
    window = db.set_control_override(window_id, percentage)
    if window is None:
        raise WindowStateError(f"Campaign Window #{window_id} not found.")
    return window


def extend_window_end(window_id: int, new_end: datetime) -> dict:
    """Extend the configured end time of an active window.

    The new end must stay after the start and at or before the finalization
    deadline so a (non-empty) grace period is preserved.
    """
    window = db.get_window(window_id)
    if window is None:
        raise WindowStateError(f"Campaign Window #{window_id} not found.")
    new_end = dates.as_business(new_end)
    start = dates.parse_utc_iso(window["start_time"])
    deadline = dates.parse_utc_iso(window["finalization_deadline"])
    if new_end <= start:
        raise WindowConfigError("Extended end_time must be after the window start.")
    if new_end > deadline:
        raise WindowConfigError(
            "Extended end_time must stay at or before the finalization deadline "
            f"({deadline.isoformat()})."
        )
    return db.update_window_end(window_id, dates.to_utc_iso(new_end))


def end_window(window_id: int) -> dict:
    """End the window (if active): close active Runs and enter the grace period."""
    window, closed_runs = db.end_window_transition(
        window_id, dates.to_utc_iso(dates.now_business())
    )
    return {**window, "closed_runs": closed_runs}


def finalize_window(window_id: int, *, now: datetime | None = None) -> dict:
    """Permanently finalize an ended window (grace period over). Immutable after.

    Before the terminal transition, the Window report is computed from the
    persisted facts (ingesting any grace-period uploads first) and frozen into
    window_reports. Finalization then freezes the window itself; afterward the
    report is the frozen snapshot and cannot change.

    Clock guards: finalization is only valid while the window's grace period is
    open - `now` must be at/after the scheduled `end_time` (a window manually
    ended early stays in grace until end_time) and at/before the configured
    `finalization_deadline`. Pass `now` explicitly (business-zone aware) for
    deterministic tests; it defaults to the current wall clock.
    """
    from app.services.window_report import build_report, freeze_report

    window = db.get_window(window_id)
    if window is None:
        raise WindowStateError(f"Campaign Window #{window_id} not found.")
    instant = dates.as_business(now) if now is not None else dates.now_business()
    end_time = dates.parse_utc_iso(window["end_time"])
    deadline = dates.parse_utc_iso(window["finalization_deadline"])
    if instant < end_time:
        raise WindowStateError(
            f"Cannot finalize Campaign Window #{window_id}: now ({instant.isoformat()}) "
            f"is before the scheduled end time ({end_time.isoformat()}). Grace starts "
            "at end_time."
        )
    if instant > deadline:
        raise WindowStateError(
            f"Cannot finalize Campaign Window #{window_id}: the finalization deadline "
            f"({deadline.isoformat()}) has passed."
        )
    report = build_report(window_id, ingest=True)
    window = db.finalize_window_transition(window_id, dates.to_utc_iso(instant))
    report["window"].update(
        {"status": window["status"], "finalized_at": window["finalized_at"]}
    )
    freeze_report(window_id, report)
    return window


# ---------------------------------------------------------------------------
# Campaign Runs + snapshots
# ---------------------------------------------------------------------------

_SNAPSHOT_PREFIX_TO_DATASET = {
    "Login_": "Login",
    "Registrations_": "Registrations",
    "Sales_": "Sales",
    "Deposit_events_": "Deposit_events",
    "Withdrawal_events_": "Withdrawal_events",
    "KYC_": "KYC",
}


def _count_csv_data_rows(path: Path) -> int:
    """Approximate CSV row count: newline count minus a header line."""
    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as fh:
            first = fh.readline()
            rows = 1
            while fh.readline():
                rows += 1
        has_header = "," in first or "\t" in first
        return max(rows - (1 if has_header else 0), 0)
    except OSError:
        return 0


def capture_source_snapshot(window_id: int, now: datetime | None = None) -> list[dict]:
    """Metadata for every recognized source file present at an instant.

    This is the inspectable record of "which source files/data state a Run
    used". It snapshots file identity + size + mtime + approximate row count;
    it deliberately does not re-parse the CSV content (the eligibility engine
    consumes the data later and the snapshot must stay stable).
    """
    captured = None if now is None else dates.as_business(now)
    captured_iso = dates.to_utc_iso(captured) if captured else None
    entries = []
    folder = settings.DATA_FOLDER
    if folder.is_dir():
        for prefix, dataset in _SNAPSHOT_PREFIX_TO_DATASET.items():
            for path in sorted(folder.glob(f"{prefix}*.csv")):
                stat = path.stat()
                entries.append(
                    {
                        "dataset": dataset,
                        "filename": path.name,
                        "size_bytes": stat.st_size,
                        "mtime": dates.to_utc_iso(datetime.fromtimestamp(stat.st_mtime)),
                        "row_count": _count_csv_data_rows(path),
                    }
                )
    return entries or []


def start_run(window_id: int, note: str | None = None, *, now: datetime | None = None) -> dict:
    """Start a Campaign Run: snapshot source data, evaluate eligibility, freeze target.

    This is an atomic operation: the Run is created, its source snapshot captured,
    eligibility evaluated against that snapshot, and the complete target audience
    admitted with run_id set. The Run target is immutable after this point.
    """
    started_at = dates.as_business(now) if now is not None else dates.now_business()
    files = capture_source_snapshot(window_id, started_at)
    snapshot, run = db.create_run_with_snapshot(
        window_id,
        run_created_at=dates.to_utc_iso(started_at),
        started_at=dates.to_utc_iso(started_at),
        note=note,
        files_snapshot=files,
        snapshot_notes="Captured at run start from the data folder.",
    )

    # Build the complete Run target: evaluate eligibility against the frozen snapshot
    # and admit all eligible users with this run_id. This happens once at start.
    evaluation = eligibility.build_run_target(run["id"], now=started_at)

    return {"run": run, "snapshot": snapshot, "evaluation": evaluation}


def stop_run(run_id: int, stop_reason: str = "operator") -> dict:
    return db.stop_run_transition(run_id, dates.to_utc_iso(dates.now_business()), stop_reason)


def complete_run(run_id: int) -> dict:
    return db.complete_run_transition(run_id, dates.to_utc_iso(dates.now_business()))


def get_run(run_id: int) -> dict:
    run = db.get_run(run_id)
    if run is None:
        raise WindowStateError(f"Campaign Run #{run_id} not found.")
    return run


def list_runs(window_id: int) -> list[dict]:
    return db.list_runs(window_id)


def get_run_snapshot(run_id: int) -> dict:
    snapshot = db.get_run_snapshot(run_id)
    if snapshot is None:
        raise WindowStateError(f"Run snapshot not found for Campaign Run #{run_id}.")
    return snapshot


def has_active_run(window_id: int) -> bool:
    """Check if any Run in the window is currently 'running'."""
    runs = db.list_runs(window_id)
    return any(r["status"] == "running" for r in runs)


def any_window_has_active_run() -> bool:
    """Check if ANY window has a running Run (for global upload blocking)."""
    windows = db.list_windows()
    for w in windows:
        if has_active_run(w["id"]):
            return True
    return False


# ---------------------------------------------------------------------------
# Audience + assignment
# ---------------------------------------------------------------------------

def add_eligible_users(window_id: int, members: list[dict]) -> dict:
    """Add eligible users to the window's audience, assigning each one.

    `members`: list of dicts with
        user_id (str) - the player id (must be integers for deterministic
                        assignment),
        segment_id (str) - the selected segment that qualified the user,
        eligibility_state (optional dict) - relevant source/eligibility state,
        phone (str, optional) - raw phone; normalized + validity snapshotted,
        phone_valid (bool, optional) - explicit exclusion only: False forces
                        exclusion; True can never approve a phone the canonical
                        gate fails; None (absent) derives from the gate.
        run_id (int, optional) - the Campaign Run whose evaluation admitted
                        this member (that Run's frozen dispatch target).

    Only NEW users are assigned (existing members keep their assignment for the
    whole window). The eligible audience is system-derived and fixed once the
    window is active: admission is only allowed while the window is 'active'
    (the evaluation step); it is blocked during grace and after finalize.
    Members whose phone is unusable (failed normalization, whatever the source)
    are NOT admitted to the audience at all: they never count toward N, never
    receive an assignment, and can never become SMS recipients. The count of
    skipped invalid-phone members is reported back as `invalid_phone`. No member
    is ever stored with phone_normalized NULL.
    """
    window = db.get_window(window_id)
    if window is None:
        raise WindowStateError(f"Campaign Window #{window_id} not found.")
    if window["control_percentage"] is None:
        raise WindowConfigError(
            "No control percentage configured for this window. Configure the "
            "eligible count (N) or an operator control override before assigning."
        )
    if window["status"] != "active":
        raise WindowStateError(
            f"Cannot add audience members: Campaign Window #{window_id} is "
            f"{window['status']!r}; the eligible audience is fixed once the window "
            "is active."
        )

    campaign_pct = campaign_percentage_for(window["control_percentage"])
    method = window["assignment_method"]
    now_iso = dates.to_utc_iso(dates.now_business())
    records = []
    invalid_phone = 0
    for m in members:
        user_id = str(m["user_id"])
        if not user_id:
            raise WindowConfigError("Each member needs a non-empty user_id.")
        segment_id = m.get("segment_id")
        if not segment_id:
            raise WindowConfigError(f"Member {user_id!r} needs a segment_id.")
        if segment_id not in window["selected_segments"]:
            raise WindowConfigError(
                f"Member segment {segment_id!r} is not among the window's selected segments: "
                f"{window['selected_segments']}."
            )
        if method == "deterministic":
            is_campaign = deterministic_is_campaign(user_id, campaign_pct)
            config = {
                "rule": "(user_id % 100) < campaign_percentage",
                "campaign_percentage": campaign_pct,
                "control_percentage": window["control_percentage"],
            }
        else:
            is_campaign = random_is_campaign(user_id, campaign_pct, seed=window_id)
            config = {
                "seed": window_id,
                "campaign_percentage": campaign_pct,
                "control_percentage": window["control_percentage"],
            }

        phone_raw = str(m["phone"]) if m.get("phone") not in (None, "") else None
        normalized = gate_phone(phone_raw) if phone_raw else None
        # normalize-or-exclude: a member is only admitted when the canonical
        # gate normalized a real phone. An explicit phone_valid flag can only
        # force exclusion (False); True can never approve an unparseable or
        # missing phone; None (absent) derives from the gate.
        phone_valid = normalized is not None and m.get("phone_valid") is not False

        if not phone_valid:
            invalid_phone += 1
            continue

        records.append(
            {
                "user_id": user_id,
                "segment_id": segment_id,
                "assignment": "campaign" if is_campaign else "control",
                "assignment_method": method,
                "assignment_config": config,
                "entered_at": now_iso,
                "eligibility_state": m.get("eligibility_state") or {},
                "phone_raw": phone_raw,
                "phone_normalized": normalized,
                "phone_valid": phone_valid,
                "run_id": m.get("run_id"),
            }
        )

    result = db.insert_audience_members(window_id, records)
    result["campaign_percentage"] = campaign_pct
    result["invalid_phone"] = invalid_phone
    return result


def refresh_eligible_counts(window_id: int) -> dict | None:
    """Recompute N from the audience actually held, by segment.

    Used after evaluations so N tracks the admitted audience (e.g. a user is
    never counted before their phone is validated). The effective Control
    percentage is established once and is never recomputed here: growing N
    updates eligible_count but cannot move the percentage. When the audience
    is empty the current N/control is left untouched and None is returned (an
    empty N cannot be configured).
    """
    window = db.get_window(window_id)
    if window is None:
        raise WindowStateError(f"Campaign Window #{window_id} not found.")
    counts = db.count_audience_by_segment(window_id)
    total = sum(counts.values())
    if total == 0:
        return None
    segment_counts = {
        s: counts.get(s, 0) for s in window["selected_segments"]
    }
    return set_eligible_count(window_id, total, segment_counts)


def get_audience(window_id: int) -> list[dict]:
    return db.get_audience(window_id)


def get_audience_member(window_id: int, user_id: str) -> dict | None:
    return db.get_audience_member(window_id, user_id)


def count_audience(window_id: int) -> dict:
    return db.count_audience(window_id)


def list_campaign_recipients(window_id: int) -> list[dict]:
    """Campaign-assigned, SMS-capable audience members (Control excluded)."""
    return db.list_campaign_recipients(window_id)