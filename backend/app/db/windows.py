"""Campaign Window / Campaign Run / audience persistence (v2.0.0 foundation).

Owns the campaign_windows, campaign_runs, run_snapshots and window_audiences
tables plus every state transition these entities can take. All mutating
operations run inside a single write transaction (BEGIN IMMEDIATE) and re-read
the window row under that lock so races around run-start / window-end /
finalize / audience-insert are serialized by SQLite's single writer.

Immutability boundary: the effective Control percentage is fixed for a
window from the moment it is established (an operator override, or the
formula suggestion when the window's audience/config is first set). N may
grow but the split is never recomputed or changed again, even while the
window is still active. The window's operator-defined configuration (control
split, eligible count, end time) and its eligible audience are otherwise
established while the window is 'active' and become immutable the moment it
ends. The grace period never permits configuration or audience changes
(facts/metrics may still be ingested for the report), and finalization
additionally freezes the report. These guards live here in the persistence
layer - the boundary holds even if a caller bypasses the service layer. This
is the enforced freeze, not a UI-level hiding.

Persistence convention: timestamps are UTC-aware ISO strings as produced by
app.core.dates.to_utc_iso; JSON columns (segments, assignment config,
eligibility state, segment counts, snapshot file lists) are serialized with
json.dumps.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.core.dates import now_business, to_utc_iso
from app.db.database import get_connection


class WindowStateError(Exception):
    """A transition is not allowed in the window's current state (lifecycle or freeze)."""


class WindowConfigError(Exception):
    """Invalid configuration for a window (deadline caps, control %, N, segments)."""


IMMUTABLE_STATES = ("finalized",)


def _now() -> str:
    return to_utc_iso(now_business())


def _tx():
    conn = get_connection()
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _row_to_dict(row) -> dict:
    return dict(row) if row is not None else None


def _ensure_active(window: dict, action: str):
    """Configuration/audience mutators are valid only while a window is active.

    The operator-defined configuration (control split, eligible count, end
    time) and the eligible audience are fixed for the window once established:
    no operator or system change is allowed once the window has ended (entered
    grace) or been finalized.
    """
    if window["status"] != "active":
        raise WindowStateError(
            f"Cannot {action}: Campaign Window #{window['id']} is {window['status']!r}; "
            "the configuration and audience of a window are fixed once it is active."
        )


# ---------------------------------------------------------------------------
# Campaign Windows
# ---------------------------------------------------------------------------

def create_window(record: dict) -> dict:
    """Insert a new 'active' Campaign Window. Returns the persisted row.

    Single-active guard: only one Campaign Window may run at a time, so the
    insert is rejected (WindowStateError) while any window is still 'active'.
    The guard runs inside the write transaction, so two concurrent creates
    cannot both win (SQLite's single writer serializes BEGIN IMMEDIATE).
    """
    conn = _tx()
    try:
        existing = conn.execute(
            "SELECT id FROM campaign_windows WHERE status = 'active' "
            "ORDER BY start_time DESC LIMIT 1"
        ).fetchone()
        if existing:
            raise WindowStateError(
                "Only one Campaign Window can run at a time: Campaign Window "
                f"#{existing['id']} is already active. End or finalize it before "
                "creating another window."
            )
        cursor = conn.execute(
            """
            INSERT INTO campaign_windows (
                name, status, start_time, end_time, finalization_deadline,
                business_timezone, selected_segments, assignment_method,
                suggested_control_percentage, control_percentage, control_override,
                eligible_count, segment_eligible_counts, created_at, updated_at
            ) VALUES (?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("name"),
                record["start_time"],
                record["end_time"],
                record["finalization_deadline"],
                record.get("business_timezone", "Africa/Lagos"),
                json.dumps(record.get("selected_segments", [])),
                record.get("assignment_method", "deterministic"),
                record.get("suggested_control_percentage"),
                record.get("control_percentage"),
                record.get("control_override"),
                record.get("eligible_count"),
                json.dumps(record.get("segment_eligible_counts", {})),
                record.get("created_at", _now()),
                record.get("updated_at", _now()),
            ),
        )
        window = _row_to_dict(
            conn.execute(
                "SELECT * FROM campaign_windows WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        )
        conn.commit()
        return _decode_window(window)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_window(window_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM campaign_windows WHERE id = ?", (window_id,)
    ).fetchone()
    conn.close()
    return _decode_window(_row_to_dict(row))


def get_active_window() -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM campaign_windows WHERE status = 'active' "
        "ORDER BY start_time DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return _decode_window(_row_to_dict(row))


def list_windows(limit: int = 50) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM campaign_windows ORDER BY start_time DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [_decode_window(dict(row)) for row in rows]


def _decode_window(window: dict | None) -> dict | None:
    """Inflate JSON columns of a window/persistence row."""
    if window is None:
        return None
    for key in ("selected_segments", "segment_eligible_counts"):
        if key in window and isinstance(window[key], str):
            try:
                window[key] = json.loads(window[key])
            except (TypeError, ValueError):
                window[key] = [] if key == "selected_segments" else {}
    if "control_locked" in window:
        window["control_locked"] = bool(window["control_locked"])
    return window


def update_window_control(
    window_id: int,
    *,
    eligible_count: int,
    suggested_control_percentage: float,
    control_percentage: float,
    segment_eligible_counts: dict[str, int] | None = None,
) -> dict:
    """Persist the window's eligible count and effective control percentage.

    Runs inside a transaction with an immutability guard. Setting the control
    percentage is a Window-level configuration step: it happens before (or as)
    audience members are assigned.
    """
    conn = _tx()
    try:
        window = _row_to_dict(
            conn.execute(
                "SELECT * FROM campaign_windows WHERE id = ?", (window_id,)
            ).fetchone()
        )
        if window is None:
            raise WindowStateError(f"Campaign Window #{window_id} not found.")
        _ensure_active(window, "change the control configuration")
        # The effective Control percentage (and the formula suggestion that
        # produced it) are established once and then fixed for the whole
        # window: only N and the per-segment counts may grow. The freeze holds
        # even if a caller passes new percentages - a later evaluation or
        # upload can never move the split.
        if window["control_percentage"] is not None:
            suggested_control_percentage = window["suggested_control_percentage"]
            control_percentage = window["control_percentage"]
        counts = (
            json.dumps(segment_eligible_counts)
            if segment_eligible_counts is not None
            else window["segment_eligible_counts"]
        )
        conn.execute(
            """
            UPDATE campaign_windows
            SET eligible_count = ?, suggested_control_percentage = ?,
                control_percentage = ?, segment_eligible_counts = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                eligible_count,
                suggested_control_percentage,
                control_percentage,
                counts,
                _now(),
                window_id,
            ),
        )
        conn.commit()
        window = _row_to_dict(
            conn.execute(
                "SELECT * FROM campaign_windows WHERE id = ?", (window_id,)
            ).fetchone()
        )
        return _decode_window(window)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_control_override(window_id: int, percentage: float) -> dict:
    """Persist an operator override for the effective control percentage.

    Valid only BEFORE the window's first Campaign Run starts (an override
    provided when the window is created, or set via this endpoint before the
    first Run). Starting the first Run locks the configuration for the whole
    window: a later override raises WindowStateError even while the window is
    active, and this holds even when that first Run admits no eligible users.
    Before the lock, the override immediately becomes the effective
    `control_percentage` when N is already known; otherwise it stays pending
    until N (or the first Run) establishes the effective value. Existing
    audience assignments are never rewritten.
    """
    conn = _tx()
    try:
        window = _row_to_dict(
            conn.execute(
                "SELECT * FROM campaign_windows WHERE id = ?", (window_id,)
            ).fetchone()
        )
        if window is None:
            raise WindowStateError(f"Campaign Window #{window_id} not found.")
        _ensure_active(window, "change the control override")
        if window["control_locked"]:
            raise WindowStateError(
                f"Cannot change the control override: Campaign Window #{window_id} "
                "has already started a Campaign Run; the Control percentage is "
                "fixed for the entire window once the first Run starts."
            )
        effective = (
            percentage
            if window["eligible_count"] is not None
            else window["control_percentage"]
        )
        conn.execute(
            "UPDATE campaign_windows SET control_override = ?, control_percentage = ?, "
            "updated_at = ? WHERE id = ?",
            (percentage, effective, _now(), window_id),
        )
        conn.commit()
        window = _row_to_dict(
            conn.execute(
                "SELECT * FROM campaign_windows WHERE id = ?", (window_id,)
            ).fetchone()
        )
        return _decode_window(window)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_window_end(window_id: int, end_time: str) -> dict:
    """Extend (or otherwise set) the configured end time of an active window."""
    conn = _tx()
    try:
        window = _row_to_dict(
            conn.execute(
                "SELECT * FROM campaign_windows WHERE id = ?", (window_id,)
            ).fetchone()
        )
        if window is None:
            raise WindowStateError(f"Campaign Window #{window_id} not found.")
        _ensure_active(window, "change the window end time")
        conn.execute(
            "UPDATE campaign_windows SET end_time = ?, updated_at = ? WHERE id = ?",
            (end_time, _now(), window_id),
        )
        conn.commit()
        window = _row_to_dict(
            conn.execute(
                "SELECT * FROM campaign_windows WHERE id = ?", (window_id,)
            ).fetchone()
        )
        return _decode_window(window)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def end_window_transition(window_id: int, ended_at: str) -> tuple[dict, list[dict]]:
    """End the window and close every active Run within it (atomic).

    Requires the window to be 'active'. Active runs move to 'stopped' with
    stop_reason 'window_ended'. Returns (window, closed_runs).
    """
    conn = _tx()
    try:
        window = _row_to_dict(
            conn.execute(
                "SELECT * FROM campaign_windows WHERE id = ?", (window_id,)
            ).fetchone()
        )
        if window is None:
            raise WindowStateError(f"Campaign Window #{window_id} not found.")
        if window["status"] != "active":
            raise WindowStateError(
                f"Cannot end Campaign Window #{window_id}: status is {window['status']!r}, "
                "only 'active' windows can be ended."
            )
        conn.execute(
            "UPDATE campaign_windows SET status = 'ended', ended_at = ?, updated_at = ? "
            "WHERE id = ?",
            (ended_at, _now(), window_id),
        )
        conn.execute(
            "UPDATE campaign_runs SET status = 'stopped', ended_at = ?, "
            "stop_reason = 'window_ended' WHERE window_id = ? AND status = 'running'",
            (ended_at, window_id),
        )
        runs = conn.execute(
            "SELECT * FROM campaign_runs WHERE window_id = ? AND status = 'stopped' "
            "AND stop_reason = 'window_ended' ORDER BY id",
            (window_id,),
        ).fetchall()
        conn.commit()
        window = _row_to_dict(
            conn.execute(
                "SELECT * FROM campaign_windows WHERE id = ?", (window_id,)
            ).fetchone()
        )
        return _decode_window(window), [dict(r) for r in runs]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finalize_window_transition(window_id: int, finalized_at: str) -> dict:
    """Permanently finalize an ended window. Requires status 'ended'.

    Finalization is the terminal transition: afterward every state mutator in
    this module raises WindowStateError, freezing audience, assignment, runs
    and results.
    """
    conn = _tx()
    try:
        window = _row_to_dict(
            conn.execute(
                "SELECT * FROM campaign_windows WHERE id = ?", (window_id,)
            ).fetchone()
        )
        if window is None:
            raise WindowStateError(f"Campaign Window #{window_id} not found.")
        if window["status"] != "ended":
            raise WindowStateError(
                f"Cannot finalize Campaign Window #{window_id}: status is {window['status']!r}, "
                "a window must be ended (grace period) before it can be finalized."
            )
        conn.execute(
            "UPDATE campaign_windows SET status = 'finalized', finalized_at = ?, "
            "updated_at = ? WHERE id = ?",
            (finalized_at, _now(), window_id),
        )
        conn.commit()
        window = _row_to_dict(
            conn.execute(
                "SELECT * FROM campaign_windows WHERE id = ?", (window_id,)
            ).fetchone()
        )
        return _decode_window(window)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Campaign Runs
# ---------------------------------------------------------------------------

def create_run_with_snapshot(
    window_id: int,
    run_created_at: str,
    started_at: str,
    note: str | None,
    files_snapshot: list[dict],
    snapshot_notes: str | None,
) -> tuple[dict, dict]:
    """Start a Run and capture its source-data snapshot in one transaction.

    The run is created 'running' only when the window is still active (a Run
    must never start in an ended or finalized window). The snapshot row is
    inserted first so the run references it. Both are atomic.
    """
    conn = _tx()
    try:
        window = _row_to_dict(
            conn.execute(
                "SELECT * FROM campaign_windows WHERE id = ?", (window_id,)
            ).fetchone()
        )
        if window is None:
            raise WindowStateError(f"Campaign Window #{window_id} not found.")
        if window["status"] != "active":
            raise WindowStateError(
                f"Cannot start a Run: Campaign Window #{window_id} is {window['status']!r}, "
                "Runs may only start in an active window."
            )
        snapshot_cursor = conn.execute(
            """
            INSERT INTO run_snapshots (
                window_id, captured_at, files, notes, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                window_id,
                started_at,
                json.dumps(files_snapshot, sort_keys=True),
                snapshot_notes,
                run_created_at,
            ),
        )
        snapshot_id = snapshot_cursor.lastrowid
        run_cursor = conn.execute(
            """
            INSERT INTO campaign_runs (
                window_id, status, started_at, note, snapshot_id, created_at
            ) VALUES (?, 'running', ?, ?, ?, ?)
            """,
            (window_id, started_at, note, snapshot_id, run_created_at),
        )
        run_id = run_cursor.lastrowid
        conn.execute(
            "UPDATE run_snapshots SET run_id = ? WHERE id = ?",
            (run_id, snapshot_id),
        )
        # Starting a Run locks the Control percentage configuration for the
        # entire window, regardless of how many eligible users the Run admits
        # (a Run with zero eligible users still locks it).
        conn.execute(
            "UPDATE campaign_windows SET control_locked = 1, updated_at = ? WHERE id = ?",
            (run_created_at, window_id),
        )
        conn.commit()
        run = dict(
            conn.execute(
                "SELECT * FROM campaign_runs WHERE id = ?", (run_cursor.lastrowid,)
            ).fetchone()
        )
        snapshot = dict(
            conn.execute(
                "SELECT * FROM run_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
        )
        return _decode_snapshot(snapshot), run
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_run(run_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM campaign_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    return _row_to_dict(row)


def list_runs(window_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM campaign_runs WHERE window_id = ? ORDER BY started_at, id",
        (window_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def stop_run_transition(run_id: int, ended_at: str, stop_reason: str) -> dict:
    """Stop a running Run. Only 'running' runs can be stopped."""
    conn = _tx()
    try:
        run = _row_to_dict(
            conn.execute("SELECT * FROM campaign_runs WHERE id = ?", (run_id,)).fetchone()
        )
        if run is None:
            raise WindowStateError(f"Campaign Run #{run_id} not found.")
        if run["status"] != "running":
            raise WindowStateError(
                f"Cannot stop Campaign Run #{run_id}: status is {run['status']!r}."
            )
        conn.execute(
            "UPDATE campaign_runs SET status = 'stopped', ended_at = ?, "
            "stop_reason = ? WHERE id = ?",
            (ended_at, stop_reason, run_id),
        )
        conn.commit()
        return dict(
            conn.execute(
                "SELECT * FROM campaign_runs WHERE id = ?", (run_id,)
            ).fetchone()
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_run_transition(run_id: int, ended_at: str) -> dict:
    """Close a running Run because its processing completed naturally."""
    conn = _tx()
    try:
        run = _row_to_dict(
            conn.execute("SELECT * FROM campaign_runs WHERE id = ?", (run_id,)).fetchone()
        )
        if run is None:
            raise WindowStateError(f"Campaign Run #{run_id} not found.")
        if run["status"] != "running":
            raise WindowStateError(
                f"Cannot complete Campaign Run #{run_id}: status is {run['status']!r}."
            )
        conn.execute(
            "UPDATE campaign_runs SET status = 'completed', ended_at = ?, "
            "stop_reason = NULL WHERE id = ?",
            (ended_at, run_id),
        )
        conn.commit()
        return dict(
            conn.execute(
                "SELECT * FROM campaign_runs WHERE id = ?", (run_id,)
            ).fetchone()
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Run snapshots
# ---------------------------------------------------------------------------

def _decode_snapshot(snapshot: dict) -> dict:
    if snapshot is None:
        return None
    if isinstance(snapshot.get("files"), str):
        snapshot["files"] = json.loads(snapshot["files"])
    return snapshot


def get_snapshot(snapshot_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM run_snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone()
    conn.close()
    return _decode_snapshot(_row_to_dict(row))


def get_run_snapshot(run_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM run_snapshots WHERE run_id = ?", (run_id,)
    ).fetchone()
    conn.close()
    return _decode_snapshot(_row_to_dict(row))


# ---------------------------------------------------------------------------
# Window audience (membership + Campaign/Control assignment)
# ---------------------------------------------------------------------------

def insert_audience_members(window_id: int, members: list[dict]) -> dict:
    """Add audience members to an active window, ignoring users already present.

    The eligible audience is system-derived and fixed for the window: inserts
    are allowed only while the window is 'active' (the evaluation step, and
    only that step, admits new users). Blocked during grace (ended) and after
    finalize. Existing members are never rewritten - their assignment persists
    for the whole window (a later upload / eligibility change does not reassign
    them). Returns per-batch counts.
    """
    if not members:
        return {"added": 0, "existing": 0, "campaign": 0, "control": 0}
    conn = _tx()
    try:
        window = _row_to_dict(
            conn.execute(
                "SELECT * FROM campaign_windows WHERE id = ?", (window_id,)
            ).fetchone()
        )
        if window is None:
            raise WindowStateError(f"Campaign Window #{window_id} not found.")
        _ensure_active(window, "add audience members")
        known = {
            str(r["user_id"])
            for r in conn.execute(
                "SELECT user_id FROM window_audiences WHERE window_id = ?",
                (window_id,),
            ).fetchall()
        }
        inserted = existing = campaign = control = 0
        for m in members:
            if m["user_id"] in known:
                existing += 1
                continue
            conn.execute(
                """
                INSERT INTO window_audiences (
                    window_id, user_id, segment_id, assignment, assignment_method,
                    assignment_config, entered_at, eligibility_state, phone_raw,
                    phone_normalized, phone_valid, run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    window_id,
                    m["user_id"],
                    m["segment_id"],
                    m["assignment"],
                    m["assignment_method"],
                    json.dumps(m.get("assignment_config", {})),
                    m["entered_at"],
                    json.dumps(m.get("eligibility_state", {})),
                    m.get("phone_raw"),
                    m.get("phone_normalized"),
                    1 if m.get("phone_valid") else 0,
                    m.get("run_id"),
                    _now(),
                ),
            )
            inserted += 1
            if m["assignment"] == "campaign":
                campaign += 1
            else:
                control += 1
            known.add(m["user_id"])
        conn.commit()
        return {"added": inserted, "existing": existing, "campaign": campaign, "control": control}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_audience(window_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM window_audiences WHERE window_id = ? ORDER BY entered_at, id",
        (window_id,),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get("assignment_config"), str):
            d["assignment_config"] = json.loads(d["assignment_config"])
        if isinstance(d.get("eligibility_state"), str):
            d["eligibility_state"] = json.loads(d["eligibility_state"])
        out.append(d)
    return out


def get_audience_member(window_id: int, user_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM window_audiences WHERE window_id = ? AND user_id = ?",
        (window_id, user_id),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    if isinstance(d.get("assignment_config"), str):
        d["assignment_config"] = json.loads(d["assignment_config"])
    if isinstance(d.get("eligibility_state"), str):
        d["eligibility_state"] = json.loads(d["eligibility_state"])
    return d


def count_audience(window_id: int) -> dict:
    conn = get_connection()
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM window_audiences WHERE window_id = ?",
        (window_id,),
    ).fetchone()["n"]
    by_assignment = {
        str(r["assignment"]): r["n"]
        for r in conn.execute(
            "SELECT assignment, COUNT(*) AS n FROM window_audiences "
            "WHERE window_id = ? GROUP BY assignment",
            (window_id,),
        ).fetchall()
    }
    conn.close()
    return {
        "total": total,
        "campaign": by_assignment.get("campaign", 0),
        "control": by_assignment.get("control", 0),
    }


def count_audience_by_segment(window_id: int) -> dict[str, int]:
    """Eligible-audience size per segment (mutually exclusive memberships).

    Because invalid-phone users are never admitted to window_audiences, these
    counts already exclude them; summing them yields N for the window.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT segment_id, COUNT(*) AS n FROM window_audiences "
        "WHERE window_id = ? GROUP BY segment_id",
        (window_id,),
    ).fetchall()
    conn.close()
    return {str(r["segment_id"]): r["n"] for r in rows}


def list_campaign_recipients(window_id: int) -> list[dict]:
    """Campaign-assigned, SMS-capable audience members of a window.

    Control users are never Campaign SMS recipients; users with unusable phone
    numbers (phone_valid = 0) are kept in the audience for reporting but are
    not campaignable SMS recipients either.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT user_id, phone_raw, phone_normalized
        FROM window_audiences
        WHERE window_id = ? AND assignment = 'campaign' AND phone_valid = 1
        ORDER BY entered_at, id
        """,
        (window_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_run_target(window_id: int, run_id: int) -> list[dict]:
    """A Run's frozen SMS-dispatch target: the members IT admitted.

    A member is part of a Run's target exactly when that Run's evaluation
    admitted them (run_id on the audience row, set once at admission; later
    runs never re-admit an existing member, so the target is fixed). Only
    Campaign-assigned members with a usable phone are dispatchable - Control
    users receive nothing and invalid phones never reached the audience. The
    eligibility_state JSON (first name, qualifying login, profile snapshot)
    is decoded for message building.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT user_id, phone_raw, phone_normalized, eligibility_state
        FROM window_audiences
        WHERE window_id = ? AND run_id = ? AND assignment = 'campaign' AND phone_valid = 1
        ORDER BY entered_at, id
        """,
        (window_id, run_id),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        d = dict(row)
        if isinstance(d.get("eligibility_state"), str):
            d["eligibility_state"] = json.loads(d["eligibility_state"])
        out.append(d)
    return out