import os
from datetime import time
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _parse_time(value: str, default: str) -> time:
    value = value.strip() if value else ""
    if not value:
        value = default
    parts = value.split(":")
    return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


class Settings:
    TERMII_API_KEY: str = os.getenv("TERMII_API_KEY", "")
    TERMII_SENDER_ID: str = os.getenv("TERMII_SENDER_ID", "Afrimillions")
    TERMII_BASE_URL: str = os.getenv("TERMII_BASE_URL", "")

    INACTIVITY_HOURS: int = int(os.getenv("INACTIVITY_HOURS", 48))
    COOLDOWN_HOURS: int = int(os.getenv("COOLDOWN_HOURS", 24))
    MAX_MESSAGES: int = int(os.getenv("MAX_MESSAGES", 0))  # 0 = unlimited
    MAX_CONCURRENCY: int = int(os.getenv("MAX_CONCURRENCY", 50))  # parallel SMS sends
    SMS_TIMEOUT: float = float(os.getenv("SMS_TIMEOUT", 30))
    CYCLE_END_HOUR: int = int(os.getenv("CYCLE_END_HOUR", 20))  # cycle must not run past this hour

    # Message-sending time range (HH:MM). Cycles may only run within this window.
    START_TIME: time = _parse_time(os.getenv("START_TIME", ""), "14:00")
    END_TIME: time = _parse_time(os.getenv("END_TIME", ""), "18:00")

    # Feature toggles. Comma-separated list of enabled features: "welcome", "inactive", or both.
    ENABLED_FEATURES: set[str] = {
        f.strip().lower() for f in os.getenv("ENABLED_FEATURES", "welcome,inactive").split(",") if f.strip()
    }

    # Feature 1: welcome-back SMS shortly after login.
    LOGIN_WINDOW_HOURS: float = float(os.getenv("LOGIN_WINDOW_HOURS", 4))
    WELCOME_MESSAGE: str = os.getenv(
        "WELCOME_MESSAGE", "Hi {first_name}, great to see you back! We missed you at AfriMillions."
    )

    # Feature 2: inactivity reminder SMS.
    INACTIVE_MESSAGE: str = os.getenv(
        "INACTIVE_MESSAGE", "Hi {first_name}, we miss you! Log in to AfriMillions to keep playing."
    )

    DATA_FOLDER: Path = BASE_DIR / os.getenv("DATA_FOLDER", "data")
    DB_PATH: Path = BASE_DIR / os.getenv("DB_PATH", "app/notified_users.db")

    LOGIN_FILE_PATTERN: str = "Login_*.csv"
    REGISTRATION_FILE_PATTERN: str = "Registrations_*.csv"


settings = Settings()
