from datetime import datetime, timezone

from fastapi import APIRouter

from app.core.models import WELCOME
from app.db.campaigns import get_active_campaign, get_campaign_stats, list_campaigns
from app.db.files import list_files
from app.db.sms import get_stats_summary
from app.db.wallet import get_latest_wallet
from app.services.reporting import active_feature_kinds, active_sms_kinds


router = APIRouter()


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


@router.get("/report/overview")
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