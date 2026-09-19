"""SMS ledger data module.

Owns the sms_log table: dispatch recording, delivery-status updates, the
unsynced-status query used by reconciliation, and the SMS statistics
aggregations (get_stats_summary / get_cycle_stats). Consumers:
app.services.sms and the reporting surfaces in app.main.
"""
from __future__ import annotations

from app.db.database import get_connection


def log_sms(record: dict):
    """Insert a single SMS log record.

    record keys: message_id (optional), user_id, kind, phone, status,
                 cost, balance_after, cycle_id, sent_at.
    """
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO sms_log
            (message_id, user_id, kind, phone, status, cost, balance_after, cycle_id, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.get("message_id"),
            record["user_id"],
            record["kind"],
            record["phone"],
            record.get("status", "sent"),
            record.get("cost", 0),
            record.get("balance_after"),
            record.get("cycle_id"),
            record["sent_at"],
        ),
    )
    conn.commit()
    conn.close()


def log_sms_batch(records: list[dict]):
    """Bulk-insert SMS log records."""
    if not records:
        return
    conn = get_connection()
    conn.executemany(
        """
        INSERT INTO sms_log
            (message_id, user_id, kind, phone, status, cost, balance_after, cycle_id, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r.get("message_id"),
                r["user_id"],
                r["kind"],
                r["phone"],
                r.get("status", "sent"),
                r.get("cost", 0),
                r.get("balance_after"),
                r.get("cycle_id"),
                r["sent_at"],
            )
            for r in records
        ],
    )
    conn.commit()
    conn.close()


def update_sms_status(message_id: str, status: str, cost: float | None = None):
    """Update the delivery status (and optionally cost) for a sent SMS."""
    conn = get_connection()
    if cost is not None:
        conn.execute(
            "UPDATE sms_log SET status = ?, cost = ? WHERE message_id = ?",
            (status, cost, message_id),
        )
    else:
        conn.execute(
            "UPDATE sms_log SET status = ? WHERE message_id = ?",
            (status, message_id),
        )
    conn.commit()
    conn.close()


def get_last_accepted_sms(user_id: str) -> str | None:
    """Most recent accepted/sent SMS for a user, across all kinds.

    Cooldown is global per user and spans Campaign Runs/Windows. Only SMS
    actually accepted/sent by the provider count (status 'sent' or 'delivered');
    failed/dnd/rejected/deferred attempts never start a cooldown. Returns the
    sent_at label of the most recent qualifying send, or None.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT MAX(sent_at) AS last_sent FROM sms_log "
        "WHERE user_id = ? AND status IN ('sent', 'delivered')",
        (user_id,),
    ).fetchone()
    conn.close()
    return str(row["last_sent"]) if row and row["last_sent"] is not None else None


def get_unsynced_sms(limit: int = 100) -> list[dict]:
    """Return sent SMS records that have a message_id but haven't reached a
    terminal delivery status yet."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, message_id, status FROM sms_log
        WHERE message_id IS NOT NULL
          AND status NOT IN ('delivered', 'dnd', 'rejected', 'expired', 'failed')
        ORDER BY id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _sms_kind_clauses(kind: str | None, kinds: set[str] | None) -> tuple[list[str], list]:
    """WHERE clauses for sms_log kind scoping.

    `kinds` (a set) is used for active reporting scope (e.g. enabled features
    plus manual); `kind` (a single value) is used for explicit feature-specific
    or historical inspection. When both are given both apply; callers normally
    pass exactly one.
    """
    clauses: list[str] = []
    params: list = []
    if kinds:
        placeholders = ",".join("?" * len(kinds))
        clauses.append(f"kind IN ({placeholders})")
        params.extend(sorted(kinds))
    elif kind:
        clauses.append("kind = ?")
        params.append(kind)
    return clauses, params


def get_sms_logs(
    kind: str | None = None,
    kinds: set[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    """Query SMS logs with optional kind/kinds and date-range filters."""
    clauses, params = _sms_kind_clauses(kind, kinds)
    if since:
        clauses.append("sent_at >= ?")
        params.append(since)
    if until:
        clauses.append("sent_at <= ?")
        params.append(until)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM sms_log{where} ORDER BY id"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)
    conn = get_connection()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def count_sms_logs(
    kind: str | None = None,
    kinds: set[str] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> int:
    """Count SMS log rows matching the same filters as get_sms_logs."""
    clauses, params = _sms_kind_clauses(kind, kinds)
    if since:
        clauses.append("sent_at >= ?")
        params.append(since)
    if until:
        clauses.append("sent_at <= ?")
        params.append(until)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = get_connection()
    row = conn.execute(f"SELECT COUNT(*) AS n FROM sms_log{where}", params).fetchone()
    conn.close()
    return row["n"]


def get_stats_summary(
    kind: str | None = None,
    kinds: set[str] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Return aggregated statistics for the sms_log table."""
    clauses, params = _sms_kind_clauses(kind, kinds)
    if since:
        clauses.append("sent_at >= ?")
        params.append(since)
    if until:
        clauses.append("sent_at <= ?")
        params.append(until)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = get_connection()

    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status IN ('sent', 'delivered') THEN 1 ELSE 0 END) AS sent,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) AS delivered,
            SUM(CASE WHEN status = 'dnd' THEN 1 ELSE 0 END) AS dnd,
            SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
            SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END) AS expired,
            SUM(CASE WHEN status = 'deferred' THEN 1 ELSE 0 END) AS deferred,
            COALESCE(SUM(cost), 0) AS total_cost,
            COALESCE(AVG(CASE WHEN cost > 0 THEN cost END), 0) AS avg_cost
        FROM sms_log{where}
        """,
        params,
    ).fetchone()

    conn.close()
    return dict(row) if row else {}


def get_cycle_stats(
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    """Return per-cycle aggregated statistics."""
    clauses: list[str] = ["cycle_id IS NOT NULL"]
    params: list = []
    if since:
        clauses.append("sent_at >= ?")
        params.append(since)
    if until:
        clauses.append("sent_at <= ?")
        params.append(until)

    where = " WHERE " + " AND ".join(clauses)
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT
            cycle_id,
            COUNT(*) AS total,
            SUM(CASE WHEN status IN ('sent', 'delivered') THEN 1 ELSE 0 END) AS sent,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) AS delivered,
            SUM(CASE WHEN status = 'deferred' THEN 1 ELSE 0 END) AS deferred,
            COALESCE(SUM(cost), 0) AS total_cost,
            MIN(sent_at) AS started_at,
            MAX(sent_at) AS ended_at
        FROM sms_log{where}
        GROUP BY cycle_id
        ORDER BY MIN(sent_at) DESC
        """,
        params,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]