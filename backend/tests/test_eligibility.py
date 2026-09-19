"""End-to-end tests for the current-welcome eligibility pipeline
(app.services.eligibility) wired to the real window/run/audience foundation.

Each test writes a tiny data snapshot into the isolated DATA_FOLDER, starts a
Run (which freezes the snapshot), optionally drops files arriving AFTER the run
start (to prove snapshot scoping), then evaluates with an explicit invalidation
instant in Africa/Lagos.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core import dates
from app.core.config import settings
from app.db.sms import log_sms
from app.db.windows import WindowStateError
from app.services import eligibility, windows as svc

LAGOS = ZoneInfo("Africa/Lagos")

#: Evaluation instant (Africa/Lagos) used by every test scenario.
NOW = datetime(2026, 9, 19, 18, 30, tzinfo=LAGOS)
#: Login band with H=3 -> [2026-09-19 14:30, 15:30).
NEVER_DEPOSITED = "NeverDepositedUnder400Hrs"
MID_TIER = "MidTierInactive"


def wdt(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=LAGOS)


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
    """The data present BEFORE a run starts (users 1..6, user 7 appears later)."""
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


def _new_window(segments=(NEVER_DEPOSITED,), **kw):
    return svc.create_window(name="test", segments=list(segments), **kw)


def _start_run(data_dir, window_id) -> int:
    started = svc.start_run(window_id)
    return started["run"]["id"]


def _member_ids(win_id) -> list[str]:
    return sorted(str(m["user_id"]) for m in svc.get_audience(win_id))


class TestEvaluateRun:
    def test_full_pipeline(self, _init_db, _fixed_bands):
        data_dir = settings.DATA_FOLDER
        _base_snapshot(data_dir)
        # A failed delivery never starts a cooldown (user 6 stays eligible);
        # a sent SMS 1.5h ago triggers it (user 3).
        log_sms({
            "user_id": "6", "kind": "campaign", "phone": "08067890123",
            "status": "failed", "sent_at": dates.to_utc_iso(wdt(2026, 9, 19, 17, 0)),
        })
        log_sms({
            "user_id": "3", "kind": "campaign", "phone": "08034567890",
            "status": "sent", "sent_at": dates.to_utc_iso(wdt(2026, 9, 19, 17, 0)),
        })
        win = _new_window()
        run_id = _start_run(data_dir, win["id"])

        report = eligibility.evaluate_run(run_id, now=NOW)

        assert report["candidates"] == 6
        assert report["decisions"] == {
            "not_registered": 0,
            "in_onboarding_cohort": 1,      # user 4: registered this week WTD
            "not_in_selected_segment": 0,
            "played_since_login": 1,        # user 2: play after the login
            "cooldown_active": 1,           # user 3: sent < 24h ago
            "eligible": 3,                  # users 1, 5, 6
        }
        assert report["members_by_segment"] == {NEVER_DEPOSITED: 3}
        # user 5's phone is unusable: skipped by the audience service, not added.
        assert report["audience"]["added"] == 2
        assert report["audience"]["invalid_phone"] == 1
        assert report["audience"]["existing"] == 0
        assert report["eligible_count"] == 2

        # N recomputed from the admitted audience (invalid phones excluded).
        win = svc.get_window(win["id"])
        assert win["eligible_count"] == 2
        assert win["control_percentage"] == 50.0  # N<=small bucket -> 50%
        assert win["segment_eligible_counts"] == {NEVER_DEPOSITED: 2}

        assert _member_ids(win["id"]) == ["1", "6"]
        for uid in ("1", "6"):
            member = svc.get_audience_member(win["id"], uid)
            assert member["segment_id"] == NEVER_DEPOSITED
            assert member["phone_valid"] == 1
        assert svc.get_audience_member(win["id"], "5") is None
        assert svc.get_audience_member(win["id"], "2") is None
        assert svc.get_audience_member(win["id"], "3") is None
        assert svc.get_audience_member(win["id"], "4") is None

        decisions = {d["user_id"]: d["decision"] for d in report["details"]}
        assert decisions == {
            "1": "eligible",
            "2": "played_since_login",
            "3": "cooldown_active",
            "4": "in_onboarding_cohort",
            "5": "eligible",   # eligible at evaluation, excluded by phone gate
            "6": "eligible",
        }
        assert report["reference_windows"]["login_age_hours"] == 3
        # snapshot scope surfaces exactly the captured files
        assert report["snapshot_scope"]["login_files"] == 1
        assert report["snapshot_scope"]["sales_files"] == 1

    def test_login_upload_after_run_start_is_scoped_out(self, _init_db, _fixed_bands):
        data_dir = settings.DATA_FOLDER
        _write_csv(
            data_dir,
            "Login_20260919.csv",
            ["userId", "timestamp"],
            [("1", "2026-09-19 15:00:00")],
        )
        win = _new_window()
        run_id = _start_run(data_dir, win["id"])
        # A Login file landing after the run started must not change the Run.
        _write_csv(
            data_dir,
            "Login_20260930.csv",
            ["userId", "timestamp"],
            [("7", "2026-09-19 15:15:00")],
        )

        report = eligibility.evaluate_run(run_id, now=NOW)

        assert report["snapshot_scope"]["login_files"] == 1
        assert report["candidates"] == 1
        assert [d["user_id"] for d in report["details"]] == ["1"]

    def test_sales_upload_after_run_start_is_scoped_out(self, _init_db, _fixed_bands):
        data_dir = settings.DATA_FOLDER
        _write_csv(
            data_dir,
            "Login_20260919.csv",
            ["userId", "timestamp"],
            [("6", "2026-09-19 15:02:00")],
        )
        _write_csv(
            data_dir,
            "Registrations_20260910.csv",
            ["userId", "firstName", "email", "phone", "timestamp"],
            [("6", "Fay", "f@x.com", "08067890123", "2026-09-10 10:00:00")],
        )
        win = _new_window()
        run_id = _start_run(data_dir, win["id"])
        # A play after the login, uploaded after the run started: with correct
        # scoping it never disqualifies the user.
        _write_csv(
            data_dir,
            "Sales_20260930.csv",
            ["userId", "gameName", "amount", "timestamp"],
            [("6", "Lotto", "500.00", "2026-09-19 15:06:00")],
        )

        report = eligibility.evaluate_run(run_id, now=NOW)

        assert report["decisions"]["played_since_login"] == 0
        assert report["audience"]["added"] == 1
        assert _member_ids(win["id"]) == ["6"]

    def test_segment_must_be_selected_on_window(self, _init_db, _fixed_bands):
        data_dir = settings.DATA_FOLDER
        _base_snapshot(data_dir)
        win = _new_window(segments=(MID_TIER,))  # candidates are never-deposited
        run_id = _start_run(data_dir, win["id"])

        report = eligibility.evaluate_run(run_id, now=NOW)

        # The WTD-cohort candidate never reaches the segment gate.
        assert report["decisions"]["not_in_selected_segment"] + report["decisions"]["in_onboarding_cohort"] == report["candidates"]
        assert report["audience"]["added"] == 0
        assert report["eligible_count"] is None
        # no N/control configured: nothing could be assigned
        assert svc.get_window(win["id"])["control_percentage"] is None

    def test_later_upload_does_not_reassign_existing_member(self, _init_db, _fixed_bands):
        data_dir = settings.DATA_FOLDER
        _base_snapshot(data_dir)
        win = _new_window()
        run_id = _start_run(data_dir, win["id"])
        first = eligibility.evaluate_run(run_id, now=NOW)
        # users 1, 3, 6 are eligible (5's phone is unusable; 2 played after
        # login; 4 is the WTD onboarding cohort).
        assert first["audience"]["added"] == 3
        assert first["audience"]["invalid_phone"] == 1
        # Re-evaluating (same snapshot) adds nothing new.
        second = eligibility.evaluate_run(run_id, now=NOW)
        assert second["audience"]["added"] == 0
        assert second["audience"]["existing"] == 3
        assert _member_ids(win["id"]) == ["1", "3", "6"]

    def test_empty_snapshot_leaves_window_untouched(self, _init_db, _fixed_bands):
        win = _new_window()
        run_id = _start_run(settings.DATA_FOLDER, win["id"])

        report = eligibility.evaluate_run(run_id, now=NOW)

        assert report["candidates"] == 0
        assert report["audience"]["added"] == 0
        assert report["eligible_count"] is None
        window = svc.get_window(win["id"])
        assert window["control_percentage"] is None
        assert window["eligible_count"] is None

    def test_only_running_runs_may_be_evaluated(self, _init_db, _fixed_bands):
        data_dir = settings.DATA_FOLDER
        _write_csv(
            data_dir,
            "Login_20260919.csv",
            ["userId", "timestamp"],
            [("1", "2026-09-19 15:00:00")],
        )
        win = _new_window()
        run_id = _start_run(data_dir, win["id"])
        svc.complete_run(run_id)

        with pytest.raises(WindowStateError):
            eligibility.evaluate_run(run_id, now=NOW)

    def test_band_edges(self, _init_db, _fixed_bands):
        # H=3 -> band [14:30, 15:30): lower bound inclusive, upper exclusive.
        data_dir = settings.DATA_FOLDER
        _write_csv(
            data_dir,
            "Login_20260919.csv",
            ["userId", "timestamp"],
            [
                ("1", "2026-09-19 14:29:59"),   # too old
                ("2", "2026-09-19 14:30:00"),   # on the lower edge -> in
                ("3", "2026-09-19 15:29:59"),   # inside
                ("4", "2026-09-19 15:30:00"),   # upper edge -> out (exclusive)
                ("5", "2026-09-19 18:29:00"),   # fresher than H hours
            ],
        )
        _write_csv(
            data_dir,
            "Registrations_20260910.csv",
            ["userId", "firstName", "email", "phone", "timestamp"],
            [
                ("2", "Bob", "b@x.com", "08023456789", "2026-09-10 10:00:00"),
                ("3", "Cara", "c@x.com", "08034567890", "2026-09-10 10:00:00"),
            ],
        )
        win = _new_window()
        run_id = _start_run(data_dir, win["id"])

        report = eligibility.evaluate_run(run_id, now=NOW)

        # Only the two in-band logins are candidates; both are eligible.
        assert report["candidates"] == 2
        assert {d["user_id"] for d in report["details"] if d["decision"] == "eligible"} == {"2", "3"}
        assert report["decisions"]["not_registered"] == 0
        assert report["audience"]["added"] == 2

    def test_unregistered_login_not_eligible(self, _init_db, _fixed_bands):
        data_dir = settings.DATA_FOLDER
        _write_csv(
            data_dir,
            "Login_20260919.csv",
            ["userId", "timestamp"],
            [("9", "2026-09-19 15:00:00")],
        )
        win = _new_window()
        run_id = _start_run(data_dir, win["id"])

        report = eligibility.evaluate_run(run_id, now=NOW)

        assert report["candidates"] == 1
        assert report["decisions"]["not_registered"] == 1
        assert report["audience"]["added"] == 0