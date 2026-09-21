"""Tests for manual CSV upload and the file registry."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("TERMII_API_KEY", "test-key")
os.environ.setdefault("TERMII_BASE_URL", "https://test.api.termii.com/api")
os.environ.setdefault("TERMII_SENDER_ID", "TestSender")

from app.db.files import get_file, list_files


def _csv_bytes(text: str) -> bytes:
    return text.encode()


def _upload(client, filename: str, content: bytes, expect: int):
    return client.post(
        "/files",
        files={"file": (filename, content, "text/csv")},
    )


def _upload_many(client, files: list[tuple[str, bytes]]):
    return client.post(
        "/files",
        files=[("file", (name, content, "text/csv")) for name, content in files],
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

        assert data["records"][0]["status"] == "succeeded"
        assert data["records"][0]["dataset"] == "Registrations"
        assert data["records"][0]["row_count"] == 2
        # File landed in the data folder under the recognized prefix.
        stored = data["records"][0]["stored_filename"]
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
        assert data["records"][0]["dataset"] == "Login"
        assert data["records"][0]["stored_filename"].startswith("Login_")

        from app.services.ingestion import read_login_events
        df = read_login_events()
        assert len(df) == 2

    def test_upload_sales_detected_by_columns(self, client, _isolated_db):
        content = _csv_bytes(
            "userId,gameName,amount,timestamp\n"
            "1,G1,10,2026-06-01 10:00:00\n"
        )
        r = _upload(client, "plays_export.csv", content, 200)
        assert r.json()["records"][0]["dataset"] == "Sales"
        assert r.json()["records"][0]["row_count"] == 1

    def test_upload_unparseable_rejected(self, client, _isolated_db):
        # A CSV arriving without a string terminator cannot be parsed -> rejected.
        content = b'"unclosed'
        r = _upload(client, "broken.csv", content, 400)
        data = r.json()
        assert data["records"][0]["status"] == "failed"
        assert data["records"][0]["parse_error"]

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
        assert ".." not in data["records"][0]["stored_filename"]
        stored = data["records"][0]["stored_filename"]
        row = get_file(data["records"][0]["id"])
        assert row["stored_filename"] == stored
        assert (_isolated_db.parent / "data" / stored).exists()

    def test_upload_known_prefix_keeps_name(self, client, _isolated_db):
        content = _csv_bytes(
            "userId,firstName,phone\n1,Ada,08012345678\n"
        )
        r = _upload(client, "Registrations_manual.csv", content, 200)
        assert r.json()["records"][0]["stored_filename"] == "Registrations_manual.csv"

    def test_upload_multiple_files(self, client, _isolated_db):
        r = _upload_many(
            client,
            [
                ("Registrations_a.csv", _csv_bytes("userId,firstName,phone\n1,Ada,08012345678\n")),
                ("Login_b.csv", _csv_bytes("userId,timestamp\n1,2026-06-01 10:00:00\n")),
            ],
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["records"]) == 2
        assert {rec["dataset"] for rec in data["records"]} == {"Registrations", "Login"}
        assert all(rec["status"] == "succeeded" for rec in data["records"])
        assert data["errors"] == []
        assert len(list_files()) == 2

    def test_upload_batch_reports_partial_failure(self, client, _isolated_db):
        r = _upload_many(
            client,
            [
                ("Registrations_ok.csv", _csv_bytes("userId,firstName,phone\n1,Ada,08012345678\n")),
                ("broken.csv", b'"unclosed'),
            ],
        )
        assert r.status_code == 200
        data = r.json()
        assert {rec["status"] for rec in data["records"]} == {"succeeded", "failed"}
        assert "1 of 2" in data["message"]
        assert "rejected" in data["message"]

    def test_upload_batch_all_rejected(self, client, _isolated_db):
        r = _upload_many(
            client,
            [
                ("broken.csv", b'"unclosed'),
                ("empty.csv", b""),
            ],
        )
        assert r.status_code == 400
        data = r.json()
        assert data["records"][0]["status"] == "failed"
        assert len(data["errors"]) == 1
        assert "empty" in data["message"].lower()