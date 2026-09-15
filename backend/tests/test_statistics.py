"""Tests for the campaign statistics reporting contract.

The Statistics surface (/stats/campaigns, /stats/campaigns/{id}) must always
aggregate only in SQL and stick to the stable terminology in
app.services.statistics: opportunities (logins) vs unique customers vs
accepted vs contacted vs converted, with 0 as a valid rate.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("TERMII_API_KEY", "test-key")
os.environ.setdefault("TERMII_BASE_URL", "https://test.api.termii.com/api")
os.environ.setdefault("TERMII_SENDER_ID", "TestSender")

from fastapi.testclient import TestClient

from app.db.database import (
    create_campaign,
    create_intervention,
    get_connection,
    log_sms,
    record_intervention_response,
    update_opportunity_status,
    upsert_opportunities,
)

from app.main import app
from app.services.statistics import campaign_statistics, campaign_summaries

SENT_AT = "2026-01-02T08:00:00+00:00"
LOGIN_AT = "2026-01-02T07:00:00+00:00"


def _start_campaign() -> dict:
    result = create_campaign("Statistics test")
    campaign = result["campaign"]
    conn = get_connection()
    conn.execute(
        "UPDATE welcome_campaigns SET started_at = ? WHERE id = ?",
        ("2026-01-01T00:00:00+00:00", campaign["id"]),
    )
    conn.commit()
    conn.close()
    return campaign


def _seed_opportunity(campaign_id: int, user_id: str, status: str) -> dict:
    upsert_opportunities([
        {
            "campaign_id": campaign_id,
            "user_id": user_id,
            "first_name": "Ada",
            "phone_raw": "08012345678",
            "phone_normalized": "2348012345678",
            "login_at": LOGIN_AT,
            "eval_delay_hours": 1,
        }
    ])
    conn = get_connection()
    row = conn.execute(
        "SELECT id, login_at FROM welcome_opportunities "
        "WHERE campaign_id = ? AND user_id = ?",
        (campaign_id, user_id),
    ).fetchone()
    conn.close()
    if status != "created":
        update_opportunity_status(row["id"], status)
    return {"id": row["id"], "login_at": row["login_at"]}


def _seed_intervention(campaign_id: int, user_id: str, message_id: str) -> int:
    opp = _seed_opportunity(campaign_id, user_id, "sent")
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

    conn = get_connection()
    rows = conn.execute(
        "SELECT i.id, i.user_id FROM welcome_interventions i "
        "WHERE i.campaign_id = ? AND i.user_id IN ('u1', 'u2', 'u3')",
        (campaign["id"],),
    ).fetchall()
    for row in rows:
        if row["user_id"] == "u3":
            conn.execute(
                "UPDATE welcome_interventions SET status = 'no_response', updated_at = ? "
                "WHERE id = ?",
                ("2026-01-03T00:00:00+00:00", row["id"]),
            )
    conn.commit()
    conn.close()

    # u1 -> 10 minutes after send; u2 -> 2 hours after send.
    conn = get_connection()
    for user_id, seconds, play_at in (
        ("u1", 600, "2026-01-02T08:10:00+00:00"),
        ("u2", 7200, "2026-01-02T10:00:00+00:00"),
    ):
        row = conn.execute(
            "SELECT id FROM welcome_interventions WHERE campaign_id = ? AND user_id = ?",
            (campaign["id"], user_id),
        ).fetchone()
        record_intervention_response(row["id"], play_at, seconds)
    conn.close()

    for user_id in ("u5", "u6", "u7", "u8"):
        _seed_opportunity(campaign["id"], user_id, {
            "u5": "disqualified_played",
            "u6": "skipped_cap",
            "u7": "skipped_invalid_phone",
            "u8": "failed_send",
        }[user_id])
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

    def test_detail_sms_funnel(self, stats_fixture):
        sms = campaign_statistics(stats_fixture["campaign"]["id"])["sms"]
        assert sms["accepted"] == 4
        assert sms["contacted_customers"] == 4
        assert sms["delivered"] == 3
        assert sms["sent_awaiting_delivery"] == 1
        assert sms["unmatched"] == 0
        assert sms["failed"] == sms["rejected"] == sms["dnd"] == sms["expired"] == 0
        assert sms["delivery_rate"] == 0.75
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
            "lt_15m": 1,
            "15m_to_1h": 0,
            "1h_to_6h": 1,
            "6h_to_24h": 0,
            "24h_to_48h": 0,
            "ge_48h": 0,
        }
        assert response["qualifying_plays"] is None

    def test_unknown_campaign_is_none(self, stats_fixture):
        assert campaign_statistics(99999) is None

    def test_empty_campaign_returns_valid_zeros(self, _init_db):
        campaign = create_campaign("Empty")["campaign"]
        report = campaign_statistics(campaign["id"])
        assert report["audience"]["opportunities"] == 0
        assert report["response"]["contacted_customers"] == 0
        assert report["response"]["conversion_rate"] == 0.0
        assert report["sms"]["delivery_rate"] == 0.0

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
            "cost": pytest.approx(3.6),
        }
        assert row["response"]["converted_customers"] == 2
        assert row["response"]["conversion_rate"] == pytest.approx(0.5)
        assert row["response"]["avg_response_seconds"] == pytest.approx(3900.0)
        assert row["response"]["still_pending"] == 1


class TestStatisticsEndpoints:
    def test_campaigns_endpoint_bare_array(self, stats_fixture):
        client = TestClient(app)
        result = client.get("/stats/campaigns")
        assert result.status_code == 200
        assert isinstance(result.json(), list)
        assert result.json()[0]["campaign_id"] == stats_fixture["campaign"]["id"]

    def test_detail_endpoint(self, stats_fixture):
        client = TestClient(app)
        result = client.get(f"/stats/campaigns/{stats_fixture['campaign']['id']}")
        assert result.status_code == 200
        body = result.json()
        assert body["campaign"]["id"] == stats_fixture["campaign"]["id"]
        assert body["response"]["converted_customers"] == 2

    def test_missing_campaign_is_404(self, _init_db):
        client = TestClient(app)
        result = client.get("/stats/campaigns/99999")
        assert result.status_code == 404
        assert result.json()["message"] == "Campaign not found."