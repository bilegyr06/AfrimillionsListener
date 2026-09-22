"""Regression: changes under data/ must never start a cycle or dispatch SMS.

Legacy behaviour: a watchdog-based filesystem watcher
(``app.workers.watcher``) reacted to CSV creation/modification in
``DATA_FOLDER`` by scheduling ``processor.begin_cycle()`` on the event loop
(it also ran periodic auto-start, delivery-status sync and wallet-snapshot
jobs). That automatic behaviour was removed: the workflow is explicitly
operator-driven — data upload/ingestion -> operator starts a Run (or
``/trigger*``) -> snapshot/evaluation -> dispatch.

These tests pin two things:

* Files changing in ``data/`` never call ``begin_cycle()`` and never reach
  the SMS dispatcher, at any time of day, even when apparently eligible data
  is present.
* The explicit operator paths still work: ``/trigger*`` continues to invoke
  ``begin_cycle()``.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from datetime import time as _time

import pandas as pd
import pytest

# Ensure env vars are set before any app imports.
os.environ.setdefault("TERMII_API_KEY", "test-key")
os.environ.setdefault("TERMII_BASE_URL", "https://test.api.termii.com/api")
os.environ.setdefault("TERMII_SENDER_ID", "TestSender")

from app.core import state
from app.core.config import settings
from app.workers import processor


def _csv(data_dir, name: str, columns, rows) -> None:
    pd.DataFrame(list(rows), columns=columns).to_csv(
        data_dir / name, index=False
    )


def _eligible_looking_data(data_dir) -> None:
    """Seed source files that an automatic cycle would consider send-worthy.

    Both features enabled: welcome (recent login, no play) and inactive
    (login older than the inactivity threshold, valid Nigerian phone).
    """
    _csv(
        data_dir,
        "Login_export.csv",
        ["userId", "timestamp"],
        [("1", "2026-09-01 10:00:00"), ("2", "2026-09-01 11:00:00")],
    )
    _csv(
        data_dir,
        "Registrations_export.csv",
        ["userId", "firstName", "email", "phone", "timestamp"],
        [
            ("1", "Ada", "ada@x.com", "08012345678", "2026-09-01 09:00:00"),
            ("2", "Bola", "bola@x.com", "08087654321", "2026-09-01 09:00:00"),
        ],
    )
    _csv(
        data_dir,
        "Sales_export.csv",
        ["userId", "gameName", "amount", "timestamp"],
        [],
    )


@pytest.fixture()
def _anytime_window(monkeypatch):
    """Neutralise the time window so a regression can't hide behind the clock."""
    monkeypatch.setattr(settings, "START_TIME", _time(0, 0))
    monkeypatch.setattr(settings, "END_TIME", _time(23, 59))
    monkeypatch.setattr(settings, "CYCLE_END_HOUR", 23)
    monkeypatch.setattr(settings, "ENABLED_FEATURES", {"welcome", "inactive"})


class TestNoAutomaticTriggerOnDataChange:
    def test_watcher_and_downloader_modules_are_gone(self):
        assert importlib.util.find_spec("app.workers.watcher") is None
        assert importlib.util.find_spec("app.integrations.csv_downloader") is None

    def test_startup_imports_no_filesystem_watcher(self, _init_db):
        import app.main  # noqa: F401  (importing main must not pull a watcher)

        assert "watchdog" not in sys.modules
        assert "app.workers.watcher" not in sys.modules

    def test_creating_and_modifying_csv_never_starts_cycle_or_sends(
        self, _init_db, _isolated_db, _anytime_window, monkeypatch
    ):
        from fastapi.testclient import TestClient
        from app.integrations.in_memory import InMemorySmsGateway
        from app.integrations.sms_gateway import set_default_gateway
        from app.main import app

        data_dir = settings.DATA_FOLDER
        assert str(data_dir) == str(_isolated_db.parent / "data")

        # Spy begin_cycle at every import site. Delegating to the real one
        # keeps the guard honest: an auto-trigger would genuinely try to run.
        begin_cycle_calls: list[str] = []
        real_begin_cycle = processor.begin_cycle

        def _spy(*args, **kwargs):
            begin_cycle_calls.append("begin_cycle")
            return real_begin_cycle(*args, **kwargs)

        monkeypatch.setattr("app.workers.processor.begin_cycle", _spy)
        monkeypatch.setattr("app.routers.system.begin_cycle", _spy)

        # An in-memory gateway records any (nonexistent) SMS dispatch attempt.
        gateway = InMemorySmsGateway()
        set_default_gateway(gateway)

        with TestClient(app) as client:
            # Reinstall the in-memory gateway: lifespan installs Termii.
            set_default_gateway(gateway)

            # CREATE a CSV, then MODIFY an existing one.
            _eligible_looking_data(data_dir)
            _csv(
                data_dir,
                "Login_export.csv",
                ["userId", "timestamp"],
                [("1", "2026-09-01 10:00:00"), ("2", "2026-09-02 11:00:00")],
            )

            # Allow any (removed) background watcher a generous chance to react.
            time.sleep(3)

        assert begin_cycle_calls == []
        assert gateway.calls["send"] == 0
        assert gateway.attempts == []
        assert state.current_task is None
        assert state.cancel_requested is False


class TestExplicitTriggerRemainsFunctional:
    def test_successful_trigger_returns_200_with_contract_body(
        self, _init_db, _anytime_window, monkeypatch
    ):
        from fastapi.testclient import TestClient
        from app.main import app

        monkeypatch.setattr("app.routers.system.begin_cycle", lambda *a, **k: True)

        with TestClient(app) as client:
            r = client.post("/trigger")

        assert r.status_code == 200
        assert r.json() == {"message": "Notification cycle started."}

    def test_trigger_routes_still_invoke_begin_cycle(
        self, _init_db, _anytime_window, monkeypatch
    ):
        from fastapi.testclient import TestClient
        from app.main import app

        calls: list[str] = []

        def _stub(*args, **kwargs):
            calls.append("begin_cycle")
            return True

        monkeypatch.setattr("app.routers.system.begin_cycle", _stub)

        with TestClient(app) as client:
            assert client.post("/trigger").status_code == 200
            assert client.post("/trigger/welcome").status_code == 200
            assert client.post("/trigger/inactive").status_code == 200

        assert calls == ["begin_cycle", "begin_cycle", "begin_cycle"]

    def test_blocked_outside_window_returns_409(
        self, _init_db, _anytime_window, monkeypatch
    ):
        from fastapi.testclient import TestClient
        from app.main import app

        monkeypatch.setattr(settings, "START_TIME", _time(12, 0))
        monkeypatch.setattr(settings, "END_TIME", _time(12, 0))

        calls: list[str] = []
        monkeypatch.setattr(
            "app.routers.system.begin_cycle",
            lambda *a, **k: calls.append("begin_cycle") or True,
        )

        with TestClient(app) as client:
            r = client.post("/trigger")

        assert r.status_code == 409
        assert r.json() == {
            "message": "Notification cycle cannot begin outside the allowed time range."
        }
        assert calls == []

    def test_blocked_while_cycle_running_returns_409(
        self, _init_db, _anytime_window, monkeypatch
    ):
        from fastapi.testclient import TestClient
        from app.main import app

        monkeypatch.setattr("app.routers.system.begin_cycle", lambda *a, **k: False)

        with TestClient(app) as client:
            r = client.post("/trigger")

        assert r.status_code == 409
        assert r.json() == {"message": "A notification cycle is already running."}

    def test_blocked_feature_not_enabled_returns_409(
        self, _init_db, _anytime_window, monkeypatch
    ):
        from fastapi.testclient import TestClient
        from app.main import app

        monkeypatch.setattr(settings, "ENABLED_FEATURES", {"inactive"})

        calls: list[str] = []
        monkeypatch.setattr(
            "app.routers.system.begin_cycle",
            lambda *a, **k: calls.append("begin_cycle") or True,
        )

        with TestClient(app) as client:
            r = client.post("/trigger/welcome")

        assert r.status_code == 409
        assert r.json() == {"message": "Requested feature is not enabled."}
        assert calls == []