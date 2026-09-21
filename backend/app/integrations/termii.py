"""Termii provider adapter behind the SMSGateway seam.

TermiiGateway implements the application-facing SMSGateway contract (send,
balance, history). Termii-specific concerns - HTTP requests, response shapes,
and error handling - stay here and never leak into the dispatcher or services.
This module is the only place that talks to the Termii REST API.
"""
from __future__ import annotations

import asyncio
import httpx

from app.core.config import settings

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


def get_client() -> httpx.AsyncClient:
    """Get or create the shared httpx client with connection pool limits.

    Limits prevent connection pool exhaustion when the Termii API is slow or
    unreachable, which would otherwise block the event loop and cause
    upstream connection resets (ECONNRESET) on the frontend proxy.
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=5.0,
                read=settings.SMS_TIMEOUT,
                write=10.0,
                pool=5.0,
            ),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=5,
                keepalive_expiry=30.0,
            ),
        )
    return _client


async def aclose_client():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _request_with_retry(method: str, url: str, **kwargs) -> httpx.Response | None:
    """Make an HTTP request with automatic client recovery on connection errors.

    If a connection error occurs (pool exhausted, connection reset, etc.), the
    client is recreated and the request is retried once. This prevents upstream
    ECONNRESET errors when the Termii API is temporarily unreachable.
    """
    for attempt in range(2):
        try:
            client = get_client()
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError) as e:
            if attempt == 0:
                # Recreate client and retry once
                global _client
                async with _client_lock:
                    if _client is not None:
                        await _client.aclose()
                    _client = None
                print(f"Termii {method} {url} connection error (attempt {attempt + 1}): {e}; recreating client")
                continue
            print(f"Termii {method} {url} failed after retry: {e}")
            return None
        except httpx.HTTPStatusError as e:
            print(f"Termii {method} {url} HTTP error: {e}")
            return None
        except Exception as e:
            print(f"Termii {method} {url} unexpected error: {e}")
            return None
    return None


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

        response = await _request_with_retry("POST", url, json=payload)
        if response is None:
            return None
        return response.json()

    async def balance(self) -> dict | None:
        """Current Termii wallet balance, or None on failure."""
        url = f"{settings.TERMII_BASE_URL}/get-balance"
        response = await _request_with_retry(
            "GET", url, params={"api_key": settings.TERMII_API_KEY}
        )
        if response is None:
            return None
        return response.json()

    async def history(self, message_id: str | None = None) -> list[dict] | None:
        """Message history from Termii, optionally for a single message."""
        url = f"{settings.TERMII_BASE_URL}/sms/inbox"
        params: dict = {"api_key": settings.TERMII_API_KEY}
        if message_id:
            params["message_id"] = message_id
        response = await _request_with_retry("GET", url, params=params)
        if response is None:
            return None
        data = response.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return None