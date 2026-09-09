"""Tests for the stats and metrics system."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

# Ensure env vars are set before any app imports.
os.environ.setdefault("TERMII_API_KEY", "test-key")
os.environ.setdefault("TERMII_BASE_URL", "https://test.api.termii.com/api")
os.environ.setdefault("TERMII_SENDER_ID", "TestSender")

from app.database import (
    get_connection,
    get_cycle_stats,
    get_latest_wallet,
    get_sms_logs,
    get_stats_summary,
    get_unsynced_sms,
    get_wallet_history,
    log_sms,
    log_sms_batch,
    log_wallet_snapshot,
    update_sms_status,
)
from app.stats_updater import sync_delivery_statuses


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_sms_log_table_exists(self, _init_db):
        conn = get_connection()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        names = {t["name"] for t in tables}
        assert "sms_log" in names
        assert "wallet_log" in names

    def test_sms_log_columns(self, _init_db):
        conn = get_connection()
        cols = conn.execute("PRAGMA table_info(sms_log)").fetchall()
        conn.close()
        col_names = {c["name"] for c in cols}
        assert "id" in col_names
        assert "message_id" in col_names
        assert "user_id" in col_names
        assert "kind" in col_names
        assert "phone" in col_names
        assert "status" in col_names
        assert "cost" in col_names
        assert "balance_after" in col_names
        assert "cycle_id" in col_names
        assert "sent_at" in col_names

    def test_wallet_log_columns(self, _init_db):
        conn = get_connection()
        cols = conn.execute("PRAGMA table_info(wallet_log)").fetchall()
        conn.close()
        col_names = {c["name"] for c in cols}
        assert "id" in col_names
        assert "balance" in col_names
        assert "currency" in col_names
        assert "fetched_at" in col_names


# ---------------------------------------------------------------------------
# SMS log CRUD
# ---------------------------------------------------------------------------

class TestSmsLog:
    def _record(self, **overrides) -> dict:
        base = {
            "user_id": "u1",
            "kind": "welcome",
            "phone": "2348012345678",
            "status": "sent",
            "cost": 0,
            "cycle_id": "cycle1",
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        base.update(overrides)
        return base

    def test_log_sms_single(self, _init_db):
        log_sms(self._record(message_id="msg_001"))
        logs = get_sms_logs()
        assert len(logs) == 1
        assert logs[0]["message_id"] == "msg_001"
        assert logs[0]["status"] == "sent"

    def test_log_sms_no_message_id(self, _init_db):
        log_sms(self._record(status="failed"))
        logs = get_sms_logs()
        assert len(logs) == 1
        assert logs[0]["message_id"] is None
        assert logs[0]["status"] == "failed"

    def test_log_sms_batch(self, _init_db):
        records = [self._record(user_id=f"u{i}") for i in range(5)]
        log_sms_batch(records)
        logs = get_sms_logs()
        assert len(logs) == 5

    def test_log_sms_batch_empty(self, _init_db):
        log_sms_batch([])
        logs = get_sms_logs()
        assert len(logs) == 0

    def test_update_sms_status(self, _init_db):
        log_sms(self._record(message_id="msg_002"))
        update_sms_status("msg_002", "delivered", cost=1.5)
        logs = get_sms_logs()
        assert logs[0]["status"] == "delivered"
        assert logs[0]["cost"] == 1.5

    def test_update_sms_status_without_cost(self, _init_db):
        log_sms(self._record(message_id="msg_003"))
        update_sms_status("msg_003", "dnd")
        logs = get_sms_logs()
        assert logs[0]["status"] == "dnd"

    def test_get_sms_logs_by_kind(self, _init_db):
        log_sms(self._record(kind="welcome"))
        log_sms(self._record(kind="inactive", user_id="u2"))
        assert len(get_sms_logs(kind="welcome")) == 1
        assert len(get_sms_logs(kind="inactive")) == 1

    def test_get_sms_logs_by_date_range(self, _init_db):
        log_sms(self._record(sent_at="2026-01-15T10:00:00+00:00"))
        log_sms(self._record(sent_at="2026-06-15T10:00:00+00:00", user_id="u2"))
        log_sms(self._record(sent_at="2026-12-15T10:00:00+00:00", user_id="u3"))

        assert len(get_sms_logs(since="2026-03-01")) == 2
        assert len(get_sms_logs(until="2026-03-01")) == 1
        assert len(get_sms_logs(since="2026-06-01", until="2026-12-01")) == 1

    def test_get_unsynced_sms(self, _init_db):
        log_sms(self._record(message_id="msg_a", status="sent"))
        log_sms(self._record(message_id="msg_b", status="delivered", user_id="u2"))
        log_sms(self._record(message_id="msg_c", status="dnd", user_id="u3"))
        log_sms(self._record(message_id="msg_d", status="deferred", user_id="u4"))
        log_sms(self._record(status="failed", user_id="u5"))

        unsynced = get_unsynced_sms()
        ids = {r["message_id"] for r in unsynced}
        assert "msg_a" in ids
        assert "msg_d" in ids
        assert "msg_b" not in ids
        assert "msg_c" not in ids


# ---------------------------------------------------------------------------
# Stats aggregation
# ---------------------------------------------------------------------------

class TestStatsSummary:
    def _seed(self):
        log_sms({"user_id": "u1", "kind": "welcome", "phone": "2341",
                  "status": "sent", "cost": 2.0, "sent_at": "2026-06-01T10:00:00+00:00"})
        log_sms({"user_id": "u2", "kind": "welcome", "phone": "2342",
                  "status": "delivered", "cost": 2.0, "sent_at": "2026-06-01T11:00:00+00:00"})
        log_sms({"user_id": "u3", "kind": "inactive", "phone": "2343",
                  "status": "failed", "cost": 0, "sent_at": "2026-06-02T10:00:00+00:00"})
        log_sms({"user_id": "u4", "kind": "inactive", "phone": "2344",
                  "status": "dnd", "cost": 1.0, "sent_at": "2026-06-02T11:00:00+00:00"})
        log_sms({"user_id": "u5", "kind": "welcome", "phone": "2345",
                  "status": "deferred", "cost": 0, "sent_at": "2026-06-03T10:00:00+00:00"})

    def test_totals(self, _init_db):
        self._seed()
        s = get_stats_summary()
        assert s["total"] == 5
        assert s["sent"] == 2  # 'sent' + 'delivered' statuses combined
        assert s["failed"] == 1
        assert s["delivered"] == 1
        assert s["dnd"] == 1
        assert s["deferred"] == 1
        assert s["total_cost"] == 5.0

    def test_filter_by_kind(self, _init_db):
        self._seed()
        w = get_stats_summary(kind="welcome")
        assert w["total"] == 3
        i = get_stats_summary(kind="inactive")
        assert i["total"] == 2

    def test_filter_by_date(self, _init_db):
        self._seed()
        # since/until are compared as strings against ISO timestamps
        s = get_stats_summary(since="2026-06-02")
        assert s["total"] == 3  # u3, u4, u5
        s = get_stats_summary(since="2026-06-03")
        assert s["total"] == 1  # u5 only
        s = get_stats_summary(until="2026-06-01T23:59:59")
        assert s["total"] == 2  # u1, u2


# ---------------------------------------------------------------------------
# Cycle stats
# ---------------------------------------------------------------------------

class TestCycleStats:
    def test_grouping_by_cycle(self, _init_db):
        now = datetime.now(timezone.utc).isoformat()
        log_sms({"user_id": "u1", "kind": "welcome", "phone": "2341",
                  "status": "sent", "cycle_id": "c1", "sent_at": now})
        log_sms({"user_id": "u2", "kind": "inactive", "phone": "2342",
                  "status": "failed", "cycle_id": "c1", "sent_at": now})
        log_sms({"user_id": "u3", "kind": "welcome", "phone": "2343",
                  "status": "sent", "cycle_id": "c2", "sent_at": now})

        cycles = get_cycle_stats()
        assert len(cycles) == 2
        c1 = next(c for c in cycles if c["cycle_id"] == "c1")
        assert c1["total"] == 2
        assert c1["sent"] == 1
        assert c1["failed"] == 1

    def test_no_cycle_id_excluded(self, _init_db):
        now = datetime.now(timezone.utc).isoformat()
        log_sms({"user_id": "u1", "kind": "welcome", "phone": "2341",
                  "status": "deferred", "sent_at": now})
        cycles = get_cycle_stats()
        assert len(cycles) == 0


# ---------------------------------------------------------------------------
# Wallet log
# ---------------------------------------------------------------------------

class TestWalletLog:
    def test_log_and_retrieve(self, _init_db):
        log_wallet_snapshot(100.50, "NGN")
        log_wallet_snapshot(95.25, "NGN")
        history = get_wallet_history()
        assert len(history) == 2
        assert history[0]["balance"] == 95.25  # newest first
        assert history[1]["balance"] == 100.50

    def test_latest_wallet(self, _init_db):
        assert get_latest_wallet() is None
        log_wallet_snapshot(50.0, "NGN")
        log_wallet_snapshot(40.0, "NGN")
        latest = get_latest_wallet()
        assert latest is not None
        assert latest["balance"] == 40.0


# ---------------------------------------------------------------------------
# Stats updater (delivery sync)
# ---------------------------------------------------------------------------

class TestStatsUpdater:
    @pytest.mark.asyncio
    async def test_sync_updates_delivered(self, _init_db):
        log_sms({
            "message_id": "msg_sync_1", "user_id": "u1", "kind": "welcome",
            "phone": "2341", "status": "sent", "sent_at": datetime.now(timezone.utc).isoformat(),
        })
        mock_history = [{"status": "Delivered", "amount": 2.5}]

        async def _mock_get_history(mid):
            return mock_history

        with patch(
            "app.stats_updater.get_message_history",
            side_effect=_mock_get_history,
        ) as mock_fn:
            result = await sync_delivery_statuses()

        mock_fn.assert_called_once_with("msg_sync_1")
        assert result["checked"] == 1
        assert result["updated"] == 1
        logs = get_sms_logs()
        assert logs[0]["status"] == "delivered"
        assert logs[0]["cost"] == 2.5

    @pytest.mark.asyncio
    async def test_sync_skips_terminal_statuses(self, _init_db):
        log_sms({
            "message_id": "msg_sync_2", "user_id": "u1", "kind": "welcome",
            "phone": "2341", "status": "delivered", "sent_at": datetime.now(timezone.utc).isoformat(),
        })

        with patch(
            "app.stats_updater.get_message_history",
            new_callable=AsyncMock,
        ) as mock_fn:
            result = await sync_delivery_statuses()
            mock_fn.assert_not_called()

        assert result["checked"] == 0

    @pytest.mark.asyncio
    async def test_sync_handles_api_exception(self, _init_db):
        log_sms({
            "message_id": "msg_sync_3", "user_id": "u1", "kind": "welcome",
            "phone": "2341", "status": "sent", "sent_at": datetime.now(timezone.utc).isoformat(),
        })

        with patch(
            "app.stats_updater.get_message_history",
            new_callable=AsyncMock,
            side_effect=Exception("Termii API unreachable"),
        ):
            result = await sync_delivery_statuses()

        assert result["checked"] == 1
        assert result["errors"] == 1

    @pytest.mark.asyncio
    async def test_sync_handles_api_returning_none(self, _init_db):
        log_sms({
            "message_id": "msg_sync_5", "user_id": "u1", "kind": "welcome",
            "phone": "2341", "status": "sent", "sent_at": datetime.now(timezone.utc).isoformat(),
        })

        with patch(
            "app.stats_updater.get_message_history",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await sync_delivery_statuses()

        assert result["checked"] == 1
        assert result["updated"] == 0
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_sync_handles_dnd(self, _init_db):
        log_sms({
            "message_id": "msg_sync_4", "user_id": "u1", "kind": "inactive",
            "phone": "2341", "status": "sent", "sent_at": datetime.now(timezone.utc).isoformat(),
        })
        mock_history = [{"status": "DND Active on Phone Number", "amount": 1.0}]

        with patch(
            "app.stats_updater.get_message_history",
            new_callable=AsyncMock,
            return_value=mock_history,
        ):
            result = await sync_delivery_statuses()

        assert result["updated"] == 1
        logs = get_sms_logs()
        assert logs[0]["status"] == "dnd"


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

class TestStatsAPI:
    @pytest.fixture()
    def client(self, _init_db):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def _seed_data(self):
        now = datetime.now(timezone.utc).isoformat()
        log_sms({"user_id": "u1", "kind": "welcome", "phone": "2341",
                  "status": "delivered", "cost": 2.0, "cycle_id": "c1", "sent_at": now})
        log_sms({"user_id": "u2", "kind": "inactive", "phone": "2342",
                  "status": "failed", "cost": 0, "cycle_id": "c1", "sent_at": now})
        log_wallet_snapshot(500.0, "NGN")

    def test_stats_overall(self, client):
        self._seed_data()
        r = client.get("/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert data["delivered"] == 1
        assert data["failed"] == 1

    def test_stats_welcome(self, client):
        self._seed_data()
        r = client.get("/stats/welcome")
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_stats_inactive(self, client):
        self._seed_data()
        r = client.get("/stats/inactive")
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_stats_cycles(self, client):
        self._seed_data()
        r = client.get("/stats/cycles")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["total"] == 2

    def test_stats_wallet(self, client):
        self._seed_data()
        r = client.get("/stats/wallet")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["balance"] == 500.0

    def test_stats_balance_mocked(self, client):
        with patch(
            "app.main.get_balance",
            new_callable=AsyncMock,
            return_value={"balance": 250.0, "currency": "NGN"},
        ):
            r = client.get("/stats/balance")
        assert r.status_code == 200
        assert r.json()["balance"] == 250.0

    def test_stats_balance_failure(self, client):
        with patch(
            "app.main.get_balance",
            new_callable=AsyncMock,
            return_value=None,
        ):
            r = client.get("/stats/balance")
        assert r.status_code == 200
        assert "Failed" in r.json()["message"]

    def test_stats_sync_mocked(self, client):
        with patch(
            "app.main.sync_delivery_statuses",
            new_callable=AsyncMock,
            return_value={"checked": 3, "updated": 2, "errors": 0},
        ):
            r = client.post("/stats/sync")
        assert r.status_code == 200
        assert r.json()["updated"] == 2
