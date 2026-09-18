"""Application-facing SMS provider seam.

SMSGateway is the narrow, provider-neutral set of operations the application
requires: send one message, read the wallet balance, and fetch message history.
Concrete providers (Termii) and test/dry-run simulators (InMemorySmsGateway)
implement this protocol. Application code depends on this seam - never on a
concrete provider module - so dispatch behaviour can run without a network.

Contract shapes:

  send(phone, message) -> dict | None
      A non-None dict at minimum carries "message_id"; "balance" (post-send
      cost snapshot) is optional. None means the provider rejected/failed.
  balance() -> dict | None
      Provider wallet payload, or None on failure.
  history(message_id=None) -> list[dict] | None
      Provider delivery-report entries, optionally narrowed to one message.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SMSGateway(Protocol):
    """Narrow provider interface: send, balance, history."""

    async def send(self, phone: str, message: str) -> dict | None:
        """Submit one SMS. Non-None dict = accepted (has "message_id")."""

    async def balance(self) -> dict | None:
        """Current provider wallet balance, or None on failure."""

    async def history(self, message_id: str | None = None) -> list[dict] | None:
        """Provider delivery-report entries, optionally for one message."""


_default: SMSGateway | None = None


def set_default_gateway(gateway: SMSGateway | None) -> None:
    """Composition-root hook: install the production gateway at startup."""
    global _default
    _default = gateway


def get_default_gateway() -> SMSGateway:
    """The installed gateway, lazily defaulting to the Termii adapter.

    Lazy import keeps the seam module free of provider imports until a default
    is actually needed.
    """
    global _default
    if _default is None:
        from app.integrations.termii import TermiiGateway

        _default = TermiiGateway()
    return _default