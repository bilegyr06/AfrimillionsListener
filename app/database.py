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
    conn.commit()
    conn.close()


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