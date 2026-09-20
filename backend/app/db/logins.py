"""Login fact persistence (Campaign Window reporting).

Owns the logins table: idempotent ingestion of Login exports and scoped reads.
Login exports (userId + timestamp) are point-in-time slices of recent sign-ins;
persisting every file gives the Campaign Window report a durable, finalization-
safe source for "logged-in" metrics that does not depend on the current CSV
contents. Like plays/deposits there is no stable login id, so the deterministic
dedup key is (user_id, logged_at) and identical same-second logins collapse
into one record. logged_at is stored with the "+00:00 relabelled wall-clock"
convention (see app.core.dates.read_source_fact).
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db.database import get_connection


def insert_login_records(records: list[dict]) -> int:
    """Idempotently insert login records; returns rows actually inserted.

    records keys: user_id, logged_at, source_file, source_key.
    """
    if not records:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    cur = conn.executemany(
        """
        INSERT OR IGNORE INTO logins
            (user_id, logged_at, source_file, created_at, source_key)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (r["user_id"], r["logged_at"], r["source_file"], now, r["source_key"])
            for r in records
        ],
    )
    conn.commit()
    conn.close()
    return cur.rowcount


def select_logins(source_files: list[str] | None = None) -> list[dict]:
    """Login rows, optionally scoped to a set of source Login files.

    Scoping by source_file mirrors select_plays/select_deposits so callers can
    reproduce exactly what a snapshot captured. Fields: user_id, logged_at,
    source_file.
    """
    fields = "user_id, logged_at, source_file"
    if source_files:
        placeholders = ",".join("?" * len(source_files))
        sql = f"SELECT {fields} FROM logins WHERE source_file IN ({placeholders})"
    else:
        sql = f"SELECT {fields} FROM logins"
    conn = get_connection()
    rows = conn.execute(sql, list(source_files) if source_files else None).fetchall()
    conn.close()
    return [dict(r) for r in rows]