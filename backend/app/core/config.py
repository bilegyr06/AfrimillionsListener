import os
from datetime import time
from pathlib import Path
from dotenv import load_dotenv

# Repository root: backend/app/core/config.py -> parents[3] is the project root,
# where .env, data/ and database/ live (in Docker this resolves to /app).
BASE_DIR = Path(__file__).resolve().parents[3]
load_dotenv(BASE_DIR / ".env")


def _parse_time(value: str, default: str) -> time:
    value = value.strip() if value else ""
    if not value:
        value = default
    parts = value.split(":")
    return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)


def _parse_bool(value: str, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # Infrastructure / secrets — never exposed through the operator API.
    TERMII_API_KEY: str = os.getenv("TERMII_API_KEY", "")
    TERMII_BASE_URL: str = os.getenv("TERMII_BASE_URL", "")
    ALOTBI_URL: str = os.getenv("ALOTBI_URL", "").rstrip("/")
    ALOTBI_USERNAME: str = os.getenv("ALOTBI_USERNAME", "")
    ALOTBI_PASSWORD: str = os.getenv("ALOTBI_PASSWORD", "")

    # Operator-configurable settings. Defaults below are bootstrap values;
    # once the database is initialized, the settings table is the source of
    # truth (see app.services.settings).
    TERMII_SENDER_ID: str = os.getenv("TERMII_SENDER_ID", "Afrimillions")

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

    # Feature 1: welcome-back SMS for sign-ins that led to no game activity.
    WELCOME_EVAL_DELAY_HOURS: float = float(os.getenv("WELCOME_EVAL_DELAY_HOURS", 1))
    WELCOME_MAX_MESSAGES: int = int(os.getenv("WELCOME_MAX_MESSAGES", 3))  # 0 = unlimited
    WELCOME_POST_LIMIT_SUPPRESS: bool = _parse_bool(
        os.getenv("WELCOME_POST_LIMIT_SUPPRESS", "true"), True
    )
    WELCOME_MESSAGE: str = os.getenv(
        "WELCOME_MESSAGE", "Hi {first_name}, great to see you back! We missed you at AfriMillions."
    )

    # Feature 2: inactivity reminder SMS.
    INACTIVE_MESSAGE: str = os.getenv(
        "INACTIVE_MESSAGE", "Hi {first_name}, we miss you! Log in to AfriMillions to keep playing."
    )

    # Data collection.
    CSV_DOWNLOADER_ENABLED: bool = _parse_bool(os.getenv("CSV_DOWNLOADER_ENABLED", "true"), True)

    DATA_FOLDER: Path = BASE_DIR / os.getenv("DATA_FOLDER", "data")
    DB_PATH: Path = BASE_DIR / os.getenv("DB_PATH", "database/notified_users.db")

    LOGIN_FILE_PATTERN: str = "Login_*.csv"
    REGISTRATION_FILE_PATTERN: str = "Registrations_*.csv"
    SALES_FILE_PATTERN: str = "Sales_*.csv"
    DEPOSIT_FILE_PATTERN: str = "Deposit_events_*.csv"

    # Campaign Window eligibility: a login qualifies only inside a fixed
    # one-hour band (now - (H+1)h, now - H] (low edge exclusive, high edge
    # inclusive). H is configurable; the band itself is always one hour wide.
    WELCOME_LOGIN_AGE_HOURS: int = int(os.getenv("WELCOME_LOGIN_AGE_HOURS", 3))


settings = Settings()