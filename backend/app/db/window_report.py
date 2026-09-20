"""Campaign Window report persistence (v2.0.0 reporting).

Owns the window_reports table (the frozen snapshot written once at
finalization) and the fact-scoped reads the report service needs. All reads
are scoped to a window's audience rows, so a report never considers a play,
deposit or login by a non-member of the window's final campaignable audience.

Timestamp frame note: plays/deposits/logins store the "+00:00 relabelled
wall-clock" label (see app.core.dates.read_source_fact / to_source_fact), so
period bounds are passed in that SAME frame (Lagos wall-clock digits labeled
as UTC). sms_log.sent_at is a true-UTC ISO instant, so run-send selection does
not range-filter here; the service checks the window period after decoding.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.db.database import get_connection

REPORT_SCHEMA_VERSION = 1

#: Accepted provider statuses for a Welcome intervention (mirrors sms.py).
ACCEPTED_SMS_STATUSES = ("sent", "delivered")


def get_frozen_report(window_id: int) -> dict | None:
    """The frozen report snapshot for a finalized window, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM window_reports WHERE window_id = ? ORDER BY created_at DESC LIMIT 1",
        (window_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    report = json.loads(row["report"])
    report["schema_version"] = row["schema_version"]
    report["finalized_at"] = row["finalized_at"]
    return report


def save_frozen_report(window_id: int, report: dict) -> None:
    """Persist the frozen report for a window (idempotent upsert)."""
    report_json = json.dumps(report, sort_keys=True)
    now = datetime.now(timezone.utc).isoformat()
    finalized_at = report.get("finalized_at") or now
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO window_reports
            (window_id, report, schema_version, finalized_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (window_id) DO UPDATE SET
            report = excluded.report,
            schema_version = excluded.schema_version,
            finalized_at = excluded.finalized_at,
            updated_at = excluded.updated_at
        """,
        (
            window_id,
            report_json,
            report.get("schema_version", REPORT_SCHEMA_VERSION),
            finalized_at,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fact-scoped reads (relabelled fact frame)
# ---------------------------------------------------------------------------

def select_window_plays(window_id: int, low: str, high: str) -> list[dict]:
    """Plays of the window's audience inside [low, high] (fact frame).

    Returns user_id, played_at, game_name, amount. The join scopes plays to the
    window's own audience; the range uses the relabelled Lagos fact frame, so a
    play at Lagos Saturday 23:59:59 is inside a window that ends that instant.
    phone_valid users only: invalid phones are no part of the campaignable
    audience (the report joins the same way for all three fact tables).
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT p.user_id, p.played_at, p.game_name, p.amount
        FROM plays p
        JOIN window_audiences a
          ON a.window_id = ? AND a.user_id = p.user_id AND a.phone_valid = 1
        WHERE p.played_at >= ? AND p.played_at <= ?
        ORDER BY p.played_at, p.id
        """,
        (window_id, low, high),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def select_window_deposits(window_id: int, low: str, high: str) -> list[dict]:
    """Deposits of the window's audience inside [low, high] (fact frame)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT d.user_id, d.deposited_at
        FROM deposits d
        JOIN window_audiences a
          ON a.window_id = ? AND a.user_id = d.user_id AND a.phone_valid = 1
        WHERE d.deposited_at >= ? AND d.deposited_at <= ?
        ORDER BY d.deposited_at, d.id
        """,
        (window_id, low, high),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def select_window_logins(window_id: int, low: str, high: str) -> list[dict]:
    """Logins of the window's audience inside [low, high] (fact frame)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT l.user_id, l.logged_at
        FROM logins l
        JOIN window_audiences a
          ON a.window_id = ? AND a.user_id = l.user_id AND a.phone_valid = 1
        WHERE l.logged_at >= ? AND l.logged_at <= ?
        ORDER BY l.logged_at, l.id
        """,
        (window_id, low, high),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def select_run_welcome_sms(run_ids: list[int]) -> list[dict]:
    """Welcome SMS ledger rows for the window's Runs (cycle_id run:{id})."""
    if not run_ids:
        return []
    placeholders = ",".join("?" * len(run_ids))
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT user_id, status, cycle_id, sent_at
        FROM sms_log
        WHERE kind = 'welcome' AND cycle_id IN ({placeholders})
        ORDER BY id
        """,
        [f"run:{rid}" for rid in run_ids],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]