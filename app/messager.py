import httpx

from app.config import settings

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


async def send_sms(phone: str, message: str) -> dict | None:
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