"""Player/notification data module.

Owns the notified_users (notification cooldown state), pending_queue (users
deferred to a future cycle) and plays (persistent player activity) tables.
Consumers: the cycle processor, the file watcher, campaigns attribution and
plays ingestion.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from app.db.database import get_connection


def get_all_notified() -> dict[str, dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM notified_users").fetchall()
    conn.close()
    return {str(row["user_id"]): dict(row) for row in rows}


def upsert_notified_records(records: list[dict], cooldown_hours: int):
    """Batch insert-or-update notified users.

    records: list of {"user_id", "phone", "is_new"}
    """
    now = datetime.now(timezone.utc)
    next_available = now + timedelta(hours=cooldown_hours)

    conn = get_connection()
    for rec in records:
        if rec["is_new"]:
            conn.execute(
                """
                INSERT OR REPLACE INTO notified_users
                    (user_id, phone, notification_count, last_notified_at, next_available_at)
                VALUES (?, ?, 1, ?, ?)
                """,
                (
                    rec["user_id"],
                    rec["phone"],
                    now.isoformat(),
                    next_available.isoformat(),
                ),
            )
        else:
            conn.execute(
                """
                UPDATE notified_users
                SET notification_count = notification_count + 1,
                    last_notified_at = ?,
                    next_available_at = ?
                WHERE user_id = ?
                """,
                (now.isoformat(), next_available.isoformat(), rec["user_id"]),
            )
    conn.commit()
    conn.close()


def reset_user(user_id: str):
    conn = get_connection()
    conn.execute("DELETE FROM notified_users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_pending(kind: str) -> list[dict]:
    """Return queued users of a given kind awaiting a future cycle, oldest first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT user_id, phone, is_new, last_login_at FROM pending_queue WHERE kind = ? ORDER BY id",
        (kind,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def add_pending(records: list[dict], kind: str):
    """Queue users whose messages were deferred by the cycle cutoff.

    records: list of {"user_id", "phone", "is_new", "last_login_at"};
    kind: "welcome" or "inactive".
    """
    if not records:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.executemany(
        """
        INSERT INTO pending_queue (user_id, phone, is_new, kind, last_login_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (r["user_id"], r["phone"], 1 if r["is_new"] else 0, kind, r.get("last_login_at", ""), now)
            for r in records
        ],
    )
    conn.commit()
    conn.close()


def clear_pending(kind: str):
    conn = get_connection()
    conn.execute("DELETE FROM pending_queue WHERE kind = ?", (kind,))
    conn.commit()
    conn.close()


def has_pending(kind: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM pending_queue WHERE kind = ?", (kind,)
    ).fetchone()
    conn.close()
    return row["n"] > 0


# ---------------------------------------------------------------------------
# Persistent player activity (Phase 2)
# ---------------------------------------------------------------------------
#
# All campaign statistics build on these aggregations; nothing pulls per-play
# rows into the UI. Attribute-time strings are stored as UTC-aware ISO
# ("...+00:00"), lexicographically comparable with every other timestamp the
# app stores.

def insert_play_records(records: list[dict]) -> int:
    """Idempotently insert play records; returns rows actually inserted.

    Dedup is enforced by the plays.source_key UNIQUE constraint, so re-running
    the same (or a cumulative overlapping) Sales export never duplicates plays.
    records keys: user_id, played_at, game_name, amount, source_file, source_key.
    """
    if not records:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    cur = conn.executemany(
        """
        INSERT OR IGNORE INTO plays
            (user_id, played_at, game_name, amount, source_file, created_at, source_key)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r["user_id"],
                r["played_at"],
                r["game_name"],
                r["amount"],
                r["source_file"],
                now,
                r["source_key"],
            )
            for r in records
        ],
    )
    conn.commit()
    conn.close()
    return cur.rowcount


def get_user_first_play_after(user_id: str, after_iso: str, until_iso: str) -> str | None:
    """Earliest play strictly after `after_iso` up to `until_iso`, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT played_at FROM plays "
        "WHERE user_id = ? AND played_at > ? AND played_at <= ? "
        "ORDER BY played_at LIMIT 1",
        (user_id, after_iso, until_iso),
    ).fetchone()
    conn.close()
    return row["played_at"] if row else None


def get_earliest_qualifying_play(user_id: str, sent_at: str, window_end: str) -> str | None:
    """Earliest play in (sent_at, window_end] - the first qualifying play.

    Mirrors the historical attribution rule verbatim: min(plays where
    sent_at < played_at <= window_end). window_end is ended_at for a closed
    campaign or the current instant while it is active.
    """
    return get_user_first_play_after(user_id, sent_at, window_end)