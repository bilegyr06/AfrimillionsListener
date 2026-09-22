"""Smoke-test helper: place the active campaign's start BEFORE the fixed login
timestamps in data/Login_smoke_20260922_1755.csv (2026-09-22 14:00 UTC+), so the
sms-test logins fall inside the campaign window and past the 1h eval delay.

Usage:  python smoke_test.py
"""
import sqlite3
from datetime import datetime, timezone

DB = "database/notified_users.db"
BACKDATE_TO = "2026-09-22T13:30:00+00:00"

conn = sqlite3.connect(DB)
row = conn.execute(
    "SELECT id, name, started_at, status FROM welcome_campaigns "
    "ORDER BY id DESC LIMIT 1"
).fetchone()

if row is None:
    raise SystemExit("No campaigns found. Start one first: POST /campaign/start")

cid, name, started_at, status = row
print(f"Newest campaign: id={cid} name={name!r} status={status}")

if status != "active":
    raise SystemExit(
        "No ACTIVE campaign. Start one first via POST /campaign/start, "
        "then re-run this script."
    )

old = started_at
conn.execute(
    "UPDATE welcome_campaigns SET started_at=? WHERE id=?", (BACKDATE_TO, cid)
)
conn.commit()
print(f"Campaign #{cid} started_at: {old} -> {BACKDATE_TO}")
print(f"Now: {datetime.now(timezone.utc).isoformat()} "
      "(logins at 14:00-14:30 UTC fall inside the window and are past eval delay)")
conn.close()