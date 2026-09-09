from __future__ import annotations

from app.database import get_unsynced_sms, update_sms_status
from app.termii_insights import get_message_history

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


def _normalize_termii_status(raw: str) -> str:
    return _TERMII_STATUS_MAP.get(raw.lower().strip(), "sent")


async def sync_delivery_statuses(batch_size: int = 100) -> dict:
    """Synchronize local sms_log records with Termii's delivery status.

    Finds records that have a message_id but haven't reached a terminal
    delivery state, queries Termii for their current status, and updates
    the local database.

    Returns a summary dict with counts of what was updated.
    """
    unsynced = get_unsynced_sms(limit=batch_size)
    if not unsynced:
        return {"checked": 0, "updated": 0, "errors": 0}

    updated = 0
    errors = 0

    for record in unsynced:
        message_id = record["message_id"]
        try:
            history = await get_message_history(message_id)
            if not history or len(history) == 0:
                continue

            entry = history[0]
            termii_status = entry.get("status", "")
            cost = entry.get("amount")
            new_status = _normalize_termii_status(termii_status)

            if new_status != record["status"]:
                update_sms_status(message_id, new_status, cost)
                updated += 1
            elif cost is not None and cost > 0:
                update_sms_status(message_id, new_status, cost)
                updated += 1
        except Exception as e:
            print(f"Failed to sync status for message {message_id}: {e}")
            errors += 1

    return {"checked": len(unsynced), "updated": updated, "errors": errors}
