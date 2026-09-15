import asyncio
import glob
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app import state
from app.config import settings
from app.database import (
    add_pending,
    clear_pending,
    close_open_interventions,
    create_intervention,
    get_active_campaign,
    get_all_notified,
    get_campaign,
    get_cap_usage,
    get_last_welcome_sent,
    get_open_interventions,
    get_open_opportunities,
    get_pending,
    log_sms_batch,
    log_wallet_snapshot,
    mark_opportunities_expired,
    record_intervention_response,
    reset_user,
    update_opportunity_status,
    upsert_notified_records,
    upsert_opportunities,
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


def _load_regs() -> pd.DataFrame:
    """Read the newest Registrations file without parse_dates.

    Registration exports contain mixed timestamp formats (with and without
    microseconds) that crash pandas' automatic datetime parsing, and the
    timestamp is not needed here anyway.
    """
    files = glob.glob(str(settings.DATA_FOLDER / settings.REGISTRATION_FILE_PATTERN))
    if not files:
        return pd.DataFrame()
    newest = sorted(files, key=lambda p: Path(p).stat().st_mtime, reverse=True)[0]
    return pd.read_csv(newest)


def _load_login_events() -> pd.DataFrame:
    """Concatenate login events from ALL Login files, not just the newest.

    A Login export is only a point-in-time slice of recent sign-ins, so the
    active campaign must scan every file that overlaps its window.
    """
    files = glob.glob(str(settings.DATA_FOLDER / settings.LOGIN_FILE_PATTERN))
    frames: list[pd.DataFrame] = []
    for f in files:
        try:
            df = pd.read_csv(f, parse_dates=["timestamp"], dtype={"userId": str})
        except (ValueError, pd.errors.ParserError):
            continue
        if df.empty or "userId" not in df.columns or "timestamp" not in df.columns:
            continue
        frames.append(df[["userId", "timestamp"]].copy())
    if not frames:
        return pd.DataFrame(columns=["userId", "timestamp"])
    merged = pd.concat(frames, ignore_index=True)
    return merged[merged["userId"].notna()]


def _is_valid_nigerian_phone(normalized: str | None) -> bool:
    """Hard safety gate: only validated Nigerian mobile numbers pass.

    Expects a normalized number (leading '234', no '+'). A Nigerian mobile is
    '234' + 3-digit leading network code starting 7/8/9 + 7 more digits.
    """
    if not normalized:
        return False
    return re.fullmatch(r"234[789]\d{9}", normalized) is not None


def _phone_gate(raw) -> str | None:
    """Normalize a stored phone and return it ONLY if it is a valid Nigerian
    mobile. Invalid numbers return None and are marked skipped_invalid_phone."""
    return _normalize_phone(raw) if _is_valid_nigerian_phone(_normalize_phone(raw)) else None


def _to_utc_series(series: pd.Series) -> pd.Series:
    """Treat naive CSV timestamps as UTC (app-wide convention) and return aware."""
    parsed = pd.to_datetime(series)
    if parsed.dt.tz is None:
        parsed = parsed.dt.tz_localize("UTC")
    else:
        parsed = parsed.dt.tz_convert("UTC")
    return parsed


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


def _plays_map(sales_df: pd.DataFrame) -> dict[str, list[datetime]]:
    """Per-user sorted lists of aware play timestamps from the Sales file."""
    if sales_df is None or sales_df.empty:
        return {}
    plays = sales_df.copy()
    plays["userId"] = plays["userId"].astype(str)
    tss = _to_utc_series(plays["timestamp"])
    out: dict[str, list[datetime]] = {}
    for uid, ts in zip(plays["userId"], tss):
        out.setdefault(str(uid), []).append(ts.to_pydatetime())
    for key in out:
        out[key].sort()
    return out


def ingest_opportunities(campaign: dict, login_df: pd.DataFrame, regs_df: pd.DataFrame) -> int:
    """Create one opportunity per login that falls inside the campaign window.

    Snapshot first name, raw phone and the validated normalized phone at
    ingestion time (from the newest Registrations file) so later changes to
    either source do not rewrite history. Idempotent via the
    (campaign_id, user_id, login_at) unique key.
    """
    if login_df is None or login_df.empty or regs_df is None or regs_df.empty:
        return 0

    campaign_start = pd.Timestamp(campaign["started_at"])
    if campaign_start.tzinfo is None:
        campaign_start = campaign_start.tz_localize("UTC")

    frame = login_df[login_df["userId"].notna()].copy()
    frame["userId"] = frame["userId"].astype(str)
    tss = _to_utc_series(frame["timestamp"])

    mask = tss >= campaign_start
    if campaign["ended_at"]:
        campaign_end = pd.Timestamp(campaign["ended_at"])
        mask = mask & (tss < campaign_end)

    regs = regs_df.copy()
    regs["userId"] = regs["userId"].astype(str)
    reg_map = {str(r["userId"]): r for _, r in regs.iterrows()}

    records: list[dict] = []
    for uid, ts in zip(frame.loc[mask, "userId"], tss[mask]):
        reg = reg_map.get(str(uid))
        phone_raw = str(reg["phone"]) if reg is not None and pd.notna(reg.get("phone")) else ""
        first_name = "User"
        if reg is not None and pd.notna(reg.get("firstName")):
            first_name = str(reg["firstName"])
        records.append({
            "campaign_id": campaign["id"],
            "user_id": str(uid),
            "first_name": first_name,
            "phone_raw": phone_raw,
            "phone_normalized": _phone_gate(phone_raw),
            "login_at": ts.isoformat(),
            "eval_delay_hours": settings.WELCOME_EVAL_DELAY_HOURS,
        })

    upsert_opportunities(records)
    return len(records)


async def _evaluate_campaign(
    campaign: dict,
    sales_df: pd.DataFrame,
    deadline: datetime,
    cycle_id: str,
) -> dict:
    opps = get_open_opportunities(campaign["id"])
    if not opps:
        return {"eligible": 0, "sent": 0, "failed": 0, "deferred": 0, "decisions": {}}

    plays = _plays_map(sales_df)
    now_utc = datetime.now(timezone.utc)
    decisions = {
        status: 0
        for status in (
            "too_early",
            "expired",
            "disqualified_played",
            "skipped_cap",
            "skipped_cooldown",
            "skipped_invalid_phone",
        )
    }
    eligible: list[dict] = []

    for opp in opps:
        login_at = datetime.fromisoformat(opp["login_at"])
        due_at = login_at + timedelta(hours=opp["eval_delay_hours"])

        if now_utc < due_at:
            decisions["too_early"] += 1
            continue

        if campaign["ended_at"]:
            end_at = datetime.fromisoformat(campaign["ended_at"])
            if due_at > end_at:
                update_opportunity_status(opp["id"], "expired")
                decisions["expired"] += 1
                continue

        # Pre-dispatch re-check: any play after the sign-in (including after
        # the eval delay) disqualifies this login, so we never SMS someone who
        # already played.
        played_after = [p for p in plays.get(opp["user_id"], []) if login_at < p <= now_utc]
        if played_after:
            update_opportunity_status(opp["id"], "disqualified_played")
            decisions["disqualified_played"] += 1
            continue

        # Cap counts successful sends (interventions), cumulative across campaigns.
        if (
            settings.WELCOME_MAX_MESSAGES > 0
            and get_cap_usage(opp["user_id"]) >= settings.WELCOME_MAX_MESSAGES
            and settings.WELCOME_POST_LIMIT_SUPPRESS
        ):
            update_opportunity_status(opp["id"], "skipped_cap")
            decisions["skipped_cap"] += 1
            continue

        # Cooldown is measured from the last successful send.
        if settings.COOLDOWN_HOURS > 0:
            last_sent = get_last_welcome_sent(opp["user_id"])
            if last_sent:
                last_dt = datetime.fromisoformat(last_sent)
                if now_utc < last_dt + timedelta(hours=settings.COOLDOWN_HOURS):
                    update_opportunity_status(opp["id"], "skipped_cooldown")
                    decisions["skipped_cooldown"] += 1
                    continue

        if not opp["phone_normalized"]:
            update_opportunity_status(opp["id"], "skipped_invalid_phone")
            decisions["skipped_invalid_phone"] += 1
            continue

        eligible.append(opp)

    if not eligible:
        return {"eligible": 0, "sent": 0, "failed": 0, "deferred": 0, "decisions": decisions}

    result = await _send_opportunities(eligible, deadline, cycle_id, campaign)
    result["decisions"] = decisions
    return result


async def _send_opportunities(
    opps: list[dict],
    deadline: datetime,
    cycle_id: str,
    campaign: dict,
) -> dict:
    """Send Welcome SMS for evaluated opportunities.

    Only validated phones reach send_sms (the safety gate already ran). A
    Termii success creates one intervention (consuming a cap slot) and marks
    the opportunity 'sent'; a failure marks it 'failed_send' with no cap
    consumption. If the cycle deadline/cancel hits, leftover opportunities stay
    'created' and are re-evaluated on the next run.
    """
    sem = asyncio.Semaphore(settings.MAX_CONCURRENCY)
    sent = failed = deferred = 0
    sms_records: list[dict] = []

    i = 0
    while i < len(opps):
        if state.cancel_requested or datetime.now() >= deadline:
            deferred += len(opps) - i
            break

        batch = opps[i : i + settings.MAX_CONCURRENCY]
        i += len(batch)

        async def worker(opp: dict):
            if state.cancel_requested or datetime.now() >= deadline:
                return ("deferred", opp)
            async with sem:
                if state.cancel_requested or datetime.now() >= deadline:
                    return ("deferred", opp)
                message = settings.WELCOME_MESSAGE.format(first_name=opp["first_name"] or "User")
                return ("attempted", opp, await send_sms(opp["phone_normalized"], message))

        for outcome in await asyncio.gather(*[worker(o) for o in batch]):
            if outcome[0] == "deferred":
                deferred += 1
                continue
            _, opp, result = outcome
            sent_at = datetime.now(timezone.utc).isoformat()
            if result:
                sent += 1
                sms_records.append({
                    "message_id": result.get("message_id"),
                    "user_id": opp["user_id"],
                    "kind": WELCOME,
                    "phone": opp["phone_normalized"],
                    "status": "sent",
                    "cost": 0,
                    "balance_after": result.get("balance"),
                    "cycle_id": cycle_id,
                    "sent_at": sent_at,
                })
                create_intervention(
                    opportunity_id=opp["id"],
                    campaign_id=campaign["id"],
                    user_id=opp["user_id"],
                    login_at=opp["login_at"],
                    sent_at=sent_at,
                    message_id=result.get("message_id"),
                )
                update_opportunity_status(opp["id"], "sent")
            else:
                failed += 1
                sms_records.append({
                    "user_id": opp["user_id"],
                    "kind": WELCOME,
                    "phone": opp["phone_normalized"],
                    "status": "failed",
                    "cost": 0,
                    "cycle_id": cycle_id,
                    "sent_at": sent_at,
                })
                update_opportunity_status(opp["id"], "failed_send")

    log_sms_batch(sms_records)
    return {"eligible": len(opps), "sent": sent, "failed": failed, "deferred": deferred}


def _attribute_interventions(
    campaign: dict,
    sales_df: pd.DataFrame,
    window_end: str | None = None,
) -> dict:
    """Attribute plays to open interventions within (sent_at, window_end].

    For the active campaign window_end defaults to now; on close it is the
    campaign's ended_at. The earliest qualifying play wins; later plays do not
    change the outcome. Attribution never runs for a non-open intervention.
    """
    opens = get_open_interventions(campaign["id"])
    if not opens:
        return {"open": 0, "responded": 0, "no_change": 0}

    plays = _plays_map(sales_df)
    end = (
        datetime.fromisoformat(window_end)
        if window_end
        else datetime.now(timezone.utc)
    )
    responded = 0
    for inv in opens:
        sent = datetime.fromisoformat(inv["sent_at"])
        matched = [p for p in plays.get(inv["user_id"], []) if sent < p <= end]
        if not matched:
            continue
        play = min(matched)
        record_intervention_response(inv["id"], play.isoformat(), (play - sent).total_seconds())
        responded += 1

    return {"open": len(opens), "responded": responded, "no_change": len(opens) - responded}


def finalize_campaign(campaign_id: int):
    """Terminal pass for a closed campaign.

    Opportunities never evaluated by campaign end expire here (they are not
    carried into the next campaign), open interventions get their final
    attribution pass against plays up to ended_at, and any still-open
    intervention becomes no_response.
    """
    campaign = get_campaign(campaign_id)
    if campaign is None or campaign["status"] != "closed":
        return

    mark_opportunities_expired(campaign_id)
    sales_df = _load_csv(settings.SALES_FILE_PATTERN)
    if not sales_df.empty:
        attribution = _attribute_interventions(campaign, sales_df, campaign["ended_at"])
        print(f"Campaign #{campaign_id} final attribution: {attribution}")
    close_open_interventions(campaign_id)
    print(f"Campaign #{campaign_id} finalized.")


async def run_welcome_pipeline(deadline: datetime, cycle_id: str) -> dict:
    """Feature 1: one welcome cycle against the active campaign.

    Ingests every Login file overlapping the campaign window, evaluates still-
    pending opportunities, sends to the eligible, and attributes plays to open
    interventions. All state lives in the campaign/opportunity/intervention
    tables; repetitions are idempotent.
    """
    campaign = get_active_campaign()
    if campaign is None:
        print("Welcome: no active campaign; start one via /campaign/start.")
        return {
            "message": "No active campaign. Start one via /campaign/start.",
            "sent": 0,
            "count": 0,
        }

    logins_df = _load_login_events()
    regs_df = _load_regs()
    sales_df = _load_csv(settings.SALES_FILE_PATTERN)

    ingested = ingest_opportunities(campaign, logins_df, regs_df)
    print(f"Welcome: campaign #{campaign['id']} — {ingested} new login opportunity(ies).")

    attribution = _attribute_interventions(campaign, sales_df)
    if attribution["responded"]:
        print(f"Welcome attribution: {attribution}")

    evaluation = await _evaluate_campaign(campaign, sales_df, deadline, cycle_id)
    print(f"Welcome evaluation: {evaluation}")

    return {
        "sent": evaluation["sent"],
        "failed": evaluation["failed"],
        "eligible": evaluation["eligible"],
        "deferred": evaluation.get("deferred", 0),
        "decisions": evaluation.get("decisions", {}),
        "attribution": attribution,
        "count": evaluation["sent"],
    }


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
    if kind != INACTIVE:
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