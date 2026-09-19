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
    <20% -> 20, else the calculated value). The operator may override.
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


def list_windows(limit: int = 50) -> list[dict]:
    return db.list_windows(limit)


def get_window_detail(window_id: int) -> dict:
    """Window plus audience counts and its runs (for API/dashboard reads)."""
    window = db.get_window(window_id)
    if window is None:
        raise WindowStateError(f"Campaign Window #{window_id} not found.")
    return {
        **window,
        "audience": db.count_audience(window_id),
        "runs": db.list_runs(window_id),
    }


def set_eligible_count(
    window_id: int,
    eligible_count: int,
    segment_counts: dict[str, int] | None = None,
) -> dict:
    """Set N (the summed eligible audience across selected segments) and the
    effective control percentage.

    If `segment_counts` is provided, its values must sum to `eligible_count`
    (each selected segment contributes its eligible users to N).
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

    Allowed up to 50%. If N is already configured, the effective
    control_percentage is updated immediately; existing audience assignments
    are never rewritten (each member records the config that produced it).
    """
    if not (0 < percentage <= 50):
        raise WindowConfigError("Control override must be in the range (0, 50] percent.")
    window = db.set_control_override(window_id, percentage)
    if window is None:
        raise WindowStateError(f"Campaign Window #{window_id} not found.")
    if window["eligible_count"] is not None:
        window = set_eligible_count(window_id, window["eligible_count"])
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


def finalize_window(window_id: int) -> dict:
    """Permanently finalize an ended window (grace period over). Immutable after."""
    return db.finalize_window_transition(
        window_id, dates.to_utc_iso(dates.now_business())
    )


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


def start_run(window_id: int, note: str | None = None) -> dict:
    """Start a Campaign Run in an active window with a frozen data snapshot.

    The snapshot is captured at start; a source-file upload after the run
    starts cannot silently change the run's eligibility/audience input.
    """
    started_at = dates.now_business()
    files = capture_source_snapshot(window_id, started_at)
    snapshot, run = db.create_run_with_snapshot(
        window_id,
        run_created_at=dates.to_utc_iso(started_at),
        started_at=dates.to_utc_iso(started_at),
        note=note,
        files_snapshot=files,
        snapshot_notes="Captured at run start from the data folder.",
    )
    return {"run": run, "snapshot": snapshot}


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
        phone_valid (bool, optional) - explicit phone-validity override.

    Only NEW users are assigned (existing members keep their assignment for the
    whole window). Members whose phone is unusable (phone_valid False, whatever
    the source) are NOT admitted to the audience at all: they never count toward
    N, never receive an assignment, and can never become SMS recipients. The
    count of skipped invalid-phone members is reported back as `invalid_phone`.
    """
    window = db.get_window(window_id)
    if window is None:
        raise WindowStateError(f"Campaign Window #{window_id} not found.")
    if window["control_percentage"] is None:
        raise WindowConfigError(
            "No control percentage configured for this window. Configure the "
            "eligible count (N) or an operator control override before assigning."
        )
    if window["status"] == "finalized":
        raise WindowStateError(
            f"Cannot add audience members: Campaign Window #{window_id} is finalized."
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
        # An explicit phone_valid bool overrides the canonical gate; None (the
        # schema default / absence of the key) means "derive from the gate".
        phone_valid = (
            bool(m.get("phone_valid"))
            if m.get("phone_valid") is not None
            else normalized is not None
        )

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
                "phone_normalized": normalized if phone_valid else None,
                "phone_valid": phone_valid,
            }
        )

    result = db.insert_audience_members(window_id, records)
    result["campaign_percentage"] = campaign_pct
    result["invalid_phone"] = invalid_phone
    return result


def refresh_eligible_counts(window_id: int) -> dict | None:
    """Recompute N from the audience actually held, by segment.

    Used after evaluations so N/control track the admitted audience (e.g. a
    user is never counted before their phone is validated). Honors an operator
    control override (the override, when present, is kept as the effective
    control percentage). When the audience is empty the current N/control is
    left untouched and None is returned (an empty N cannot be configured).
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