"""Cycle orchestration and Feature 2 (inactivity reminders).

The processor owns when cycles run, which features run within one, and the
inactivity pipeline. Feature 1's campaign logic lives in app.services.campaigns
and dispatch mechanics in app.services.sms.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.core import state
from app.core.config import settings
from app.core.models import InactiveUser, INACTIVE, WELCOME
from app.core.phones import normalize_phone
from app.db.database import (
    add_pending,
    clear_pending,
    get_all_notified,
    get_pending,
    log_wallet_snapshot,
    reset_user,
    upsert_notified_records,
)
from app.integrations.termii import get_balance
from app.services.campaigns import run_welcome_pipeline
from app.services.ingestion import read_latest, read_registrations
from app.services.sms import SmsDispatcher


def enabled_features() -> set[str]:
    return settings.ENABLED_FEATURES & {WELCOME, INACTIVE}


def _build_inactive_message(user: InactiveUser) -> str:
    return settings.INACTIVE_MESSAGE.format(first_name=user.first_name)


# ---------------------------------------------------------------------------
# Inactivity discovery
# ---------------------------------------------------------------------------

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
        logins_df = read_latest(settings.LOGIN_FILE_PATTERN, parse_timestamp=True)
    regs_df = read_registrations()

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

        phone = normalize_phone(row.get("phone", ""))
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


# ---------------------------------------------------------------------------
# Inactivity dispatch
# ---------------------------------------------------------------------------

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
    """Send messages through the shared dispatcher, respecting the deadline.

    If the cycle is cancelled or the deadline passes, remaining users are
    deferred: ledged in sms_log as 'deferred' and persisted to the pending
    queue for a future cycle. Invalid numbers are rejected by the dispatcher's
    phone gate — they never reach Termii, never consume a cap/cooldown slot,
    and surface in sms_log as failed.
    """
    dispatcher = SmsDispatcher()
    completed: list[tuple[InactiveUser, object]] = []
    deferred: list[dict] = []

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
            remaining = users[i:]
            i += len(remaining)
            for user in remaining:
                dispatcher.record_deferred(
                    phone=user.phone,
                    message=build_message(user),
                    kind=kind,
                    user_id=user.user_id,
                    cycle_id=cycle_id,
                )
                deferred.append(_defer(user))
            break

        batch = users[i : i + settings.MAX_CONCURRENCY]
        i += len(batch)

        outcomes = await asyncio.gather(*[
            dispatcher.dispatch(
                phone=user.phone,
                message=build_message(user),
                kind=kind,
                user_id=user.user_id,
                cycle_id=cycle_id,
                deadline=deadline,
                cancelled=lambda: state.cancel_requested,
            )
            for user in batch
        ])

        for user, outcome in zip(batch, outcomes):
            if outcome.status == "deferred":
                dispatcher.record_deferred(
                    phone=outcome.phone,
                    message=outcome.message,
                    kind=kind,
                    user_id=user.user_id,
                    cycle_id=cycle_id,
                )
                deferred.append(_defer(user))
            else:
                completed.append((user, outcome))

    dispatcher.flush()
    _persist_successes(completed, kind)
    if deferred:
        add_pending(deferred, kind)

    sent = sum(1 for _, outcome in completed if outcome.accepted)
    failed = sum(1 for _, outcome in completed if not outcome.accepted)
    queued = len(deferred)
    return {
        "message": f"Notified {sent} {kind} user(s).",
        "sent": sent,
        "failed": failed,
        "queued": queued,
        "cancelled": False,
    }


def _persist_successes(completed: list[tuple[InactiveUser, object]], kind: str):
    """Only Termii-accepted sends advance a user's notified state."""
    if kind != INACTIVE:
        return
    records = [
        {
            "user_id": user.user_id,
            "phone": user.phone,
            "is_new": user.is_new,
        }
        for user, outcome in completed
        if outcome.accepted
    ]
    if records:
        upsert_notified_records(records, settings.COOLDOWN_HOURS)


# ---------------------------------------------------------------------------
# Cycle orchestration
# ---------------------------------------------------------------------------

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

    logins_df = read_latest(settings.LOGIN_FILE_PATTERN, parse_timestamp=True)

    results = {}
    if WELCOME in features:
        results[WELCOME] = await run_welcome_pipeline(deadline, cycle_id)
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