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