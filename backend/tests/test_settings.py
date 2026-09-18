"""Tests for persistent operator settings and campaign config snapshots."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("TERMII_API_KEY", "test-key")
os.environ.setdefault("TERMII_BASE_URL", "https://test.api.termii.com/api")
os.environ.setdefault("TERMII_SENDER_ID", "TestSender")

from app.core.config import settings
from app.core.settings_spec import SPECS
from app.db.campaigns import get_campaign
from app.db.settings import (
    get_all_settings,
    seed_settings_from_db,
    upsert_setting,
)
from app.services.campaigns import start_campaign
from app.services.settings import (
    apply_persisted_settings,
    list_operator_settings,
    update_setting,
)


class TestSeed:
    def test_seed_populates_all_spec_keys(self, _init_db):
        seed_settings_from_db()
        persisted = get_all_settings()
        assert set(SPECS) <= set(persisted)

    def test_seed_round_trips_live_values(self, _init_db):
        seed_settings_from_db()
        persisted = get_all_settings()
        for key, spec in SPECS.items():
            assert spec.parse(persisted[key]) == getattr(settings, key), key

    def test_seed_is_idempotent(self, _init_db):
        seed_settings_from_db()
        before = get_all_settings()
        upsert_setting("COOLDOWN_HOURS", "77")
        seed_settings_from_db()
        after = get_all_settings()
        # Existing (operator-edited) values are never overwritten.
        assert after["COOLDOWN_HOURS"] == "77"
        assert len(before) == len(after) == len(SPECS)


class TestApply:
    def test_apply_overlays_persisted_value(self, _init_db, monkeypatch):
        seed_settings_from_db()
        original = settings.COOLDOWN_HOURS
        upsert_setting("COOLDOWN_HOURS", "72")
        apply_persisted_settings()
        assert settings.COOLDOWN_HOURS == 72
        monkeypatch.setattr(settings, "COOLDOWN_HOURS", original)

    def test_apply_ignores_unknown_keys(self, _init_db, monkeypatch):
        seed_settings_from_db()
        upsert_setting("NOT_A_SETTING", "oops")
        before = get_all_settings()
        apply_persisted_settings()  # must not raise
        assert "NOT_A_SETTING" in get_all_settings()

    def test_type_parsing(self, _init_db, monkeypatch):
        seed_settings_from_db()
        originals = (settings.START_TIME, settings.INACTIVITY_HOURS)
        upsert_setting("START_TIME", "08:45")
        upsert_setting("INACTIVITY_HOURS", "13")
        apply_persisted_settings()
        assert settings.START_TIME.hour == 8
        assert settings.START_TIME.minute == 45
        assert settings.INACTIVITY_HOURS == 13
        monkeypatch.setattr(settings, "START_TIME", originals[0])
        monkeypatch.setattr(settings, "INACTIVITY_HOURS", originals[1])


class TestExposure:
    def test_secrets_not_operator_settings(self):
        assert "TERMII_API_KEY" not in SPECS
        assert "TERMII_BASE_URL" not in SPECS
        assert "ALOTBI_PASSWORD" not in SPECS
        assert "DB_PATH" not in SPECS

    def test_list_operator_settings_is_curated(self, _init_db):
        seed_settings_from_db()
        rows = list_operator_settings()
        assert {r["key"] for r in rows} == set(SPECS)
        for row in rows:
            assert row["label"]
            assert row["input_type"]
            assert row["kind"]
            assert row["value"] is not None

    def test_update_setting_persists_and_applies(self, _init_db, monkeypatch):
        seed_settings_from_db()
        original = settings.COOLDOWN_HOURS
        result = update_setting("COOLDOWN_HOURS", "100")
        assert result["value"] == "100"
        assert settings.COOLDOWN_HOURS == 100
        assert get_all_settings()["COOLDOWN_HOURS"] == "100"
        monkeypatch.setattr(settings, "COOLDOWN_HOURS", original)

    def test_update_setting_unknown_raises(self, _init_db):
        with pytest.raises(KeyError):
            update_setting("NOPE", "1")


class TestCampaignSnapshot:
    def test_start_snapshots_config(self, _init_db):
        seed_settings_from_db()
        apply_persisted_settings()
        result = start_campaign(name="snapshot-test")
        campaign = get_campaign(result["campaign"]["id"])
        snapshot = json.loads(campaign["config"])
        assert set(SPECS) <= set(snapshot)
        assert snapshot["TERMII_SENDER_ID"] == settings.TERMII_SENDER_ID

    def test_plain_db_create_leaves_config_none(self, _init_db):
        from app.db.campaigns import create_campaign
        result = create_campaign(name="plain")
        campaign = get_campaign(result["campaign"]["id"])
        assert campaign["config"] is None