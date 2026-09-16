"""Canonical campaign statistics for the operator Statistics surface.

This module is the single source of truth for the aggregations behind
GET /stats/campaigns and GET /stats/campaigns/{id}. It always reads already-
aggregated rows from app.db.database (which in turn only runs GROUP BY /
COUNT(DISTINCT) SQL) - it never pulls per-customer rows into the UI and it
never rescans downloaded CSVs.

Terminology is strict and stable:
  opportunities         welcome_opportunities rows (one login inside the window)
  unique_customers      distinct user_id across opportunities
  accepted              welcome_interventions rows (Termii-accepted Welcome SMS)
  contacted_customers   distinct user_id across interventions
  delivered             accepted sends whose Termii status is 'delivered'
  converted_customers   distinct user_id with a responded intervention
  conversion_events     responded interventions. Each is a customer's *first*
                        qualifying play.
  conversion_rate       converted customers / contacted customers (0.0 valid)
  cost_per_contacted    total SMS cost / distinct contacted customers
  cost_per_conversion   total SMS cost / distinct converted customers
  qualifying_plays      every customer play inside the attribution window.
                        Requires play-level persistence (the plays table); the
                        activity group below is the total-play-volume view.
  players               distinct customers with at least one qualifying play
  repeat_players        distinct players with two or more qualifying plays

The attribution window opens at a customer's first accepted Welcome SMS in the
campaign (per-campaign first sent_at) and closes at ended_at for closed
campaigns or the instant the report is built while active. A play qualifies
when it lies strictly inside that window; plays at or before the SMS are never
converted, and plays after a closed campaign's ended_at are reported as
after-window context.

Money/rate convention: a rate or unit cost whose denominator is zero is
reported as None ("unavailable"), never as a misleading 0.0. Plain counts such
as converted_customers and conversion_rate genuinely are zero and report 0.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.models import WELCOME
from app.db.database import (
    get_campaign,
    get_campaign_audience,
    get_campaign_game_stats,
    get_campaign_intervention_counts,
    get_campaign_play_edges,
    get_campaign_play_stats,
    get_campaign_response_plays,
    get_campaign_sms_funnel,
    get_campaigns_audience,
    get_campaigns_interventions,
    get_campaigns_play_summary,
    get_campaigns_sms_funnel,
    get_converted_customers,
    list_campaigns,
)

#: Opportunity statuses that mean "identified inside the window but no Welcome
#: SMS was accepted" - the audience the campaign did not actually reach.
_NOT_SENT_STATUSES = (
    "disqualified_played",
    "skipped_cap",
    "skipped_cooldown",
    "skipped_invalid_phone",
    "failed_send",
    "expired",
)

#: Time-to-first-play distribution buckets, in seconds. Boundaries follow the
#: reporting contract: under 1 h, 1-6 h, 6-12 h, 12-24 h, 24 h or more.
_RESPONSE_BUCKETS = (
    ("lt_1h", lambda s: s < 60 * 60),
    ("1h_to_6h", lambda s: 60 * 60 <= s < 6 * 60 * 60),
    ("6h_to_12h", lambda s: 6 * 60 * 60 <= s < 12 * 60 * 60),
    ("12h_to_24h", lambda s: 12 * 60 * 60 <= s < 24 * 60 * 60),
    ("ge_24h", lambda s: s >= 24 * 60 * 60),
)


def _percentile(sorted_seconds: list[float], pct: float) -> float | None:
    """Nearest-rank percentile of pre-sorted seconds (0 <= pct <= 1).

    Sub-percentile positions round to the nearest value, so small samples stay
    meaningful: p25/p75 on a two-point sample report the two endpoints and the
    median reports their average.
    """
    if not sorted_seconds:
        return None
    idx = min(len(sorted_seconds) - 1, int(round(pct * (len(sorted_seconds) - 1))))
    return round(sorted_seconds[idx], 1)


def _median(sorted_seconds: list[float]) -> float | None:
    if not sorted_seconds:
        return None
    n = len(sorted_seconds)
    return round((sorted_seconds[n // 2] + sorted_seconds[(n - 1) // 2]) / 2, 1)


def _response_metrics(plays: list[dict]) -> dict:
    """Timing aggregates over first-qualifying-play records (all responded)."""
    seconds = sorted(float(p["response_seconds"]) for p in plays if p.get("response_seconds") is not None)
    first_at = min((p["play_at"] for p in plays if p.get("play_at")), default=None)
    counts = {"count": len(seconds)}
    if seconds:
        counts.update(
            {
                "avg": round(sum(seconds) / len(seconds), 1),
                "median": _median(seconds),
                "p25": _percentile(seconds, 0.25),
                "p75": _percentile(seconds, 0.75),
                "min": round(seconds[0], 1),
                "max": round(seconds[-1], 1),
            }
        )
    else:
        counts.update(
            {
                "avg": None,
                "median": None,
                "p25": None,
                "p75": None,
                "min": None,
                "max": None,
            }
        )
    buckets = {name: sum(1 for s in seconds if pred(s)) for name, pred in _RESPONSE_BUCKETS}
    return {
        "first_qualifying_play_at": first_at,
        "time_to_first_play": counts,
        "buckets": buckets,
    }


def _activity_metrics(
    play_stats: list[dict],
    converted: set[str],
    edges: dict,
) -> dict:
    """Play-volume aggregates over per-customer qualifying-play rows.

    play_stats is one row per engaged customer ({user_id, play_count, amount}),
    so the arithmetic below is constant-size regardless of play volume.
    Per-customer rates keep the zero-denominator -> None convention; plain
    counts are genuine zeros.
    """
    players = len(play_stats)
    qualifying_plays = sum(int(r["play_count"]) for r in play_stats)
    repeat_players = sum(1 for r in play_stats if int(r["play_count"]) >= 2)
    converted_players = sum(1 for r in play_stats if r["user_id"] in converted)
    total_amount = round(sum(float(r["amount"]) for r in play_stats), 2)
    max_plays = max((int(r["play_count"]) for r in play_stats), default=0)
    return {
        "qualifying_plays": qualifying_plays,
        "players": players,
        "repeat_players": repeat_players,
        "converted_players": converted_players,
        "avg_plays_per_player": (
            round(qualifying_plays / players, 2) if players else None
        ),
        "repeat_rate": round(repeat_players / players, 4) if players else None,
        "max_plays_per_player": max_plays,
        "total_play_amount": total_amount,
        "avg_play_amount": (
            round(total_amount / qualifying_plays, 2) if qualifying_plays else None
        ),
        "before_sms": int(edges.get("before_sms", 0)),
        "after_window": int(edges.get("after_end", 0)),
    }


def _attribution_end(campaign: dict) -> str:
    """ISO instant to which a campaign's qualifying window extends."""
    if campaign.get("ended_at"):
        return campaign["ended_at"]
    return datetime.now(timezone.utc).isoformat()


def _activity_group(campaign: dict, game_limit: int = 10) -> dict:
    """The persistent activity + per-game projection for one campaign."""
    window_end = _attribution_end(campaign)
    play_stats = get_campaign_play_stats(campaign["id"], window_end)
    converted = get_converted_customers(campaign["id"])
    edges = get_campaign_play_edges(campaign["id"], window_end)
    games = get_campaign_game_stats(campaign["id"], window_end, limit=game_limit)
    return {
        "window_end": window_end,
        "metrics": _activity_metrics(play_stats, converted, edges),
        "games": games,
    }


def campaign_statistics(campaign_id: int, game_limit: int = 10) -> dict | None:
    """Full statistics report for a single campaign (None if it does not exist).

    The report is a pure projection of the database state: no campaign is ever
    auto-created here, and attribution follows the existing rules unchanged
    (first qualifying play after acceptance, within the campaign window).
    game_limit bounds the included per-game ranking (the games list is the only
    play-level data the report carries).
    """
    campaign = get_campaign(campaign_id)
    if campaign is None:
        return None

    audience = get_campaign_audience(campaign_id)
    interventions = get_campaign_intervention_counts(campaign_id)
    funnel = get_campaign_sms_funnel(campaign_id)
    response = _response_metrics(get_campaign_response_plays(campaign_id))
    activity = _activity_group(campaign, game_limit=game_limit)

    contacted = interventions["contacted_customers"]
    converted = interventions["converted_customers"]
    rate = round(converted / contacted, 4) if contacted else 0.0
    delivery_rate = round(funnel["delivered"] / funnel["accepted"], 4) if funnel["accepted"] else None
    avg_cost = round(funnel["cost"] / funnel["accepted"], 2) if funnel["accepted"] else None
    cost_per_contacted = round(funnel["cost"] / contacted, 2) if contacted else None
    cost_per_conversion = round(funnel["cost"] / converted, 2) if converted else None

    statuses = audience["statuses"]
    not_sent_to = {
        status: statuses.get(status, {"rows": 0})["rows"] for status in _NOT_SENT_STATUSES
    }

    return {
        "campaign": {
            "id": campaign["id"],
            "name": campaign.get("name"),
            "status": campaign["status"],
            "feature": WELCOME,
            "started_at": campaign["started_at"],
            "ended_at": campaign.get("ended_at"),
            "created_at": campaign["created_at"],
        },
        "window": {
            "started_at": campaign["started_at"],
            "ended_at": campaign.get("ended_at"),
            "attribution_end": campaign.get("ended_at"),
            "description": (
                "Plays are attributed to (Welcome-SMS sent, ended_at] for closed "
                "campaigns and (Welcome-SMS sent, now] while a campaign is active. "
                "A customer's first qualifying play is the conversion event."
            ),
        },
        "audience": {
            "opportunities": audience["opportunities"],
            "unique_customers": audience["unique_customers"],
            "pending_evaluation": statuses.get("created", {"rows": 0})["rows"],
            "not_sent_to": not_sent_to,
            "statuses": statuses,
        },
        # The conceptual funnel. Stages are deliberately heterogeneous: the
        # funnel starts from opportunities (login events), narrows to distinct
        # customers, then to accepted SESSION sends (interventions), then to
        # delivery outcomes, then to distinct converted customers. A customer
        # can appear at more than one stage, so these are NOT strict 1:1 drops.
        "funnel": {
            "opportunities": audience["opportunities"],
            "unique_customers": audience["unique_customers"],
            "accepted": funnel["accepted"],
            "delivered": funnel["delivered"],
            "converted_customers": interventions["converted_customers"],
        },
        "sms": {
            "accepted": funnel["accepted"],
            "contacted_customers": funnel["contacted_customers"],
            "unmatched": funnel["unmatched"],
            "delivered": funnel["delivered"],
            "failed": funnel["failed"],
            "rejected": funnel["rejected"],
            "expired": funnel["expired"],
            "dnd": funnel["dnd"],
            "deferred": funnel["deferred"],
            "sent_awaiting_delivery": funnel["sent"],
            "delivery_rate": delivery_rate,
            "cost": round(funnel["cost"], 2),
            "avg_cost_per_accepted": avg_cost,
        },
        "response": {
            "accepted_sms": interventions["accepted"],
            "contacted_customers": interventions["contacted_customers"],
            "converted_customers": interventions["converted_customers"],
            "conversion_events": interventions["conversion_events"],
            "not_converted_customers": max(
                0,
                interventions["contacted_customers"] - interventions["converted_customers"],
            ),
            "conversion_rate": rate,
            "pending_outcome": interventions["pending_outcome"],
            "first_qualifying_play_at": response["first_qualifying_play_at"],
            "time_to_first_play": response["time_to_first_play"],
            "buckets": response["buckets"],
        },
        "activity": {**activity["metrics"], "window_end": activity["window_end"]},
        "games": activity["games"],
        "economics": {
            "sms_cost": round(funnel["cost"], 2),
            "avg_cost_per_accepted": avg_cost,
            "cost_per_contacted": cost_per_contacted,
            "cost_per_conversion": cost_per_conversion,
        },
    }


def campaign_summaries(limit: int = 50) -> list[dict]:
    """Compact statistics for every campaign, newest first.

    The front-end overview intentionally reuses the same SQL aggregations (via
    the *_campaigns batch functions) instead of calling the per-campaign path
    in a loop, so there is no N+1 query cost.
    """
    audience_map = get_campaigns_audience()
    interventions_map = get_campaigns_interventions()
    funnel_map = get_campaigns_sms_funnel()
    play_summary_map = get_campaigns_play_summary(
        datetime.now(timezone.utc).isoformat()
    )

    summaries: list[dict] = []
    for campaign in list_campaigns(limit):
        campaign_id = campaign["id"]
        audience = audience_map.get(campaign_id, {"opportunities": 0, "unique_customers": 0})
        interventions = interventions_map.get(
            campaign_id,
            {
                "accepted": 0,
                "contacted_customers": 0,
                "converted_customers": 0,
                "conversion_events": 0,
                "still_open": 0,
                "avg_response_seconds": None,
            },
        )
        funnel = funnel_map.get(
            campaign_id,
            {"accepted": 0, "contacted_customers": 0, "delivered": 0, "deferred": 0, "cost": 0},
        )
        plays = play_summary_map.get(
            campaign_id,
            {"qualifying_plays": 0, "players": 0, "total_amount": 0.0},
        )
        contacted = interventions["contacted_customers"]
        converted = interventions["converted_customers"]
        rate = round(converted / contacted, 4) if contacted else 0.0
        cost = round(funnel["cost"], 2)
        summaries.append(
            {
                "campaign_id": campaign_id,
                "name": campaign.get("name"),
                "status": campaign["status"],
                "feature": WELCOME,
                "started_at": campaign["started_at"],
                "ended_at": campaign.get("ended_at"),
                "audience": {
                    "opportunities": audience["opportunities"],
                    "unique_customers": audience["unique_customers"],
                },
                "sms": {
                    "accepted": funnel["accepted"],
                    "contacted_customers": interventions["contacted_customers"],
                    "delivered": funnel["delivered"],
                    "deferred": funnel["deferred"],
                    "cost": cost,
                },
                "response": {
                    "converted_customers": converted,
                    "conversion_rate": rate,
                    "avg_response_seconds": interventions["avg_response_seconds"],
                    "still_pending": interventions["still_open"],
                },
                "activity": {
                    "qualifying_plays": int(plays["qualifying_plays"]),
                    "players": int(plays["players"]),
                    "total_play_amount": round(float(plays["total_amount"]), 2),
                },
                "economics": {
                    "cost_per_contacted": round(cost / contacted, 2) if contacted else None,
                    "cost_per_conversion": round(cost / converted, 2) if converted else None,
                },
            }
        )
    return summaries