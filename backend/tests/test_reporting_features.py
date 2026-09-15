"""Feature-scoping contract for active dashboard reporting.

The active reporting endpoints (/report/overview, /stats, default /sms/logs)
must aggregate only the kinds belonging to features currently enabled in
ENABLED_FEATURES, plus manual operator sends. Explicit feature-specific
endpoints (/stats/welcome, /stats/inactive, campaign history) and account-level
data stay unaffected.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TERMII_API_KEY", "test-key")
os.environ.setdefault("TERMII_BASE_URL", "https://test.api.termii.com/api")
os.environ.setdefault("TERMII_SENDER_ID", "TestSender")

from app.core.config import settings
from app.core.models import INACTIVE, WELCOME
from app.services.sms import MANUAL


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_sms(kind, user_id, status="sent", cost=1.0):
    from app.db.database import log_sms
    log_sms({
        "message_id": f"m_{user_id}",
        "user_id": user_id,
        "kind": kind,
        "phone": "2348012345678",
        "status": status,
        "cost": cost,
        "cycle_id": "c1",
        "sent_at": _now_iso(),
    })


@pytest.fixture()
def client(_init_db):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture()
def features(monkeypatch):
    """Set the enabled features for one test (restored automatically)."""
    def _set(*enabled):
        monkeypatch.setattr(settings, "ENABLED_FEATURES", set(enabled))
    return _set


# ---------------------------------------------------------------------------
# /report/overview
# ---------------------------------------------------------------------------

class TestOverviewScoping:
    def test_welcome_only(self, client, features):
        features(WELCOME)
        _log_sms(WELCOME, "u1", status="delivered")
        _log_sms(INACTIVE, "u2", status="failed")
        _log_sms(MANUAL, "u3", status="sent")

        data = client.get("/report/overview").json()
        assert data["features"]["enabled_features"] == ["welcome"]
        assert set(data["features"]["active_kinds"]) == {"manual", "welcome"}
        assert set(data["features"]["breakdown"]) == {"welcome"}
        # Welcome + manual contribute; the disabled Inactive row does not.
        assert data["phone_sms"]["total"] == 2
        assert data["phone_sms"]["delivered"] == 1
        assert data["phone_sms"]["sent"] == 2  # delivered welcome + manual sent
        assert data["phone_sms"]["failed"] == 0

    def test_inactive_only(self, client, features):
        features(INACTIVE)
        _log_sms(WELCOME, "u1", status="delivered")
        _log_sms(INACTIVE, "u2", status="failed")

        data = client.get("/report/overview").json()
        assert data["features"]["enabled_features"] == ["inactive"]
        assert set(data["features"]["breakdown"]) == {"inactive"}
        assert data["phone_sms"]["total"] == 1
        assert data["phone_sms"]["failed"] == 1
        assert data["phone_sms"]["delivered"] == 0

    def test_both(self, client, features):
        features(WELCOME, INACTIVE)
        _log_sms(WELCOME, "u1", status="delivered")
        _log_sms(INACTIVE, "u2", status="failed")
        _log_sms(MANUAL, "u3", status="sent")

        data = client.get("/report/overview").json()
        assert set(data["features"]["enabled_features"]) == {"welcome", "inactive"}
        assert set(data["features"]["breakdown"]) == {"welcome", "inactive"}
        assert data["features"]["breakdown"]["welcome"]["total"] == 1
        assert data["features"]["breakdown"]["inactive"]["total"] == 1
        assert data["phone_sms"]["total"] == 3

    def test_none(self, client, features):
        features()
        _log_sms(WELCOME, "u1", status="delivered")
        _log_sms(INACTIVE, "u2", status="failed")
        _log_sms(MANUAL, "u4", status="sent")

        data = client.get("/report/overview").json()
        assert data["features"]["enabled_features"] == []
        assert data["features"]["breakdown"] == {}
        # No feature data; manual operator sends remain visible.
        assert data["phone_sms"]["total"] == 1
        assert data["phone_sms"]["sent"] == 1
        assert data["phone_sms"]["delivered"] == 0
        assert data["phone_sms"]["failed"] == 0

    def test_campaign_gated_when_welcome_disabled(self, client, features):
        features(INACTIVE)
        client.post("/campaign/start", json={"name": "Wave"})
        data = client.get("/report/overview").json()
        assert data["campaign"]["active"] is False
        assert data["campaign"]["current"] is None
        assert data["campaign"]["feature"] == "welcome"

    def test_campaign_shown_when_welcome_enabled(self, client, features):
        features(WELCOME)
        started = client.post("/campaign/start", json={"name": "Wave"}).json()
        data = client.get("/report/overview").json()
        assert data["campaign"]["active"] is True
        assert data["campaign"]["current"]["campaign_id"] == started["campaign"]["id"]


# ---------------------------------------------------------------------------
# /stats (active) vs /stats/welcome + /stats/inactive (historical)
# ---------------------------------------------------------------------------

class TestStatsScoping:
    def test_active_stats_scoped(self, client, features):
        features(WELCOME)
        _log_sms(WELCOME, "u1", status="delivered")
        _log_sms(INACTIVE, "u2", status="failed")

        data = client.get("/stats").json()
        assert data["total"] == 1
        assert data["delivered"] == 1
        assert data["failed"] == 0

    def test_active_stats_includes_manual(self, client, features):
        features()
        _log_sms(MANUAL, "u1", status="sent")
        _log_sms(WELCOME, "u2", status="sent")
        assert client.get("/stats").json()["total"] == 1

    def test_feature_specific_endpoints_stay_historical(self, client, features):
        features(INACTIVE)
        _log_sms(WELCOME, "u1", status="delivered")
        _log_sms(INACTIVE, "u2", status="failed")

        # Disabled Welcome is still fully inspectable through its own endpoint.
        welcome = client.get("/stats/welcome").json()
        assert welcome["total"] == 1
        assert welcome["delivered"] == 1

        inactive = client.get("/stats/inactive").json()
        assert inactive["total"] == 1
        assert inactive["failed"] == 1


# ---------------------------------------------------------------------------
# /sms/logs
# ---------------------------------------------------------------------------

class TestSmsLogsScoping:
    def test_default_excludes_disabled_feature(self, client, features):
        features(WELCOME)
        _log_sms(WELCOME, "u1")
        _log_sms(INACTIVE, "u2")

        data = client.get("/sms/logs").json()
        assert data["total"] == 1
        assert {item["kind"] for item in data["items"]} == {"welcome"}
        assert data["enabled_features"] == ["welcome"]

    def test_manual_always_visible(self, client, features):
        features()
        _log_sms(MANUAL, "u1")
        _log_sms(WELCOME, "u2")

        data = client.get("/sms/logs").json()
        assert data["total"] == 1
        assert data["items"][0]["kind"] == "manual"
        assert data["enabled_features"] == []

    def test_explicit_kind_inspects_history(self, client, features):
        features(WELCOME)
        _log_sms(INACTIVE, "u2")

        data = client.get("/sms/logs", params={"kind": "inactive"}).json()
        assert data["total"] == 1
        assert data["items"][0]["kind"] == "inactive"
        # Scope metadata still reflects the live enablement.
        assert data["enabled_features"] == ["welcome"]

    def test_both_features_listed(self, client, features):
        features(WELCOME, INACTIVE)
        _log_sms(WELCOME, "u1")
        _log_sms(INACTIVE, "u2")

        data = client.get("/sms/logs").json()
        assert data["total"] == 2
        assert set(data["enabled_features"]) == {"welcome", "inactive"}


# ---------------------------------------------------------------------------
# Campaign endpoints
# ---------------------------------------------------------------------------

class TestCampaignEndpoints:
    def test_current_empty_when_welcome_disabled(self, client, features):
        features(INACTIVE)
        client.post("/campaign/start", json={"name": "Hist"})
        assert client.get("/campaign/current").json() == {"active": False}

    def test_current_welcome_campaign_when_enabled(self, client, features):
        features(WELCOME)
        started = client.post("/campaign/start", json={"name": "Live"}).json()
        data = client.get("/campaign/current").json()
        assert data["active"] is True
        assert data["campaign"]["feature"] == "welcome"
        assert data["campaign"]["id"] == started["campaign"]["id"]

    def test_history_queryable_and_tagged_when_disabled(self, client, features):
        features(INACTIVE)
        started = client.post("/campaign/start", json={"name": "Hist"}).json()
        campaign_id = started["campaign"]["id"]

        listing = client.get("/campaigns").json()
        assert len(listing) == 1
        assert listing[0]["campaign"]["feature"] == "welcome"
        assert listing[0]["campaign"]["id"] == campaign_id

        detail = client.get(f"/campaign/{campaign_id}").json()
        assert detail["campaign"]["feature"] == "welcome"


# ---------------------------------------------------------------------------
# Historical preservation
# ---------------------------------------------------------------------------

class TestHistoricalPreservation:
    def test_disabled_records_remain_in_db(self, client, features):
        features(WELCOME, INACTIVE)
        _log_sms(WELCOME, "u1", status="delivered")
        _log_sms(INACTIVE, "u2", status="failed")

        # Move to Welcome-only: active views exclude the Inactive history.
        features(WELCOME)
        overview = client.get("/report/overview").json()
        assert overview["phone_sms"]["total"] == 1
        active_logs = client.get("/sms/logs").json()
        assert active_logs["total"] == 1

        # Historical inspection still sees the preserved Inactive row.
        history = client.get("/sms/logs", params={"kind": "inactive"}).json()
        assert history["total"] == 1
        assert history["items"][0]["status"] == "failed"
        assert client.get("/stats/inactive").json()["total"] == 1


# ---------------------------------------------------------------------------
# Account-level data
# ---------------------------------------------------------------------------

class TestAccountDataUnaffected:
    def test_balance_not_feature_filtered(self, client, features):
        features()
        with patch(
            "app.main.get_balance",
            new_callable=AsyncMock,
            return_value={"balance": 162968.58, "currency": "NGN"},
        ):
            r = client.get("/stats/balance")
        assert r.status_code == 200
        assert r.json()["balance"] == 162968.58