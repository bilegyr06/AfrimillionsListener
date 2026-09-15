"""Feature-aware reporting scope for the active operator dashboard.

sms_log.kind distinguishes three sources:
  * welcome  — Feature 1 (welcome-back) campaign sends. Gated by ENABLED_FEATURES.
  * inactive — Feature 2 (inactivity reminder) sends. Gated by ENABLED_FEATURES.
  * manual   — operator /sms sends. Always visible, never a feature.

Active dashboard endpoints (/report/overview, /stats, default /sms/logs)
aggregate only the kinds that belong to currently enabled features, plus
manual operator sends. Explicit feature-specific endpoints (/stats/welcome,
/stats/inactive, campaign history) remain queryable regardless of enablement.

This module is the single source of truth for that scope so the rule is not
re-derived in every endpoint.
"""
from __future__ import annotations

from app.core.config import settings
from app.core.models import INACTIVE, WELCOME
from app.services.sms import MANUAL

#: sms_log kinds that map to campaign features.
FEATURE_KINDS = frozenset({WELCOME, INACTIVE})


def active_feature_kinds() -> set[str]:
    """The sms_log kinds whose campaign features are currently enabled.

    Unknown values in ENABLED_FEATURES are ignored; an empty enabled set is a
    valid state that means "no features" (never "all features").
    """
    return set(settings.ENABLED_FEATURES) & set(FEATURE_KINDS)


def active_sms_kinds() -> set[str]:
    """Kinds allowed in active dashboard SMS reporting.

    Enabled feature kinds plus manual operator sends. `manual` is deliberately
    NOT a feature — it is never added to ENABLED_FEATURES — but it stays
    visible because manual sends are an operator action, not feature output.
    """
    return active_feature_kinds() | {MANUAL}


def decorate_campaign(campaign: dict | None) -> dict | None:
    """Tag a campaign row with its feature so the API is unambiguous.

    Campaigns are Welcome-specific (welcome_campaigns). The tag lets the
    frontend tell active dashboard data apart from Welcome campaign history.
    """
    if campaign is None:
        return None
    return {**campaign, "feature": WELCOME}