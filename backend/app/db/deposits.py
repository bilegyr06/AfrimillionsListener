"""Deposit fact persistence (segment evaluation support).

Owns the deposits table: idempotent ingestion of Deposit_events exports and
scoped reads. Deposit rows carry recency/count facts only (Deposit_events
exports userId + timestamp, with no amount). Dedup is enforced by the
deposits.source_key UNIQUE constraint, mirroring plays: the deterministic key
is (user_id, deposited_at) because the source has no stable deposit id and
identical same-second deposits are indistinguishable from duplication.
deposited_at is stored with the same "+00:00 relabelled wall-clock" convention
as plays.played_at (see app.core.dates.read_source_fact).
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db.database import get_connection


def insert_deposit_records(records: list[dict]) -> int:
    """Idempotently insert deposit records; returns rows actually inserted.

    records keys: user_id, deposited_at, source_file, source_key.
    """
    if not records:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    cur = conn.executemany(
        """
        INSERT OR IGNORE INTO deposits
            (user_id, deposited_at, source_file, created_at, source_key)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (r["user_id"], r["deposited_at"], r["source_file"], now, r["source_key"])
            for r in records
        ],
    )
    conn.commit()
    conn.close()
    return cur.rowcount


def select_deposits(source_files: list[str] | None = None) -> list[dict]:
    """Deposit rows, optionally scoped to a set of source files.

    Scoping by source_file mirrors select_plays: a Campaign Run sees exactly
    the deposits its snapshot captured, not ones from later uploads.
    """
    fields = "user_id, deposited_at, source_file"
    if source_files:
        placeholders = ",".join("?" * len(source_files))
        sql = f"SELECT {fields} FROM deposits WHERE source_file IN ({placeholders})"
    else:
        sql = f"SELECT {fields} FROM deposits"
    conn = get_connection()
    rows = conn.execute(sql, list(source_files) if source_files else None).fetchall()
    conn.close()
    return [dict(r) for r in rows]