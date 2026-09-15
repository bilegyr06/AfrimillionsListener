"""Tests for the operator-driven welcome (post-sign-in) campaign model.

Model: campaigns (operator windows) -> login opportunities -> interventions.
Cap counts only Termii-accepted sends (interventions); failed sends consume
nothing; phones must pass a Nigerian-mobile safety gate before any send.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

# Ensure env vars are set before any app imports.
os.environ.setdefault("TERMII_API_KEY", "test-key")
os.environ.setdefault("TERMII_BASE_URL", "https://test.api.termii.com/api")
os.environ.setdefault("TERMII_SENDER_ID", "TestSender")

from app.config import settings
from app.database import (
    close_active_campaign,
    create_campaign,
    create_intervention,
    get_campaign_stats,
    get_cap_usage,
    get_connection,
    get_open_opportunities,
    get_welcome_sms_state,
    update_opportunity_status,
    upsert_opportunities,
)
from app.processor import (
    _is_valid_nigerian_phone,
    _normalize_phone,
    _phone_gate,
    finalize_campaign,
    ingest_opportunities,
    run_welcome_pipeline,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ago(hours: float) -> datetime:
    return _now() - timedelta(hours=hours)


def _slug(dt: datetime) -> str:
    """Naive CSV timestamp string (app-wide CSV timestamps are treated as UTC)."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _write_login(data_dir, rows, name="Login_test.csv"):
    pd.DataFrame(rows, columns=["userId", "timestamp"]).to_csv(
        data_dir / name, index=False
    )


def _write_regs(data_dir):
    rows = [
        {"userId": uid, "firstName": name, "email": f"{name}@x.com",
         "phone": phone, "timestamp": _slug(_ago(48))}
        for uid, name, phone in [("1", "Ada", "07870123456"), ("2", "Bola", "08012345678")]
    ]
    pd.DataFrame(rows).to_csv(data_dir / "Registrations_test.csv", index=False)


def _write_sales(data_dir, rows):
    pd.DataFrame(rows, columns=["userId", "gameName", "amount", "timestamp"]).to_csv(
        data_dir / "Sales_test.csv", index=False
    )


@pytest.fixture()
def welcome_settings(_init_db, monkeypatch):
    """Deterministic welcome campaign settings against an isolated DB/data dir."""
    monkeypatch.setattr(settings, "WELCOME_EVAL_DELAY_HOURS", 1)
    monkeypatch.setattr(settings, "WELCOME_MAX_MESSAGES", 3)
    monkeypatch.setattr(settings, "WELCOME_POST_LIMIT_SUPPRESS", True)
    monkeypatch.setattr(settings, "COOLDOWN_HOURS", 24)
    return settings.DATA_FOLDER


def _start_campaign(hours_ago: float = 6, name: str | None = None) -> dict:
    """Start a campaign and backdate it so older logins fall inside its window."""
    result = create_campaign(name)
    campaign = result["campaign"]
    conn = get_connection()
    conn.execute(
        "UPDATE welcome_campaigns SET started_at = ? WHERE id = ?",
        (_ago(hours_ago).isoformat(), campaign["id"]),
    )
    conn.commit()
    conn.close()
    campaign["started_at"] = _ago(hours_ago).isoformat()
    return campaign


def _run(campaign=None):
    """Run one welcome pipeline, returning the whole evaluation dict."""
    if campaign is None:
        campaign = _start_campaign()
    deadline = datetime.now() + timedelta(hours=1)
    return asyncio.run(run_welcome_pipeline(deadline, "test-cycle"))


def _monkeypatch_send(monkeypatch, result: dict | None):
    async def _fake_send(phone, message):
        return result
    monkeypatch.setattr("app.processor.send_sms", _fake_send)


def _seed_success(campaign_id: int, user_id: str, login_at: str, sent_at: str,
                  phone: str = "2348012345678"):
    """Create a successful send history row (opportunity + intervention)."""
    upsert_opportunities([{
        "campaign_id": campaign_id,
        "user_id": user_id,
        "first_name": "Ada",
        "phone_raw": "07870123456",
        "phone_normalized": phone,
        "login_at": login_at,
        "eval_delay_hours": 1,
    }])
    opp = next(o for o in get_open_opportunities(campaign_id) if o["user_id"] == user_id)
    update_opportunity_status(opp["id"], "sent")
    create_intervention(
        opportunity_id=opp["id"],
        campaign_id=campaign_id,
        user_id=user_id,
        login_at=opp["login_at"],
        sent_at=sent_at,
        message_id=f"m_{user_id}",
    )


def _status_counts(campaign_id: int) -> dict:
    conn = get_connection()
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM welcome_opportunities "
        "WHERE campaign_id = ? GROUP BY status",
        (campaign_id,),
    ).fetchall()
    conn.close()
    return {r["status"]: r["n"] for r in rows}


def _intervention_states(campaign_id: int) -> dict:
    conn = get_connection()
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM welcome_interventions "
        "WHERE campaign_id = ? GROUP BY status",
        (campaign_id,),
    ).fetchall()
    conn.close()
    return {r["status"]: r["n"] for r in rows}


# ---------------------------------------------------------------------------
# Phone safety gate
# ---------------------------------------------------------------------------

class TestPhoneGate:
    def test_normalization_forms(self):
        assert _normalize_phone("07870123456") == "2347870123456"
        assert _normalize_phone("08012345678") == "2348012345678"
        assert _normalize_phone("8012345678") == "2348012345678"
        assert _normalize_phone("+234 801 2345 678") == "2348012345678"
        assert _normalize_phone("not-a-phone") is None

    def test_valid_nigerian_mobile(self):
        assert _is_valid_nigerian_phone("2348012345678")
        assert _is_valid_nigerian_phone("2347078654321")

    def test_invalid_numbers_rejected(self):
        # Ghanaian format is normalized but NOT a Nigerian mobile -> rejected
        assert _is_valid_nigerian_phone("233999999999") is False
        # 13 digits but network code starts with 1 -> rejected
        assert _is_valid_nigerian_phone("2341234567890") is False
        assert _is_valid_nigerian_phone("1234567890123") is False
        assert _is_valid_nigerian_phone(None) is False

    def test_phone_gate_normalizes_valid_only(self):
        assert _phone_gate("08012345678") == "2348012345678"
        assert _phone_gate("233999999999") is None


# ---------------------------------------------------------------------------
# Campaign lifecycle
# ---------------------------------------------------------------------------

class TestCampaignLifecycle:
    def test_start_creates_active_campaign(self, welcome_settings):
        campaign = _start_campaign()
        assert campaign["status"] == "active"
        assert campaign["ended_at"] is None

    def test_starting_closes_previous(self, welcome_settings):
        first = _start_campaign(name="first")
        _start_campaign(name="second")
        conn = get_connection()
        row = conn.execute(
            "SELECT status FROM welcome_campaigns WHERE id = ?", (first["id"],)
        ).fetchone()
        conn.close()
        assert row["status"] == "closed"

    def test_close_no_active_returns_none(self, welcome_settings):
        assert close_active_campaign() is None

    def test_close_finalizes(self, welcome_settings):
        campaign = _start_campaign()
        _write_login(
            welcome_settings,
            [("1", _slug(_ago(0.5)))],  # login not yet due (delay 1h) -> stays created
        )
        _write_regs(welcome_settings)
        _run(campaign)
        assert _status_counts(campaign["id"]).get("created", 0) == 1

        closed = close_active_campaign()
        assert closed is not None
        finalize_campaign(closed["id"])

        # Unevaluated logins expire at close; they are not carried forward.
        assert _status_counts(closed["id"]).get("expired", 0) == 1
        assert _status_counts(closed["id"]).get("created", 0) == 0


# ---------------------------------------------------------------------------
# Login ingestion
# ---------------------------------------------------------------------------

class TestOpportunityIngestion:
    def test_scans_all_login_files_within_window(self, welcome_settings):
        campaign = _start_campaign(hours_ago=6)
        _write_login(welcome_settings, [("1", _slug(_ago(5)))], name="Login_a.csv")
        _write_login(
            welcome_settings,
            [("1", _slug(_ago(4))), ("2", _slug(_ago(4)))],
            name="Login_b.csv",
        )
        _write_regs(welcome_settings)
        _run(campaign)

        conn = get_connection()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM welcome_opportunities WHERE campaign_id = ?",
            (campaign["id"],),
        ).fetchone()["n"]
        conn.close()
        # user 1 has two distinct logins, user 2 one => 3 opportunities
        assert n == 3

    def test_idempotent_on_reruns(self, welcome_settings):
        campaign = _start_campaign(hours_ago=6)
        _write_login(welcome_settings, [("1", _slug(_ago(5)))])
        _write_regs(welcome_settings)
        _run(campaign)
        _run(campaign)

        conn = get_connection()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM welcome_opportunities WHERE campaign_id = ?",
            (campaign["id"],),
        ).fetchone()["n"]
        conn.close()
        assert n == 1

    def test_logins_before_campaign_start_excluded(self, welcome_settings):
        campaign = _start_campaign(hours_ago=3)
        login_df = pd.DataFrame(
            [("1", _slug(_ago(20)))], columns=["userId", "timestamp"]
        )
        regs_df = pd.DataFrame(
            [{"userId": "1", "firstName": "Ada", "phone": "08012345678"}]
        )
        assert ingest_opportunities(campaign, login_df, regs_df) == 0

    def test_normalized_phone_snapshotted(self, welcome_settings):
        campaign = _start_campaign(hours_ago=6)
        login_df = pd.DataFrame([("1", _slug(_ago(5)))], columns=["userId", "timestamp"])
        regs_df = pd.DataFrame(
            [{"userId": "1", "firstName": "Ada", "phone": "07870123456"}]
        )
        ingest_opportunities(campaign, login_df, regs_df)
        opp = get_open_opportunities(campaign["id"])[0]
        assert opp["phone_normalized"] == "2347870123456"
        assert opp["phone_raw"] == "07870123456"
        assert opp["first_name"] == "Ada"


# ---------------------------------------------------------------------------
# Evaluation decisions
# ---------------------------------------------------------------------------

class TestEvaluation:
    def test_eligible_login_sent(self, welcome_settings, monkeypatch):
        _monkeypatch_send(monkeypatch, {"message_id": "mid", "balance": 90})
        campaign = _start_campaign(hours_ago=6)
        _write_login(welcome_settings, [("1", _slug(_ago(5)))])
        _write_regs(welcome_settings)
        _run(campaign)

        assert _status_counts(campaign["id"]).get("sent", 0) == 1
        assert get_cap_usage("1") == 1
        assert get_welcome_sms_state()["1"]["count"] == 1

    def test_too_early_stays_created(self, welcome_settings, monkeypatch):
        _monkeypatch_send(monkeypatch, {"message_id": "mid"})
        campaign = _start_campaign(hours_ago=6)
        _write_login(welcome_settings, [("1", _slug(_ago(0.5)))])
        _write_regs(welcome_settings)
        result = _run(campaign)

        assert result["decisions"]["too_early"] == 1
        assert _status_counts(campaign["id"]).get("created", 0) == 1
        assert get_cap_usage("1") == 0

    def test_played_during_delay_disqualified(self, welcome_settings, monkeypatch):
        _monkeypatch_send(monkeypatch, {"message_id": "mid"})
        campaign = _start_campaign(hours_ago=6)
        _write_login(welcome_settings, [("1", _slug(_ago(5)))])
        _write_regs(welcome_settings)
        _write_sales(welcome_settings, [("1", "G", 1, _slug(_ago(4.5)))])
        result = _run(campaign)

        assert result["decisions"]["disqualified_played"] == 1
        assert _status_counts(campaign["id"]).get("disqualified_played", 0) == 1
        assert get_cap_usage("1") == 0

    def test_play_after_delay_before_send_disqualified(self, welcome_settings, monkeypatch):
        # Re-check: a play AFTER the eval delay but BEFORE dispatch still blocks.
        _monkeypatch_send(monkeypatch, {"message_id": "mid"})
        campaign = _start_campaign(hours_ago=6)
        _write_login(welcome_settings, [("1", _slug(_ago(5)))])
        _write_regs(welcome_settings)
        _write_sales(welcome_settings, [("1", "G", 1, _slug(_ago(3.5)))])  # login+1.5h
        result = _run(campaign)

        assert result["decisions"]["disqualified_played"] == 1
        assert _status_counts(campaign["id"]).get("disqualified_played", 0) == 1

    def test_play_before_login_does_not_disqualify(self, welcome_settings, monkeypatch):
        _monkeypatch_send(monkeypatch, {"message_id": "mid"})
        campaign = _start_campaign(hours_ago=6)
        _write_login(welcome_settings, [("1", _slug(_ago(5)))])
        _write_regs(welcome_settings)
        _write_sales(welcome_settings, [("1", "G", 1, _slug(_ago(6)))])
        _run(campaign)

        assert _status_counts(campaign["id"]).get("sent", 0) == 1

    def test_cooldown_active_skips(self, welcome_settings, monkeypatch):
        _monkeypatch_send(monkeypatch, {"message_id": "mid"})
        campaign = _start_campaign(hours_ago=6)
        _seed_success(campaign["id"], "1", _ago(30).isoformat(), _ago(2).isoformat())
        _write_login(welcome_settings, [("1", _slug(_ago(5)))])
        _write_regs(welcome_settings)
        result = _run(campaign)

        assert result["decisions"]["skipped_cooldown"] == 1
        assert _status_counts(campaign["id"]).get("skipped_cooldown", 0) == 1

    def test_cooldown_expired_allows_send(self, welcome_settings, monkeypatch):
        _monkeypatch_send(monkeypatch, {"message_id": "mid"})
        campaign = _start_campaign(hours_ago=6)
        _seed_success(campaign["id"], "1", _ago(30).isoformat(), _ago(48).isoformat())
        _write_login(welcome_settings, [("1", _slug(_ago(5)))])
        _write_regs(welcome_settings)
        _run(campaign)

        # prior seeded login + this login = two distinct logged-in opportunities
        assert _status_counts(campaign["id"]).get("sent", 0) == 2
        assert get_cap_usage("1") == 2  # prior + new

    def test_cap_reached_skips_with_suppress(self, welcome_settings, monkeypatch):
        monkeypatch.setattr(settings, "WELCOME_MAX_MESSAGES", 1)
        _monkeypatch_send(monkeypatch, {"message_id": "mid"})
        campaign = _start_campaign(hours_ago=6)
        _seed_success(campaign["id"], "1", _ago(30).isoformat(), _ago(48).isoformat())
        _write_login(welcome_settings, [("1", _slug(_ago(5)))])
        _write_regs(welcome_settings)
        result = _run(campaign)

        assert result["decisions"]["skipped_cap"] == 1
        assert _status_counts(campaign["id"]).get("skipped_cap", 0) == 1

    def test_cap_still_sends_without_suppress(self, welcome_settings, monkeypatch):
        monkeypatch.setattr(settings, "WELCOME_MAX_MESSAGES", 1)
        monkeypatch.setattr(settings, "WELCOME_POST_LIMIT_SUPPRESS", False)
        _monkeypatch_send(monkeypatch, {"message_id": "mid"})
        campaign = _start_campaign(hours_ago=6)
        _seed_success(campaign["id"], "1", _ago(30).isoformat(), _ago(48).isoformat())
        _write_login(welcome_settings, [("1", _slug(_ago(5)))])
        _write_regs(welcome_settings)
        _run(campaign)

        # without suppress the cap is ignored: prior seeded + this login both sent
        assert _status_counts(campaign["id"]).get("sent", 0) == 2

    def test_invalid_phone_never_calls_gateway(self, welcome_settings, monkeypatch):
        calls = []

        async def _fake_send(phone, message):
            calls.append(phone)
            return {"message_id": "mid"}

        monkeypatch.setattr("app.processor.send_sms", _fake_send)
        campaign = _start_campaign(hours_ago=6)
        # Registrations carries a Ghanaian-style number that fails the gate.
        pd.DataFrame(
            [{"userId": "1", "firstName": "Ada", "email": "a@x.com",
              "phone": "+233 99 999 9999", "timestamp": _slug(_ago(48))}]
        ).to_csv(welcome_settings / "Registrations_test.csv", index=False)
        _write_login(welcome_settings, [("1", _slug(_ago(5)))])
        result = _run(campaign)

        assert result["decisions"]["skipped_invalid_phone"] == 1
        assert _status_counts(campaign["id"]).get("skipped_invalid_phone", 0) == 1
        assert calls == []

    def test_failed_send_no_intervention(self, welcome_settings, monkeypatch):
        _monkeypatch_send(monkeypatch, None)  # gateway rejects
        campaign = _start_campaign(hours_ago=6)
        _write_login(welcome_settings, [("1", _slug(_ago(5)))])
        _write_regs(welcome_settings)
        result = _run(campaign)

        assert result["failed"] == 1
        assert _status_counts(campaign["id"]).get("failed_send", 0) == 1
        assert get_cap_usage("1") == 0
        assert get_welcome_sms_state().get("1") is None


class TestCapAcrossCampaigns:
    def test_cap_is_cumulative_across_campaigns(self, welcome_settings, monkeypatch):
        _monkeypatch_send(monkeypatch, {"message_id": "mid"})
        first = _start_campaign(hours_ago=10, name="first")
        _write_login(welcome_settings, [("1", _slug(_ago(9)))])
        _write_regs(welcome_settings)
        _run(first)  # sends user 1 in campaign one

        monkeypatch.setattr(settings, "WELCOME_MAX_MESSAGES", 1)
        second = _start_campaign(hours_ago=2, name="second")
        _write_login(welcome_settings, [("1", _slug(_ago(1)))], name="Login_second.csv")
        deadline = datetime.now() + timedelta(hours=1)
        result = asyncio.run(run_welcome_pipeline(deadline, "test-cycle-2"))

        assert result["decisions"]["skipped_cap"] == 1
        assert get_cap_usage("1") == 1  # still just the first campaign's send


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------

class TestAttribution:
    def test_play_after_send_marks_responded(self, welcome_settings, monkeypatch):
        _monkeypatch_send(monkeypatch, {"message_id": "mid"})
        campaign = _start_campaign(hours_ago=6)
        _seed_success(campaign["id"], "1", _ago(30).isoformat(), _ago(2).isoformat())
        _write_sales(welcome_settings, [("1", "G", 1, _slug(_ago(1)))])  # 1h after send
        _run(campaign)

        states = _intervention_states(campaign["id"])
        assert states.get("responded", 0) == 1
        stats = get_campaign_stats(campaign["id"])
        assert stats["response_rate"] == 1.0
        assert stats["avg_response_seconds"] == pytest.approx(3600, abs=120)

    def test_play_before_send_not_attributed(self, welcome_settings, monkeypatch):
        campaign = _start_campaign(hours_ago=6)
        _seed_success(campaign["id"], "1", _ago(30).isoformat(), _ago(2).isoformat())
        _write_sales(welcome_settings, [("1", "G", 1, _slug(_ago(3)))])  # before send
        _run(campaign)

        states = _intervention_states(campaign["id"])
        assert states.get("responded", 0) == 0
        assert states.get("open", 0) == 1

    def test_close_without_play_is_no_response(self, welcome_settings, monkeypatch):
        campaign = _start_campaign(hours_ago=6)
        _seed_success(campaign["id"], "1", _ago(30).isoformat(), _ago(2).isoformat())
        closed = close_active_campaign()
        finalize_campaign(closed["id"])

        states = _intervention_states(closed["id"])
        assert states.get("no_response", 0) == 1
        assert states.get("open", 0) == 0

    def test_close_attributes_plays_up_to_ended_at(self, welcome_settings, monkeypatch):
        campaign = _start_campaign(hours_ago=6)
        _seed_success(campaign["id"], "1", _ago(30).isoformat(), _ago(2).isoformat())
        # A play occurred inside the campaign window (after the send).
        _write_sales(welcome_settings, [("1", "G", 1, _slug(_ago(0.5)))])
        closed = close_active_campaign()
        finalize_campaign(closed["id"])

        states = _intervention_states(closed["id"])
        assert states.get("responded", 0) == 1


# ---------------------------------------------------------------------------
# Campaign API
# ---------------------------------------------------------------------------

class TestCampaignAPI:
    @pytest.fixture()
    def client(self, _init_db):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_start_and_current(self, client):
        r = client.post("/campaign/start", json={"name": "Summer"})
        assert r.status_code == 200
        data = r.json()
        assert data["campaign"]["status"] == "active"
        assert data["campaign"]["name"] == "Summer"

        cur = client.get("/campaign/current").json()
        assert cur["active"] is True
        assert cur["campaign"]["id"] == data["campaign"]["id"]

    def test_start_closes_previous(self, client):
        first = client.post("/campaign/start").json()["campaign"]
        second = client.post("/campaign/start").json()["campaign"]
        campaigns = client.get("/campaigns").json()
        by_id = {c["campaign"]["id"]: c["campaign"] for c in campaigns}
        assert by_id[first["id"]]["status"] == "closed"
        assert by_id[second["id"]]["status"] == "active"

    def test_close_no_active_is_404(self, client):
        r = client.post("/campaign/close")
        assert r.status_code == 404

    def test_close_active_campaign(self, client):
        started = client.post("/campaign/start").json()["campaign"]
        r = client.post("/campaign/close")
        assert r.status_code == 200
        closed = r.json()
        assert closed["campaign"]["id"] == started["id"]
        assert closed["campaign"]["status"] == "closed"
        assert closed["campaign"]["ended_at"] is not None
        assert closed["stats"]["campaign_id"] == started["id"]

    def test_tracking_endpoint_removed(self, client):
        assert client.get("/stats/welcome/tracking").status_code == 404