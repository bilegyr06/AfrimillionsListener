"""Feature 1: operator-driven welcome-back campaign service.

Model: campaigns (operator windows) -> login opportunities -> interventions.
Cap counts only Termii-accepted sends (interventions); failed sends consume
nothing; phones must pass the canonical phone gate (app.core.phones) before any
dispatch. Dispatch mechanics live in app.services.sms.SmsDispatcher; this
service owns campaign decisions only.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.core import state
from app.core.config import settings
from app.core.models import WELCOME
from app.core.phones import gate_phone
from app.db.database import (
    close_active_campaign,
    close_open_interventions,
    create_campaign,
    create_intervention,
    get_active_campaign,
    get_campaign,
    get_campaign_stats,
    get_cap_usage,
    get_last_welcome_sent,
    get_open_interventions,
    get_open_opportunities,
    mark_opportunities_expired,
    record_intervention_response,
    update_opportunity_status,
    upsert_opportunities,
)
from app.services.ingestion import read_login_events, read_registrations, read_sales
from app.services.settings import build_campaign_config_snapshot
from app.services.sms import SmsDispatcher


# ---------------------------------------------------------------------------
# Campaign lifecycle wrappers (DB + finalize + dashboard numbers)
# ---------------------------------------------------------------------------

def start_campaign(name: str | None = None) -> dict:
    """Start a new campaign; any active campaign is closed at this instant."""
    result = create_campaign(name, config=build_campaign_config_snapshot())
    closed = result["closed_campaign"]
    if closed:
        finalize_campaign(closed["id"])
    return {
        "campaign": result["campaign"],
        "stats": get_campaign_stats(result["campaign"]["id"]),
        "closed_campaign": (
            {"campaign": closed, "stats": get_campaign_stats(closed["id"])}
            if closed
            else None
        ),
    }


def close_campaign() -> dict | None:
    """Close the active campaign, run final attribution, finalize outcomes."""
    closed = close_active_campaign()
    if closed is None:
        return None
    finalize_campaign(closed["id"])
    return {"campaign": closed, "stats": get_campaign_stats(closed["id"])}


# ---------------------------------------------------------------------------
# Data shaping
# ---------------------------------------------------------------------------

def _to_utc_series(series: pd.Series) -> pd.Series:
    """Treat naive CSV timestamps as UTC (app-wide convention) and return aware."""
    parsed = pd.to_datetime(series)
    if parsed.dt.tz is None:
        parsed = parsed.dt.tz_localize("UTC")
    else:
        parsed = parsed.dt.tz_convert("UTC")
    return parsed


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


# ---------------------------------------------------------------------------
# Opportunity ingestion
# ---------------------------------------------------------------------------

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
            "phone_normalized": gate_phone(phone_raw),
            "login_at": ts.isoformat(),
            "eval_delay_hours": settings.WELCOME_EVAL_DELAY_HOURS,
        })

    upsert_opportunities(records)
    return len(records)


# ---------------------------------------------------------------------------
# Evaluation and dispatch
# ---------------------------------------------------------------------------

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

    Only validated phones reach the dispatcher (the safety gate already ran).
    A Termii success creates one intervention (consuming a cap slot) and marks
    the opportunity 'sent'; a failure marks it 'failed_send' with no cap
    consumption. If the cycle deadline/cancel hits, leftover opportunities stay
    'created' and are re-evaluated on the next run; deferred attempts are not
    written to sms_log (the caller keeps no record of an unsent welcome).
    """
    dispatcher = SmsDispatcher()
    sent = failed = deferred = 0

    i = 0
    while i < len(opps):
        if state.cancel_requested or datetime.now() >= deadline:
            deferred += len(opps) - i
            break

        batch = opps[i : i + settings.MAX_CONCURRENCY]
        i += len(batch)

        outcomes = await asyncio.gather(*[
            dispatcher.dispatch(
                phone=opp["phone_normalized"],
                message=settings.WELCOME_MESSAGE.format(first_name=opp["first_name"] or "User"),
                kind=WELCOME,
                user_id=opp["user_id"],
                cycle_id=cycle_id,
                deadline=deadline,
                cancelled=lambda: state.cancel_requested,
            )
            for opp in batch
        ])

        for opp, outcome in zip(batch, outcomes):
            if outcome.status == "deferred":
                deferred += 1
                continue
            if outcome.accepted:
                sent += 1
                create_intervention(
                    opportunity_id=opp["id"],
                    campaign_id=campaign["id"],
                    user_id=opp["user_id"],
                    login_at=opp["login_at"],
                    sent_at=outcome.sent_at,
                    message_id=outcome.message_id,
                )
                update_opportunity_status(opp["id"], "sent")
            else:
                failed += 1
                update_opportunity_status(opp["id"], "failed_send")

    dispatcher.flush()
    return {"eligible": len(opps), "sent": sent, "failed": failed, "deferred": deferred}


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------

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
    sales_df = read_sales()
    if not sales_df.empty:
        attribution = _attribute_interventions(campaign, sales_df, campaign["ended_at"])
        print(f"Campaign #{campaign_id} final attribution: {attribution}")
    close_open_interventions(campaign_id)
    print(f"Campaign #{campaign_id} finalized.")


# ---------------------------------------------------------------------------
# Pipeline entrypoint (one welcome cycle)
# ---------------------------------------------------------------------------

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

    logins_df = read_login_events()
    regs_df = read_registrations()
    sales_df = read_sales()

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