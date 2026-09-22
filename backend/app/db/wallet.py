"""Wallet balance data module.

Owns the wallet_log table (Termii balance snapshots). Consumers: the stats
wallet reporting surfaces in app.routers.stats and app.main.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db.database import get_connection


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