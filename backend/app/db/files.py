"""Uploaded-files registry data module.

Owns the files table: manual-upload registration, listing, the per-dataset
"already ingested" ledger (sales_file_processed / mark_*_ingested), and the
single-flight ingestion claim (claim_file_for_ingestion /
release_file_claim). Consumers: app.services.ingestion and the per-dataset
ingestion services.

The files table doubles as the ingestion ledger. A file moves through
'received'/'succeeded' (accepted, not yet ingested) -> 'processing' (claimed by
exactly one ingester) -> 'processed' (facts durable) or 'failed' (a read error
released the claim so the file is retried, never silently marked processed).
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db.database import get_connection


def insert_file(record: dict) -> int:
    """Register an uploaded data file. Returns the new row id."""
    conn = get_connection()
    cursor = conn.execute(
        """
        INSERT INTO files
            (original_filename, stored_filename, dataset, uploaded_at,
             uploaded_by, status, row_count, parse_error, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["original_filename"],
            record["stored_filename"],
            record["dataset"],
            record["uploaded_at"],
            record.get("uploaded_by"),
            record.get("status", "received"),
            record.get("row_count"),
            record.get("parse_error"),
            record.get("processed_at"),
        ),
    )
    conn.commit()
    conn.close()
    return cursor.lastrowid


def get_file(file_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_files(limit: int = 100) -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM files ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def sales_file_processed(stored_filename: str) -> bool:
    """Whether a Sales file has already been fully ingested into plays."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM files WHERE dataset = 'Sales' "
        "AND stored_filename = ? AND status = 'processed'",
        (stored_filename,),
    ).fetchone()
    conn.close()
    return row["n"] > 0


def deposit_file_processed(stored_filename: str) -> bool:
    """Whether a Deposit_events file has already been fully ingested."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM files WHERE dataset = 'Deposit_events' "
        "AND stored_filename = ? AND status = 'processed'",
        (stored_filename,),
    ).fetchone()
    conn.close()
    return row["n"] > 0


def login_file_processed(stored_filename: str) -> bool:
    """Whether a Login file has already been fully ingested into logins."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM files WHERE dataset = 'Login' "
        "AND stored_filename = ? AND status = 'processed'",
        (stored_filename,),
    ).fetchone()
    conn.close()
    return row["n"] > 0


def mark_ingested(
    dataset: str, stored_filename: str, report: dict, uploaded_by: str | None = None
):
    """Promote a claimed file to 'processed' in the files ledger.

    The files table doubles as the ingestion ledger: manual uploads already have
    a row (status 'succeeded' written at accept time), which is promoted to
    'processed'; generic/auto files get a new 'processed' row. row_count records
    inserted rows; parse_error carries the rejection summary.
    """
    now = datetime.now(timezone.utc).isoformat()
    summary = (
        f"inserted {report.get('inserted', 0)}; "
        f"duplicates {report.get('skipped', 0)}; "
        f"invalid {report.get('invalid', 0)}; "
        f"parse_errors {report.get('parse_errors', 0)}"
    )
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM files WHERE dataset = ? AND stored_filename = ?",
        (dataset, stored_filename),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE files SET status = 'processed', row_count = ?, "
            "parse_error = ?, processed_at = ? WHERE id = ?",
            (report.get("inserted", 0), summary, now, row["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO files
                (original_filename, stored_filename, dataset, uploaded_at,
                 uploaded_by, status, row_count, parse_error, processed_at)
            VALUES (?, ?, ?, ?, ?, 'processed', ?, ?, ?)
            """,
            (
                stored_filename,
                stored_filename,
                dataset,
                now,
                uploaded_by,
                report.get("inserted", 0),
                summary,
                now,
            ),
        )
    conn.commit()
    conn.close()


#: Ledger states that mean "this file is not available to be claimed".
CLAIMED_STATUS = "processing"
PROCESSED_STATUS = "processed"


def claim_file_for_ingestion(dataset: str, stored_filename: str) -> bool:
    """Atomically claim an unprocessed source file for ingestion (single-flight).

    A claim is a compare-and-swap on the ledger row's status, executed inside a
    ``BEGIN IMMEDIATE`` transaction. SQLite's write lock serializes competing
    callers, so exactly one of them can move a file into 'processing'; any other
    caller sees 'processing'/'processed' and is refused. When no ledger row
    exists yet (a file placed directly in the data folder), one is
    created as 'processing' in the same transaction, so the claim is race-free
    with or without a pre-existing row.

    Returns True only for the caller that now owns the file.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.isolation_level = None  # take explicit control of the transaction
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, status FROM files WHERE dataset = ? AND stored_filename = ? "
            "ORDER BY id DESC LIMIT 1",
            (dataset, stored_filename),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO files
                    (original_filename, stored_filename, dataset, uploaded_at,
                     uploaded_by, status, row_count, parse_error, processed_at)
                VALUES (?, ?, ?, ?, NULL, ?, NULL, NULL, NULL)
                """,
                (stored_filename, stored_filename, dataset, now, CLAIMED_STATUS),
            )
            conn.execute("COMMIT")
            return True
        if row["status"] in (CLAIMED_STATUS, PROCESSED_STATUS):
            conn.execute("COMMIT")
            return False
        conn.execute(
            "UPDATE files SET status = ? WHERE id = ?",
            (CLAIMED_STATUS, row["id"]),
        )
        conn.execute("COMMIT")
        return True
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def release_file_claim(dataset: str, stored_filename: str, error: str) -> None:
    """Release a claimed file that could not be ingested.

    Only a row still in 'processing' is released (to 'failed'), so a release can
    never overwrite a completed ingest. A failed file is not 'processed' and is
    therefore retried by a later ingestion pass instead of being silently lost.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.execute(
        "UPDATE files SET status = 'failed', parse_error = ?, processed_at = ? "
        "WHERE dataset = ? AND stored_filename = ? AND status = ?",
        (error, now, dataset, stored_filename, CLAIMED_STATUS),
    )
    conn.commit()
    conn.close()