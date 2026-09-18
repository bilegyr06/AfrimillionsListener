"""Feature registry: the single source of truth for campaign features.

A feature is declared once as a Feature(kind, gate, template, pipeline) and
registered by the module that owns its pipeline. Everything that needs to
know which features exist (reporting scope, cycle selection, trigger loop)
reads from the registry instead of hardcoding feature kinds, so a third
feature is simply one more registration.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.features import (
    FEATURES,
    Feature,
    all_feature_kinds,
    enabled_feature_kinds,
    get_feature,
    register_feature,
)
from app.core.models import INACTIVE, WELCOME


class TestFeatureRegistrySeed:
    """The two production features are registered and exposed by the registry."""

    def test_both_features_declared(self):
        assert WELCOME in FEATURES
        assert INACTIVE in FEATURES

    def test_all_feature_kinds(self):
        assert all_feature_kinds() == frozenset({WELCOME, INACTIVE})

    def test_get_feature(self):
        assert get_feature(WELCOME) is FEATURES[WELCOME]
        assert get_feature("no-such-feature") is None

    def test_registered_features_carry_kind_gate_template_pipeline(self):
        for feature in FEATURES.values():
            assert feature.kind
            assert callable(feature.gate)
            assert callable(feature.pipeline)
            assert isinstance(feature.template, str)


class TestFeatureGates:
    """The gate decides whether a feature is part of the active scope."""

    def test_inactive_is_excluded_when_feature_disabled(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "ENABLED_FEATURES", {WELCOME})
        assert enabled_feature_kinds() == {WELCOME}

    def test_all_disabled_yields_empty_scope(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "ENABLED_FEATURES", set())
        assert enabled_feature_kinds() == set()


class TestFeatureExtension:
    """Registering a third feature makes it flow through generic loops."""

    def _register_third(self):
        from app.core.config import settings

        async def _third(deadline, cycle_id, logins_df=None):
            return {"sent": 0}

        return register_feature(
            Feature(
                kind="followup",
                gate=lambda: "followup" in settings.ENABLED_FEATURES,
                template=settings.INACTIVE_MESSAGE,
                pipeline=_third,
            )
        )

    def test_extension_adds_to_registry_and_reporting_scope(self, monkeypatch):
        from app.core.config import settings

        self._register_third()
        monkeypatch.setattr(settings, "ENABLED_FEATURES", {"welcome", "inactive", "followup"})

        from app.services import reporting

        assert "followup" in reporting.FEATURE_KINDS
        assert "followup" in reporting.active_feature_kinds()
        assert "followup" in reporting.active_sms_kinds()

    def test_third_feature_is_run_by_generic_loop(self, monkeypatch):
        from datetime import datetime, timezone

        from app.core.config import settings
        from app.workers.processor import _run_features

        captured = []

        async def _fake(deadline, cycle_id, logins_df=None):
            captured.append(cycle_id)
            return {"sent": 0}

        register_feature(
            Feature(
                kind="followup",
                gate=lambda: "followup" in settings.ENABLED_FEATURES,
                template=settings.INACTIVE_MESSAGE,
                pipeline=_fake,
            )
        )
        monkeypatch.setattr(settings, "ENABLED_FEATURES", {"welcome", "inactive", "followup"})

        deadline = datetime(2026, 9, 17, 15, tzinfo=timezone.utc)
        result = asyncio.run(_run_features(deadline, features={"followup"}))

        assert captured == [result["cycle_id"]]
        assert result["features"]["followup"]["sent"] == 0
