"""Campaign Window business-time helpers.

All source CSV timestamps and campaign business times are in Nigerian time
(Africa/Lagos, fixed UTC+1 with no DST). Campaign Window boundaries, Run
timestamps, login evaluation times, cooldown calculations and finalization
deadlines are derived from this timezone.

Persistence convention: v2 timestamps are stored as UTC-aware ISO 8601 strings
("...+00:00"), matching the format the rest of the application uses for
`plays.played_at`, `sms_log.sent_at`, etc., so lexicographic SQL comparisons
against those tables remain correct. The business wall-clock meaning of a
boundary is defined here in Africa/Lagos and converted to its UTC instant for
storage. Wall-clock times are always expressed explicitly (00:00 / 23:59:59 /
14:00 / 17:00), never as ambiguous 12 a.m. / 12 p.m.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = "Africa/Lagos"
BUSINESS_TZ = ZoneInfo(BUSINESS_TIMEZONE)

# Default Campaign Window schedule (business week): Monday 00:00:00 through
# Saturday 23:59:59, Africa/Lagos. The finalization/grace-period deadline is
# the Sunday of the same week; it defaults to 14:00 and may be configured no
# later than 17:00 (5:00 p.m.), Africa/Lagos.
_WINDOW_START_WEEKDAY = 0  # Monday
_WINDOW_END_WEEKDAY = 5  # Friday +1 -> end on Saturday
_DEADLINE_WEEKDAY = 6  # Sunday

DAY_SECONDS = 24 * 60 * 60


def now_business() -> datetime:
    """Current instant as an aware datetime in Africa/Lagos."""
    return datetime.now(BUSINESS_TZ)


def as_business(dt: datetime) -> datetime:
    """Normalize a datetime (aware or naive) into business time.

    Naive datetimes are interpreted as Africa/Lagos wall-clock time.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BUSINESS_TZ)
    return dt.astimezone(BUSINESS_TZ)


def to_utc_iso(dt: datetime) -> str:
    """Serialize a business datetime into the app's UTC-aware ISO form."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BUSINESS_TZ)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_utc_iso(value: str) -> datetime:
    """Parse a stored UTC-aware ISO string back into an aware datetime."""
    return datetime.fromisoformat(value)


def from_business_iso(value: str) -> datetime:
    """Parse an ISO string the operator supplied (explicit offset or naive)."""
    dt = datetime.fromisoformat(value)
    return as_business(dt)


# ---------------------------------------------------------------------------
# Default schedule
# ---------------------------------------------------------------------------

def default_window_start(now: datetime | None = None) -> datetime:
    """Monday 00:00:00 Africa/Lagos starting the window's business week.

    References Monday through Saturday map to the Sunday-ending business week
    they fall in. A Sunday reference instant starts the *following* Monday (a
    window created on Sunday for the already-finished Mon-Sat week would
    otherwise be past its Saturday end).
    """
    ref = now_business() if now is None else as_business(now)
    days_since_monday = (ref.weekday() - _WINDOW_START_WEEKDAY) % 7
    start = ref - timedelta(days=days_since_monday)
    if ref.weekday() == _DEADLINE_WEEKDAY:
        start = start + timedelta(days=7)
    return start.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=BUSINESS_TZ)


def default_window_end(start: datetime) -> datetime:
    """Saturday 23:59:59 Africa/Lagos for the window beginning at `start`."""
    start = as_business(start)
    days_after = (_WINDOW_END_WEEKDAY - _WINDOW_START_WEEKDAY) % 7
    end = start + timedelta(days=days_after)
    return end.replace(hour=23, minute=59, second=59, microsecond=0, tzinfo=BUSINESS_TZ)


def default_finalization_deadline(start: datetime) -> datetime:
    """Sunday 14:00 Africa/Lagos for the window beginning at `start`."""
    start = as_business(start)
    days_after = (_DEADLINE_WEEKDAY - _WINDOW_START_WEEKDAY) % 7
    deadline = start + timedelta(days=days_after)
    return deadline.replace(hour=14, minute=0, second=0, microsecond=0, tzinfo=BUSINESS_TZ)


def max_finalization_deadline(start: datetime) -> datetime:
    """Sunday 17:00 Africa/Lagos - the latest allowed deadline for the window."""
    start = as_business(start)
    days_after = (_DEADLINE_WEEKDAY - _WINDOW_START_WEEKDAY) % 7
    deadline = start + timedelta(days=days_after)
    return deadline.replace(hour=17, minute=0, second=0, microsecond=0, tzinfo=BUSINESS_TZ)