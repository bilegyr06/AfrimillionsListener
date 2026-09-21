"""Segment scope lives on Campaign Runs (v2 build-out of Campaign Windows).

Proves the "segment selection moved from Window -> Run" contract end to end:

  * a Window never selects segments (its persisted selection stays empty);
  * a Run requires >=1 non-empty unique segment at start and persists that
    selection immutably for its whole lifecycle;
  * eligibility and audience admission are scoped to the RUN's selection, and a
    legacy `campaign_windows.selected_segments` value can never influence a Run;
  * N/segment counts are derived from the audience actually held, never from a
    Run's selection alone.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.core import dates
from app.core.config import settings
from app.db.database import get_connection
from app.db.windows import WindowConfigError
from app.services import eligibility, windows as svc

LAGOS = ZoneInfo("Africa/Lagos")

#: Evaluation instant (Africa/Lagos) shared by the eligibility scenarios.
NOW = datetime(2026, 9, 19, 18, 30, tzinfo=LAGOS)
NEVER_DEPOSITED = "NeverDepositedUnder400Hrs"
MID_TIER = "MidTierInactive"


def _dt(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=LAGOS)


@pytest.fixture()
def client(_init_db):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture()
def _fixed_bands(monkeypatch):
    """Pin the eligibility knobs a scenario depends on."""
    monkeypatch.setattr(settings, "WELCOME_LOGIN_AGE_HOURS", 3)
    monkeypatch.setattr(settings, "COOLDOWN_HOURS", 24)


def _write_csv(data_dir, name, header, rows):
    path = data_dir / name
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def _base_snapshot(data_dir):
    """The data present BEFORE a run starts (users 1..6)."""
    _write_csv(
        data_dir,
        "Login_20260919.csv",
        ["userId", "timestamp"],
        [
            ("1", "2026-09-19 15:00:00"),
            ("2", "2026-09-19 15:00:00"),
            ("3", "2026-09-19 15:10:00"),
            ("4", "2026-09-19 15:20:00"),
            ("5", "2026-09-19 15:05:00"),
            ("6", "2026-09-19 15:02:00"),
        ],
    )
    _write_csv(
        data_dir,
        "Registrations_20260910.csv",
        ["userId", "firstName", "email", "phone", "timestamp"],
        [
            ("1", "Alice", "a@x.com", "08012345678", "2026-09-10 10:00:00"),
            ("2", "Bob", "b@x.com", "08023456789", "2026-09-10 10:00:00"),
            ("3", "Cara", "c@x.com", "08034567890", "2026-09-10 10:00:00"),
            ("4", "Dan", "d@x.com", "08045678901", "2026-09-15 09:00:00"),
            ("5", "Eve", "e@x.com", "not-a-number", "2026-09-10 10:00:00"),
            ("6", "Fay", "f@x.com", "08067890123", "2026-09-10 10:00:00"),
        ],
    )
    _write_csv(
        data_dir,
        "Sales_20260919.csv",
        ["userId", "gameName", "amount", "timestamp"],
        [("2", "Lotto", "500.00", "2026-09-19 15:05:00")],
    )
    _write_csv(data_dir, "Deposit_events_20260919.csv", ["userId", "timestamp"], [])


def _new_window(**kw):
    if "start" in kw:
        kw["start_time"] = kw.pop("start")
    return svc.create_window(name="test", **kw)


def _member(user_id, segment="unsegmented", phone="08012345678", **extra):
    return {"user_id": str(user_id), "segment_id": segment, "phone": phone, **extra}


def _raw_run_row(run_id: int) -> sqlite3.Row:
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM campaign_runs WHERE id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()


class TestWindowHasNoSegmentScope:
    def test_create_window_never_selects_segments(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        assert w["selected_segments"] == []

    def test_create_window_accepts_no_segments_argument(self, _init_db):
        # The Window surface has no segment knob at all (API/model removal,
        # not a silent ignore).
        with pytest.raises(TypeError):
            svc.create_window(name="x", segments=["a"])
        with pytest.raises(TypeError):
            svc.create_window(name="x", start_time=_dt(2026, 9, 14), segments=[])


class TestRunSegmentValidation:
    def test_run_requires_at_least_one_segment(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        with pytest.raises(WindowConfigError):
            svc.start_run(w["id"], segments=None)
        with pytest.raises(WindowConfigError):
            svc.start_run(w["id"], segments=[])

    def test_invalid_segment_kinds_are_rejected(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        for bad in (["a", "a"], [""], [1, 2], [None], ["a", 1]):
            with pytest.raises(WindowConfigError):
                svc.start_run(w["id"], segments=bad)


class TestRunPersistsSelection:
    def test_selection_persists_and_decodes_across_lifecycle(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))

        run_a = svc.start_run(w["id"], segments=["A", "B"])["run"]
        assert run_a["selected_segments"] == ["A", "B"]
        assert svc.get_run(run_a["id"])["selected_segments"] == ["A", "B"]
        listed = [r for r in svc.list_runs(w["id"]) if r["id"] == run_a["id"]][0]
        assert listed["selected_segments"] == ["A", "B"]

        svc.stop_run(run_a["id"], stop_reason="test")
        assert svc.get_run(run_a["id"])["selected_segments"] == ["A", "B"]

        run_b = svc.start_run(w["id"], segments=["C"])["run"]
        svc.complete_run(run_b["id"])
        assert svc.get_run(run_b["id"])["selected_segments"] == ["C"]
        assert svc.get_run(run_a["id"])["selected_segments"] == ["A", "B"]

    def test_selection_is_immutable_in_the_database(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        run = svc.start_run(w["id"], segments=["SuperActive", "Affinity"])["run"]
        svc.complete_run(run["id"])

        row = _raw_run_row(run["id"])
        assert json.loads(row["selected_segments"]) == ["SuperActive", "Affinity"]


class TestEligibilityUsesRunScope:
    def test_eligibility_scopes_to_the_runs_selection(self, _init_db, _fixed_bands):
        data_dir = settings.DATA_FOLDER
        _base_snapshot(data_dir)
        w = _new_window(start=_dt(2026, 9, 14))
        run = svc.start_run(w["id"], segments=[NEVER_DEPOSITED])["run"]

        report = eligibility.evaluate_run(run["id"], now=NOW)

        # Users 1/3/6 pass the never-deposited gate (5's phone is unusable),
        # user 4 is the unconditional WTD onboarding cohort, user 2 played.
        assert report["decisions"]["not_in_selected_segment"] == 0
        assert report["audience"]["added"] == 3
        assert all(m["run_id"] == run["id"] for m in svc.get_audience(w["id"]))

    def test_two_runs_same_window_select_independently(self, _init_db, _fixed_bands):
        data_dir = settings.DATA_FOLDER
        _base_snapshot(data_dir)
        w = _new_window(start=_dt(2026, 9, 14))

        run1 = svc.start_run(w["id"], segments=[NEVER_DEPOSITED])["run"]
        eval1 = eligibility.evaluate_run(run1["id"], now=NOW)
        assert eval1["audience"]["added"] == 3

        # A second Run in the SAME window scoped to MID_TIER: every candidate
        # is never-deposited, so nothing passes that Run's gate and existing
        # members are never re-admitted or rewritten.
        run2 = svc.start_run(w["id"], segments=[MID_TIER])["run"]
        eval2 = eligibility.evaluate_run(run2["id"], now=NOW)
        assert run2["selected_segments"] == [MID_TIER]
        assert eval2["audience"]["added"] == 0
        # Every candidate fails run2's gate except the unconditional WTD
        # onboarding cohort (user 4), which never reaches the segment gate.
        assert eval2["decisions"]["not_in_selected_segment"] == 5
        members = svc.get_audience(w["id"])
        assert len(members) == 3
        assert all(m["run_id"] == run1["id"] for m in members)


class TestAudienceRunScope:
    def test_add_eligible_users_enforces_run_segment_scope(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.set_eligible_count(w["id"], 10, {"A": 5, "B": 5})
        run = svc.start_run(w["id"], segments=["A", "B"])["run"]

        in_scope = _member("1", segment="A", run_id=run["id"])
        assert svc.add_eligible_users(w["id"], [in_scope])["added"] == 1

        out_of_scope = _member("2", segment="C", run_id=run["id"])
        with pytest.raises(WindowConfigError):
            svc.add_eligible_users(w["id"], [out_of_scope])

        missing_run = _member("3", segment="A", run_id=99999)
        with pytest.raises(WindowConfigError):
            svc.add_eligible_users(w["id"], [missing_run])

    def test_legacy_window_column_cannot_influence_a_run(self, _init_db, _fixed_bands):
        data_dir = settings.DATA_FOLDER
        _base_snapshot(data_dir)
        w = _new_window(start=_dt(2026, 9, 14))

        # Simulate a legacy row (pre-refactor window whose selected_segments was
        # set before this deployment). The Run must ignore it entirely.
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE campaign_windows SET selected_segments = ? WHERE id = ?",
                (json.dumps([MID_TIER]), w["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        stale_window = svc.get_window(w["id"])
        assert stale_window["selected_segments"] == [MID_TIER]

        run = svc.start_run(w["id"], segments=[NEVER_DEPOSITED])["run"]
        assert run["selected_segments"] == [NEVER_DEPOSITED]

        report = eligibility.evaluate_run(run["id"], now=NOW)
        # The run's selection wins: never-deposited users are admitted even
        # though the window row claims MID_TIER.
        assert report["decisions"]["not_in_selected_segment"] == 0
        assert report["audience"]["added"] == 3


class TestEligibleCountsFromAudience:
    def test_refresh_derives_segment_counts_from_actual_audience(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.set_eligible_count(w["id"], 10, {"A": 5, "B": 5})
        run = svc.start_run(w["id"], segments=["A", "B"])["run"]

        # Only segment A members are ever admitted despite the Run selecting
        # both A and B.
        svc.add_eligible_users(
            w["id"],
            [_member("1", segment="A", run_id=run["id"]),
             _member("2", segment="A", run_id=run["id"])],
        )
        w = svc.refresh_eligible_counts(w["id"])

        assert w["eligible_count"] == 2
        assert w["segment_eligible_counts"] == {"A": 2}


class TestRunSegmentsApi:
    def test_window_creation_and_run_segments_flow(self, client):
        w = client.post("/windows", json={"name": "W1"}).json()
        assert w["selected_segments"] == []

        # A Run without a segment scope is rejected (400 config error).
        no_segments = client.post(f"/windows/{w['id']}/runs", json={"note": "x"})
        assert no_segments.status_code == 400

        ok = client.post(
            f"/windows/{w['id']}/runs",
            json={"segments": ["unsegmented"], "note": "go"},
        )
        assert ok.status_code == 200
        assert ok.json()["run"]["selected_segments"] == ["unsegmented"]