"""Feature 2: inactivity-reminder pipeline.

Owns "inactive" end to end, mirroring how Feature 1 (welcome) is owned by
app.services.campaigns: who is inactive, who gets messaged, and which accepted
sends advance a user's notified state. The feature is declared in the registry
(app.core.features) at the bottom of this module, where its pipeline lives.

The cycle orchestrator (app.workers.processor) only supplies the lifecycle this
pipeline plugs into — the deadline, cancellation, and the deferred-work queue —
via defer_work / deferred_work. Everything else is this module's business.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.core import state
from app.core.config import settings
from app.core.features import Feature, register_feature
from app.core.models import InactiveUser, INACTIVE
from app.core.phones import normalize_phone
from app.db.players import get_all_notified, reset_user, upsert_notified_records
from app.services.ingestion import read_latest, read_registrations
from app.services.sms import SmsDispatcher
from app.workers.processor import defer_work, deferred_work


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
    """Merge work a previous interrupted cycle deferred into the current list.

    The deferred-work queue is cycle lifecycle (owned by the orchestrator), so
    the records come from there; turning the opaque records back into this
    feature's users is the pipeline's job.
    """
    queued = deferred_work(kind)
    if not queued:
        return users

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
    deferred: ledged in sms_log as 'deferred' and handed back to the cycle's
    deferred-work queue for a future cycle. Invalid numbers are rejected by the
    dispatcher's phone gate — they never reach Termii, never consume a
    cap/cooldown slot, and surface in sms_log as failed.
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
    defer_work(kind, deferred)

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
# Feature registry: Feature 2 (inactive) is declared here, where its pipeline
# lives. The pipeline fetches its own login data when the cycle provides none;
# the per-cycle state reset belongs to this pipeline, not to the orchestrator.
# ---------------------------------------------------------------------------

async def _run_inactive_pipeline(deadline, cycle_id, logins_df=None) -> dict:
    """Canonical registry pipeline: clear in-window logins, then notify due users."""
    if logins_df is None:
        logins_df = read_latest(settings.LOGIN_FILE_PATTERN, parse_timestamp=True)
    reset_active_users(logins_df)
    return await notify_inactive_users(logins_df, deadline, cycle_id)


register_feature(
    Feature(
        kind=INACTIVE,
        gate=lambda: INACTIVE in settings.ENABLED_FEATURES,
        template=settings.INACTIVE_MESSAGE,
        pipeline=_run_inactive_pipeline,
    )
)