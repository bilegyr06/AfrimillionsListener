import asyncio
import glob
from datetime import datetime, timedelta, timezone

import pandas as pd

from app import state
from app.config import settings
from app.database import get_all_notified, reset_user, upsert_notified_records
from app.messager import send_sms
from app.models import InactiveUser


def _build_message(user: InactiveUser) -> str:
    return f"Hi {user.first_name}, we miss you! Log in to AfriMillions to keep playing."


def _normalize_phone(raw) -> str | None:
    """Convert stored phone numbers to Termii international format.

    Returns the normalized number (e.g. 07870665161 -> 2347870665161) or
    None if the value is not a usable phone number.
    """
    digits = "".join(ch for ch in str(raw) if ch.isdigit())

    if digits.startswith("0") and len(digits) == 11:
        return "234" + digits[1:]
    if digits.startswith("234") and len(digits) == 13:
        return digits
    if digits.startswith("233") and len(digits) == 12:
        return digits
    return None


def _load_csv(pattern: str) -> pd.DataFrame:
    files = glob.glob(str(settings.DATA_FOLDER / pattern))
    if not files:
        print(f"No files found for pattern: {pattern}")
        return pd.DataFrame()
    return pd.read_csv(files[0], parse_dates=["timestamp"])


def reset_active_users(logins_df: pd.DataFrame):
    """Users who have logged in within the inactivity window get their
    notification state cleared, so their cycle restarts from zero."""
    notified = get_all_notified()
    if not notified or logins_df.empty:
        return

    now_naive = datetime.now()
    cutoff = now_naive - timedelta(hours=settings.INACTIVITY_HOURS)

    recent_logins = logins_df[logins_df["timestamp"] >= cutoff]["userId"].unique()

    reset_count = 0
    for user_id in recent_logins:
        if str(user_id) in notified:
            reset_user(str(user_id))
            reset_count += 1

    if reset_count:
        print(f"Reset {reset_count} user(s) who logged back in.")


def find_inactive_users(logins_df: pd.DataFrame | None = None) -> list[InactiveUser]:
    if logins_df is None:
        logins_df = _load_csv(settings.LOGIN_FILE_PATTERN)
    regs_df = _load_csv(settings.REGISTRATION_FILE_PATTERN)

    if logins_df.empty or regs_df.empty:
        print("No login or registration data available.")
        return []

    now_naive = datetime.now()
    cutoff = now_naive - timedelta(hours=settings.INACTIVITY_HOURS)

    last_logins = (
        logins_df.groupby("userId")["timestamp"]
        .max()
        .reset_index()
        .rename(columns={"timestamp": "last_login"})
    )

    inactive = last_logins[last_logins["last_login"] < cutoff].copy()

    merged = inactive.merge(
        regs_df[["userId", "firstName", "phone"]],
        on="userId",
        how="left",
    )

    notified = get_all_notified()
    now_utc = datetime.now(timezone.utc)

    results: list[InactiveUser] = []
    for _, row in merged.iterrows():
        user_id = str(row["userId"])
        record = notified.get(user_id)

        is_new = record is None
        if record:
            next_available = datetime.fromisoformat(record["next_available_at"])
            if now_utc < next_available:
                continue
            if settings.MAX_MESSAGES > 0 and record["notification_count"] >= settings.MAX_MESSAGES:
                continue

        phone = _normalize_phone(row.get("phone", ""))
        if phone is None:
            continue

        results.append(
            InactiveUser(
                user_id=user_id,
                first_name=str(row.get("firstName", "User")),
                phone=phone,
                last_login=str(row["last_login"]),
                is_new=is_new,
            )
        )

    return results


async def run_cycle_async(deadline: datetime) -> dict:
    """Run a full notification cycle as an async task. Cancellable via task.cancel()."""
    state.cancel_requested = False

    try:
        return await notify_inactive_users(deadline)
    except asyncio.CancelledError:
        print("Notification cycle cancelled.")
        return {"message": "Notification cycle cancelled.", "count": 0, "cancelled": True}


def begin_cycle() -> bool:
    """Start a new cycle as an asyncio task. Returns False if one is already running."""
    if state.current_task is not None and not state.current_task.done():
        return False

    now = datetime.now()
    deadline = now.replace(hour=settings.CYCLE_END_HOUR, minute=0, second=0, microsecond=0)

    task = asyncio.create_task(run_cycle_async(deadline))
    state.current_task = task

    def _on_done(t: asyncio.Task):
        if state.current_task is t:
            state.current_task = None

    task.add_done_callback(_on_done)
    return True


async def notify_inactive_users(deadline: datetime) -> dict:
    logins_df = _load_csv(settings.LOGIN_FILE_PATTERN)
    reset_active_users(logins_df)

    users = find_inactive_users(logins_df)
    if not users:
        return {"message": "No inactive users to notify.", "count": 0}

    sem = asyncio.Semaphore(settings.MAX_CONCURRENCY)
    completed: list[tuple[InactiveUser, dict | None]] = []

    async def worker(user: InactiveUser) -> None:
        if state.cancel_requested or datetime.now() >= deadline:
            return
        async with sem:
            if state.cancel_requested or datetime.now() >= deadline:
                return
            result = await send_sms(user.phone, _build_message(user))
            completed.append((user, result))

    tasks = [asyncio.create_task(worker(user)) for user in users]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        # Record whatever already succeeded before surfacing cancellation.
        _persist_successes(completed)
        raise

    _persist_successes(completed)

    sent = sum(1 for _, result in completed if result)
    failed = sum(1 for _, result in completed if not result)
    return {
        "message": f"Notified {sent} user(s).",
        "count": sent,
        "failed": failed,
        "cancelled": False,
    }


def _persist_successes(completed: list[tuple[InactiveUser, dict | None]]):
    records = [
        {
            "user_id": user.user_id,
            "phone": user.phone,
            "is_new": user.is_new,
        }
        for user, result in completed
        if result
    ]
    if records:
        upsert_notified_records(records, settings.COOLDOWN_HOURS)