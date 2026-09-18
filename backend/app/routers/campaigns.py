import json

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.db.campaigns import (
    count_campaign_customers,
    get_active_campaign,
    get_campaign,
    get_campaign_stats,
    list_campaign_customers,
    list_campaigns,
)
from app.core.models import WELCOME
from app.services.campaigns import close_campaign, start_campaign
from app.services.reporting import active_feature_kinds, decorate_campaign


router = APIRouter()


class CampaignStartRequest(BaseModel):
    name: str | None = Field(None, max_length=200, description="Optional campaign label")


@router.get("/campaign/current")
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


@router.get("/campaigns")
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


@router.post("/campaign/start")
def campaign_start(req: CampaignStartRequest | None = None):
    """Start a new campaign. Any active campaign is closed at this instant."""
    return start_campaign(req.name if req else None)


@router.post("/campaign/close")
def campaign_close():
    """Close the active campaign, run final attribution, and finalize outcomes."""
    result = close_campaign()
    if result is None:
        return JSONResponse({"message": "No active campaign to close."}, status_code=404)
    return result


@router.get("/campaign/{campaign_id}")
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


@router.get("/campaign/{campaign_id}/customers")
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