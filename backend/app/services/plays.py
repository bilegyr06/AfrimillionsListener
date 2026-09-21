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
from app.db.players import insert_play_records
from app.services.ingestion import ingest_claimed_file

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
    now_iso = datetime.now(timezone.utc).isoformat()
    future = 0
    # Column-wise extraction + a records loop, instead of per-cell frame.loc
    # scalar access: identical semantics (same rows, same order, same dedup key)
    # without the per-row label lookup that made large inserts quadratic.
    valid_frame = frame.loc[valid]
    if len(valid_frame):
        users = valid_frame["userId"].tolist()
        games = valid_frame["gameName"].tolist()
        amounts = valid_frame["amount"].tolist()
        played_ats = played_iso.reindex(valid_frame.index).tolist()
        for raw_user, raw_game, raw_amount, played_at in zip(
            users, games, amounts, played_ats
        ):
            if played_at > now_iso:
                future += 1
            amount = round(float(raw_amount), 2)
            uid = str(raw_user)
            game = str(raw_game)
            records.append(
                {
                    "user_id": uid,
                    "played_at": played_at,
                    "game_name": game,
                    "amount": amount,
                    "source_file": source_file,
                    "source_key": play_source_key(uid, played_at, game, amount),
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


def _empty_sales_report(name: str) -> dict:
    return {
        "source_file": name,
        "discovered": 0,
        "inserted": 0,
        "skipped": 0,
        "invalid": 0,
        "parse_errors": 0,
        "invalid_reasons": {},
        "future": 0,
    }


def _ingest_sales_file(path: Path, name: str) -> dict:
    """Read + persist one Sales CSV (the caller already holds the claim)."""
    report = _empty_sales_report(name)
    try:
        df = pd.read_csv(
            path,
            dtype={"userId": str, "gameName": str, "amount": str, "timestamp": str},
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
    return report


def ingest_sales_file(path: Path) -> dict:
    """Ingest one Sales CSV into plays under a single-flight ledger claim.

    Returns a per-file report. When another caller owns the file or it has
    already been processed, `claimed` is False and the report is an empty stub.
    """
    name = Path(path).name
    claimed, report = ingest_claimed_file(
        "Sales", name, lambda: _ingest_sales_file(path, name)
    )
    if not claimed:
        stub = _empty_sales_report(name)
        stub["claimed"] = False
        return stub
    report["claimed"] = True
    return report


def ingest_new_sales_files() -> dict:
    """Ingest Sales files not yet processed (historical + incremental).

    Files are processed oldest-first by name so cumulative exports replay their
    rows in order and the plays table converges without operator intervention.
    Each file is claimed exactly once; repeated calls only ever touch new files.
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
        report = ingest_sales_file(Path(f))
        if not report.get("claimed"):
            continue
        totals["files"] += 1
        for key in ("discovered", "inserted", "skipped", "invalid", "parse_errors", "future"):
            totals[key] += report[key]
        print(
            f"Plays ingest {report['source_file']}: discovered={report['discovered']} "
            f"inserted={report['inserted']} skipped={report['skipped']} "
            f"invalid={report['invalid']} parse_errors={report['parse_errors']} "
            f"future={report['future']} reasons={report['invalid_reasons']}"
        )

    if totals["files"]:
        print(f"Plays ingest total: {totals}")
    return totals