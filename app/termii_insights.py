from __future__ import annotations

from app.config import settings
from app.messager import get_client


async def get_balance() -> dict | None:
    """Retrieve the current Termii wallet balance.

    Returns {"balance": float, "currency": str, ...} or None on failure.
    """
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


async def get_message_history(message_id: str | None = None) -> list[dict] | None:
    """Retrieve message history from Termii.

    If message_id is provided, returns the record for that single message.
    Otherwise returns all recent messages on the account.

    Returns a list of message dicts or None on failure.
    """
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
