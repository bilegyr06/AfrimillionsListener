import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.config import settings


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notified_users (
            user_id TEXT PRIMARY KEY,
            phone TEXT NOT NULL,
            notification_count INTEGER DEFAULT 1,
            last_notified_at TEXT NOT NULL,
            next_available_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            phone TEXT NOT NULL,
            is_new INTEGER NOT NULL DEFAULT 1,
            kind TEXT NOT NULL DEFAULT 'inactive',
            last_login_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS welcome_sent (
            user_id TEXT PRIMARY KEY,
            last_login_at TEXT NOT NULL,
            sent_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS welcome_analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sms_log_id INTEGER NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            cycle_id TEXT,
            window_hours REAL NOT NULL,
            first_play_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sms_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            phone TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'sent',
            cost REAL DEFAULT 0,
            balance_after REAL,
            cycle_id TEXT,
            sent_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wallet_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            balance REAL NOT NULL,
            currency TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
        """
    )
    _ensure_column(conn, "pending_queue", "kind", "TEXT NOT NULL DEFAULT 'inactive'")
    _ensure_column(conn, "pending_queue", "last_login_at", "TEXT NOT NULL DEFAULT ''")
    conn.commit()
    conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def get_all_notified() -> dict[str, dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM notified_users").fetchall()
    conn.close()
    return {str(row["user_id"]): dict(row) for row in rows}


def get_notified_user(user_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM notified_users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


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


def insert_notified_user(user_id: str, phone: str, cooldown_hours: int):
    now = datetime.now(timezone.utc)
    next_available = now + timedelta(hours=cooldown_hours)
    conn = get_connection()
    conn.execute(
        """
        INSERT OR REPLACE INTO notified_users
            (user_id, phone, notification_count, last_notified_at, next_available_at)
        VALUES (?, ?, 1, ?, ?)
        """,
        (user_id, phone, now.isoformat(), next_available.isoformat()),
    )
    conn.commit()
    conn.close()


def update_notified_user(user_id: str, cooldown_hours: int):
    now = datetime.now(timezone.utc)
    next_available = now + timedelta(hours=cooldown_hours)
    conn = get_connection()
    conn.execute(
        """
        UPDATE notified_users
        SET notification_count = notification_count + 1,
            last_notified_at = ?,
            next_available_at = ?
        WHERE user_id = ?
        """,
        (now.isoformat(), next_available.isoformat(), user_id),
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


def get_welcome_sent() -> dict[str, str]:
    """Map of user_id -> last_login_at for users already sent a welcome SMS."""
    conn = get_connection()
    rows = conn.execute("SELECT user_id, last_login_at FROM welcome_sent").fetchall()
    conn.close()
    return {str(row["user_id"]): row["last_login_at"] for row in rows}


def upsert_welcome_sent(records: list[dict]):
    """Mark users as welcomed for a given login event.

    records: list of {"user_id", "last_login_at"}
    """
    if not records:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.executemany(
        """
        INSERT OR REPLACE INTO welcome_sent (user_id, last_login_at, sent_at)
        VALUES (?, ?, ?)
        """,
        [(r["user_id"], r["last_login_at"], now) for r in records],
    )
    conn.commit()
    conn.close()


def get_welcome_sms_state() -> dict[str, dict]:
    """Welcome campaign state per user, derived from the SMS send log.

    Returns {user_id: {"count": n, "last_sent_at": iso}} for welcome messages
    that were actually handed off (status 'sent' or 'delivered'). Cooldown and
    the notification cap are computed from this rather than stored, so the
    source of truth is the actual send history.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT user_id, COUNT(*) AS count, MAX(sent_at) AS last_sent_at
        FROM sms_log
        WHERE kind = 'welcome' AND status IN ('sent', 'delivered')
        GROUP BY user_id
        """
    ).fetchall()
    conn.close()
    return {str(row["user_id"]): dict(row) for row in rows}


# ---------------------------------------------------------------------------
# Welcome post-send analytics (review only; does not affect sending)
# ---------------------------------------------------------------------------

def ingest_welcome_analytics(window_hours: float):
    """Copy welcome sends from sms_log into welcome_analytics (once each).

    Each successful welcome handoff gets one analytics row; re-runs are no-ops
    thanks to the unique sms_log_id constraint.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.execute(
        """
        INSERT OR IGNORE INTO welcome_analytics
            (sms_log_id, user_id, sent_at, cycle_id, window_hours, updated_at)
        SELECT id, user_id, sent_at, cycle_id, ?, ?
        FROM sms_log
        WHERE kind = 'welcome' AND status IN ('sent', 'delivered')
        """,
        (window_hours, now),
    )
    conn.commit()
    conn.close()


def get_open_welcome_tracking() -> list[dict]:
    """Welcome analytics rows that have not yet recorded a post-send play."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT sms_log_id, user_id, sent_at, window_hours
        FROM welcome_analytics
        WHERE first_play_at IS NULL
        ORDER BY id
        """
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_welcome_play(sms_log_id: int, first_play_at: str):
    """Record the earliest confirmed play within the post-send window."""
    conn = get_connection()
    conn.execute(
        """
        UPDATE welcome_analytics SET first_play_at = ?, updated_at = ?
        WHERE sms_log_id = ?
        """,
        (first_play_at, datetime.now(timezone.utc).isoformat(), sms_log_id),
    )
    conn.commit()
    conn.close()


def get_welcome_analytics(limit: int = 50) -> dict:
    """Summarize post-send tracking and return the most recent rows.

    pending = sends still inside their tracking window with no recorded play;
    no_response = sends whose window has closed with no recorded play;
    responded = sends with a recorded play inside the window.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT sms_log_id, user_id, sent_at, cycle_id, window_hours, first_play_at, updated_at
        FROM welcome_analytics
        ORDER BY id
        """
    ).fetchall()
    conn.close()

    now = datetime.now(timezone.utc)
    responded = 0
    pending = 0
    no_response = 0
    for row in rows:
        if row["first_play_at"]:
            responded += 1
            continue
        sent = datetime.fromisoformat(row["sent_at"])
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=timezone.utc)
        if sent + timedelta(hours=row["window_hours"]) > now:
            pending += 1
        else:
            no_response += 1

    resolved = responded + no_response
    return {
        "summary": {
            "tracked": len(rows),
            "responded": responded,
            "pending": pending,
            "no_response": no_response,
            "response_rate": round(responded / resolved, 4) if resolved else None,
        },
        "recent": [dict(row) for row in reversed(rows[-limit:])],
    }


# ---------------------------------------------------------------------------
# SMS log
# ---------------------------------------------------------------------------

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


def get_sms_logs(
    kind: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    """Query SMS logs with optional kind and date-range filters."""
    clauses: list[str] = []
    params: list = []
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if since:
        clauses.append("sent_at >= ?")
        params.append(since)
    if until:
        clauses.append("sent_at <= ?")
        params.append(until)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = get_connection()
    rows = conn.execute(
        f"SELECT * FROM sms_log{where} ORDER BY id", params
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_stats_summary(
    kind: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Return aggregated statistics for the sms_log table."""
    clauses: list[str] = []
    params: list = []
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
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


# ---------------------------------------------------------------------------
# Wallet log
# ---------------------------------------------------------------------------

def log_wallet_snapshot(balance: float, currency: str):
    """Record a wallet balance snapshot."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT INTO wallet_log (balance, currency, fetched_at) VALUES (?, ?, ?)",
        (balance, currency, now),
    )
    conn.commit()
    conn.close()


def get_wallet_history() -> list[dict]:
    """Return all wallet balance snapshots, newest first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM wallet_log ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_latest_wallet() -> dict | None:
    """Return the most recent wallet snapshot, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM wallet_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None