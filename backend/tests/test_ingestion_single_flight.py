"""Single-flight ingestion + read-path isolation tests.

Locks in the invariants introduced to stop report reads from ingesting source
files and to keep concurrent callers from parsing the same file:

  * a report READ never ingests pending source files (no hidden writes from
    GET /windows/{id}/report);
  * the files-ledger claim is atomic and exclusive, and a released (failed)
    claim can be re-claimed;
  * concurrent ingestion of one file parses/inserts it exactly once;
  * a file whose read fails is NOT marked processed, so it is retried;
  * a manual upload ingests immediately at the upload lifecycle point;
  * explicit ingest_pending_files() is what makes pending files visible.

Parsing itself is covered by tests/test_plays.py; the equivalence test here
guards the vectorized loop against ordering/rounding regressions.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.core.config import settings
from app.db.database import get_connection
from app.db.files import (
    claim_file_for_ingestion,
    release_file_claim,
    sales_file_processed,
)
from app.services import window_report
from app.services import windows as svc
from app.services.ingestion import ingest_pending_files
from app.services.plays import ingest_sales_file, parse_plays_frame

LAGOS = ZoneInfo("Africa/Lagos")


def _write_sales(data_dir, rows, name="Sales_test.csv"):
    pd.DataFrame(rows, columns=["userId", "gameName", "amount", "timestamp"]).to_csv(
        data_dir / name, index=False
    )


def _count_plays() -> int:
    conn = get_connection()
    n = conn.execute("SELECT COUNT(*) AS n FROM plays").fetchone()["n"]
    conn.close()
    return n


def _ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


class TestClaimLedger:
    def test_claim_is_exclusive(self, _init_db):
        assert claim_file_for_ingestion("Sales", "Sales_x.csv") is True
        # Second claim by anyone (this connection or another) is refused.
        assert claim_file_for_ingestion("Sales", "Sales_x.csv") is False

    def test_released_claim_can_be_reclaimed(self, _init_db):
        assert claim_file_for_ingestion("Sales", "Sales_x.csv") is True
        release_file_claim("Sales", "Sales_x.csv", error="read failed")
        # A failed file is retryable, not permanently locked or marked done.
        assert claim_file_for_ingestion("Sales", "Sales_x.csv") is True

    def test_claim_scoped_by_dataset(self, _init_db):
        assert claim_file_for_ingestion("Sales", "same.csv") is True
        assert claim_file_for_ingestion("Deposit_events", "same.csv") is True


class TestConcurrentIngestion:
    def test_two_threads_parse_the_same_file_once(self, _init_db):
        _write_sales(settings.DATA_FOLDER, [("1", "Aviator", 10, _ago(1))])
        path = settings.DATA_FOLDER / "Sales_test.csv"

        results: list[dict] = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            results.append(ingest_sales_file(path))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 2
        assert sum(1 for r in results if r.get("claimed")) == 1
        assert _count_plays() == 1
        assert sales_file_processed("Sales_test.csv") is True

    def test_failed_read_is_not_marked_processed_and_is_retried(self, _init_db):
        path = settings.DATA_FOLDER / "Sales_test.csv"
        path.write_text("")  # empty -> pandas read failure

        report = ingest_sales_file(path)
        assert report["parse_errors"] == 1
        assert sales_file_processed("Sales_test.csv") is False

        # The next pass claims it again instead of skipping it as done.
        retry = ingest_sales_file(path)
        assert retry["claimed"] is True


class TestReportReadDoesNotIngest:
    def _window(self):
        return svc.create_window(name="ro", start_time=datetime(2026, 9, 14, tzinfo=LAGOS))

    def test_report_read_leaves_pending_file_unprocessed(self, _init_db):
        win = self._window()
        _write_sales(settings.DATA_FOLDER, [("1", "Aviator", 10, _ago(1))])

        report = window_report.get_report(win["id"])
        assert report["window"]["id"] == win["id"]

        # Reading the report must not have parsed or marked the file.
        assert sales_file_processed("Sales_test.csv") is False
        assert _count_plays() == 0

    def test_explicit_ingest_then_report_reflects_facts(self, _init_db):
        win = self._window()
        _write_sales(settings.DATA_FOLDER, [("1", "Aviator", 10, _ago(1))])

        ingest_pending_files()
        assert sales_file_processed("Sales_test.csv") is True
        assert _count_plays() == 1

        window_report.get_report(win["id"])  # pure read still succeeds
        # Idempotent: a second explicit pass does not re-parse or duplicate.
        ingest_pending_files()
        assert _count_plays() == 1


class TestUploadIngestsImmediately:
    def test_uploaded_sales_file_is_ingested_and_ledger_processed(
        self, _init_db, _isolated_db
    ):
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        content = (
            "userId,gameName,amount,timestamp\n"
            f"1,Aviator,10,{_ago(1)}\n"
        ).encode()
        r = client.post("/files", files={"file": ("Sales_manual.csv", content, "text/csv")})
        assert r.status_code == 200
        stored = r.json()["records"][0]["stored_filename"]

        assert sales_file_processed(stored) is True
        assert _count_plays() == 1


class TestParsePlaysFrameEquivalence:
    def test_vectorized_loop_preserves_order_amounts_and_future_count(self):
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        df = pd.DataFrame(
            [
                ("1", "Aviator", "0.1", recent),
                ("2", "Mines", "12.50", future),
                ("3", "Mines", "5", recent),
            ],
            columns=["userId", "gameName", "amount", "timestamp"],
        )
        parsed = parse_plays_frame("Sales_test.csv", df)
        assert [r["user_id"] for r in parsed["records"]] == ["1", "2", "3"]
        assert [r["amount"] for r in parsed["records"]] == [0.1, 12.5, 5.0]
        assert parsed["future"] == 1
        assert parsed["discovered"] == 3
