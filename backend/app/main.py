import asyncio
import json
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, File, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core import state
from app.core.config import settings
from app.core.phones import validate_phone
from app.db.database import (
    count_campaign_customers,
    count_sms_logs,
    get_active_campaign,
    get_campaign,
    get_campaign_stats,
    get_cycle_stats,
    get_latest_wallet,
    get_sms_logs,
    get_stats_summary,
    get_wallet_history,
    init_db,
    list_campaign_customers,
    list_campaigns,
    list_files,
)
from app.services.statistics import campaign_statistics, campaign_summaries
from app.integrations.termii import aclose_client, get_balance
from app.services.campaigns import close_campaign, start_campaign
from app.services.ingestion import persist_upload
from app.services.reporting import active_feature_kinds, active_sms_kinds, decorate_campaign
from app.services.settings import (
    apply_persisted_settings,
    list_operator_settings,
    update_setting,
)
from app.services.sms import MANUAL, SmsDispatcher, sync_delivery_statuses
from app.workers.processor import INACTIVE, WELCOME, begin_cycle
from app.workers.watcher import start_watcher
from app.db.settings import seed_settings_from_db


class SMSRequest(BaseModel):
    phone: str = Field(..., min_length=7, description="Recipient phone number")
    message: str = Field(..., min_length=1, max_length=160, description="SMS text")


class CampaignStartRequest(BaseModel):
    name: str | None = Field(None, max_length=200, description="Optional campaign label")


class CampaignCompareRequest(BaseModel):
    campaign_ids: list[int] = Field(
        ..., min_length=1, max_length=10, description="Campaigns to compare (descriptive metrics only)"
    )


class SettingsUpdateRequest(BaseModel):
    key: str = Field(..., description="Setting key from the settings registry")
    value: str = Field(..., description="New value as a string")


def _report_sms_block(sms: dict, today_sms: dict) -> dict:
    """Shape a stats summary for the SMS traffic section of an active report."""
    return {
        "total": sms["total"],
        "sent": sms["sent"],
        "delivered": sms["delivered"],
        "failed": sms["failed"],
        "dnd": sms["dnd"],
        "rejected": sms["rejected"],
        "expired": sms["expired"],
        "deferred": sms["deferred"],
        "total_cost": sms["total_cost"],
        "today": today_sms["total"],
    }


def _check_if_within_time_range() -> bool:
    now = datetime.now().time()
    if now < settings.START_TIME or now > settings.END_TIME:
        print(f"Notification cycle cannot begin outside {settings.START_TIME} to {settings.END_TIME}")
        return False

    print("Notifications can be sent")
    return True


def _trigger(features: set[str] | None = None):
    if not _check_if_within_time_range():
        return {"message": "Notification cycle cannot begin outside the allowed time range."}, 409
    if features is not None and not (features & settings.ENABLED_FEATURES):
        return {"message": "Requested feature is not enabled."}, 409
    started = begin_cycle(features)
    if not started:
        return {"message": "A notification cycle is already running."}, 409
    return {"message": "Notification cycle started."}, 200


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_settings_from_db()
    apply_persisted_settings()
    state.loop = asyncio.get_running_loop()

    watcher_thread = threading.Thread(target=start_watcher, daemon=True)
    watcher_thread.start()

    print("Afrimillions listener started.")
    print(f"  Enabled features: {', '.join(sorted(settings.ENABLED_FEATURES)) or 'none'}")
    print(f"  Sending window: {settings.START_TIME} to {settings.END_TIME}")
    print(f"  Inactivity threshold: {settings.INACTIVITY_HOURS}h")
    print(f"  Welcome evaluation delay: {settings.WELCOME_EVAL_DELAY_HOURS}h")
    print(f"  Welcome max messages: {'unlimited' if settings.WELCOME_MAX_MESSAGES == 0 else settings.WELCOME_MAX_MESSAGES} "
          f"(post-limit suppress: {'on' if settings.WELCOME_POST_LIMIT_SUPPRESS else 'off'})")
    print(f"  Cooldown: {settings.COOLDOWN_HOURS}h")
    print(f"  Max messages: {'unlimited' if settings.MAX_MESSAGES == 0 else settings.MAX_MESSAGES}")
    print(f"  Max concurrent SMS: {settings.MAX_CONCURRENCY}")

    yield

    if state.current_task is not None and not state.current_task.done():
        state.current_task.cancel()
        try:
            await state.current_task
        except (asyncio.CancelledError, Exception):
            pass
    await aclose_client()


app = FastAPI(title="Afrimillions Listener", lifespan=lifespan)


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/sms")
async def send_custom_sms(req: SMSRequest):
    """Manual operator send. The request goes through the same canonical phone
    gate and dispatcher as every other SMS; a send is logged in sms_log."""
    check = validate_phone(req.phone)
    if check.canonical is None:
        return JSONResponse(
            {"message": f"SMS not sent: {check.reason}", "phone": req.phone},
            status_code=400,
        )

    dispatcher = SmsDispatcher(max_concurrency=1)
    outcome = await dispatcher.dispatch(
        phone=req.phone,
        message=req.message,
        kind=MANUAL,
        user_id=req.phone,
    )
    dispatcher.flush()

    if outcome.accepted:
        return {
            "message": "SMS sent.",
            "phone": outcome.phone,
            "response": {"message_id": outcome.message_id, "balance": outcome.balance_after},
        }
    return {"message": "SMS delivery failed.", "phone": outcome.phone}


@app.post("/trigger")
async def trigger():
    message, status = _trigger()
    return message, status


@app.post("/trigger/welcome")
async def trigger_welcome():
    message, status = _trigger({WELCOME})
    return message, status


@app.post("/trigger/inactive")
async def trigger_inactive():
    message, status = _trigger({INACTIVE})
    return message, status


@app.get("/status")
def status():
    running = state.current_task is not None and not state.current_task.done()
    return {"running": running, "cancel_requested": state.cancel_requested}


@app.post("/cancel")
async def cancel():
    if state.current_task is not None and not state.current_task.done():
        state.cancel_requested = True
        state.current_task.cancel()
        return {"message": "Cancellation requested."}
    return {"message": "No cycle running."}


# ---------------------------------------------------------------------------
# Manual CSV uploads
# ---------------------------------------------------------------------------

@app.post("/files")
async def upload_file(file: UploadFile = File(...)):
    """Accept a manually uploaded data CSV.

    The file is validated and parsed at the boundary; unparseable content is
    rejected (nothing written to the data folder). Parseable files are placed
    under a recognized dataset prefix and become visible to the next cycle.
    """
    content = await file.read()
    if not content:
        return JSONResponse({"message": "Uploaded file is empty."}, status_code=400)

    record = persist_upload(file.filename or "upload.csv", content, uploaded_by="operator")

    if record["status"] == "failed":
        return JSONResponse(
            {"message": "File rejected: not a parseable CSV.", "record": record},
            status_code=400,
        )

    return {"message": "File accepted and ingested.", "record": record}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@app.get("/stats")
def stats(
    since: str | None = Query(None, description="Start date YYYY-MM-DD"),
    until: str | None = Query(None, description="End date YYYY-MM-DD"),
):
    """Active aggregate: enabled feature kinds plus manual operator sends."""
    return get_stats_summary(kinds=active_sms_kinds(), since=since, until=until)


@app.get("/stats/welcome")
def stats_welcome(
    since: str | None = Query(None),
    until: str | None = Query(None),
):
    return get_stats_summary(kind="welcome", since=since, until=until)


@app.get("/stats/inactive")
def stats_inactive(
    since: str | None = Query(None),
    until: str | None = Query(None),
):
    return get_stats_summary(kind="inactive", since=since, until=until)


@app.get("/stats/cycles")
def stats_cycles(
    since: str | None = Query(None),
    until: str | None = Query(None),
):
    return get_cycle_stats(since=since, until=until)


@app.get("/stats/wallet")
def stats_wallet():
    return get_wallet_history()


@app.get("/stats/campaigns")
def stats_campaigns(limit: int = Query(50, ge=1, le=1000)):
    """Campaign statistics summaries, newest first (Statistics surface)."""
    return campaign_summaries(limit)


@app.get("/stats/campaigns/{campaign_id}")
def stats_campaign_detail(
    campaign_id: int,
    game_limit: int = Query(10, ge=1, le=200),
):
    """Full statistics report for a single campaign (Statistics surface)."""
    stats = campaign_statistics(campaign_id, game_limit=game_limit)
    if stats is None:
        return JSONResponse({"message": "Campaign not found."}, status_code=404)
    return stats


@app.post("/stats/campaigns/compare")
def stats_campaigns_compare(req: CampaignCompareRequest):
    """Descriptive comparison of selected campaigns, in the requested order.

    Deliberately presentation-neutral: the rows are comparable facts (contacted,
    conversion rate, response time, qualifying plays, attributed play amount,
    SMS cost, cost per conversion, activity/cost ratio). No "best campaign"
    score or winner/loser ranking is produced - interpretation is the
    operator's job.
    """
    summaries = {s["campaign_id"]: s for s in campaign_summaries(limit=1000)}
    return [summaries[cid] for cid in req.campaign_ids if cid in summaries]


@app.get("/stats/balance")
async def stats_balance():
    """Live Termii balance (never stale).

    The dashboard polls this every ~15s and owns its own freshness/error
    rendering, so each request reports the provider's current state instead of
    a cached snapshot.
    """
    info = await get_balance()
    if info is None:
        return {"message": "Failed to retrieve balance from Termii."}
    return info


@app.post("/stats/sync")
async def stats_sync():
    result = await sync_delivery_statuses()
    return result


# ---------------------------------------------------------------------------
# Welcome campaign lifecycle (operator-driven)
# ---------------------------------------------------------------------------

@app.get("/campaign/current")
def campaign_current():
    """The active Welcome campaign and its dashboard numbers, or active: false.

    Campaigns are Welcome-specific; when the Welcome feature is disabled there
    is no active campaign to present as dashboard data.
    """
    if WELCOME not in active_feature_kinds():
        return {"active": False}
    campaign = get_active_campaign()
    if campaign is None:
        return {"active": False}
    return {
        "active": True,
        "campaign": decorate_campaign(campaign),
        "stats": get_campaign_stats(campaign["id"]),
    }


@app.get("/campaigns")
def campaigns(limit: int = Query(50, ge=1, le=1000)):
    """Campaign history (newest first) with per-campaign dashboard numbers.

    Campaigns are Welcome-specific and remain queryable regardless of the
    current feature enablement; every entry carries its feature tag.
    """
    return [
        {
            "campaign": decorate_campaign(c),
            "stats": get_campaign_stats(c["id"]),
        }
        for c in list_campaigns(limit)
    ]


@app.post("/campaign/start")
def campaign_start(req: CampaignStartRequest | None = None):
    """Start a new campaign. Any active campaign is closed at this instant."""
    return start_campaign(req.name if req else None)


@app.post("/campaign/close")
def campaign_close():
    """Close the active campaign, run final attribution, and finalize outcomes."""
    result = close_campaign()
    if result is None:
        return JSONResponse({"message": "No active campaign to close."}, status_code=404)
    return result


# ---------------------------------------------------------------------------
# Operator reporting API (business terminology)
# ---------------------------------------------------------------------------

@app.get("/report/overview")
def report_overview():
    """High-level business numbers scoped to the currently enabled features:
    SMS traffic, the active Welcome campaign, files, and wallet balance.

    Historical records of disabled features are excluded from the active
    aggregates here; they remain queryable through the feature-specific
    endpoints (/stats/welcome, /stats/inactive, /sms/logs?kind=...).
    """
    enabled = active_feature_kinds()
    active_kinds = active_sms_kinds()

    sms = get_stats_summary(kinds=active_kinds)
    today = datetime.now(timezone.utc).date().isoformat()
    today_sms = get_stats_summary(kinds=active_kinds, since=today)

    active = get_active_campaign() if WELCOME in enabled else None
    campaigns = list_campaigns(100)

    features_breakdown = {
        kind: _report_sms_block(
            get_stats_summary(kind=kind),
            get_stats_summary(kind=kind, since=today),
        )
        for kind in sorted(enabled)
    }

    files = list_files(100)

    return {
        "phone_sms": _report_sms_block(sms, today_sms),
        "features": {
            "enabled_features": sorted(enabled),
            "active_kinds": sorted(active_kinds),
            "breakdown": features_breakdown,
        },
        "campaign": {
            "active": active is not None,
            "total_campaigns": len(campaigns),
            "current": get_campaign_stats(active["id"]) if active else None,
            "feature": WELCOME,
        },
        "files": {
            "total": len(files),
            "failed": sum(1 for f in files if f["status"] == "failed"),
            "latest": files[0] if files else None,
        },
        "wallet": get_latest_wallet(),
    }


@app.get("/campaign/{campaign_id}")
def campaign_detail(campaign_id: int):
    """A campaign with its stored configuration snapshot (parsed) and stats.

    Campaign history stays queryable when Welcome is disabled; the feature tag
    identifies these records as Welcome campaign data."""
    campaign = get_campaign(campaign_id)
    if campaign is None:
        return JSONResponse({"message": "Campaign not found."}, status_code=404)
    campaign = decorate_campaign(campaign)
    campaign["config"] = json.loads(campaign["config"]) if campaign.get("config") else None
    return {"campaign": campaign, "stats": get_campaign_stats(campaign_id)}


@app.get("/campaign/{campaign_id}/customers")
def campaign_customers(
    campaign_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
):
    """Paged list of a campaign's customers and their per-login outcome."""
    if get_campaign(campaign_id) is None:
        return JSONResponse({"message": "Campaign not found."}, status_code=404)
    total = count_campaign_customers(campaign_id)
    items = list_campaign_customers(
        campaign_id, limit=page_size, offset=(page - 1) * page_size
    )
    return {
        "campaign_id": campaign_id,
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": -(-total // page_size) if total else 0,
    }


@app.get("/sms/logs")
def sms_logs(
    kind: str | None = Query(None, description="welcome | inactive | manual"),
    since: str | None = Query(None),
    until: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
):
    """Persisted SMS attempts, newest first, with page metadata.

    With no `kind` this is the active SMS activity view: records of the
    currently enabled features plus manual operator sends. An explicit `kind`
    keeps historical/feature-specific inspection available regardless of
    enablement. `enabled_features` is the backend's authoritative scope, so
    the frontend never derives it itself.
    """
    kinds = None if kind else active_sms_kinds()
    total = count_sms_logs(kind=kind, kinds=kinds, since=since, until=until)
    items = get_sms_logs(
        kind=kind, kinds=kinds, since=since, until=until,
        limit=page_size, offset=(page - 1) * page_size,
    )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": -(-total // page_size) if total else 0,
        "enabled_features": sorted(active_feature_kinds()),
    }


@app.get("/files")
def files(limit: int = Query(100, ge=1, le=1000)):
    """Registry of uploaded files (newest first)."""
    return {"items": list_files(limit)}


# ---------------------------------------------------------------------------
# Operator settings
# ---------------------------------------------------------------------------

@app.get("/settings")
def get_settings():
    """Operator settings with current effective values and UI hints."""
    return {"items": list_operator_settings()}


@app.post("/settings")
def post_settings(req: SettingsUpdateRequest):
    """Persist and apply one operator setting. Secrets are not editable here."""
    try:
        change = update_setting(req.key, req.value)
    except KeyError:
        return JSONResponse(
            {"message": f"Unknown setting key: {req.key}"}, status_code=404
        )
    except (ValueError, TypeError):
        return JSONResponse(
            {"message": f"Invalid value: {req.value}"}, status_code=400
        )
    return {"message": "Setting updated.", "setting": change}