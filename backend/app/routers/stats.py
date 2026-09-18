from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.db.sms import get_cycle_stats, get_stats_summary
from app.db.wallet import get_wallet_history
from app.integrations.sms_gateway import get_default_gateway
from app.services.reporting import active_sms_kinds
from app.services.sms import sync_delivery_statuses
from app.services.statistics import campaign_statistics, campaign_summaries


router = APIRouter()


class CampaignCompareRequest(BaseModel):
    campaign_ids: list[int] = Field(
        ..., min_length=1, max_length=10, description="Campaigns to compare (descriptive metrics only)"
    )


@router.get("/stats")
def stats(
    since: str | None = Query(None, description="Start date YYYY-MM-DD"),
    until: str | None = Query(None, description="End date YYYY-MM-DD"),
):
    """Active aggregate: enabled feature kinds plus manual operator sends."""
    return get_stats_summary(kinds=active_sms_kinds(), since=since, until=until)


@router.get("/stats/welcome")
def stats_welcome(
    since: str | None = Query(None),
    until: str | None = Query(None),
):
    return get_stats_summary(kind="welcome", since=since, until=until)


@router.get("/stats/inactive")
def stats_inactive(
    since: str | None = Query(None),
    until: str | None = Query(None),
):
    return get_stats_summary(kind="inactive", since=since, until=until)


@router.get("/stats/cycles")
def stats_cycles(
    since: str | None = Query(None),
    until: str | None = Query(None),
):
    return get_cycle_stats(since=since, until=until)


@router.get("/stats/wallet")
def stats_wallet():
    return get_wallet_history()


@router.get("/stats/campaigns")
def stats_campaigns(limit: int = Query(50, ge=1, le=1000)):
    """Campaign statistics summaries, newest first (Statistics surface)."""
    return campaign_summaries(limit)


@router.get("/stats/campaigns/{campaign_id}")
def stats_campaign_detail(
    campaign_id: int,
    game_limit: int = Query(10, ge=1, le=200),
):
    """Full statistics report for a single campaign (Statistics surface)."""
    stats = campaign_statistics(campaign_id, game_limit=game_limit)
    if stats is None:
        return JSONResponse({"message": "Campaign not found."}, status_code=404)
    return stats


@router.post("/stats/campaigns/compare")
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


@router.get("/stats/balance")
async def stats_balance():
    """Live Termii balance (never stale).

    The dashboard polls this every ~15s and owns its own freshness/error
    rendering, so each request reports the provider's current state instead of
    a cached snapshot.
    """
    info = await get_default_gateway().balance()
    if info is None:
        return {"message": "Failed to retrieve balance from Termii."}
    return info


@router.post("/stats/sync")
async def stats_sync():
    result = await sync_delivery_statuses()
    return result