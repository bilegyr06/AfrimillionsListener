"""Shared SMS dispatch and delivery-status reconciliation.

The forward path: callers (Welcome service, Inactivity pipeline, manual /sms)
decide who should be messaged; this module owns the mechanics of placing it —
concurrency, deadline/cancel deferral, the provider call, response
classification, and the sms_log ledger. It also reconciles provider delivery
status back into sms_log.

Ledgering contract: termii-attempted outcomes (sent/failed) are always recorded
by dispatch(). A "deferred" outcome means NO provider call was made and dispatch
does NOT record it; callers that want a deferral row call record_deferred().
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import settings
from app.core.phones import validate_phone
from app.db.sms import get_unsynced_sms, log_sms_batch, update_sms_status
from app.integrations.sms_gateway import SMSGateway, get_default_gateway

STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_DEFERRED = "deferred"

# Dispatch kinds stored in sms_log. "manual" covers operator /sms requests,
# whose user_id is the recipient phone itself.
MANUAL = "manual"

# Map Termii status strings to our normalized statuses.
_TERMII_STATUS_MAP: dict[str, str] = {
    "delivered": "delivered",
    "message delivered": "delivered",
    "dnd active on phone number": "dnd",
    "message failed": "failed",
    "rejected": "rejected",
    "expired": "expired",
    "message sent": "sent",
}


@dataclass
class DispatchResult:
    """Outcome of one dispatch attempt against the provider."""

    phone: str
    message: str
    kind: str
    user_id: str
    cycle_id: str | None
    status: str
    message_id: str | None = None
    cost: float = 0.0
    balance_after: float | None = None
    sent_at: str | None = None
    reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status == STATUS_SENT


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SmsDispatcher:
    """Send batch SMS through an SMSGateway with bounded concurrency/deadline.

    One instance per dispatch run. Compose it around a per-send payload; the
    caller decides eligibility/cap/cooldown BEFORE dispatching. The gateway is
    injected; when omitted the application default (Termii) is used.
    """

    def __init__(
        self,
        gateway: SMSGateway | None = None,
        max_concurrency: int | None = None,
    ):
        self._gateway = gateway or get_default_gateway()
        self._sem = asyncio.Semaphore(max_concurrency or settings.MAX_CONCURRENCY)
        self._ledger: list[dict] = []

    def _record(self, *, message_id, user_id, kind, phone, status, cost,
                balance_after, cycle_id, sent_at):
        self._ledger.append({
            "message_id": message_id,
            "user_id": user_id,
            "kind": kind,
            "phone": phone,
            "status": status,
            "cost": cost,
            "balance_after": balance_after,
            "cycle_id": cycle_id,
            "sent_at": sent_at,
        })

    async def dispatch(
        self,
        *,
        phone: str,
        message: str,
        kind: str,
        user_id: str,
        cycle_id: str | None = None,
        deadline: datetime | None = None,
        cancelled=None,
    ) -> DispatchResult:
        """Place one SMS. 'deferred' means no provider call was made and no
        ledger row exists unless the caller records one."""
        def past_deadline() -> bool:
            return deadline is not None and datetime.now() >= deadline

        if past_deadline() or (cancelled is not None and cancelled()):
            return DispatchResult(
                phone=phone, message=message, kind=kind, user_id=user_id,
                cycle_id=cycle_id, status=STATUS_DEFERRED,
            )

        # Final safety gate: no invalid number ever reaches the provider.
        check = validate_phone(phone)
        if check.canonical is None:
            sent_at = _now_iso()
            outcome = DispatchResult(
                phone=phone, message=message, kind=kind, user_id=user_id,
                cycle_id=cycle_id, status=STATUS_FAILED, reason=check.reason,
                sent_at=sent_at,
            )
            self._record(
                message_id=None, user_id=user_id, kind=kind, phone=phone,
                status=STATUS_FAILED, cost=0, balance_after=None,
                cycle_id=cycle_id, sent_at=sent_at,
            )
            return outcome

        async with self._sem:
            if past_deadline() or (cancelled is not None and cancelled()):
                return DispatchResult(
                    phone=check.canonical, message=message, kind=kind, user_id=user_id,
                    cycle_id=cycle_id, status=STATUS_DEFERRED,
                )
            result = await self._gateway.send(check.canonical, message)

        sent_at = _now_iso()
        if result:
            outcome = DispatchResult(
                phone=check.canonical, message=message, kind=kind, user_id=user_id,
                cycle_id=cycle_id, status=STATUS_SENT,
                message_id=result.get("message_id"),
                balance_after=result.get("balance"),
                sent_at=sent_at,
            )
        else:
            outcome = DispatchResult(
                phone=check.canonical, message=message, kind=kind, user_id=user_id,
                cycle_id=cycle_id, status=STATUS_FAILED,
                reason="Provider rejected the SMS request.",
                sent_at=sent_at,
            )

        self._record(
            message_id=outcome.message_id,
            user_id=user_id,
            kind=kind,
            phone=outcome.phone,
            status=outcome.status,
            cost=outcome.cost,
            balance_after=outcome.balance_after,
            cycle_id=cycle_id,
            sent_at=sent_at,
        )
        return outcome

    def record_deferred(self, *, phone, message, kind, user_id, cycle_id=None):
        """Ledger a deferral decided by the caller (no provider call made)."""
        self._record(
            message_id=None,
            user_id=user_id,
            kind=kind,
            phone=phone,
            status=STATUS_DEFERRED,
            cost=0,
            balance_after=None,
            cycle_id=cycle_id,
            sent_at=_now_iso(),
        )

    def flush(self):
        if self._ledger:
            log_sms_batch(self._ledger)
            self._ledger = []


def _normalize_termii_status(raw: str) -> str:
    return _TERMII_STATUS_MAP.get(raw.lower().strip(), "sent")


async def sync_delivery_statuses(
    batch_size: int = 100,
    gateway: SMSGateway | None = None,
) -> dict:
    """Synchronize sms_log delivery statuses with the provider's reports.

    Finds records that have a message_id but haven't reached a terminal
    delivery state, queries the gateway, and updates the local database.
    """
    unsynced = get_unsynced_sms(limit=batch_size)
    if not unsynced:
        return {"checked": 0, "updated": 0, "errors": 0}

    gateway = gateway or get_default_gateway()
    updated = 0
    errors = 0

    for record in unsynced:
        message_id = record["message_id"]
        try:
            history = await gateway.history(message_id)
            if not history or len(history) == 0:
                continue

            entry = history[0]
            termii_status = entry.get("status", "")
            cost = entry.get("amount")
            new_status = _normalize_termii_status(termii_status)

            if new_status != record["status"] or cost is not None and cost > 0:
                update_sms_status(message_id, new_status, cost)
                updated += 1

        except Exception as e:
            print(f"Failed to sync status for message {message_id}: {e}")
            errors += 1

    return {"checked": len(unsynced), "updated": updated, "errors": errors}