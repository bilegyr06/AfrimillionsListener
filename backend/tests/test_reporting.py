"""Tests for the operator reporting API and settings endpoints."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("TERMII_API_KEY", "test-key")
os.environ.setdefault("TERMII_BASE_URL", "https://test.api.termii.com/api")
os.environ.setdefault("TERMII_SENDER_ID", "TestSender")

from app.core.config import settings
from app.db.database import get_connection, upsert_opportunities


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_sms(kind, user_id, status="sent", phone="2348012345678"):
    from app.db.database import log_sms
    log_sms({
        "message_id": f"m_{user_id}",
        "user_id": user_id,
        "kind": kind,
        "phone": phone,
        "status": status,
        "cost": 1.0,
        "cycle_id": "c1",
        "sent_at": _now_iso(),
    })


@pytest.fixture()
def client(_init_db):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


class TestReportOverview:
    def test_overview_shape(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ENABLED_FEATURES", {"welcome", "inactive"})
        _log_sms("welcome", "u1", status="delivered")
        _log_sms("inactive", "u2", status="failed")
        r = client.get("/report/overview")
        assert r.status_code == 200
        data = r.json()

        assert data["phone_sms"]["total"] == 2
        assert data["phone_sms"]["delivered"] == 1
        assert data["phone_sms"]["failed"] == 1
        assert data["phone_sms"]["today"] == 2
        assert data["campaign"]["active"] is False
        assert data["files"]["total"] == 0
        assert data["wallet"] is None

    def test_overview_with_active_campaign(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ENABLED_FEATURES", {"welcome", "inactive"})
        started = client.post("/campaign/start", json={"name": "Wave"}).json()
        data = client.get("/report/overview").json()
        assert data["campaign"]["active"] is True
        assert data["campaign"]["current"]["campaign_id"] == started["campaign"]["id"]

    def test_overview_includes_uploaded_files(self, client):
        content = "userId,firstName,phone\n1,Ada,08012345678\n"
        client.post("/files", files={"file": ("Registrations_x.csv", content.encode(), "text/csv")})
        data = client.get("/report/overview").json()
        assert data["files"]["total"] == 1


class TestSmsLogsPaginated:
    def test_pagination(self, client, monkeypatch):
        monkeypatch.setattr(settings, "ENABLED_FEATURES", {"welcome", "inactive"})
        _log_sms("welcome", "u1")
        _log_sms("welcome", "u2")
        _log_sms("inactive", "u3")

        r = client.get("/sms/logs", params={"page_size": 2})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert data["pages"] == 2
        assert len(data["items"]) == 2

        page2 = client.get("/sms/logs", params={"page": 2, "page_size": 2}).json()
        assert len(page2["items"]) == 1

    def test_kind_filter(self, client):
        _log_sms("welcome", "u1")
        _log_sms("inactive", "u2")
        data = client.get("/sms/logs", params={"kind": "manual"}).json()
        assert data["total"] == 0
        data = client.get("/sms/logs", params={"kind": "inactive"}).json()
        assert data["total"] == 1


class TestCampaignDetailAndCustomers:
    def test_detail_returns_config_snapshot(self, client):
        started = client.post("/campaign/start", json={"name": "S1"}).json()
        r = client.get(f"/campaign/{started['campaign']['id']}")
        assert r.status_code == 200
        data = r.json()
        assert data["campaign"]["name"] == "S1"
        assert "TERMII_SENDER_ID" in data["campaign"]["config"]
        assert data["stats"]["campaign_id"] == started["campaign"]["id"]

    def test_detail_404(self, client):
        assert client.get("/campaign/99999").status_code == 404

    def test_customers_paginated(self, client):
        started = client.post("/campaign/start", json={"name": "S2"}).json()
        campaign_id = started["campaign"]["id"]
        now = datetime.now(timezone.utc)
        upsert_opportunities([{
            "campaign_id": campaign_id,
            "user_id": str(i),
            "first_name": f"U{i}",
            "phone_raw": "08012345678",
            "phone_normalized": "2348012345678",
            "login_at": (now - timedelta(minutes=i)).isoformat(),
            "eval_delay_hours": 1,
        } for i in range(3)])

        r = client.get(f"/campaign/{campaign_id}/customers", params={"page_size": 2})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert data["pages"] == 2
        assert len(data["items"]) == 2
        assert data["items"][0]["opportunity_status"] == "created"

    def test_customers_404(self, client):
        assert client.get("/campaign/99999/customers").status_code == 404


class TestFilesList:
    def test_lists_upload_success(self, client):
        content = "userId,firstName,phone\n1,Ada,08012345678\n"
        r = client.post("/files", files={"file": ("Registrations_a.csv", content.encode(), "text/csv")})
        record_id = r.json()["record"]["id"]
        data = client.get("/files").json()
        assert data["items"][0]["id"] == record_id


class TestSettingsApi:
    def test_get_settings(self, client):
        data = client.get("/settings").json()
        keys = {row["key"] for row in data["items"]}
        assert "COOLDOWN_HOURS" in keys
        assert "TERMII_API_KEY" not in keys

    def test_post_and_get_round_trip(self, client, monkeypatch):
        original = settings.COOLDOWN_HOURS
        r = client.post("/settings", json={"key": "COOLDOWN_HOURS", "value": "82"})
        assert r.status_code == 200
        assert r.json()["setting"]["value"] == "82"
        assert settings.COOLDOWN_HOURS == 82

        rows = {row["key"]: row["value"] for row in client.get("/settings").json()["items"]}
        assert rows["COOLDOWN_HOURS"] == "82"
        monkeypatch.setattr(settings, "COOLDOWN_HOURS", original)

    def test_unknown_setting_404(self, client):
        assert client.post("/settings", json={"key": "NOPE", "value": "1"}).status_code == 404

    def test_invalid_value_400(self, client, monkeypatch):
        original = settings.INACTIVITY_HOURS
        r = client.post("/settings", json={"key": "INACTIVITY_HOURS", "value": "not-a-number"})
        assert r.status_code == 400
        assert settings.INACTIVITY_HOURS == original
        monkeypatch.setattr(settings, "INACTIVITY_HOURS", original)