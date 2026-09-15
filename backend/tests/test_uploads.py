"""Tests for manual CSV upload and the file registry."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("TERMII_API_KEY", "test-key")
os.environ.setdefault("TERMII_BASE_URL", "https://test.api.termii.com/api")
os.environ.setdefault("TERMII_SENDER_ID", "TestSender")

from app.db.database import get_file, list_files


def _csv_bytes(text: str) -> bytes:
    return text.encode()


def _upload(client, filename: str, content: bytes, expect: int):
    return client.post(
        "/files",
        files={"file": (filename, content, "text/csv")},
    )


@pytest.fixture()
def client(_init_db):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


class TestUpload:
    def test_upload_valid_registrations(self, client, _isolated_db):
        content = _csv_bytes(
            "userId,firstName,email,phone,timestamp\n"
            "1,Ada,ada@x.com,08012345678,2026-06-01 10:00:00\n"
            "2,Bola,bola@x.com,08087654321,2026-06-01 11:00:00\n"
        )
        r = _upload(client, "Registrations_exports.csv", content, 200)
        data = r.json()

        assert data["record"]["status"] == "succeeded"
        assert data["record"]["dataset"] == "Registrations"
        assert data["record"]["row_count"] == 2
        # File landed in the data folder under the recognized prefix.
        stored = data["record"]["stored_filename"]
        assert stored == "Registrations_exports.csv"
        assert (_isolated_db.parent / "data" / stored).exists()

        records = list_files()
        assert len(records) == 1
        assert records[0]["status"] == "succeeded"

    def test_upload_login_visible_to_pipeline(self, client, _isolated_db):
        content = _csv_bytes(
            "userId,timestamp\n"
            "1,2026-06-01 10:00:00\n"
            "2,2026-06-01 11:00:00\n"
        )
        r = _upload(client, "mystery_export.csv", content, 200)
        data = r.json()
        assert data["record"]["dataset"] == "Login"
        assert data["record"]["stored_filename"].startswith("Login_")

        from app.services.ingestion import read_login_events
        df = read_login_events()
        assert len(df) == 2

    def test_upload_sales_detected_by_columns(self, client, _isolated_db):
        content = _csv_bytes(
            "userId,gameName,amount,timestamp\n"
            "1,G1,10,2026-06-01 10:00:00\n"
        )
        r = _upload(client, "plays_export.csv", content, 200)
        assert r.json()["record"]["dataset"] == "Sales"
        assert r.json()["record"]["row_count"] == 1

    def test_upload_unparseable_rejected(self, client, _isolated_db):
        # A CSV arriving without a string terminator cannot be parsed -> rejected.
        content = b'"unclosed'
        r = _upload(client, "broken.csv", content, 400)
        data = r.json()
        assert data["record"]["status"] == "failed"
        assert data["record"]["parse_error"]

        records = list_files()
        assert len(records) == 1
        assert records[0]["stored_filename"] == ""
        # Nothing was written into the data folder.
        assert list((_isolated_db / "data").glob("*.csv")) == []

    def test_upload_empty_rejected(self, client, _isolated_db):
        r = _upload(client, "empty.csv", b"", 400)
        assert "empty" in r.json()["message"].lower()
        assert list_files() == []

    def test_upload_sanitizes_traversal_name(self, client, _isolated_db):
        content = _csv_bytes(
            "userId,timestamp\n1,2026-06-01 10:00:00\n"
        )
        r = _upload(client, "../evil.csv", content, 200)
        data = r.json()
        assert ".." not in data["record"]["stored_filename"]
        stored = data["record"]["stored_filename"]
        row = get_file(data["record"]["id"])
        assert row["stored_filename"] == stored
        assert (_isolated_db.parent / "data" / stored).exists()

    def test_upload_known_prefix_keeps_name(self, client, _isolated_db):
        content = _csv_bytes(
            "userId,firstName,phone\n1,Ada,08012345678\n"
        )
        r = _upload(client, "Registrations_manual.csv", content, 200)
        assert r.json()["record"]["stored_filename"] == "Registrations_manual.csv"