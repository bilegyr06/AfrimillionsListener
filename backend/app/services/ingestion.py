"""Data intake boundary: CSV discovery, reading, and (Phase 2) manual uploads.

Every source of data — the Playwright scraper, manual UI uploads, files dropped
into the data folder — converges on this module so the processing pipeline reads
files one way and dataset definitions live in one place.
"""
from __future__ import annotations

import glob
import io
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

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
    from app.db.files import insert_file

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


# ---------------------------------------------------------------------------
# Single-flight source-file ingestion
# ---------------------------------------------------------------------------
#
# Ingestion is a WRITE that belongs to explicit operational points (a manual
# upload, a Run start, a Window finalization) - never to a report READ. Two
# mechanisms protect a source file from being parsed by concurrent callers:
#
#   * the ledger claim (app.db.files.claim_file_for_ingestion) is the atomic,
#     SQLite-backed single-flight: a BEGIN IMMEDIATE compare-and-swap ensures
#     only one caller can move a file into 'processing';
#   * a per-file in-process lock makes a second caller in this single-process
#     backend wait for the in-flight ingest to finish, then see the file as
#     already processed (so it never reads half-ingested facts).
#
# Together they replace the racy "is_processed() -> parse -> mark_processed()"
# sequence, which let concurrent callers all observe the file as unprocessed.

_ingest_locks: dict[tuple[str, str], threading.Lock] = {}
_ingest_locks_guard = threading.Lock()


def _file_ingest_lock(dataset: str, stored_filename: str) -> threading.Lock:
    key = (dataset, stored_filename)
    with _ingest_locks_guard:
        lock = _ingest_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _ingest_locks[key] = lock
        return lock


def ingest_claimed_file(
    dataset: str, stored_filename: str, ingest: Callable[[], dict]
) -> tuple[bool, dict | None]:
    """Ingest one persisted source file exactly once across concurrent callers.

    `ingest` performs the dataset-specific read+persist and returns a report
    containing at least ``parse_errors``. On success the ledger is marked
    'processed'; a read failure releases the claim as 'failed' so the file is
    retried rather than falsely recorded as ingested. Returns
    ``(claimed, report)``; ``claimed`` is False when another caller owns the
    file or it has already been processed.
    """
    from app.db import files as files_db

    lock = _file_ingest_lock(dataset, stored_filename)
    with lock:
        if not files_db.claim_file_for_ingestion(dataset, stored_filename):
            return False, None

        try:
            report = ingest()
        except Exception as exc:
            files_db.release_file_claim(dataset, stored_filename, error=str(exc))
            raise

        if report.get("parse_errors"):
            reason = report.get("invalid_reasons", {}).get("read_failed", "parse failed")
            files_db.release_file_claim(dataset, stored_filename, error=str(reason))
        else:
            files_db.mark_ingested(dataset, stored_filename, report)
        return True, report


def ingest_pending_files() -> dict:
    """Sync the durable fact tables with every not-yet-processed source file.

    The explicit "ingest all pending Sales/Deposit/Login files" operation, run
    from operational lifecycle points (Window finalization) and never from a
    report read. Each dataset's own incremental pass is idempotent and
    single-flight.
    """
    from app.services.deposits import ingest_new_deposit_files
    from app.services.logins import ingest_new_login_files
    from app.services.plays import ingest_new_sales_files

    return {
        "sales": ingest_new_sales_files(),
        "deposits": ingest_new_deposit_files(),
        "logins": ingest_new_login_files(),
    }