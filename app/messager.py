import httpx

from app.config import settings


def send_sms(phone: str, message: str) -> dict | None:
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
        response = httpx.post(url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        print(f"SMS sent to {phone}: {data}")
        return data
    except httpx.HTTPError as e:
        print(f"Failed to send SMS to {phone}: {e}")
        return None
