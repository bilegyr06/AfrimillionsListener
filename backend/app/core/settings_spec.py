"""Operator-facing setting registry.

Declares which settings an operator may configure, how each is typed, parsed,
and serialized, and how the UI should render the input. Secrets and
infrastructure paths are deliberately NOT listed here — they stay env-only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time


def _parse_time(raw: str) -> time:
    parts = raw.strip().split(":")
    return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


def _serialize_time(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


_TRUTHY = ("1", "true", "yes", "on")


@dataclass(frozen=True)
class SettingSpec:
    key: str
    kind: str  # str | int | float | bool | time | set
    label: str
    description: str
    input_type: str  # text | textarea | number | time | checkbox | tags

    def parse(self, raw: str):
        if self.kind == "int":
            return int(raw)
        if self.kind == "float":
            return float(raw)
        if self.kind == "bool":
            return raw.strip().lower() in _TRUTHY if raw else False
        if self.kind == "time":
            return _parse_time(raw)
        if self.kind == "set":
            return {item.strip() for item in raw.split(",") if item.strip()}
        return raw

    def serialize(self, value) -> str:
        if self.kind == "bool":
            return "true" if value else "false"
        if self.kind == "time":
            return _serialize_time(value)
        if self.kind == "set":
            return ",".join(sorted(value))
        return str(value)


def _spec(key, kind, label, description, input_type) -> SettingSpec:
    return SettingSpec(key=key, kind=kind, label=label, description=description, input_type=input_type)


#: key -> SettingSpec, ordered for stable display.
SPECS: dict[str, SettingSpec] = {
    s.key: s
    for s in (
        _spec("TERMII_SENDER_ID", "str", "Sender ID", "Sender ID shown on delivered SMS.", "text"),
        _spec("INACTIVITY_HOURS", "int", "Inactivity threshold (hours)", "Users idle this long may receive a reminder.", "number"),
        _spec("COOLDOWN_HOURS", "int", "Cooldown (hours)", "Minimum gap between messages to the same user.", "number"),
        _spec("MAX_MESSAGES", "int", "Max messages per user", "Lifetime cap for inactivity reminders; 0 = unlimited.", "number"),
        _spec("MAX_CONCURRENCY", "int", "Max concurrent sends", "Parallel SMS requests allowed per cycle.", "number"),
        _spec("SMS_TIMEOUT", "float", "Provider timeout (seconds)", "Per-request timeout for Termii calls.", "number"),
        _spec("CYCLE_END_HOUR", "int", "Cycle end hour", "Cycles never start or continue a send after this hour (0-23).", "number"),
        _spec("START_TIME", "time", "Sending window start", "Earliest time a cycle may begin (HH:MM).", "time"),
        _spec("END_TIME", "time", "Sending window end", "Latest time a cycle may run (HH:MM).", "time"),
        _spec("ENABLED_FEATURES", "set", "Enabled features", "Comma-separated list: welcome, inactive.", "tags"),
        _spec("WELCOME_EVAL_DELAY_HOURS", "float", "Welcome evaluation delay (hours)", "Wait before a sign-in is eligible for a welcome SMS.", "number"),
        _spec("WELCOME_MAX_MESSAGES", "int", "Welcome cap per user", "Lifetime cap of accepted welcome sends; 0 = unlimited.", "number"),
        _spec("WELCOME_POST_LIMIT_SUPPRESS", "bool", "Suppress after cap", "Stop sending when a user has reached their cap.", "checkbox"),
        _spec("WELCOME_MESSAGE", "str", "Welcome SMS template", "Template with {first_name} placeholder.", "textarea"),
        _spec("INACTIVE_MESSAGE", "str", "Inactive SMS template", "Template with {first_name} placeholder.", "textarea"),
        _spec("CSV_DOWNLOADER_ENABLED", "bool", "Auto CSV downloader", "Allow the scraper to download CSVs from ALOT BI.", "checkbox"),
    )
}