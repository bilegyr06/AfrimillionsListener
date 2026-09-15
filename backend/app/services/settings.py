"""Compact API over the persisted operator settings.

The runtime `settings` object (core.config) is populated from the environment.
After startup, the database becomes the source of truth: values edited by an
operator are stored there and re-applied on every boot.
"""
from __future__ import annotations

import json

from app.core.config import settings
from app.core.settings_spec import SPECS
from app.db.settings import get_all_settings, upsert_setting


def apply_persisted_settings():
    """Overlay persisted values onto the runtime settings object.

    Unrecognized keys and unparseable values are skipped defensively.
    """
    persisted = get_all_settings()
    for key, raw in persisted.items():
        spec = SPECS.get(key)
        if spec is None:
            continue
        try:
            setattr(settings, key, spec.parse(raw))
        except (ValueError, TypeError):
            continue


def update_setting(key: str, raw_value: str) -> dict:
    """Persist one setting AND apply it to the running configuration.

    Returns a description of the change for the API response. Raises KeyError
    for unknown settings (routes translate this to 404/400).
    """
    spec = SPECS[key]
    value = spec.parse(raw_value)
    setattr(settings, key, value)
    upsert_setting(key, spec.serialize(value))
    return {"key": key, "label": spec.label, "value": spec.serialize(value)}


def list_operator_settings() -> list[dict]:
    """Settings in display order with current effective values + UI hints."""
    persisted = get_all_settings()
    out = []
    for key, spec in SPECS.items():
        raw = persisted.get(key, spec.serialize(getattr(settings, key, "")))
        current = getattr(settings, key, spec.parse(raw))
        rendered = spec.serialize(current) if not isinstance(current, str) else current
        out.append({
            "key": key,
            "label": spec.label,
            "description": spec.description,
            "input_type": spec.input_type,
            "kind": spec.kind,
            "value": rendered,
        })
    return out


def build_campaign_config_snapshot() -> str:
    """JSON snapshot of the operator settings at campaign start (audit only)."""
    return json.dumps(
        {key: spec.serialize(getattr(settings, key)) for key, spec in SPECS.items()},
        sort_keys=True,
    )