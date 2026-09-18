"""Uploaded-files registry data module.

Owns the files table: manual-upload registration, listing, and the Sales
ingestion ledger (sales_file_processed / mark_plays_ingested). Consumers:
app.services.ingestion and app.services.plays.
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


def mark_plays_ingested(stored_filename: str, report: dict, uploaded_by: str | None = None):
    """Record that a Sales file was ingested (ledger row upsert).

    The files table doubles as the ingestion ledger: manual uploads already
    have a row (status 'succeeded' written at accept time), which is promoted
    to 'processed'; generic/auto files get a new 'processed' row. row_count
    records inserted plays; parse_error carries the rejection summary.
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
        "SELECT id FROM files WHERE dataset = 'Sales' AND stored_filename = ?",
        (stored_filename,),
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
            VALUES (?, ?, 'Sales', ?, ?, 'processed', ?, ?, ?)
            """,
            (
                stored_filename,
                stored_filename,
                now,
                uploaded_by,
                report.get("inserted", 0),
                summary,
                now,
            ),
        )
    conn.commit()
    conn.close()