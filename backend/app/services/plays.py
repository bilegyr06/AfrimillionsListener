"""Player activity persistence (Phase 2).

Sales files (userId, gameName, amount, timestamp) become the durable `plays`
table via idempotent ingestion. This module owns the boundary between CSV rows
and database records:

  * parsing + row-level validation (data quality)
  * the deterministic dedup key (no stable play ID exists in the source)
  * historical backfill -> scan every Sales_*.csv, oldest first
  * incremental sync -> only files not yet marked 'processed' in the files
    ledger run on each cycle

Source semantics (verified against the Sales_2026-09-14 export and the code):

  * userId maps directly to the application's customer identifier.
  * timestamp is a naive, second-precision string; the app-wide convention
    treats naive CSV timestamps as UTC (see campaigns._to_utc_series), so this
    module normalizes to the same UTC-aware ISO form.
  * amount is a two-decimal money string; stored REAL, matching the
    application's existing monetary representation (sms_log.cost is REAL).
  * gameName is a short list of stable game names.
  * The export is a rolling/cumulative window (the sample spans ~30 days and
    reaches up to the download instant), so consecutive downloads re-ship old
    rows - the plays.source_key UNIQUE constraint is what prevents duplicates.
  * There is NO stable play/transaction identifier, and identical full rows
    occur inside a single file (same user, game, amount, second). The
    deterministic key is therefore (user_id, played_at, game_name, amount):
    identical same-second plays are indistinguishable from source duplication
    and are deliberately collapsed into one play record. If a real play ID
    later appears, migrate source_key to it.
"""
from __future__ import annotations

import glob
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.core.config import settings
from app.db.database import (
    insert_play_records,
    mark_plays_ingested,
    sales_file_processed,
)

SALES_COLUMNS = ("userId", "gameName", "amount", "timestamp")

#: Field separator for the deterministic dedup key (game names are plain text
#: without control characters, so a unit separator cannot collide).
_SOURCE_KEY_SEP = "\x1f"


def play_source_key(user_id: str, played_at: str, game_name: str, amount: float) -> str:
    """Deterministic idempotency key for one Sales row."""
    return _SOURCE_KEY_SEP.join(
        (user_id, played_at, game_name, f"{amount:.2f}")
    )


def _normalize_played_at(series: pd.Series) -> tuple[pd.Series, list[int]]:
    """Parse naive CSV timestamps to UTC-aware ISO strings; report failures."""
    parsed = pd.to_datetime(series, errors="coerce", format="%Y-%m-%d %H:%M:%S")
    valid = parsed.notna()
    iso = parsed[valid].dt.tz_localize("UTC").dt.strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
    return iso, list(parsed[~valid].index)


def parse_plays_frame(source_file: str, df: pd.DataFrame) -> dict:
    """Validate a Sales frame and build insertable play records.

    Returns:
      {
        "discovered": int,
        "records": [ {user_id, played_at, game_name, amount, source_file, source_key}, ... ],
        "invalid_reasons": {reason: count},
        "future": int,
      }
    """
    discovered = len(df)
    invalid_reasons: dict[str, int] = {}
    missing_keys = [c for c in SALES_COLUMNS if c not in df.columns]
    if missing_keys:
        invalid_reasons["missing_columns"] = 1
        return {
            "discovered": discovered,
            "records": [],
            "invalid_reasons": invalid_reasons,
            "future": 0,
        }

    frame = df.copy()
    frame["userId"] = frame["userId"].astype("string").str.strip()
    frame["gameName"] = frame["gameName"].astype("string").str.strip()
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")

    bad_user = frame["userId"].isna() | (frame["userId"] == "")
    bad_game = frame["gameName"].isna() | (frame["gameName"] == "")
    bad_amount = frame["amount"].isna() | (frame["amount"] < 0)

    played_iso, bad_ts = _normalize_played_at(frame["timestamp"])

    reason_masks = {
        "missing_user_id": bad_user,
        "missing_game_name": bad_game,
        "invalid_amount": bad_amount,
        "invalid_timestamp": frame.index.isin(bad_ts),
    }
    invalid = pd.Series(False, index=frame.index)
    for reason, mask in reason_masks.items():
        n = int(mask.sum())
        if n:
            invalid_reasons[reason] = invalid_reasons.get(reason, 0) + n
        invalid = invalid | mask

    valid = ~invalid
    records: list[dict] = []
    now = datetime.now(timezone.utc)
    future = 0
    for i in frame.index[valid]:
        played_at = played_iso.loc[i]
        if played_at > now.isoformat():
            future += 1
        amount = round(float(frame.loc[i, "amount"]), 2)
        records.append(
            {
                "user_id": str(frame.loc[i, "userId"]),
                "played_at": played_at,
                "game_name": str(frame.loc[i, "gameName"]),
                "amount": amount,
                "source_file": source_file,
                "source_key": play_source_key(
                    str(frame.loc[i, "userId"]),
                    played_at,
                    str(frame.loc[i, "gameName"]),
                    amount,
                ),
            }
        )

    # Collapse identical rows inside this frame before touching the database.
    unique_records: list[dict] = []
    seen: set[str] = set()
    dup = 0
    for rec in records:
        if rec["source_key"] in seen:
            dup += 1
            continue
        seen.add(rec["source_key"])
        unique_records.append(rec)
    if dup:
        invalid_reasons["duplicate_row"] = invalid_reasons.get("duplicate_row", 0) + dup

    return {
        "discovered": discovered,
        "records": unique_records,
        "invalid_reasons": invalid_reasons,
        "future": future,
    }


def ingest_sales_file(path: Path) -> dict:
    """Ingest one Sales CSV into plays. Returns a per-file report."""
    name = Path(path).name
    report = {
        "source_file": name,
        "discovered": 0,
        "inserted": 0,
        "skipped": 0,
        "invalid": 0,
        "parse_errors": 0,
        "invalid_reasons": {},
        "future": 0,
    }
    try:
        df = pd.read_csv(
            path,
            dtype={"userId": str, "gameName": str, "amount": str, "timestamp": str},
            engine="python",
        )
    except Exception as exc:
        report["parse_errors"] = 1
        report["invalid_reasons"]["read_failed"] = str(exc)
        return report

    parsed = parse_plays_frame(name, df)
    report["discovered"] = parsed["discovered"]
    report["invalid"] = sum(parsed["invalid_reasons"].values()) - parsed["invalid_reasons"].get("duplicate_row", 0)
    report["invalid_reasons"] = parsed["invalid_reasons"]
    report["future"] = parsed["future"]

    if parsed["records"]:
        report["inserted"] = insert_play_records(parsed["records"])
        report["skipped"] = len(parsed["records"]) - report["inserted"]
    report["skipped"] += parsed["invalid_reasons"].get("duplicate_row", 0)

    mark_plays_ingested(name, report)
    return report


def ingest_new_sales_files() -> dict:
    """Ingest Sales files not yet marked processed (historical + incremental).

    Files are processed oldest-first by name so cumulative exports replay their
    rows in order and the plays table converges without operator intervention.
    Repeated calls only ever touch new/not-yet-processed files.
    """
    files = sorted(glob.glob(str(settings.DATA_FOLDER / settings.SALES_FILE_PATTERN)))
    totals = {
        "files": 0,
        "discovered": 0,
        "inserted": 0,
        "skipped": 0,
        "invalid": 0,
        "parse_errors": 0,
        "future": 0,
    }
    for f in files:
        name = Path(f).name
        if sales_file_processed(name):
            continue
        totals["files"] += 1
        report = ingest_sales_file(Path(f))
        for key in ("discovered", "inserted", "skipped", "invalid", "parse_errors", "future"):
            totals[key] += report[key]
        print(
            f"Plays ingest {name}: discovered={report['discovered']} "
            f"inserted={report['inserted']} skipped={report['skipped']} "
            f"invalid={report['invalid']} parse_errors={report['parse_errors']} "
            f"future={report['future']} reasons={report['invalid_reasons']}"
        )

    if totals["files"]:
        print(f"Plays ingest total: {totals}")
    return totals