"""Persistent operator settings (database-backed).

After startup the settings table is the source of truth for the operator
settings declared in app.core.settings_spec. Values are stored as canonical
strings and typed at read time.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import settings as live_settings
from app.core.settings_spec import SPECS
from app.db.database import get_connection


def get_all_settings() -> dict[str, str]:
    """All persisted rows as {key: value_string}."""
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {str(row["key"]): str(row["value"]) for row in rows}


def get_setting(key: str) -> str | None:
    conn = get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return str(row["value"]) if row else None


def upsert_setting(key: str, value: str):
    """Write a canonical string value, replacing any prior value."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, now),
    )
    conn.commit()
    conn.close()


def seed_settings_from_db():
    """Insert settings keys that are missing, taking their value from the live
    (env-effective) configuration. Idempotent; never overwrites an existing,
    operator-edited value."""
    existing = get_all_settings()
    for key, spec in SPECS.items():
        if key in existing:
            continue
        upsert_setting(key, spec.serialize(getattr(live_settings, key)))