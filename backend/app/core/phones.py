"""Canonical phone-number boundary.

Every phone value entering the system (data files, manual send requests) flows
through this module before any dispatch decision. Raw values are normalized to
the international form Termii accepts (leading "234", no "+"), then validated
against the Nigerian-mobile rule. No invalid number may reach the provider.

    raw phone -> normalize_phone -> is_valid_nigerian_phone -> validate_phone
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_NIGERIAN_MOBILE = re.compile(r"234[789]\d{9}\Z")

REASON_NOT_RECOGNIZED = "Phone number could not be recognized."
REASON_NOT_NIGERIAN_MOBILE = "Phone number is not a valid Nigerian mobile number."


@dataclass(frozen=True)
class PhoneCheck:
    """Result of the canonical phone path."""

    canonical: str | None
    reason: str | None = None


def normalize_phone(raw) -> str | None:
    """Convert a stored phone value to Termii international format.

    Returns the normalized number (e.g. 0787077... -> 2347870...) or None when
    the value carries no recognizable phone digits.
    """
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None

    if digits.startswith("0") and len(digits) == 11:
        return "234" + digits[1:]
    if len(digits) == 10:
        return "234" + digits
    if digits.startswith("234") and len(digits) == 13:
        return digits
    if digits.startswith("233") and len(digits) == 12:
        return digits
    return None


def is_valid_nigerian_phone(normalized: str | None) -> bool:
    """Hard check for the supported format: '234' + 7/8/9 + 9 digits."""
    if not normalized:
        return False
    return _NIGERIAN_MOBILE.match(normalized) is not None


def validate_phone(raw) -> PhoneCheck:
    """Apply the full canonical path: normalize, then validate.

    Returns a PhoneCheck carrying the canonical number when usable, otherwise
    a human-readable rejection reason (canonical is None).
    """
    normalized = normalize_phone(raw)
    if normalized is None:
        return PhoneCheck(None, REASON_NOT_RECOGNIZED)
    if not is_valid_nigerian_phone(normalized):
        return PhoneCheck(None, REASON_NOT_NIGERIAN_MOBILE)
    return PhoneCheck(normalized, None)


def gate_phone(raw) -> str | None:
    """Return the canonical number only when the raw value is a sendable
    Nigerian mobile, otherwise None (backward-compatible gate)."""
    return validate_phone(raw).canonical