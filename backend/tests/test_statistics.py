"""Tests for the campaign statistics reporting contract (Phase 1 + Phase 2).

The Statistics surface (/stats/campaigns, /stats/campaigns/{id}) must always
aggregate only in SQL and stick to the stable terminology in
app.services.statistics: opportunities (logins) vs unique customers vs
accepted vs contacted vs converted, with 0 as a valid rate but None for rates
whose denominator is zero (zero vs unavailable). Phase 2 adds the persisted
activity group (qualifying plays from the plays table) and the per-game
ranking.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("TERMII_API_KEY", "test-key")
os.environ.setdefault("TERMII_BASE_URL", "https://test.api.termii.com/api")
os.environ.setdefault("TERMII_SENDER_ID", "TestSender")

from fastapi.testclient import TestClient

from app.db.campaigns import (
    count_campaign_customers,
    create_campaign,
    create_intervention,
    list_campaign_customers,
    record_intervention_response,
    update_opportunity_status,
    upsert_opportunities,
)
from app.db.database import get_connection
from app.db.players import insert_play_records
from app.db.sms import log_sms

from app.main import app
from app.services.plays import play_source_key
from app.services.statistics import campaign_statistics, campaign_summaries

CAMP_START = "2026-01-01T00:00:00+00:00"
CAMP_END = "2026-01-05T00:00:00+00:00"
SENT_AT = "2026-01-02T08:00:00+00:00"
LOGIN_AT = "2026-01-02T07:00:00+00:00"

EMPTY_BUCKETS = {
    "lt_1h": 0,
    "1h_to_6h": 0,
    "6h_to_12h": 0,
    "12h_to_24h": 0,
    "ge_24h": 0,
}


def _start_campaign(closed: bool = False) -> dict:
    result = create_campaign("Statistics test")
    campaign = result["campaign"]
    conn = get_connection()
    conn.execute(
        "UPDATE welcome_campaigns SET started_at = ?, ended_at = ?, status = ? WHERE id = ?",
        (
            CAMP_START,
            CAMP_END if closed else None,
            "closed" if closed else "active",
            campaign["id"],
        ),
    )
    conn.commit()
    conn.close()
    return campaign


def _seed_opportunity(campaign_id: int, user_id: str, status: str, login_at: str = LOGIN_AT) -> dict:
    upsert_opportunities([
        {
            "campaign_id": campaign_id,
            "user_id": user_id,
            "first_name": "Ada",
            "phone_raw": "08012345678",
            "phone_normalized": "2348012345678",
            "login_at": login_at,
            "eval_delay_hours": 1,
        }
    ])
    conn = get_connection()
    row = conn.execute(
        "SELECT id, login_at FROM welcome_opportunities "
        "WHERE campaign_id = ? AND user_id = ? AND login_at = ?",
        (campaign_id, user_id, login_at),
    ).fetchone()
    conn.close()
    if status != "created":
        update_opportunity_status(row["id"], status)
    return {"id": row["id"], "login_at": row["login_at"]}


def _seed_intervention(campaign_id: int, user_id: str, message_id: str, login_at: str = LOGIN_AT) -> int:
    opp = _seed_opportunity(campaign_id, user_id, "sent", login_at)
    return create_intervention(
        opportunity_id=opp["id"],
        campaign_id=campaign_id,
        user_id=user_id,
        login_at=opp["login_at"],
        sent_at=SENT_AT,
        message_id=message_id,
    )


def _seed_delivery(message_id: str, user_id: str, status: str, cost: float):
    log_sms({
        "message_id": message_id,
        "user_id": user_id,
        "kind": "welcome",
        "phone": "2348012345678",
        "status": status,
        "cost": cost,
        "balance_after": 1000.0,
        "cycle_id": "c1",
        "sent_at": SENT_AT,
    })


def _respond(campaign_id: int, message_id: str, seconds: float):
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM welcome_interventions WHERE message_id = ?", (message_id,)
    ).fetchone()
    conn.close()
    play_at = (datetime.fromisoformat(SENT_AT) + timedelta(seconds=seconds)).isoformat()
    record_intervention_response(row["id"], play_at, seconds)


def _seed_play(user_id: str, game: str, amount: float, played_at: str):
    """Persist one play row (the Phase 2 activity source)."""
    insert_play_records([
        {
            "user_id": user_id,
            "played_at": played_at,
            "game_name": game,
            "amount": amount,
            "source_file": "Sales_test.csv",
            "source_key": play_source_key(user_id, played_at, game, amount),
        }
    ])


def _set_intervention_status(campaign_id: int, message_id: str, status: str):
    conn = get_connection()
    conn.execute(
        "UPDATE welcome_interventions SET status = ? WHERE message_id = ?",
        (status, message_id),
    )
    conn.commit()
    conn.close()


def _active_campaign_with_results() -> dict:
    """Campaign with accepted sends in every response state plus drops.

    Responded: u1 (10 min) and u2 (2 h); no_response: u3; open: u4.
    Opportunities never sent to: played/cap/invalid-phone/failed/expired.
    """
    campaign = _start_campaign()

    _seed_intervention(campaign["id"], "u1", "m_1")
    _seed_intervention(campaign["id"], "u2", "m_2")
    _seed_intervention(campaign["id"], "u3", "m_3")
    _seed_intervention(campaign["id"], "u4", "m_4")

    _set_intervention_status(campaign["id"], "m_3", "no_response")

    _respond(campaign["id"], "m_1", 600)
    _respond(campaign["id"], "m_2", 7200)

    for user_id, status in {
        "u5": "disqualified_played",
        "u6": "skipped_cap",
        "u7": "skipped_invalid_phone",
        "u8": "failed_send",
    }.items():
        _seed_opportunity(campaign["id"], user_id, status)
    # u9 stays 'created' (pending evaluation); u10 expires with the campaign.
    _seed_opportunity(campaign["id"], "u9", "created")
    _seed_opportunity(campaign["id"], "u10", "expired")

    for message_id, user_id, status in (
        ("m_1", "u1", "delivered"),
        ("m_2", "u2", "delivered"),
        ("m_3", "u3", "delivered"),
        ("m_4", "u4", "sent"),
    ):
        _seed_delivery(message_id, user_id, status, 0.9)

    return campaign


@pytest.fixture()
def stats_fixture(_init_db):
    return {"campaign": _active_campaign_with_results()}


class TestCampaignStatisticsService:
    def test_detail_audience_payload(self, stats_fixture):
        report = campaign_statistics(stats_fixture["campaign"]["id"])
        audience = report["audience"]
        assert audience["opportunities"] == 10
        assert audience["unique_customers"] == 10
        assert audience["pending_evaluation"] == 1
        assert audience["not_sent_to"] == {
            "disqualified_played": 1,
            "skipped_cap": 1,
            "skipped_cooldown": 0,
            "skipped_invalid_phone": 1,
            "failed_send": 1,
            "expired": 1,
        }

    def test_detail_funnel(self, stats_fixture):
        funnel = campaign_statistics(stats_fixture["campaign"]["id"])["funnel"]
        assert funnel == {
            "opportunities": 10,
            "unique_customers": 10,
            "accepted": 4,
            "delivered": 3,
            "converted_customers": 2,
        }

    def test_detail_sms_funnel(self, stats_fixture):
        sms = campaign_statistics(stats_fixture["campaign"]["id"])["sms"]
        assert sms["accepted"] == 4
        assert sms["contacted_customers"] == 4
        assert sms["delivered"] == 3
        assert sms["sent_awaiting_delivery"] == 1
        assert sms["unmatched"] == 0
        assert sms["deferred"] == 0
        assert sms["failed"] == sms["rejected"] == sms["dnd"] == sms["expired"] == 0
        assert sms["delivery_rate"] == pytest.approx(0.75)
        assert sms["cost"] == pytest.approx(3.6)
        assert sms["avg_cost_per_accepted"] == pytest.approx(0.9)

    def test_detail_response_metrics(self, stats_fixture):
        response = campaign_statistics(stats_fixture["campaign"]["id"])["response"]
        assert response["accepted_sms"] == 4
        assert response["contacted_customers"] == 4
        assert response["converted_customers"] == 2
        assert response["conversion_events"] == 2
        assert response["not_converted_customers"] == 2
        assert response["pending_outcome"] == 1
        assert response["conversion_rate"] == pytest.approx(0.5)
        timing = response["time_to_first_play"]
        assert timing == {
            "count": 2,
            "avg": pytest.approx(3900.0),
            "median": pytest.approx(3900.0),
            "p25": pytest.approx(600.0),
            "p75": pytest.approx(7200.0),
            "min": pytest.approx(600.0),
            "max": pytest.approx(7200.0),
        }
        assert response["buckets"] == {
            **EMPTY_BUCKETS,
            "lt_1h": 1,
            "1h_to_6h": 1,
        }
        # No plays are persisted in this fixture, so the activity volume is
        # genuinely zero and the per-engagement rates are None (unavailable).
        assert response["conversion_events"] == 2
        activity = campaign_statistics(stats_fixture["campaign"]["id"])["activity"]
        assert activity["qualifying_plays"] == 0
        assert activity["players"] == 0
        assert activity["total_play_amount"] == 0.0
        assert activity["avg_plays_per_player"] is None
        assert activity["avg_play_amount"] is None

    def test_detail_economics(self, stats_fixture):
        economics = campaign_statistics(stats_fixture["campaign"]["id"])["economics"]
        assert economics == {
            "sms_cost": pytest.approx(3.6),
            "avg_cost_per_accepted": pytest.approx(0.9),
            "cost_per_contacted": pytest.approx(0.9),
            "cost_per_conversion": pytest.approx(1.8),
            # No plays are persisted, so play-amount economics are genuine
            # zeros (both denominators exist), not None.
            "total_play_amount": pytest.approx(0.0),
            "play_amount_per_converted": pytest.approx(0.0),
            "play_amount_per_contacted": pytest.approx(0.0),
            "activity_cost_ratio": pytest.approx(0.0),
        }

    def test_unknown_campaign_is_none(self, stats_fixture):
        assert campaign_statistics(99999) is None

    def test_empty_campaign_returns_valid_zeros(self, _init_db):
        campaign = create_campaign("Empty")["campaign"]
        report = campaign_statistics(campaign["id"])
        assert report["audience"]["opportunities"] == 0
        assert report["response"]["contacted_customers"] == 0
        assert report["response"]["conversion_rate"] == 0.0
        assert report["response"]["buckets"] == EMPTY_BUCKETS
        # Rates with a zero denominator are unavailable, not misleading zeros.
        assert report["sms"]["delivery_rate"] is None
        assert report["sms"]["avg_cost_per_accepted"] is None
        assert report["economics"]["cost_per_contacted"] is None
        assert report["economics"]["cost_per_conversion"] is None

    def test_summaries_newest_first_and_aggregated(self, stats_fixture, _init_db):
        empty = create_campaign("Empty")["campaign"]
        summaries = campaign_summaries()
        ids = [s["campaign_id"] for s in summaries]
        assert ids[0] == empty["id"]
        assert ids[1] == stats_fixture["campaign"]["id"]

        row = summaries[1]
        assert row["audience"]["opportunities"] == 10
        assert row["audience"]["unique_customers"] == 10
        assert row["sms"] == {
            "accepted": 4,
            "contacted_customers": 4,
            "delivered": 3,
            "deferred": 0,
            "cost": pytest.approx(3.6),
        }
        assert row["response"]["converted_customers"] == 2
        assert row["response"]["conversion_rate"] == pytest.approx(0.5)
        assert row["response"]["avg_response_seconds"] == pytest.approx(3900.0)
        assert row["response"]["still_pending"] == 1
        assert row["economics"] == {
            "sms_cost": pytest.approx(3.6),
            "avg_cost_per_accepted": pytest.approx(0.9),
            "cost_per_contacted": pytest.approx(0.9),
            "cost_per_conversion": pytest.approx(1.8),
            "total_play_amount": pytest.approx(0.0),
            "play_amount_per_converted": pytest.approx(0.0),
            "play_amount_per_contacted": pytest.approx(0.0),
            "activity_cost_ratio": pytest.approx(0.0),
        }


class TestOpportunityVsCustomerCounting:
    def test_multiple_opportunities_one_customer(self, _init_db):
        campaign = _start_campaign()
        # Two accepted sends for the same customer from two separate logins.
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_intervention(campaign["id"], "u1", "m_2", login_at="2026-01-02T09:30:00+00:00")
        _seed_delivery("m_1", "u1", "delivered", 0.9)
        _seed_delivery("m_2", "u1", "delivered", 0.9)
        _respond(campaign["id"], "m_1", 600)

        report = campaign_statistics(campaign["id"])
        assert report["audience"]["opportunities"] == 2
        assert report["audience"]["unique_customers"] == 1
        assert report["funnel"]["accepted"] == 2
        assert report["sms"]["delivered"] == 2
        assert report["response"]["contacted_customers"] == 1
        assert report["response"]["converted_customers"] == 1
        assert report["response"]["conversion_events"] == 1
        assert report["response"]["conversion_rate"] == pytest.approx(1.0)

    def test_opportunities_and_customers_differ(self, _init_db):
        campaign = _start_campaign()
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_intervention(campaign["id"], "u1", "m_2", login_at="2026-01-02T09:30:00+00:00")
        _seed_opportunity(campaign["id"], "u2", "disqualified_played")

        report = campaign_statistics(campaign["id"])
        assert report["audience"]["opportunities"] == 3
        assert report["audience"]["unique_customers"] == 2


class TestSmsStatusBreakdown:
    def test_deferred_status_counted(self, _init_db):
        campaign = _start_campaign()
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_delivery("m_1", "u1", "deferred", 0.0)

        sms = campaign_statistics(campaign["id"])["sms"]
        assert sms["deferred"] == 1
        assert sms["delivered"] == 0
        # All delivery outcomes must sum to accepted sends.
        outcomes = (
            sms["delivered"] + sms["failed"] + sms["rejected"] + sms["dnd"]
            + sms["expired"] + sms["deferred"] + sms["sent_awaiting_delivery"]
        )
        assert outcomes == sms["accepted"]

    def test_full_status_breakdown(self, _init_db):
        campaign = _start_campaign()
        for user, (mid, status) in {
            "u1": ("m_1", "delivered"),
            "u2": ("m_2", "failed"),
            "u3": ("m_3", "rejected"),
            "u4": ("m_4", "dnd"),
            "u5": ("m_5", "expired"),
            "u6": ("m_6", "deferred"),
            "u7": ("m_7", "sent"),
        }.items():
            _seed_intervention(campaign["id"], user, mid)
            _seed_delivery(mid, user, status, 0.9)

        sms = campaign_statistics(campaign["id"])["sms"]
        assert sms["accepted"] == 7
        assert sms["delivered"] == 1
        assert sms["failed"] == 1
        assert sms["rejected"] == 1
        assert sms["dnd"] == 1
        assert sms["expired"] == 1
        assert sms["deferred"] == 1
        assert sms["sent_awaiting_delivery"] == 1


class TestResponseTimeCalculations:
    def test_odd_sample_median(self, _init_db):
        campaign = _start_campaign()
        for user, mid, seconds in (
            ("u1", "m_1", 600),       # 10 min
            ("u2", "m_2", 7200),      # 2 h
            ("u3", "m_3", 90000),     # 25 h
        ):
            _seed_intervention(campaign["id"], user, mid)
            _seed_delivery(mid, user, "delivered", 0.9)
            _respond(campaign["id"], mid, seconds)

        timing = campaign_statistics(campaign["id"])["response"]["time_to_first_play"]
        assert timing["count"] == 3
        assert timing["median"] == pytest.approx(7200.0)
        assert timing["avg"] == pytest.approx((600 + 7200 + 90000) / 3)
        assert timing["min"] == pytest.approx(600.0)
        assert timing["max"] == pytest.approx(90000.0)

        buckets = campaign_statistics(campaign["id"])["response"]["buckets"]
        assert buckets == {
            "lt_1h": 1,
            "1h_to_6h": 1,
            "6h_to_12h": 0,
            "12h_to_24h": 0,
            "ge_24h": 1,
        }

    def test_response_seconds_boundaries(self, _init_db):
        # Boundary semantics: a value exactly on a bucket's lower edge rolls up
        # into that bucket (1 h -> 1h_to_6h, 12 h -> 12h_to_24h, 24 h -> ge_24h).
        campaign = _start_campaign()
        boundaries = {
            "m_a": (3599, "lt_1h"),       # just under 1 h
            "m_b": (3600, "1h_to_6h"),    # exactly 1 h
            "m_c": (23400, "6h_to_12h"),  # 6.5 h
            "m_d": (43200, "12h_to_24h"), # exactly 12 h
            "m_e": (86400, "ge_24h"),     # exactly 24 h
        }
        for user, (mid, (seconds, bucket)) in enumerate(boundaries.items(), start=1):
            _seed_intervention(campaign["id"], f"u{user}", mid)
            _seed_delivery(mid, f"u{user}", "delivered", 0.9)
            _respond(campaign["id"], mid, seconds)

        buckets = campaign_statistics(campaign["id"])["response"]["buckets"]
        for bucket in EMPTY_BUCKETS:
            assert buckets[bucket] == 1, bucket


class TestActivityAndGamesGroups:
    """Phase 2: the persisted activity report and its windowing rules.

    The activity group is a projection of the plays table joined onto the
    campaign's contacted audience. A play qualifies when it is strictly after
    the customer's first Welcome SMS and at or before the campaign's ended_at
    (or the report instant while active).
    """

    def test_activity_from_persisted_plays(self, _init_db):
        campaign = _start_campaign()
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_intervention(campaign["id"], "u2", "m_2")
        _seed_delivery("m_1", "u1", "delivered", 0.9)
        _seed_delivery("m_2", "u2", "delivered", 0.9)
        _seed_play("u1", "Aviator", 10, "2026-01-02T09:00:00+00:00")
        _seed_play("u1", "Mines", 5, "2026-01-02T10:00:00+00:00")
        _seed_play("u1", "Aviator", 20, "2026-01-02T11:00:00+00:00")
        _seed_play("u2", "Mines", 7.5, "2026-01-02T09:30:00+00:00")

        report = campaign_statistics(campaign["id"])
        activity = report["activity"]
        assert activity["qualifying_plays"] == 4
        assert activity["players"] == 2
        assert activity["repeat_players"] == 1
        assert activity["converted_players"] == 0  # no intervention responded
        assert activity["total_play_amount"] == pytest.approx(42.5)
        assert activity["avg_plays_per_player"] == pytest.approx(2.0)
        assert activity["max_plays_per_player"] == 3
        assert activity["before_sms"] == 0
        assert activity["after_window"] == 0

    def test_games_group_ranking_and_limit(self, _init_db):
        campaign = _start_campaign()
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_delivery("m_1", "u1", "delivered", 0.9)
        _seed_play("u1", "Aviator", 10, "2026-01-02T09:00:00+00:00")
        _seed_play("u1", "Aviator", 15, "2026-01-02T10:00:00+00:00")
        _seed_play("u1", "Mines", 5, "2026-01-02T11:00:00+00:00")
        _seed_play("u1", "Roulette", 2, "2026-01-02T12:00:00+00:00")

        report = campaign_statistics(campaign["id"], game_limit=2)
        games = report["games"]
        assert [g["game_name"] for g in games] == ["Aviator", "Mines"]
        assert games[0] == {
            "game_name": "Aviator",
            "plays": 2,
            "customers": 1,
            "amount": pytest.approx(25.0),
            "avg_amount": pytest.approx(12.5),
        }

    def test_activity_window_scoped_by_ended_at(self, _init_db):
        campaign = _start_campaign(closed=True)
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_delivery("m_1", "u1", "delivered", 0.9)
        _seed_play("u1", "Aviator", 10, "2026-01-03T00:00:00+00:00")  # inside
        _seed_play("u1", "Mines", 5, "2026-01-06T00:00:00+00:00")    # after end

        report = campaign_statistics(campaign["id"])
        assert report["activity"]["qualifying_plays"] == 1
        assert report["activity"]["total_play_amount"] == pytest.approx(10.0)
        assert report["activity"]["after_window"] == 1
        assert report["window"]["attribution_end"] == CAMP_END

    def test_activity_excludes_pre_sms_plays(self, _init_db):
        campaign = _start_campaign()
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_delivery("m_1", "u1", "delivered", 0.9)
        _seed_play("u1", "Aviator", 90, "2026-01-01T23:00:00+00:00")  # before SMS

        report = campaign_statistics(campaign["id"])
        assert report["activity"]["qualifying_plays"] == 0
        assert report["activity"]["total_play_amount"] == 0.0
        assert report["activity"]["before_sms"] == 1

    def test_repeat_rate_zero_when_no_repeats(self, _init_db):
        campaign = _start_campaign()
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_delivery("m_1", "u1", "delivered", 0.9)
        _seed_play("u1", "Aviator", 10, "2026-01-02T09:00:00+00:00")

        activity = campaign_statistics(campaign["id"])["activity"]
        assert activity["repeat_players"] == 0
        assert activity["repeat_rate"] == 0.0  # players=1, genuine zero

    def test_per_customer_activity_averages(self, _init_db):
        """Player and amount averages scoped to converted / contacted customers.

        Converted = u1 (responded), contacted = u1/u2/u3 (all SMS recipients).
        u3 never plays; u2 plays once; u1 plays three times. Per-player and
        per-customer denominators therefore differ and are all real numbers.
        """
        campaign = _start_campaign()
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_intervention(campaign["id"], "u2", "m_2")
        _seed_intervention(campaign["id"], "u3", "m_3")
        _seed_delivery("m_1", "u1", "delivered", 0.9)
        _seed_delivery("m_2", "u2", "delivered", 0.9)
        _seed_delivery("m_3", "u3", "delivered", 0.9)
        _respond(campaign["id"], "m_1", 600)

        _seed_play("u1", "Aviator", 10, "2026-01-02T09:00:00+00:00")
        _seed_play("u1", "Aviator", 20, "2026-01-02T10:00:00+00:00")
        _seed_play("u1", "Mines", 30, "2026-01-02T11:00:00+00:00")
        _seed_play("u2", "Mines", 5, "2026-01-02T09:30:00+00:00")

        report = campaign_statistics(campaign["id"])
        activity = report["activity"]
        assert activity["players"] == 2
        assert activity["single_play_players"] == 1  # u2 played exactly once
        assert activity["repeat_players"] == 1       # u1 played three times
        assert activity["converted_players"] == 1
        assert activity["qualifying_plays"] == 4
        assert activity["avg_plays_per_player"] == pytest.approx(2.0)
        assert activity["avg_plays_per_converted"] == pytest.approx(3.0)
        assert activity["avg_plays_per_contacted"] == pytest.approx(1.33)
        assert activity["total_play_amount"] == pytest.approx(65.0)
        assert activity["avg_amount_per_converted"] == pytest.approx(60.0)
        assert activity["avg_amount_per_contacted"] == pytest.approx(21.67)

    def test_per_customer_averages_none_when_no_base(self, _init_db):
        campaign = _start_campaign()
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_delivery("m_1", "u1", "delivered", 0.9)

        activity = campaign_statistics(campaign["id"])["activity"]
        # No plays at all: nothing to divide over converted (0 customers -> None),
        # while the contacted denominator exists, making those genuine zeros.
        assert activity["converted_players"] == 0
        assert activity["avg_plays_per_converted"] is None
        assert activity["avg_plays_per_contacted"] == 0.0
        assert activity["avg_amount_per_converted"] is None
        assert activity["avg_amount_per_contacted"] == 0.0
        assert activity["single_play_players"] == 0

    def test_game_avg_amount_column(self, _init_db):
        campaign = _start_campaign()
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_delivery("m_1", "u1", "delivered", 0.9)
        _seed_play("u1", "Aviator", 10, "2026-01-02T09:00:00+00:00")
        _seed_play("u1", "Aviator", 15, "2026-01-02T10:00:00+00:00")
        _seed_play("u1", "Mines", 5, "2026-01-02T11:00:00+00:00")

        games = campaign_statistics(campaign["id"])["games"]
        by_name = {g["game_name"]: g for g in games}
        assert by_name["Aviator"]["amount"] == pytest.approx(25.0)
        assert by_name["Aviator"]["avg_amount"] == pytest.approx(12.5)
        assert by_name["Mines"]["amount"] == pytest.approx(5.0)
        assert by_name["Mines"]["avg_amount"] == pytest.approx(5.0)


class TestCampaignEconomicsAndDrilldown:
    """Phase 3: campaign economics, game_count, and the customer drill-down."""

    def _campaign_with_activity(self) -> dict:
        campaign = _start_campaign()
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_intervention(campaign["id"], "u2", "m_2")
        _seed_intervention(campaign["id"], "u3", "m_3")
        _seed_delivery("m_1", "u1", "delivered", 0.9)
        _seed_delivery("m_2", "u2", "delivered", 0.9)
        _seed_delivery("m_3", "u3", "delivered", 0.9)
        _respond(campaign["id"], "m_1", 600)

        _seed_play("u1", "Aviator", 10, "2026-01-02T09:00:00+00:00")
        _seed_play("u1", "Aviator", 20, "2026-01-02T10:00:00+00:00")
        _seed_play("u1", "Mines", 30, "2026-01-02T11:00:00+00:00")
        _seed_play("u2", "Mines", 5, "2026-01-02T09:30:00+00:00")
        return campaign

    def test_economics_use_attributed_play_amount(self, _init_db):
        campaign = self._campaign_with_activity()
        economics = campaign_statistics(campaign["id"])["economics"]
        assert economics == {
            "sms_cost": pytest.approx(2.7),
            "avg_cost_per_accepted": pytest.approx(0.9),
            "cost_per_contacted": pytest.approx(0.9),
            "cost_per_conversion": pytest.approx(2.7),
            "total_play_amount": pytest.approx(65.0),
            "play_amount_per_converted": pytest.approx(65.0),
            "play_amount_per_contacted": pytest.approx(21.67),
            "activity_cost_ratio": pytest.approx(24.07),
        }

    def test_economics_zero_cost_ratio_is_none(self, _init_db):
        # Deferred sends are charged 0.0: the cost denominator is zero even
        # though an intervention exists, so the ratio is None, not infinity.
        campaign = _start_campaign()
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_delivery("m_1", "u1", "deferred", 0.0)

        economics = campaign_statistics(campaign["id"])["economics"]
        assert economics["sms_cost"] == pytest.approx(0.0)
        assert economics["cost_per_contacted"] == pytest.approx(0.0)
        assert economics["cost_per_conversion"] is None
        assert economics["activity_cost_ratio"] is None

    def test_game_count_from_persisted_plays(self, _init_db):
        campaign = self._campaign_with_activity()
        activity = campaign_statistics(campaign["id"])["activity"]
        assert activity["game_count"] == 2  # Aviator + Mines

    def test_game_count_scoped_inside_closed_window(self, _init_db):
        campaign = _start_campaign(closed=True)
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_delivery("m_1", "u1", "delivered", 0.9)
        _seed_play("u1", "Aviator", 10, "2026-01-03T00:00:00+00:00")  # inside
        _seed_play("u1", "Roulette", 5, "2026-01-06T00:00:00+00:00")  # after end

        activity = campaign_statistics(campaign["id"])["activity"]
        assert activity["game_count"] == 1

    def test_customer_drilldown_enriched(self, _init_db):
        campaign = self._campaign_with_activity()
        _seed_opportunity(campaign["id"], "u4", "created")

        rows = list_campaign_customers(campaign["id"], limit=10, offset=0)
        by_user = {r["user_id"]: r for r in rows}
        assert count_campaign_customers(campaign["id"]) == 4

        u1 = by_user["u1"]
        assert u1["delivery_status"] == "delivered"
        assert u1["intervention_status"] == "responded"
        assert u1["qualifying_plays"] == 3
        assert u1["attributed_amount"] == pytest.approx(60.0)
        assert u1["games_played"] == 2

        u2 = by_user["u2"]
        assert u2["delivery_status"] == "delivered"
        assert u2["qualifying_plays"] == 1
        assert u2["attributed_amount"] == pytest.approx(5.0)
        assert u2["games_played"] == 1

        u4 = by_user["u4"]
        assert u4["delivery_status"] is None
        assert u4["intervention_status"] is None
        assert u4["qualifying_plays"] == 0
        assert u4["attributed_amount"] == 0.0
        assert u4["games_played"] == 0

    def test_customer_endpoint_paginates_with_new_fields(self, stats_fixture):
        client = TestClient(app)
        campaign_id = stats_fixture["campaign"]["id"]
        r = client.get(f"/campaign/{campaign_id}/customers", params={"page_size": 5})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 10
        assert "delivery_status" in data["items"][0]
        assert "qualifying_plays" in data["items"][0]
        assert "attributed_amount" in data["items"][0]
        assert "games_played" in data["items"][0]

    def test_compare_endpoint_descriptive_in_requested_order(self, _init_db):
        client = TestClient(app)
        a = create_campaign("Compare A")["campaign"]
        b = create_campaign("Compare B")["campaign"]
        _seed_intervention(a["id"], "u1", "m_1")
        _seed_delivery("m_1", "u1", "delivered", 0.9)
        _seed_play("u1", "Aviator", 10, "2026-01-02T09:00:00+00:00")
        _seed_intervention(b["id"], "u2", "m_2")
        _seed_delivery("m_2", "u2", "delivered", 0.9)

        r = client.post(
            "/stats/campaigns/compare",
            json={"campaign_ids": [b["id"], a["id"]]},
        )
        assert r.status_code == 200
        body = r.json()
        assert [row["campaign_id"] for row in body] == [b["id"], a["id"]]
        # b has cost but no plays -> genuine 0.0 ratio, not None.
        assert body[0]["economics"]["activity_cost_ratio"] == pytest.approx(0.0)
        assert body[1]["economics"]["activity_cost_ratio"] == pytest.approx(11.11)

    def test_compare_endpoint_requires_campaigns(self, _init_db):
        client = TestClient(app)
        assert client.post("/stats/campaigns/compare", json={"campaign_ids": []}).status_code == 422
        too_many = list(range(11))
        assert (
            client.post("/stats/campaigns/compare", json={"campaign_ids": too_many}).status_code == 422
        )


class TestCampaignStates:
    def test_campaign_with_no_sms(self, _init_db):
        campaign = _start_campaign()
        _seed_opportunity(campaign["id"], "u1", "created")

        report = campaign_statistics(campaign["id"])
        assert report["audience"]["opportunities"] == 1
        assert report["sms"]["accepted"] == 0
        assert report["response"]["contacted_customers"] == 0
        assert report["response"]["conversion_rate"] == 0.0
        assert report["sms"]["delivery_rate"] is None
        assert report["economics"]["cost_per_contacted"] is None
        assert report["economics"]["cost_per_conversion"] is None

    def test_campaign_with_sms_but_zero_conversions(self, _init_db):
        campaign = _start_campaign()
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_delivery("m_1", "u1", "delivered", 1.2)

        report = campaign_statistics(campaign["id"])
        assert report["response"]["contacted_customers"] == 1
        assert report["response"]["converted_customers"] == 0
        assert report["response"]["conversion_rate"] == 0.0
        assert report["economics"]["cost_per_contacted"] == pytest.approx(1.2)
        assert report["economics"]["cost_per_conversion"] is None

    def test_closed_campaign_reported(self, _init_db):
        campaign = _start_campaign(closed=True)
        _seed_intervention(campaign["id"], "u1", "m_1")
        _seed_delivery("m_1", "u1", "delivered", 0.9)
        _respond(campaign["id"], "m_1", 1800)

        report = campaign_statistics(campaign["id"])
        assert report["campaign"]["status"] == "closed"
        assert report["window"]["ended_at"] == CAMP_END
        assert report["window"]["attribution_end"] == CAMP_END
        assert report["response"]["converted_customers"] == 1

    def test_active_campaign_reported(self, stats_fixture):
        report = campaign_statistics(stats_fixture["campaign"]["id"])
        assert report["campaign"]["status"] == "active"
        assert report["window"]["ended_at"] is None
        assert report["window"]["attribution_end"] is None


class TestStatisticsEndpoints:
    def test_campaigns_endpoint_bare_array(self, stats_fixture):
        client = TestClient(app)
        result = client.get("/stats/campaigns")
        assert result.status_code == 200
        assert isinstance(result.json(), list)
        summary = result.json()[0]
        assert summary["campaign_id"] == stats_fixture["campaign"]["id"]
        assert "economics" in summary
        assert "deferred" in summary["sms"]

    def test_detail_endpoint(self, stats_fixture):
        client = TestClient(app)
        result = client.get(f"/stats/campaigns/{stats_fixture['campaign']['id']}")
        assert result.status_code == 200
        body = result.json()
        assert body["campaign"]["id"] == stats_fixture["campaign"]["id"]
        assert body["response"]["converted_customers"] == 2
        assert body["funnel"]["accepted"] == 4
        assert body["economics"]["cost_per_conversion"] == pytest.approx(1.8)

    def test_missing_campaign_is_404(self, _init_db):
        client = TestClient(app)
        result = client.get("/stats/campaigns/99999")
        assert result.status_code == 404
        assert result.json()["message"] == "Campaign not found."