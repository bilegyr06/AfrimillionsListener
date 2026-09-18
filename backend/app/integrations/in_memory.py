"""In-memory SMS gateway: no network, deterministic canned responses.

Implements the SMSGateway seam for tests and dry-run scenarios so dispatch
logic can run without a provider. Every call is recorded so tests can assert
exactly what was dispatched and read.

  send_result: canned send() return value. Default: fabricate an accepted
      payload ("message_id"). Pass None explicitly to simulate a provider
      rejection (dispatch treats a None as a failed send).
  balance: canned balance() value (or None).
  history: canned history() report entries (or None).
"""
from __future__ import annotations

_UNSET = object()


class InMemorySmsGateway:
    """Deterministic, network-free gateway implementing SMSGateway."""

    def __init__(
        self,
        *,
        send_result: dict | None = _UNSET,
        balance: dict | None = None,
        history: list[dict] | None = None,
    ):
        self.send_result = send_result
        self.balance_result = balance
        self.history_result = history
        self.attempts: list[dict] = []
        self.history_requests: list[str | None] = []
        self.calls: dict[str, int] = {"send": 0, "balance": 0, "history": 0}

    async def send(self, phone: str, message: str) -> dict | None:
        self.calls["send"] += 1
        self.attempts.append({"phone": phone, "message": message})
        if self.send_result is not _UNSET:
            return self.send_result
        return {"message_id": f"mem-{self.calls['send']}", "balance": 0.0}

    async def balance(self) -> dict | None:
        self.calls["balance"] += 1
        return self.balance_result

    async def history(self, message_id: str | None = None) -> list[dict] | None:
        self.calls["history"] += 1
        self.history_requests.append(message_id)
        return self.history_result