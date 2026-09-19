"""Deposit_events ingestion (segment evaluation support).

Deposit_events exports (userId, timestamp) become the durable `deposits` table
via idempotent ingestion, mirroring plays ingestion:

  * parsing + row-level validation (data quality)
  * the deterministic dedup key (no stable deposit id exists in the source)
  * historical backfill -> scan every Deposit_events_*.csv, oldest first
  * incremental sync -> only files not yet marked 'processed' run on each cycle

Source semantics (verified against the Deposit_events export):

  * userId maps directly to the application's customer identifier.
  * timestamp is a naive, second-precision string; consistent with plays, the
    naive wall-clock is stored as a "+00:00 relabelled" label (see
    app.core.dates.read_source_fact) so plays and deposits share one frame.
  * There is NO deposit amount in the export (recency/count facts only) and no
    stable deposit identifier: the deterministic key is (user_id, deposited_at)
    and identical same-second deposits are collapsed into one record.
"""
from __future__ import annotations

import glob
from pathlib import Path

import pandas as pd

from app.core.config import settings
from app.db.deposits import insert_deposit_records
from app.db.files import deposit_file_processed, mark_deposits_ingested

DEPOSIT_COLUMNS = ("userId", "timestamp")

#: Field separator for the deterministic dedup key (mirrors plays).
_SOURCE_KEY_SEP = "\x1f"


def deposit_source_key(user_id: str, deposited_at: str) -> str:
    """Deterministic idempotency key for one Deposit_events row."""
    return _SOURCE_KEY_SEP.join((user_id, deposited_at))


def _normalize_deposited_at(series: pd.Series) -> tuple[pd.Series, list[int]]:
    """Parse naive CSV timestamps to UTC-aware ISO strings; report failures."""
    parsed = pd.to_datetime(series, errors="coerce", format="%Y-%m-%d %H:%M:%S")
    valid = parsed.notna()
    iso = parsed[valid].dt.tz_localize("UTC").dt.strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
    return iso, list(parsed[~valid].index)


def parse_deposits_frame(source_file: str, df: pd.DataFrame) -> dict:
    """Validate a Deposit_events frame and build insertable records.

    Returns:
      {
        "discovered": int,
        "records": [ {user_id, deposited_at, source_file, source_key}, ... ],
        "invalid_reasons": {reason: count},
      }
    """
    discovered = len(df)
    invalid_reasons: dict[str, int] = {}
    missing_keys = [c for c in DEPOSIT_COLUMNS if c not in df.columns]
    if missing_keys:
        invalid_reasons["missing_columns"] = 1
        return {
            "discovered": discovered,
            "records": [],
            "invalid_reasons": invalid_reasons,
        }

    frame = df.copy()
    frame["userId"] = frame["userId"].astype("string").str.strip()

    bad_user = frame["userId"].isna() | (frame["userId"] == "")
    deposited_iso, bad_ts = _normalize_deposited_at(frame["timestamp"])
    bad_ts_mask = frame.index.isin(bad_ts)

    invalid = bad_user | bad_ts_mask
    if bool(bad_user.sum()):
        invalid_reasons["missing_user_id"] = int(bad_user.sum())
    if bool(bad_ts_mask.sum()):
        invalid_reasons["invalid_timestamp"] = int(bad_ts_mask.sum())

    records: list[dict] = []
    for i in frame.index[~invalid]:
        deposited_at = deposited_iso.loc[i]
        records.append(
            {
                "user_id": str(frame.loc[i, "userId"]),
                "deposited_at": deposited_at,
                "source_file": source_file,
                "source_key": deposit_source_key(
                    str(frame.loc[i, "userId"]),
                    deposited_at,
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
    }


def ingest_deposit_file(path: Path) -> dict:
    """Ingest one Deposit_events CSV into deposits. Returns a per-file report."""
    name = Path(path).name
    report = {
        "source_file": name,
        "discovered": 0,
        "inserted": 0,
        "skipped": 0,
        "invalid": 0,
        "parse_errors": 0,
        "invalid_reasons": {},
    }
    try:
        df = pd.read_csv(
            path,
            dtype={"userId": str, "timestamp": str},
            engine="python",
        )
    except Exception as exc:
        report["parse_errors"] = 1
        report["invalid_reasons"]["read_failed"] = str(exc)
        return report

    parsed = parse_deposits_frame(name, df)
    report["discovered"] = parsed["discovered"]
    report["invalid"] = sum(parsed["invalid_reasons"].values()) - parsed["invalid_reasons"].get("duplicate_row", 0)
    report["invalid_reasons"] = parsed["invalid_reasons"]

    if parsed["records"]:
        report["inserted"] = insert_deposit_records(parsed["records"])
        report["skipped"] = len(parsed["records"]) - report["inserted"]
    report["skipped"] += parsed["invalid_reasons"].get("duplicate_row", 0)

    mark_deposits_ingested(name, report)
    return report


def ingest_new_deposit_files() -> dict:
    """Ingest Deposit_events files not yet marked processed (historical +
    incremental). Oldest-first by name; repeated calls only ever touch new files.
    """
    files = sorted(glob.glob(str(settings.DATA_FOLDER / settings.DEPOSIT_FILE_PATTERN)))
    totals = {
        "files": 0,
        "discovered": 0,
        "inserted": 0,
        "skipped": 0,
        "invalid": 0,
        "parse_errors": 0,
    }
    for f in files:
        name = Path(f).name
        if deposit_file_processed(name):
            continue
        totals["files"] += 1
        report = ingest_deposit_file(Path(f))
        for key in ("discovered", "inserted", "skipped", "invalid", "parse_errors"):
            totals[key] += report[key]
        print(
            f"Deposits ingest {name}: discovered={report['discovered']} "
            f"inserted={report['inserted']} skipped={report['skipped']} "
            f"invalid={report['invalid']} parse_errors={report['parse_errors']} "
            f"reasons={report['invalid_reasons']}"
        )

    if totals["files"]:
        print(f"Deposits ingest total: {totals}")
    return totals