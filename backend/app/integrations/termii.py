"""Termii provider adapter behind the SMSGateway seam.

TermiiGateway implements the application-facing SMSGateway contract (send,
balance, history). Termii-specific concerns - HTTP requests, response shapes,
and error handling - stay here and never leak into the dispatcher or services.
This module is the only place that talks to the Termii REST API.
"""
from __future__ import annotations

import httpx

from app.core.config import settings

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=settings.SMS_TIMEOUT)
    return _client


async def aclose_client():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class TermiiGateway:
    """Adapts the Termii REST API to the SMSGateway seam."""

    async def send(self, phone: str, message: str) -> dict | None:
        """Submit an SMS to Termii. Returns the provider payload on acceptance
        (non-None), or None when the provider rejected/failed the request."""
        url = f"{settings.TERMII_BASE_URL}/sms/send"
        payload = {
            "api_key": settings.TERMII_API_KEY,
            "to": phone,
            "from": settings.TERMII_SENDER_ID,
            "sms": message,
            "type": "plain",
            "channel": "generic",
        }

        try:
            response = await get_client().post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data
        except httpx.HTTPError as e:
            print(f"Failed to send SMS to {phone}: {e}")
            return None

    async def balance(self) -> dict | None:
        """Current Termii wallet balance, or None on failure."""
        url = f"{settings.TERMII_BASE_URL}/get-balance"
        try:
            response = await get_client().get(
                url, params={"api_key": settings.TERMII_API_KEY}
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Failed to retrieve Termii balance: {e}")
            return None

    async def history(self, message_id: str | None = None) -> list[dict] | None:
        """Message history from Termii, optionally for a single message."""
        url = f"{settings.TERMII_BASE_URL}/sms/inbox"
        params: dict = {"api_key": settings.TERMII_API_KEY}
        if message_id:
            params["message_id"] = message_id
        try:
            response = await get_client().get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
            return None
        except Exception as e:
            print(f"Failed to retrieve Termii message history: {e}")
            return None