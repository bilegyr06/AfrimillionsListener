"""Shared test fixtures for stats and metrics tests."""
import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("TERMII_API_KEY", "test-key")
os.environ.setdefault("TERMII_BASE_URL", "https://test.api.termii.com/api")
os.environ.setdefault("TERMII_SENDER_ID", "TestSender")

_TEST_DIR = Path(__file__).resolve().parent.parent / ".pytest-tmp"


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    """Point the database to a fresh temp directory for each test."""
    _TEST_DIR.mkdir(parents=True, exist_ok=True)
    import uuid
    test_id = _TEST_DIR / uuid.uuid4().hex[:8]
    test_id.mkdir(parents=True, exist_ok=True)
    db_path = test_id / "test.db"
    data_dir = test_id / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("app.config.settings.DB_PATH", db_path)
    monkeypatch.setattr("app.config.settings.DATA_FOLDER", data_dir)
    yield db_path
    shutil.rmtree(test_id, ignore_errors=True)


@pytest.fixture()
def _init_db(_isolated_db):
    """Initialize the database tables."""
    from app.database import init_db
    init_db()
    return _isolated_db
