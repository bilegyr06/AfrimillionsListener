"""Data intake boundary: CSV discovery, reading, and (Phase 2) manual uploads.

Every source of data — the Playwright scraper, manual UI uploads, files dropped
into the data folder — converges on this module so the processing pipeline reads
files one way and dataset definitions live in one place.
"""
from __future__ import annotations

import glob
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.core.config import settings

# File-name prefixes produced by the auto-downloader and recognized datasets.
KNOWN_PREFIXES = (
    "Login_",
    "Registrations_",
    "Sales_",
    "Deposit_events_",
    "Withdrawal_events_",
    "KYC_",
)


def read_latest(pattern: str, parse_timestamp: bool = False) -> pd.DataFrame:
    """Read the newest file matching a glob pattern (newest-mtime), or empty."""
    files = glob.glob(str(settings.DATA_FOLDER / pattern))
    if not files:
        return pd.DataFrame()
    newest = sorted(files, key=lambda p: Path(p).stat().st_mtime, reverse=True)[0]
    if parse_timestamp:
        return pd.read_csv(newest, parse_dates=["timestamp"])
    return pd.read_csv(newest)


def read_registrations() -> pd.DataFrame:
    """Newest Registrations file WITHOUT date parsing.

    Registration exports mix timestamp formats (with/without microseconds) that
    crash pandas' automatic datetime parsing, and the timestamp is not needed.
    """
    return read_latest(settings.REGISTRATION_FILE_PATTERN)


def read_login_events() -> pd.DataFrame:
    """Concatenate login events from ALL Login files, not just the newest.

    A Login export is a point-in-time slice of recent sign-ins, so the active
    campaign scans every file that overlaps its window.
    """
    files = glob.glob(str(settings.DATA_FOLDER / settings.LOGIN_FILE_PATTERN))
    frames: list[pd.DataFrame] = []
    for f in files:
        try:
            df = pd.read_csv(f, parse_dates=["timestamp"], dtype={"userId": str})
        except (ValueError, pd.errors.ParserError):
            continue
        if df.empty or "userId" not in df.columns or "timestamp" not in df.columns:
            continue
        frames.append(df[["userId", "timestamp"]].copy())
    if not frames:
        return pd.DataFrame(columns=["userId", "timestamp"])
    merged = pd.concat(frames, ignore_index=True)
    return merged[merged["userId"].notna()]


def read_sales() -> pd.DataFrame:
    """Newest Sales (game plays) file, timestamps parsed."""
    return read_latest(settings.SALES_FILE_PATTERN, parse_timestamp=True)


# ---------------------------------------------------------------------------
# Manual upload ingestion
# ---------------------------------------------------------------------------

def detect_dataset(filename: str, df: pd.DataFrame) -> str:
    """Infer a dataset family from the file name, then from its columns."""
    lower = filename.lower()
    for prefix in KNOWN_PREFIXES:
        if lower.startswith(prefix.lower()):
            return prefix.rstrip("_")

    columns = set(df.columns)
    has_login_ts = {"userId", "timestamp"} <= columns
    if has_login_ts and not ({"amount", "gameName"} & columns):
        return "Login"
    if has_login_ts and ({"amount", "gameName"} & columns):
        return "Sales"
    if "phone" in columns:
        return "Registrations"
    return "Data"


def _unique_stored_name(prefix: str) -> str:
    """A storage name that will not collide with an existing data file."""
    base = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    if not (settings.DATA_FOLDER / base).exists():
        return base
    return f"{prefix}_{uuid.uuid4().hex[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"


def persist_upload(filename: str, content: bytes, uploaded_by: str | None = None) -> dict:
    """Safely accept a manually uploaded CSV.

    Validation runs BEFORE anything is written: unparseable content is rejected
    (registry row status 'failed', nothing persisted). Parseable files land in
    the data folder atomically under a recognized dataset prefix so the normal
    pipeline picks them up (newest-wins for Registrations/Sales, all-scan for
    Login). Returns the registry record.
    """
    from app.db.database import insert_file

    safe_name = Path(filename or "upload.csv").name
    uploaded_at = datetime.now(timezone.utc).isoformat()

    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as exc:
        record = _registry_record(
            original_filename=safe_name,
            stored_filename="",
            dataset="Data",
            uploaded_at=uploaded_at,
            uploaded_by=uploaded_by or None,
            status="failed",
            parse_error=str(exc),
        )
        file_id = insert_file(record)
        record["id"] = file_id
        return record

    dataset = detect_dataset(safe_name, df)
    stored = safe_name if safe_name.startswith(KNOWN_PREFIXES) else _unique_stored_name(dataset)

    settings.DATA_FOLDER.mkdir(parents=True, exist_ok=True)
    dest = settings.DATA_FOLDER / stored
    tmp = settings.DATA_FOLDER / f".{stored}.tmp-{uuid.uuid4().hex[:8]}"
    tmp.write_bytes(content)
    tmp.replace(dest)

    record = _registry_record(
        original_filename=safe_name,
        stored_filename=stored,
        dataset=dataset,
        uploaded_at=uploaded_at,
        uploaded_by=uploaded_by or None,
        status="succeeded",
        row_count=len(df),
    )
    file_id = insert_file(record)
    record["id"] = file_id
    return record


def _registry_record(**kwargs) -> dict:
    return {k: v for k, v in kwargs.items()}