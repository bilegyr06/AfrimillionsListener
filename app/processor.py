import asyncio
import glob
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app import state
from app.config import settings
from app.database import (
    add_pending,
    clear_pending,
    get_all_notified,
    get_pending,
    get_welcome_sent,
    get_welcome_sms_state,
    log_sms_batch,
    log_wallet_snapshot,
    reset_user,
    upsert_notified_records,
    upsert_welcome_sent,
)
from app.messager import send_sms
from app.models import InactiveUser
from app.termii_insights import get_balance

WELCOME = "welcome"
INACTIVE = "inactive"


def enabled_features() -> set[str]:
    return settings.ENABLED_FEATURES & {WELCOME, INACTIVE}


def _build_inactive_message(user: InactiveUser) -> str:
    return settings.INACTIVE_MESSAGE.format(first_name=user.first_name)


def _build_welcome_message(user: InactiveUser) -> str:
    return settings.WELCOME_MESSAGE.format(first_name=user.first_name)


def _normalize_phone(raw) -> str | None:
    """Convert stored phone numbers to Termii international format.

    Returns the normalized number (e.g. 07870665161 -> 2347870665161) or
    None if the value is not a usable phone number.
    """
    digits = "".join(ch for ch in str(raw) if ch.isdigit())

    if digits.startswith("0") and len(digits) == 11:
        return "234" + digits[1:]
    if len(digits) == 10:
        return "234" + digits
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
    newest = sorted(files, key=lambda p: Path(p).stat().st_mtime, reverse=True)[0]
    return pd.read_csv(newest, parse_dates=["timestamp"])


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


def find_recent_login_users(
    logins_df: pd.DataFrame | None = None,
    sales_df: pd.DataFrame | None = None,
) -> list[InactiveUser]:
    """Feature 1: post-sign-in welcome campaign.

    A sign-in becomes eligible once WELCOME_EVAL_DELAY_HOURS have elapsed, but
    only if the user played no game during the wait period. Each sign-in can
    trigger at most one Welcome SMS (see welcome_sent), subject to the per-user
    cooldown and the configured Welcome notification cap / post-limit rule.
    """
    if logins_df is None:
        logins_df = _load_csv(settings.LOGIN_FILE_PATTERN)
    regs_df = _load_csv(settings.REGISTRATION_FILE_PATTERN)
    if sales_df is None:
        sales_df = _load_csv(settings.SALES_FILE_PATTERN)

    if logins_df.empty or regs_df.empty or sales_df.empty:
        print("No login, registration, or sales data available.")
        return []

    # Join on a common string key so int/str user ids from different sources merge cleanly.
    logins_df = logins_df.copy()
    logins_df["userId"] = logins_df["userId"].astype(str)
    regs_df = regs_df.copy()
    regs_df["userId"] = regs_df["userId"].astype(str)
    sales_df = sales_df.copy()
    sales_df["userId"] = sales_df["userId"].astype(str)

    now_naive = datetime.now()
    delay = timedelta(hours=settings.WELCOME_EVAL_DELAY_HOURS)

    last_logins = (
        logins_df.groupby("userId")["timestamp"]
        .max()
        .reset_index()
        .rename(columns={"timestamp": "last_login"})
    )

    # Only sign-ins whose evaluation delay has elapsed are considered.
    evaluable = last_logins[last_logins["last_login"] <= now_naive - delay].copy()

    merged = evaluable.merge(
        regs_df[["userId", "firstName", "phone"]],
        on="userId",
        how="left",
    )

    # Users who played during the wait window (login, login + delay] are
    # disqualified for that sign-in regardless of anything else.
    plays = sales_df.rename(columns={"timestamp": "play_at"})
    joined = merged.merge(plays, on="userId", how="inner", suffixes=("", "_play"))
    played_in_window = joined[
        (joined["play_at"] > joined["last_login"])
        & (joined["play_at"] <= joined["last_login"] + delay)
    ]
    played_user_ids = set(played_in_window["userId"].astype(str))

    welcomed = get_welcome_sent()
    welcome_state = get_welcome_sms_state()
    now_utc = datetime.now(timezone.utc)

    results: list[InactiveUser] = []
    for _, row in merged.iterrows():
        user_id = str(row["userId"])
        login_at = row["last_login"].isoformat()

        if user_id in played_user_ids:
            continue

        if user_id in welcomed and welcomed[user_id] == login_at:
            continue

        record = welcome_state.get(user_id)
        if record:
            next_available = datetime.fromisoformat(
                record["last_sent_at"]
            ) + timedelta(hours=settings.COOLDOWN_HOURS)
            if now_utc < next_available:
                continue
            if (
                settings.WELCOME_MAX_MESSAGES > 0
                and record["count"] >= settings.WELCOME_MAX_MESSAGES
                and settings.WELCOME_POST_LIMIT_SUPPRESS
            ):
                continue

        phone = _normalize_phone(row.get("phone", ""))
        if phone is None:
            continue

        results.append(
            InactiveUser(
                user_id=user_id,
                first_name=str(row.get("firstName", "User")),
                phone=phone,
                last_login=login_at,
                is_new=True,
            )
        )

    return results


async def run_cycle_async(deadline: datetime, features: set[str] | None = None) -> dict:
    """Run a notification cycle as an async task. Cancellable via task.cancel()."""
    state.cancel_requested = False

    try:
        return await _run_features(deadline, features)
    except asyncio.CancelledError:
        print("Notification cycle cancelled.")
        return {"message": "Notification cycle cancelled.", "count": 0, "cancelled": True}


async def _run_features(deadline: datetime, features: set[str] | None = None) -> dict:
    """Run the given feature subset (default: all enabled) and aggregate results."""
    features = (features & settings.ENABLED_FEATURES) if features else enabled_features()
    if not features:
        print("Cycle: no features enabled; nothing to run.")
        return {"message": "No features enabled.", "count": 0}

    cycle_id = uuid.uuid4().hex[:16]
    print(f"Cycle {cycle_id} started at {datetime.now().strftime('%H:%M:%S')}, running features: {', '.join(sorted(features))}")

    balance_info = await get_balance()
    if balance_info:
        log_wallet_snapshot(
            balance=balance_info.get("balance", 0),
            currency=balance_info.get("currency", "NGN"),
        )

    logins_df = _load_csv(settings.LOGIN_FILE_PATTERN)

    results = {}
    if WELCOME in features:
        results[WELCOME] = await notify_welcome_users(logins_df, deadline, cycle_id)
    if INACTIVE in features:
        reset_active_users(logins_df)
        results[INACTIVE] = await notify_inactive_users(logins_df, deadline, cycle_id)

    total = sum(r.get("sent", 0) for r in results.values())
    print(f"Cycle {cycle_id} finished: total sent={total}. Per-feature: {results}")
    return {
        "message": f"Cycle complete: {total} message(s) sent.",
        "count": total,
        "cycle_id": cycle_id,
        "features": results,
        "cancelled": False,
    }


def begin_cycle(features: set[str] | None = None) -> bool:
    """Start a new cycle as an asyncio task. Returns False if one is already running.

    If features is None, all enabled features run.
    """
    if state.current_task is not None and not state.current_task.done():
        return False

    now = datetime.now()
    deadline = now.replace(hour=settings.CYCLE_END_HOUR, minute=0, second=0, microsecond=0)

    task = asyncio.create_task(run_cycle_async(deadline, features))
    state.current_task = task

    def _on_done(t: asyncio.Task):
        if state.current_task is t:
            state.current_task = None

    task.add_done_callback(_on_done)
    return True


def _merge_queued(kind: str, users: list[InactiveUser]) -> list[InactiveUser]:
    """Merge queued users of a kind into the current user list, deduped by id."""
    queued = get_pending(kind)
    if not queued:
        return users

    clear_pending(kind)
    print(f"Merging {len(queued)} queued {kind} user(s) from a previous deferred cycle.")

    existing_ids = {u.user_id for u in users}
    for q in queued:
        if q["user_id"] in existing_ids:
            continue
        users.append(
            InactiveUser(
                user_id=q["user_id"],
                first_name="User",
                phone=q["phone"],
                last_login=q.get("last_login_at", ""),
                is_new=bool(q["is_new"]),
            )
        )
    return users


async def notify_welcome_users(logins_df: pd.DataFrame, deadline: datetime, cycle_id: str) -> dict:
    users = _merge_queued(WELCOME, find_recent_login_users(logins_df))
    if not users:
        print("Welcome: no recent-login users to welcome.")
        return {"message": "No recent-login users to welcome.", "count": 0}

    print(f"Welcome: {len(users)} user(s) to send to.")
    result = await _send_users(users, deadline, WELCOME, _build_welcome_message, cycle_id)
    result["count"] = result.get("sent", 0)
    print(f"Welcome result: {result}")
    return result


async def notify_inactive_users(logins_df: pd.DataFrame, deadline: datetime, cycle_id: str) -> dict:
    users = _merge_queued(INACTIVE, find_inactive_users(logins_df))
    if not users:
        print("Inactive: no inactive users to notify.")
        return {"message": "No inactive users to notify.", "count": 0}

    print(f"Inactive: {len(users)} user(s) to send to.")
    result = await _send_users(users, deadline, INACTIVE, _build_inactive_message, cycle_id)
    result["count"] = result.get("sent", 0)
    print(f"Inactive result: {result}")
    return result


async def _send_users(
    users: list[InactiveUser],
    deadline: datetime,
    kind: str,
    build_message,
    cycle_id: str,
) -> dict:
    """Send messages to users, respecting concurrency and the cycle deadline.

    If the cycle is cancelled or the deadline passes, remaining unsent users
    are persisted to the pending queue for a future cycle.
    """
    sem = asyncio.Semaphore(settings.MAX_CONCURRENCY)
    completed: list[tuple[InactiveUser, dict | None]] = []
    deferred: list[dict] = []
    sms_log_records: list[dict] = []

    def _defer(user: InactiveUser) -> dict:
        return {
            "user_id": user.user_id,
            "phone": user.phone,
            "is_new": user.is_new,
            "last_login_at": user.last_login,
        }

    i = 0
    while i < len(users):
        if state.cancel_requested or datetime.now() >= deadline:
            deferred.extend(_defer(u) for u in users[i:])
            break

        batch = users[i : i + settings.MAX_CONCURRENCY]
        i += len(batch)

        async def worker(user: InactiveUser):
            if state.cancel_requested or datetime.now() >= deadline:
                return ("deferred", user)
            async with sem:
                if state.cancel_requested or datetime.now() >= deadline:
                    return ("deferred", user)
                return ("attempted", user, await send_sms(user.phone, build_message(user)))

        for outcome in await asyncio.gather(*[worker(u) for u in batch]):
            if outcome[0] == "deferred":
                _, user = outcome
                deferred.append(_defer(user))
                sms_log_records.append({
                    "user_id": user.user_id,
                    "kind": kind,
                    "phone": user.phone,
                    "status": "deferred",
                    "cost": 0,
                    "cycle_id": cycle_id,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                })
            else:
                _, user, result = outcome
                completed.append((user, result))
                if result:
                    sms_log_records.append({
                        "message_id": result.get("message_id"),
                        "user_id": user.user_id,
                        "kind": kind,
                        "phone": user.phone,
                        "status": "sent",
                        "cost": 0,
                        "balance_after": result.get("balance"),
                        "cycle_id": cycle_id,
                        "sent_at": datetime.now(timezone.utc).isoformat(),
                    })
                else:
                    sms_log_records.append({
                        "user_id": user.user_id,
                        "kind": kind,
                        "phone": user.phone,
                        "status": "failed",
                        "cost": 0,
                        "cycle_id": cycle_id,
                        "sent_at": datetime.now(timezone.utc).isoformat(),
                    })

    log_sms_batch(sms_log_records)
    _persist_successes(completed, kind)
    if deferred:
        add_pending(deferred, kind)

    sent = sum(1 for _, result in completed if result)
    failed = sum(1 for _, result in completed if not result)
    queued = len(deferred)
    return {
        "message": f"Notified {sent} {kind} user(s).",
        "sent": sent,
        "failed": failed,
        "queued": queued,
        "cancelled": False,
    }


def _persist_successes(completed: list[tuple[InactiveUser, dict | None]], kind: str):
    if kind == WELCOME:
        records = [
            {"user_id": user.user_id, "last_login_at": user.last_login}
            for user, result in completed
            if result and user.last_login
        ]
        if records:
            upsert_welcome_sent(records)
        return

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