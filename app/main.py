import asyncio
from datetime import datetime
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import state
from app.config import settings
from app.database import (
    close_active_campaign,
    create_campaign,
    get_active_campaign,
    get_campaign_stats,
    get_cycle_stats,
    get_sms_logs,
    get_stats_summary,
    get_wallet_history,
    init_db,
    list_campaigns,
)
from app.messager import aclose_client, send_sms
from app.processor import finalize_campaign, begin_cycle, WELCOME, INACTIVE
from app.stats_updater import sync_delivery_statuses
from app.termii_insights import get_balance
from app.watcher import start_watcher


class SMSRequest(BaseModel):
    phone: str = Field(..., min_length=7, description="Recipient phone number")
    message: str = Field(..., min_length=1, max_length=160, description="SMS text")


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


app = FastAPI(title="Afrimillions Inactivity Listener", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/sms")
async def send_custom_sms(req: SMSRequest):
    result = await send_sms(req.phone, req.message)
    if result:
        return {"message": "SMS sent.", "phone": req.phone, "response": result}
    return {"message": "SMS delivery failed.", "phone": req.phone}


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
# Stats endpoints
# ---------------------------------------------------------------------------

@app.get("/stats")
def stats(
    since: str | None = Query(None, description="Start date YYYY-MM-DD"),
    until: str | None = Query(None, description="End date YYYY-MM-DD"),
):
    return get_stats_summary(since=since, until=until)


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


@app.get("/stats/balance")
async def stats_balance():
    info = await get_balance()
    if info is None:
        return {"message": "Failed to retrieve balance from Termii."}
    return info


@app.post("/stats/sync")
async def stats_sync():
    result = await sync_delivery_statuses()
    return result


# ---------------------------------------------------------------------------
# Welcome campaign endpoints (operator-driven lifecycle)
# ---------------------------------------------------------------------------

class CampaignStartRequest(BaseModel):
    name: str | None = Field(None, max_length=200, description="Optional campaign label")


@app.get("/campaign/current")
def campaign_current():
    """The active campaign and its dashboard numbers, or active: false."""
    campaign = get_active_campaign()
    if campaign is None:
        return {"active": False}
    return {"active": True, "campaign": campaign, "stats": get_campaign_stats(campaign["id"])}


@app.get("/campaigns")
def campaigns(limit: int = Query(50, ge=1, le=1000)):
    """Campaign list (newest first) with per-campaign dashboard numbers."""
    return [
        {"campaign": c, "stats": get_campaign_stats(c["id"])}
        for c in list_campaigns(limit)
    ]


@app.post("/campaign/start")
def campaign_start(req: CampaignStartRequest | None = None):
    """Start a new campaign. Any active campaign is closed at this instant."""
    result = create_campaign(req.name if req else None)
    closed = result["closed_campaign"]
    if closed:
        finalize_campaign(closed["id"])
    return {
        "campaign": result["campaign"],
        "stats": get_campaign_stats(result["campaign"]["id"]),
        "closed_campaign": (
            {"campaign": closed, "stats": get_campaign_stats(closed["id"])} if closed else None
        ),
    }


@app.post("/campaign/close")
def campaign_close():
    """Close the active campaign, run final attribution, and finalize outcomes."""
    closed = close_active_campaign()
    if closed is None:
        return JSONResponse({"message": "No active campaign to close."}, status_code=404)
    finalize_campaign(closed["id"])
    return {"campaign": closed, "stats": get_campaign_stats(closed["id"])}