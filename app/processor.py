import glob
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.config import settings
from app.database import get_all_notified, reset_user, upsert_notified_records
from app.models import InactiveUser
from app.messager import send_sms


def _build_message(user: InactiveUser) -> str:
    return f"Hi {user.first_name}, we miss you! Log in to AfriMillions to keep playing."


def _load_csv(pattern: str) -> pd.DataFrame:
    files = glob.glob(str(settings.DATA_FOLDER / pattern))
    if not files:
        print(f"No files found for pattern: {pattern}")
        return pd.DataFrame()
    return pd.read_csv(files[0], parse_dates=["timestamp"])


def reset_active_users(logins_df: pd.DataFrame):
    """Users who have logged in within the inactivity window get their
    notification state cleared, so their cycle restarts from zero."""
    notified = get_all_notified()
    if not notified:
        return

    now_naive = datetime.now()
    cutoff = now_naive - timedelta(hours=settings.INACTIVITY_HOURS)

    if logins_df.empty:
        return

    recent_logins = (
        logins_df[logins_df["timestamp"] >= cutoff]["userId"].unique()
    )

    reset_count = 0
    for user_id in recent_logins:
        if str(user_id) in notified:
            reset_user(str(user_id))
            reset_count += 1

    if reset_count:
        print(f"Reset {reset_count} user(s) who logged back in.")


def find_inactive_users() -> list[InactiveUser]:
    logins_df = _load_csv(settings.LOGIN_FILE_PATTERN)
    regs_df = _load_csv(settings.REGISTRATION_FILE_PATTERN)

    if logins_df.empty or regs_df.empty:
        print("No login or registration data available.")
        return []

    now_naive = datetime.now()
    cutoff = now_naive - timedelta(hours=settings.INACTIVITY_HOURS)

    last_logins = (
        logins_df.groupby("userId")["timestamp"]
        .max()
        .reset_index()
        .rename(columns={"timestamp": "last_login"})
    )

    inactive = last_logins[last_logins["last_login"] < cutoff].copy()

    merged = inactive.merge(
        regs_df[["userId", "firstName", "phone"]],
        on="userId",
        how="left",
    )

    notified = get_all_notified()
    now_utc = datetime.now(timezone.utc)

    results: list[InactiveUser] = []
    for _, row in merged.iterrows():
        user_id = str(row["userId"])
        record = notified.get(user_id)

        is_new = record is None
        if record:
            next_available = datetime.fromisoformat(record["next_available_at"])
            if now_utc < next_available:
                continue
            if settings.MAX_MESSAGES > 0 and record["notification_count"] >= settings.MAX_MESSAGES:
                continue

        phone = str(row.get("phone", "")).strip()
        if not phone or phone == "nan":
            continue

        results.append(
            InactiveUser(
                user_id=user_id,
                first_name=str(row.get("firstName", "User")),
                phone=phone,
                last_login=str(row["last_login"]),
                is_new=is_new,
            )
        )

    return results


def notify_inactive_users() -> dict:
    logins_df = _load_csv(settings.LOGIN_FILE_PATTERN)
    reset_active_users(logins_df)

    users = find_inactive_users()
    if not users:
        return {"message": "No inactive users to notify.", "count": 0}

    sent = 0
    failed = 0
    records = []
    for user in users:
        result = send_sms(user.phone, _build_message(user))
        if result:
            records.append(
                {
                    "user_id": user.user_id,
                    "phone": user.phone,
                    "is_new": user.is_new,
                }
            )
            sent += 1
        else:
            failed += 1

    if records:
        upsert_notified_records(records, settings.COOLDOWN_HOURS)

    return {"message": f"Notified {sent} user(s).", "count": sent, "failed": failed}