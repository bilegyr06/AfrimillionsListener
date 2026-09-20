"""Campaign Run -> Welcome SMS execution (v2.0.0).

dispatch_run() sends the Welcome SMS for a running Run's FROZEN target through
the existing SMS infrastructure (SmsDispatcher -> provider gateway -> sms_log).
It sends the operator's Welcome template, honors the global cooldown and the
welcome cap, and never re-evaluates membership or changes the target at
dispatch time.

Dispatch guarantees:
  * Target is fixed: the audience members THIS Run admitted (window_audiences
    run_id), Campaign-assigned with a usable phone. Control users and invalid
    phones never reached the audience, so they are structurally excluded.
  * Server-side boundary: nothing outside the frozen target reaches the
    provider.
  * Cooldown: measured from the user's last accepted SMS of any kind
    (global cooldown); a user accepted < COOLDOWN_HOURS ago is skipped.
  * Cap: WELCOME_MAX_MESSAGES accepted Welcome sends per user, cumulative
    across legacy v1 and v2 runs (sms_log kind = 'welcome'); failures do not
    consume a slot.
  * No automatic retry: a user already accepted for this Run (sms_log
    cycle_id "run:{run_id}") is never sent again by it; only failed attempts
    remain open for an explicit operator re-dispatch. Deferred attempts
    (deadline/cancel) are not written to sms_log.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.core import state
from app.core.config import settings
from app.core.dates import as_business, now_business, to_utc_iso
from app.core.models import WELCOME
from app.db.sms import (
    count_accepted_welcome_sms,
    get_accepted_run_sends,
    get_last_accepted_sms,
)
from app.db.windows import WindowStateError, get_run, get_run_target, get_window
from app.integrations.sms_gateway import SMSGateway
from app.services.sms import SmsDispatcher


DISPATCH_KIND = WELCOME


def _as_naive_utc(dt: datetime | None) -> datetime | None:
    """Deadline normalization for the shared SmsDispatcher.

    SmsDispatcher compares deadlines against a naive datetime.now(); an aware
    deadline would raise. Normalize to naive UTC so the comparison is
    consistent regardless of how the caller built the instant.
    """
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _cooldown_active(user_id: str, now: datetime) -> bool:
    """True when the user's last accepted SMS any kind is inside the cooldown."""
    if settings.COOLDOWN_HOURS <= 0:
        return False
    last = get_last_accepted_sms(user_id)
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return False
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc) < last_dt + timedelta(hours=settings.COOLDOWN_HOURS)


def _cap_reached(user_id: str) -> bool:
    """True when the user reached the welcome cap AND post-limit sends are suppressed."""
    if settings.WELCOME_MAX_MESSAGES <= 0:
        return False
    return (
        count_accepted_welcome_sms(user_id) >= settings.WELCOME_MAX_MESSAGES
        and settings.WELCOME_POST_LIMIT_SUPPRESS
    )


async def _send_attempts(
    run_id: int,
    attempts: list[dict],
    *,
    deadline: datetime | None,
    cancelled,
    gateway: SMSGateway | None,
) -> dict:
    """Place one SMS per attempt through SmsDispatcher; tally outcomes.

    Mirrors the v1 welcome sender but for a fixed Run target: a send carries
    cycle_id "run:{run_id}" so repeat dispatch stays idempotent, and a failed
    send consumes no cooldown/cap slot (the user is not in get_accepted_run_sends,
    so the next dispatch tries again).
    """
    dispatcher = SmsDispatcher(gateway=gateway)
    sent = failed = deferred = 0

    def _cancelled() -> bool:
        if state.cancel_requested:
            return True
        return bool(cancelled) and cancelled()

    i = 0
    while i < len(attempts):
        if _cancelled() or (deadline is not None and datetime.now() >= deadline):
            deferred += len(attempts) - i
            break

        batch = attempts[i : i + settings.MAX_CONCURRENCY]
        i += len(batch)

        outcomes = await asyncio.gather(*[
            dispatcher.dispatch(
                phone=a["phone"],
                message=a["message"],
                kind=WELCOME,
                user_id=a["user_id"],
                cycle_id=f"run:{run_id}",
                deadline=deadline,
                cancelled=_cancelled,
            )
            for a in batch
        ])

        for outcome in outcomes:
            if outcome.status == "deferred":
                deferred += 1
            elif outcome.accepted:
                sent += 1
            else:
                failed += 1

    dispatcher.flush()
    return {"sent": sent, "failed": failed, "deferred": deferred}


async def dispatch_run(
    run_id: int,
    *,
    now: datetime | None = None,
    deadline: datetime | None = None,
    cancelled=None,
    gateway: SMSGateway | None = None,
) -> dict:
    """Send the Welcome SMS to a running Run's frozen target.

    Lifecycle guards: the Run must be 'running' and its window not finalized
    (a finalized window is immutable; ended windows stop every active Run).
    Cooldown and cap are enforced at dispatch time; nothing below is written
    to the DB except the sms_log ledger produced by the dispatcher.
    """
    now = as_business(now) if now is not None else now_business()
    deadline = _as_naive_utc(deadline)

    run = get_run(run_id)
    if run is None:
        raise WindowStateError(f"Campaign Run #{run_id} not found.")
    if run["status"] != "running":
        raise WindowStateError(
            f"Cannot dispatch Campaign Run #{run_id}: status is {run['status']!r}, "
            "only a running Run can be dispatched."
        )
    window = get_window(run["window_id"])
    if window is None:
        raise WindowStateError(f"Campaign Window #{run['window_id']} not found.")
    if window["status"] == "finalized":
        raise WindowStateError(
            f"Cannot dispatch Campaign Run #{run_id}: Campaign Window "
            f"#{window['id']} is finalized and immutable."
        )

    target = get_run_target(window["id"], run_id)
    already_accepted = get_accepted_run_sends(run_id)

    counts = {
        "target": len(target),
        "already_sent": 0,
        "skipped_cooldown": 0,
        "skipped_cap": 0,
        "sent": 0,
        "failed": 0,
        "deferred": 0,
    }

    attempts: list[dict] = []
    for member in target:
        user_id = member["user_id"]
        if user_id in already_accepted:
            counts["already_sent"] += 1
            continue
        if _cooldown_active(user_id, now):
            counts["skipped_cooldown"] += 1
            continue
        if _cap_reached(user_id):
            counts["skipped_cap"] += 1
            continue

        first_name = (member.get("eligibility_state") or {}).get("first_name") or "User"
        attempts.append({
            "user_id": user_id,
            "phone": member["phone_normalized"],
            "message": settings.WELCOME_MESSAGE.format(first_name=first_name),
        })

    if attempts:
        counts.update(
            await _send_attempts(
                run_id,
                attempts,
                deadline=deadline,
                cancelled=cancelled,
                gateway=gateway,
            )
        )

    return {
        "run_id": run_id,
        "window_id": window["id"],
        "run_status": run["status"],
        "dispatched_at": to_utc_iso(now),
        **counts,
    }