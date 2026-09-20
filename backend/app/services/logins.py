"""Login export ingestion (Campaign Window reporting).

Login_*.csv exports (userId, timestamp) become the durable `logins` table via
idempotent ingestion, mirroring plays/deposits ingestion:

  * parsing + row-level validation (data quality)
  * the deterministic dedup key (no stable login id exists in the source)
  * historical backfill -> scan every Login_*.csv, oldest first
  * incremental sync -> only files not yet marked 'processed' run on each cycle

Source semantics (verified against Login exports):

  * userId maps directly to the application's customer identifier.
  * timestamp is a naive, second-precision (occasionally microsecond) string;
    consistent with plays and deposits, the naive wall-clock is stored as a
    "+00:00 relabelled" label (see app.core.dates.read_source_fact) so all
    facts share one frame for Window evaluation-period comparisons.
  * There is NO stable login identifier: the deterministic key is
    (user_id, logged_at) and identical same-second logins are collapsed into
    one record.
"""
from __future__ import annotations

import glob
from pathlib import Path

import pandas as pd

from app.core.config import settings
from app.db.files import login_file_processed, mark_logins_ingested
from app.db.logins import insert_login_records

LOGIN_COLUMNS = ("userId", "timestamp")

#: Field separator for the deterministic dedup key (mirrors plays/deposits).
_SOURCE_KEY_SEP = "\x1f"
_SOURCE_FACT = "%Y-%m-%dT%H:%M:%S+00:00"


def login_source_key(user_id: str, logged_at: str) -> str:
    """Deterministic idempotency key for one Login row."""
    return _SOURCE_KEY_SEP.join((user_id, logged_at))


def _normalize_logged_at(series: pd.Series) -> tuple[pd.Series, list[int]]:
    """Parse naive CSV timestamps to UTC-aware ISO strings; report failures.

    Login exports may carry microseconds, so parsing is tolerant (pandas
    inference) and the parsed wall-clock is stored as the "+00:00 relabelled"
    label the other fact tables use.
    """
    parsed = pd.to_datetime(series, errors="coerce")
    valid = parsed.notna()
    iso = parsed[valid].dt.strftime(_SOURCE_FACT)
    return iso, list(parsed[~valid].index)


def parse_logins_frame(source_file: str, df: pd.DataFrame) -> dict:
    """Validate a Login frame and build insertable login records.

    Returns:
      {
        "discovered": int,
        "records": [ {user_id, logged_at, source_file, source_key}, ... ],
        "invalid_reasons": {reason: count},
      }
    """
    discovered = len(df)
    invalid_reasons: dict[str, int] = {}
    missing_keys = [c for c in LOGIN_COLUMNS if c not in df.columns]
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
    logged_iso, bad_ts = _normalize_logged_at(frame["timestamp"])
    bad_ts_mask = frame.index.isin(bad_ts)

    invalid = bad_user | bad_ts_mask
    if bool(bad_user.sum()):
        invalid_reasons["missing_user_id"] = int(bad_user.sum())
    if bool(bad_ts_mask.sum()):
        invalid_reasons["invalid_timestamp"] = int(bad_ts_mask.sum())

    records: list[dict] = []
    for i in frame.index[~invalid]:
        logged_at = logged_iso.loc[i]
        records.append(
            {
                "user_id": str(frame.loc[i, "userId"]),
                "logged_at": logged_at,
                "source_file": source_file,
                "source_key": login_source_key(
                    str(frame.loc[i, "userId"]),
                    logged_at,
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


def ingest_login_file(path: Path) -> dict:
    """Ingest one Login CSV into logins. Returns a per-file report."""
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

    parsed = parse_logins_frame(name, df)
    report["discovered"] = parsed["discovered"]
    report["invalid"] = sum(parsed["invalid_reasons"].values()) - parsed["invalid_reasons"].get("duplicate_row", 0)
    report["invalid_reasons"] = parsed["invalid_reasons"]

    if parsed["records"]:
        report["inserted"] = insert_login_records(parsed["records"])
        report["skipped"] = len(parsed["records"]) - report["inserted"]
    report["skipped"] += parsed["invalid_reasons"].get("duplicate_row", 0)

    mark_logins_ingested(name, report)
    return report


def ingest_new_login_files() -> dict:
    """Ingest Login files not yet marked processed (historical + incremental).

    Files are processed oldest-first by name so overlapping point-in-time
    exports replay their rows in order and the logins table converges without
    operator intervention. Repeated calls only ever touch new files.
    """
    files = sorted(glob.glob(str(settings.DATA_FOLDER / settings.LOGIN_FILE_PATTERN)))
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
        if login_file_processed(name):
            continue
        totals["files"] += 1
        report = ingest_login_file(Path(f))
        for key in ("discovered", "inserted", "skipped", "invalid", "parse_errors"):
            totals[key] += report[key]
        print(
            f"Logins ingest {name}: discovered={report['discovered']} "
            f"inserted={report['inserted']} skipped={report['skipped']} "
            f"invalid={report['invalid']} parse_errors={report['parse_errors']} "
            f"reasons={report['invalid_reasons']}"
        )

    if totals["files"]:
        print(f"Logins ingest total: {totals}")
    return totals