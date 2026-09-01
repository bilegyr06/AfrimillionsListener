import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    TERMII_API_KEY: str = os.getenv("TERMII_API_KEY", "")
    TERMII_SENDER_ID: str = os.getenv("TERMII_SENDER_ID", "Afrimillions")
    TERMII_BASE_URL: str = "https://api.ng.termii.com/api/v1"

    INACTIVITY_HOURS: int = int(os.getenv("INACTIVITY_HOURS", 48))
    COOLDOWN_HOURS: int = int(os.getenv("COOLDOWN_HOURS", 24))
    MAX_MESSAGES: int = int(os.getenv("MAX_MESSAGES", 0))  # 0 = unlimited
    MAX_CONCURRENCY: int = int(os.getenv("MAX_CONCURRENCY", 50))  # parallel SMS sends
    SMS_TIMEOUT: float = float(os.getenv("SMS_TIMEOUT", 30))

    DATA_FOLDER: Path = BASE_DIR / os.getenv("DATA_FOLDER", "data")
    DB_PATH: Path = BASE_DIR / os.getenv("DB_PATH", "app/notified_users.db")

    LOGIN_FILE_PATTERN: str = "Logins_*.csv"
    REGISTRATION_FILE_PATTERN: str = "Registrations_*.csv"


settings = Settings()
