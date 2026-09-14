"""Tests for the post-sign-in (welcome) campaign logic."""
from __future__ import annotations

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
    get_welcome_analytics,
    get_welcome_sms_state,
    log_sms_batch,
    upsert_welcome_sent,
)
from app.processor import find_recent_login_users, refresh_welcome_tracking


def _ago(hours: float) -> pd.Timestamp:
    return pd.Timestamp(datetime.now() - timedelta(hours=hours))


@pytest.fixture()
def welcome_settings(_init_db, monkeypatch):
    """Deterministic welcome campaign settings against an isolated DB/data dir."""
    monkeypatch.setattr(settings, "WELCOME_EVAL_DELAY_HOURS", 1)
    monkeypatch.setattr(settings, "WELCOME_MAX_MESSAGES", 3)
    monkeypatch.setattr(settings, "WELCOME_POST_LIMIT_SUPPRESS", True)
    monkeypatch.setattr(settings, "COOLDOWN_HOURS", 24)
    return settings.DATA_FOLDER


def _write_regs(data_dir):
    rows = [
        {"userId": uid, "firstName": name, "email": f"{name}@x.com",
         "phone": phone, "timestamp": _ago(48)}
        for uid, name, phone in [("1", "Ada", "07870123456"), ("2", "Bola", "08012345678")]
    ]
    pd.DataFrame(rows).to_csv(data_dir / "Registrations_test.csv", index=False)


def _candidates(data_dir, logins, sales):
    _write_regs(data_dir)
    logins_df = pd.DataFrame(logins, columns=["userId", "timestamp"])
    sales_df = pd.DataFrame(sales, columns=["userId", "gameName", "amount", "timestamp"])
    return [u.user_id for u in find_recent_login_users(logins_df=logins_df, sales_df=sales_df)]


def _seed_welcome_logs(user_id: str, n: int, hours_ago: float):
    now = datetime.now(timezone.utc)
    records = [
        {
            "message_id": f"m_{user_id}_{i}",
            "user_id": user_id,
            "kind": "welcome",
            "phone": "2348012345678",
            "status": "sent",
            "sent_at": (now - timedelta(hours=hours_ago)).isoformat(),
        }
        for i in range(n)
    ]
    log_sms_batch(records)


class TestWelcomeEligibility:
    def test_eligible_after_delay_without_play(self, welcome_settings):
        # sign-in 5h ago (delay elapsed), no game in the hour after sign-in
        assert _candidates(welcome_settings, [("1", _ago(5))], [("9", "G", 1, _ago(5.5))]) == ["1"]

    def test_not_eligible_if_played_during_delay(self, welcome_settings):
        # played 30min after a sign-in 5h ago -> inside the 1h wait window
        assert _candidates(welcome_settings, [("1", _ago(5))], [("1", "G", 1, _ago(4.5))]) == []

    def test_play_before_log_in_does_not_disqualify(self, welcome_settings):
        # a play before the sign-in is outside the wait window
        assert _candidates(welcome_settings, [("1", _ago(5))], [("1", "G", 1, _ago(6))]) == ["1"]

    def test_play_after_wait_window_does_not_disqualify(self, welcome_settings):
        # played 2h after sign-in, but the 1h wait window has already passed
        assert _candidates(welcome_settings, [("1", _ago(5))], [("1", "G", 1, _ago(3))]) == ["1"]

    def test_delay_not_elapsed(self, welcome_settings):
        # sign-in 30min ago with a 1h delay -> not evaluated yet
        assert _candidates(welcome_settings, [("1", _ago(0.5))], [("9", "G", 1, _ago(0.2))]) == []

    def test_same_sign_in_not_welcomed_twice(self, welcome_settings):
        login_at = _ago(5)
        upsert_welcome_sent([{"user_id": "1", "last_login_at": login_at.isoformat()}])
        assert _candidates(welcome_settings, [("1", login_at)], [("9", "G", 1, _ago(4.5))]) == []

    def test_new_sign_in_eligible_after_previous_welcomed(self, welcome_settings):
        upsert_welcome_sent([{"user_id": "1", "last_login_at": _ago(20).isoformat()}])
        assert _candidates(welcome_settings, [("1", _ago(5))], [("9", "G", 1, _ago(4.5))]) == ["1"]

    def test_late_login_evaluated_eventually(self, welcome_settings):
        # an older sign-in whose delay elapsed long ago is still evaluated once
        assert _candidates(welcome_settings, [("1", _ago(25))], [("9", "G", 1, _ago(24.5))]) == ["1"]

    def test_only_latest_sign_in_is_evaluated(self, welcome_settings):
        # two sign-ins within the delay window: the later one supersedes the earlier
        logins = [("1", _ago(4)), ("1", _ago(3))]
        assert _candidates(welcome_settings, logins, [("9", "G", 1, _ago(3.5))]) == ["1"]


class TestWelcomeCooldownAndCap:
    def test_cooldown_active(self, welcome_settings):
        # one welcome 2h ago -> cooldown (24h) still active -> no send
        _seed_welcome_logs("1", 1, 2)
        assert _candidates(welcome_settings, [("1", _ago(5))], [("9", "G", 1, _ago(4.5))]) == []

    def test_cooldown_expired(self, welcome_settings):
        # one welcome 48h ago -> cooldown expired -> send allowed
        _seed_welcome_logs("1", 1, 48)
        assert _candidates(welcome_settings, [("1", _ago(5))], [("9", "G", 1, _ago(4.5))]) == ["1"]

    def test_max_messages_reached_with_suppress(self, welcome_settings):
        # 3 welcome SMS already sent, cooldown expired
        _seed_welcome_logs("1", 3, 48)
        assert _candidates(welcome_settings, [("1", _ago(5))], [("9", "G", 1, _ago(4.5))]) == []

    def test_max_messages_reached_without_suppress(self, welcome_settings, monkeypatch):
        monkeypatch.setattr(settings, "WELCOME_POST_LIMIT_SUPPRESS", False)
        _seed_welcome_logs("1", 3, 48)
        assert _candidates(welcome_settings, [("1", _ago(5))], [("9", "G", 1, _ago(4.5))]) == ["1"]

    def test_below_max_still_eligible(self, welcome_settings):
        _seed_welcome_logs("1", 2, 48)
        assert _candidates(welcome_settings, [("1", _ago(5))], [("9", "G", 1, _ago(4.5))]) == ["1"]

    def test_failed_and_deferred_dont_block_cooldown(self, welcome_settings):
        # failed/deferred sends are not "sent" -> no cooldown, no count
        now = datetime.now(timezone.utc).isoformat()
        log_sms_batch([
            {"message_id": None, "user_id": "1", "kind": "welcome", "phone": "x",
             "status": "failed", "sent_at": now},
            {"message_id": None, "user_id": "1", "kind": "welcome", "phone": "x",
             "status": "deferred", "sent_at": now},
        ])
        assert _candidates(welcome_settings, [("1", _ago(5))], [("9", "G", 1, _ago(4.5))]) == ["1"]


class TestWelcomeState:
    def test_counts_only_sent_and_delivered(self, _init_db):
        old = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        log_sms_batch([
            {"message_id": "a", "user_id": "1", "kind": "welcome", "phone": "x",
             "status": "sent", "sent_at": old},
            {"message_id": "b", "user_id": "1", "kind": "welcome", "phone": "x",
             "status": "delivered", "sent_at": now},
            {"message_id": "c", "user_id": "1", "kind": "welcome", "phone": "x",
             "status": "failed", "sent_at": now},
            {"message_id": "d", "user_id": "1", "kind": "welcome", "phone": "x",
             "status": "deferred", "sent_at": now},
            {"message_id": "e", "user_id": "2", "kind": "inactive", "phone": "x",
             "status": "sent", "sent_at": now},
        ])
        state = get_welcome_sms_state()
        assert state["1"]["count"] == 2
        assert state["1"]["last_sent_at"] == now
        assert "2" not in state


def _seed_welcome_send(user_id: str, hours_ago: float):
    log_sms_batch([
        {"message_id": f"trk_{user_id}_{hours_ago}", "user_id": user_id, "kind": "welcome",
         "phone": "2348012345678", "status": "sent",
         "sent_at": (datetime.now() - timedelta(hours=hours_ago)).isoformat()},
    ])


def _plays(*events):
    return pd.DataFrame(events, columns=["userId", "timestamp"])


class TestWelcomePostSendTracking:
    def test_disabled_when_window_zero(self, welcome_settings, monkeypatch):
        monkeypatch.setattr(settings, "WELCOME_POST_TRACK_HOURS", 0)
        assert refresh_welcome_tracking(_plays()) == {"disabled": True}

    def test_sends_ingested_from_log(self, welcome_settings, monkeypatch):
        monkeypatch.setattr(settings, "WELCOME_POST_TRACK_HOURS", 24)
        _seed_welcome_send("1", 10)
        refresh_welcome_tracking(_plays(("9", _ago(1))))
        assert get_welcome_analytics()["summary"]["tracked"] == 1

    def test_play_inside_window_marks_responded(self, welcome_settings, monkeypatch):
        monkeypatch.setattr(settings, "WELCOME_POST_TRACK_HOURS", 24)
        _seed_welcome_send("1", 10)
        # play 8h ago = 2h after the send, well inside the 24h window
        refresh_welcome_tracking(_plays(("1", _ago(8))))
        summary = get_welcome_analytics()["summary"]
        assert summary["responded"] == 1
        assert summary["no_response"] == 0

    def test_play_before_send_not_counted(self, welcome_settings, monkeypatch):
        monkeypatch.setattr(settings, "WELCOME_POST_TRACK_HOURS", 24)
        _seed_welcome_send("1", 10)
        # play 12h ago = 2h before the send -> outside (sent, sent+window]
        refresh_welcome_tracking(_plays(("1", _ago(12))))
        summary = get_welcome_analytics()["summary"]
        assert summary["responded"] == 0
        assert summary["pending"] == 1

    def test_window_closed_without_play_counts_no_response(self, welcome_settings, monkeypatch):
        monkeypatch.setattr(settings, "WELCOME_POST_TRACK_HOURS", 24)
        _seed_welcome_send("1", 30)
        refresh_welcome_tracking(_plays(("9", _ago(1))))
        summary = get_welcome_analytics()["summary"]
        assert summary["no_response"] == 1
        assert summary["pending"] == 0