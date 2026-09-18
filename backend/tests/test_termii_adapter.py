"""Independent Termii adapter tests (HTTP implementation, no network).

Verifies the TermiiGateway adapter's HTTP contract in isolation from dispatch
behaviour: request URLs/payloads, response parsing, and provider-error
handling. Uses httpx.MockTransport so nothing leaves the process. Dispatch
behaviour itself is exercised through the in-memory gateway elsewhere.
"""
from __future__ import annotations

import json
import os

import httpx
import pytest

os.environ.setdefault("TERMII_API_KEY", "test-key")
os.environ.setdefault("TERMII_BASE_URL", "https://test.api.termii.com/api")
os.environ.setdefault("TERMII_SENDER_ID", "TestSender")

from app.integrations import termii
from app.integrations.termii import TermiiGateway


def _mock_client(monkeypatch, handler) -> httpx.AsyncClient:
    """Install a transport-mocked httpx client as the adapter's HTTP client."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(termii, "get_client", lambda: client)
    return client


@pytest.mark.asyncio
async def test_send_builds_termii_request(monkeypatch):
    seen: dict = {}

    async def handler(request):
        seen["url"] = str(request.url)
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"message_id": "mid", "balance": 90})

    client = _mock_client(monkeypatch, handler)
    try:
        result = await TermiiGateway().send("2348012345678", "hello")
    finally:
        await client.aclose()

    assert result == {"message_id": "mid", "balance": 90}
    assert seen["url"].endswith("/sms/send")
    payload = seen["json"]
    assert payload["api_key"] == "test-key"
    assert payload["to"] == "2348012345678"
    assert payload["from"] == "TestSender"
    assert payload["sms"] == "hello"
    assert payload["type"] == "plain"
    assert payload["channel"] == "generic"


@pytest.mark.asyncio
async def test_send_returns_none_on_http_error(monkeypatch):
    async def handler(request):
        return httpx.Response(500, json={"error": "rejected"})

    client = _mock_client(monkeypatch, handler)
    try:
        result = await TermiiGateway().send("2348012345678", "hello")
    finally:
        await client.aclose()

    assert result is None


@pytest.mark.asyncio
async def test_balance_queries_wallet(monkeypatch):
    seen: dict = {}

    async def handler(request):
        seen["url"] = str(request.url)
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"balance": 250.0, "currency": "NGN"})

    client = _mock_client(monkeypatch, handler)
    try:
        result = await TermiiGateway().balance()
    finally:
        await client.aclose()

    assert result == {"balance": 250.0, "currency": "NGN"}
    assert seen["url"].split("?")[0].endswith("/get-balance")
    assert seen["params"]["api_key"] == "test-key"


@pytest.mark.asyncio
async def test_history_wraps_report_shapes(monkeypatch):
    async def handler(request):
        return httpx.Response(200, json={"status": "Delivered", "amount": 2.5})

    client = _mock_client(monkeypatch, handler)
    try:
        result = await TermiiGateway().history("mid")
    finally:
        await client.aclose()

    assert result == [{"status": "Delivered", "amount": 2.5}]


@pytest.mark.asyncio
async def test_history_returns_none_on_network_failure(monkeypatch):
    async def handler(request):
        raise httpx.ConnectError("unreachable")

    client = _mock_client(monkeypatch, handler)
    try:
        result = await TermiiGateway().history("mid")
    finally:
        await client.aclose()

    assert result is None