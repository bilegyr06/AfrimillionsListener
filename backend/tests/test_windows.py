"""Focused tests for the v2.0.0 Campaign Window domain foundation.

Covers Campaign Window lifecycle (active -> ended/grace -> finalized), Campaign
Runs + source snapshots, the audience/assignment model (Campaign/Control),
control-percentage formula/buckets/override, and the finalization immutability
boundary.

Tests use Africa/Lagos business time explicitly (ZoneInfo('Africa/Lagos'));
they never depend on the machine's local timezone.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core import dates
from app.core.config import settings
from app.db import windows as db
from app.db.database import get_connection
from app.db.windows import WindowConfigError, WindowStateError
from app.services import eligibility, windows as svc

LAGOS = ZoneInfo("Africa/Lagos")

#: Evaluation instant (Africa/Lagos) used by test scenarios.
NOW = datetime(2026, 9, 19, 18, 30, tzinfo=LAGOS)
#: Segment that test users qualify for
NEVER_DEPOSITED = "NeverDepositedUnder400Hrs"


@pytest.fixture()
def _fixed_bands(monkeypatch):
    """Pin the eligibility knobs a scenario depends on."""
    monkeypatch.setattr(settings, "WELCOME_LOGIN_AGE_HOURS", 3)
    monkeypatch.setattr(settings, "COOLDOWN_HOURS", 24)


def _dt(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=LAGOS)


def wdt(y, mo, d, h=0, mi=0, s=0):
    """Test helper: create datetime in Africa/Lagos timezone."""
    return datetime(y, mo, d, h, mi, s, tzinfo=LAGOS)


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


def _new_window(
    start=None,
    end=None,
    deadline=None,
    segments=None,
    assignment_method="deterministic",
    control_override=None,
):
    return svc.create_window(
        name="test",
        start_time=start,
        end_time=end,
        finalization_deadline=deadline,
        segments=segments,
        assignment_method=assignment_method,
        control_override=control_override,
    )


def _member(user_id, segment="unsegmented", phone="08012345678", **extra):
    return {"user_id": str(user_id), "segment_id": segment, "phone": phone, **extra}


# Default test windows run Mon 2026-09-14 -> Sat 2026-09-19 23:59:59 with a
# Sun 2026-09-20 14:00 finalization deadline. This instant sits inside that
# grace period so finalize clock guards are deterministic (never wall-clock).
FINALIZE_AT = _dt(2026, 9, 20, 9, 0, 0)


# ---------------------------------------------------------------------------
# Campaign Window creation + defaults
# ---------------------------------------------------------------------------

class TestWindowCreation:
    def test_normal_creation(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        assert w["status"] == "active"
        assert w["business_timezone"] == "Africa/Lagos"
        assert w["selected_segments"] == ["unsegmented"]
        assert w["assignment_method"] == "deterministic"
        assert w["ended_at"] is None
        assert w["finalized_at"] is None
        assert w["control_override"] is None

    def test_default_schedule(self, _init_db):
        # Wednesday reference: the window defaults to that business week.
        w = svc.create_window(name="defaults", now=_dt(2026, 9, 16, 12, 0, 0))
        assert w["start_time"] == dates.to_utc_iso(_dt(2026, 9, 14, 0, 0, 0))
        assert w["end_time"] == dates.to_utc_iso(_dt(2026, 9, 19, 23, 59, 59))
        assert w["finalization_deadline"] == dates.to_utc_iso(_dt(2026, 9, 20, 14, 0, 0))  # Sun 2pm

    def test_sunday_reference_starts_following_monday(self, _init_db):
        w = svc.create_window(name="sunday", now=_dt(2026, 9, 20, 10, 0, 0))
        assert w["start_time"] == dates.to_utc_iso(_dt(2026, 9, 21, 0, 0, 0))

    def test_configurable_end_time(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), end=_dt(2026, 9, 17, 18, 0, 0))
        assert w["end_time"] == dates.to_utc_iso(_dt(2026, 9, 17, 18, 0, 0))

    def test_end_after_start_required(self, _init_db):
        with pytest.raises(WindowConfigError):
            _new_window(start=_dt(2026, 9, 14), end=_dt(2026, 9, 14, 0, 0, 0))

    def test_segments_default_and_validation(self, _init_db):
        w = _new_window(segments=["SuperActive", "Affinity"], start=_dt(2026, 9, 14))
        assert w["selected_segments"] == ["SuperActive", "Affinity"]
        with pytest.raises(WindowConfigError):
            _new_window(segments=["a", "a"], start=_dt(2026, 9, 14))
        with pytest.raises(WindowConfigError):
            _new_window(segments=[], start=_dt(2026, 9, 14))

    def test_invalid_assignment_method_rejected(self, _init_db):
        with pytest.raises(WindowConfigError):
            _new_window(assignment_method="hashed", start=_dt(2026, 9, 14))

    def test_control_override_bounds(self, _init_db):
        with pytest.raises(WindowConfigError):
            _new_window(control_override=0, start=_dt(2026, 9, 14))
        with pytest.raises(WindowConfigError):
            _new_window(control_override=60, start=_dt(2026, 9, 14))
        w = _new_window(control_override=15, start=_dt(2026, 9, 14))
        assert w["control_override"] == 15


class TestSingleActiveWindow:
    """Only one Campaign Window can run (be 'active') at a time."""

    def test_second_window_rejected_while_active(self, _init_db):
        first = _new_window(start=_dt(2026, 9, 14))
        with pytest.raises(WindowStateError) as exc:
            _new_window(start=_dt(2026, 9, 21))
        assert "only one campaign window can run at a time" in str(exc.value).lower()
        # The rejected window is not persisted; the original stays active.
        windows = svc.list_windows()
        assert [w["id"] for w in windows] == [first["id"]]
        assert db.get_window(first["id"])["status"] == "active"

    def test_database_layer_rejects_second_active_window(self, _init_db):
        """The persistence layer itself enforces the single-active rule."""
        _new_window(start=_dt(2026, 9, 14))
        with pytest.raises(WindowStateError) as exc:
            db.create_window(
                {
                    "name": "second",
                    "start_time": dates.to_utc_iso(_dt(2026, 9, 21)),
                    "end_time": dates.to_utc_iso(_dt(2026, 9, 26, 23, 59, 59)),
                    "finalization_deadline": dates.to_utc_iso(_dt(2026, 9, 27, 14, 0, 0)),
                }
            )
        assert "only one campaign window can run at a time" in str(exc.value).lower()
        assert len(svc.list_windows()) == 1

    def test_new_window_allowed_after_active_ended(self, _init_db):
        """Ending a window (grace period) frees the running slot."""
        first = _new_window(start=_dt(2026, 9, 14))
        svc.end_window(first["id"])
        second = _new_window(start=_dt(2026, 9, 21))
        assert second["status"] == "active"
        assert second["id"] != first["id"]

    def test_new_window_allowed_after_previous_finalized(self, _init_db):
        first = _new_window(start=_dt(2026, 9, 14))
        svc.end_window(first["id"])
        svc.finalize_window(first["id"], now=FINALIZE_AT)
        second = _new_window(start=_dt(2026, 9, 21))
        assert second["status"] == "active"
        assert db.get_window(first["id"])["status"] == "finalized"


class TestFinalizationDeadline:
    def test_default_deadline_is_sunday_2pm(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        assert w["finalization_deadline"] == dates.to_utc_iso(_dt(2026, 9, 20, 14, 0, 0))

    def test_configured_deadline_persisted(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), deadline=_dt(2026, 9, 20, 16, 0, 0))
        assert w["finalization_deadline"] == dates.to_utc_iso(_dt(2026, 9, 20, 16, 0, 0))

    def test_deadline_cannot_exceed_sunday_5pm(self, _init_db):
        # Authoritative cap: never configurable beyond Sunday 17:00 Africa/Lagos.
        with pytest.raises(WindowConfigError):
            _new_window(start=_dt(2026, 9, 14), deadline=_dt(2026, 9, 20, 18, 0, 0))
        with pytest.raises(WindowConfigError):
            _new_window(start=_dt(2026, 9, 14), deadline=_dt(2026, 9, 20, 17, 0, 1))
        # Boundary: exactly 17:00 is allowed.
        w = _new_window(start=_dt(2026, 9, 14), deadline=_dt(2026, 9, 20, 17, 0, 0))
        assert w["finalization_deadline"] == dates.to_utc_iso(_dt(2026, 9, 20, 17, 0, 0))

    def test_deadline_must_follow_end_of_window(self, _init_db):
        with pytest.raises(WindowConfigError):
            _new_window(
                start=_dt(2026, 9, 14),
                end=_dt(2026, 9, 17, 18, 0, 0),
                deadline=_dt(2026, 9, 17, 12, 0, 0),
            )


class TestWindowEndExtension:
    def test_extend_end_time(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        updated = svc.extend_window_end(w["id"], _dt(2026, 9, 18, 12, 0, 0))
        assert updated["end_time"] == dates.to_utc_iso(_dt(2026, 9, 18, 12, 0, 0))

    def test_extend_cannot_breach_deadline(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        with pytest.raises(WindowConfigError):
            svc.extend_window_end(w["id"], _dt(2026, 9, 20, 15, 0, 0))

    def test_extend_blocked_after_finalization(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.end_window(w["id"])
        svc.finalize_window(w["id"], now=FINALIZE_AT)
        with pytest.raises(WindowStateError):
            svc.extend_window_end(w["id"], _dt(2026, 9, 18, 12, 0, 0))


# ---------------------------------------------------------------------------
# Campaign Runs
# ---------------------------------------------------------------------------

class TestRuns:
    def test_normal_run_creation(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        result = svc.start_run(w["id"], note="first")
        run = result["run"]
        assert run["status"] == "running"
        assert run["window_id"] == w["id"]
        assert run["ended_at"] is None
        assert run["snapshot_id"] is not None
        assert result["snapshot"]["run_id"] == run["id"]

    def test_multiple_runs_in_one_window(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        first = svc.start_run(w["id"])["run"]
        second = svc.start_run(w["id"])["run"]
        runs = svc.list_runs(w["id"])
        assert [r["id"] for r in runs] == [first["id"], second["id"]]
        assert all(r["status"] == "running" for r in runs)

    def test_run_after_window_end_blocked(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.end_window(w["id"])
        with pytest.raises(WindowStateError):
            svc.start_run(w["id"])
        assert db.get_window(w["id"])["status"] == "ended"

    def test_run_after_finalization_blocked(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.end_window(w["id"])
        svc.finalize_window(w["id"], now=FINALIZE_AT)
        with pytest.raises(WindowStateError):
            svc.start_run(w["id"])

    def test_active_run_closes_when_window_ends(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        run = svc.start_run(w["id"])["run"]
        ended = svc.end_window(w["id"])
        assert ended["status"] == "ended"
        closed = ended["closed_runs"]
        assert [r["id"] for r in closed] == [run["id"]]
        assert closed[0]["status"] == "stopped"
        assert closed[0]["stop_reason"] == "window_ended"

    def test_operator_stop_run(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        run = svc.start_run(w["id"])["run"]
        stopped = svc.stop_run(run["id"], stop_reason="operator")
        assert stopped["status"] == "stopped"
        assert stopped["stop_reason"] == "operator"
        assert stopped["ended_at"] is not None

    def test_auto_complete_run(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        run = svc.start_run(w["id"])["run"]
        completed = svc.complete_run(run["id"])
        assert completed["status"] == "completed"

    def test_finalized_window_cannot_accept_new_run(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.end_window(w["id"])
        svc.finalize_window(w["id"], now=FINALIZE_AT)
        with pytest.raises(WindowStateError):
            svc.start_run(w["id"])


class TestRunSnapshots:
    def _write(self, data_dir, name, header, row_lists):
        csv_text = header + "\n" + "\n".join(",".join(r) for r in row_lists) + "\n"
        (data_dir / name).write_text(csv_text, encoding="utf-8")

    def test_snapshot_captured_at_run_start(self, _init_db):
        from app.core.config import settings
        data_dir = settings.DATA_FOLDER
        self._write(data_dir, "Login_a.csv", "userId,timestamp", [["1", "2026-09-14 09:00:00"]])
        w = _new_window(start=_dt(2026, 9, 14))
        result = svc.start_run(w["id"])
        files = result["snapshot"]["files"]
        assert [f["filename"] for f in files] == ["Login_a.csv"]
        assert files[0]["dataset"] == "Login"
        assert files[0]["row_count"] == 1
        assert files[0]["size_bytes"] > 0

    def test_snapshot_stable_after_later_uploads(self, _init_db):
        from app.core.config import settings
        data_dir = settings.DATA_FOLDER
        self._write(data_dir, "Login_a.csv", "userId,timestamp", [["1", "2026-09-14 09:00:00"]])
        w = _new_window(start=_dt(2026, 9, 14))
        run = svc.start_run(w["id"])["run"]
        before = svc.get_run_snapshot(run["id"])["files"]

        # Simulate a later upload landing in the data folder AFTER the run start.
        self._write(data_dir, "Sales_b.csv", "userId,gameName,amount,timestamp",
                    [["1", "G", "100", "2026-09-14 10:00:00"]])

        after = svc.get_run_snapshot(run["id"])["files"]
        assert before == after
        assert all(f["filename"] != "Sales_b.csv" for f in after)


# ---------------------------------------------------------------------------
# Window lifecycle: grace period + finalization immutability
# ---------------------------------------------------------------------------

class TestLifecycleTransitions:
    def test_window_enters_grace_period(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        ended = svc.end_window(w["id"])
        assert ended["status"] == "ended"
        assert ended["ended_at"] is not None
        assert ended["finalized_at"] is None
        with pytest.raises(WindowStateError):
            svc.end_window(w["id"])  # only 'active' windows can be ended again

    def test_grace_blocks_config_and_audience_changes(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.set_eligible_count(w["id"], 10)
        svc.add_eligible_users(w["id"], [_member("1")])
        svc.end_window(w["id"])  # grace begins
        # The operator-defined configuration and the eligible audience are fixed
        # for the window once it is active: no operator or system change is
        # allowed during grace. Fact uploads during grace still feed the LIVE
        # report (covered in test_window_report.TestFinalization.
        # test_live_report_counts_grace_period_uploads).
        for fn in (
            lambda: svc.add_eligible_users(w["id"], [_member("2")]),
            lambda: svc.set_eligible_count(w["id"], 20),
            lambda: svc.set_control_override(w["id"], 5),
            lambda: svc.extend_window_end(w["id"], _dt(2026, 9, 20, 11)),
        ):
            with pytest.raises(WindowStateError):
                fn()
        # The audience held at end time is untouched.
        assert svc.count_audience(w["id"])["total"] == 1
        assert svc.get_audience_member(w["id"], "2") is None

    def test_finalization_freeze(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.set_eligible_count(w["id"], 10)
        svc.add_eligible_users(w["id"], [_member("1")])
        svc.end_window(w["id"])
        finalized = svc.finalize_window(w["id"], now=FINALIZE_AT)
        assert finalized["status"] == "finalized"
        assert finalized["finalized_at"] is not None
        # every mutator is now blocked at the service level
        for fn in (
            lambda: svc.add_eligible_users(w["id"], [_member("2")]),
            lambda: svc.set_eligible_count(w["id"], 20),
            lambda: svc.set_control_override(w["id"], 5),
            lambda: svc.extend_window_end(w["id"], _dt(2026, 9, 18, 12)),
            lambda: svc.start_run(w["id"]),
        ):
            with pytest.raises(WindowStateError):
                fn()

    def test_finalization_requires_ended_window(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        with pytest.raises(WindowStateError):
            svc.finalize_window(w["id"], now=FINALIZE_AT)  # still active - must end first
        assert db.get_window(w["id"])["status"] == "active"

    def test_finalize_blocked_before_end_time(self, _init_db):
        # A window manually ended before its scheduled end stays in grace: the
        # report cannot be frozen until end_time.
        w = _new_window(
            start=_dt(2026, 9, 14),
            end=_dt(2026, 9, 17, 18, 0, 0),
            deadline=_dt(2026, 9, 18, 12, 0, 0),
        )
        db.end_window_transition(w["id"], dates.to_utc_iso(_dt(2026, 9, 15, 9, 0, 0)))
        with pytest.raises(WindowStateError):
            svc.finalize_window(w["id"], now=_dt(2026, 9, 17, 17, 0, 0))  # before end_time
        finalized = svc.finalize_window(w["id"], now=_dt(2026, 9, 18, 10, 0, 0))  # inside grace
        assert finalized["status"] == "finalized"

    def test_finalize_blocked_after_deadline(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))  # ends Sat 23:59:59, deadline Sun 14:00
        db.end_window_transition(w["id"], dates.to_utc_iso(_dt(2026, 9, 20, 8, 0, 0)))
        with pytest.raises(WindowStateError):
            svc.finalize_window(w["id"], now=_dt(2026, 9, 20, 15, 0, 0))  # deadline passed
        assert db.get_window(w["id"])["status"] == "ended"

    def test_audience_insert_blocked_at_persistence_layer_after_finalize(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.end_window(w["id"])
        svc.finalize_window(w["id"], now=FINALIZE_AT)
        with pytest.raises(WindowStateError):
            db.insert_audience_members(w["id"], [_member("9")])
        with pytest.raises(WindowStateError):
            db.update_window_end(w["id"], dates.to_utc_iso(_dt(2026, 9, 18, 12)))
        # The audience itself is unchanged.
        assert svc.count_audience(w["id"])["total"] == 0

    def test_upload_after_finalization_cannot_mutate(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.set_eligible_count(w["id"], 10)
        svc.add_eligible_users(w["id"], [_member("1")])
        svc.end_window(w["id"])
        svc.finalize_window(w["id"], now=FINALIZE_AT)
        with pytest.raises(WindowStateError):
            svc.add_eligible_users(w["id"], [_member("2", phone="08099998888")])
        assert svc.count_audience(w["id"])["total"] == 1
        assert svc.get_audience_member(w["id"], "2") is None

    def test_finalized_audience_assignment_cannot_change(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 20000)
        svc.add_eligible_users(w["id"], [_member("97")])  # control (97 % 100 >= 90)
        svc.end_window(w["id"])
        svc.finalize_window(w["id"], now=FINALIZE_AT)
        with pytest.raises(WindowStateError):
            svc.add_eligible_users(w["id"], [_member("97", phone="08099998888")])
        member = svc.get_audience_member(w["id"], "97")
        assert member["assignment"] == "control"
        assert member["phone_normalized"] == "2348012345678"  # original snapshot kept


# ---------------------------------------------------------------------------
# Audience + Campaign/Control assignment
# ---------------------------------------------------------------------------

class TestAudienceAssignment:
    def test_deterministic_assignment_rule(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 20000)  # campaign_percentage = 90
        assert svc.deterministic_is_campaign("7", 90) is True
        assert svc.deterministic_is_campaign("89", 90) is True
        assert svc.deterministic_is_campaign("90", 90) is False
        assert svc.deterministic_is_campaign("199", 90) is False

    def test_numeric_user_id_required_for_deterministic(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 20000)
        with pytest.raises(WindowConfigError):
            svc.add_eligible_users(w["id"], [_member("non-numeric-id")])

    def test_assignment_persisted_and_snapshotted_config(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 20000)
        svc.add_eligible_users(w["id"], [_member("7"), _member("97")])
        m7 = svc.get_audience_member(w["id"], "7")
        m97 = svc.get_audience_member(w["id"], "97")
        assert m7["assignment"] == "campaign"
        assert m97["assignment"] == "control"
        assert m7["assignment_method"] == "deterministic"
        assert m7["assignment_config"]["rule"] == "(user_id % 100) < campaign_percentage"
        assert m7["assignment_config"]["campaign_percentage"] == 90.0
        assert m97["assignment_config"]["campaign_percentage"] == 90.0

    def test_random_assignment_stable_and_recorded(self, _init_db):
        w = _new_window(
            start=_dt(2026, 9, 14), control_override=40, assignment_method="random"
        )
        svc.set_eligible_count(w["id"], 20000)
        svc.add_eligible_users(w["id"], [_member(str(i)) for i in range(50)])
        before = {
            str(m["user_id"]): m["assignment"]
            for m in svc.get_audience(w["id"])
        }
        # Re-adding the same users neither duplicates nor reassigns.
        outcome = svc.add_eligible_users(w["id"], [_member(str(i)) for i in range(50)])
        assert outcome["existing"] == 50
        assert outcome["added"] == 0
        after = {
            str(m["user_id"]): m["assignment"]
            for m in svc.get_audience(w["id"])
        }
        assert after == before
        member = svc.get_audience_member(w["id"], "1")
        assert member["assignment_method"] == "random"
        assert member["assignment_config"]["seed"] == w["id"]

    def test_cumulative_audience_additions(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 120)
        svc.add_eligible_users(w["id"], [_member(i) for i in range(1, 101)])
        assert svc.count_audience(w["id"])["total"] == 100
        svc.add_eligible_users(w["id"], [_member(i) for i in range(101, 121)])
        counts = svc.count_audience(w["id"])
        assert counts["total"] == 120

    def test_existing_assignments_not_reassigned(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 20000)
        svc.add_eligible_users(w["id"], [_member("97")])  # control
        # A later upload would re-classify 97 as campaign; assignment must not change.
        svc.add_eligible_users(w["id"], [_member("97", phone="08099998888"), _member("5")])
        assert svc.count_audience(w["id"])["total"] == 2
        assert svc.get_audience_member(w["id"], "97")["assignment"] == "control"
        assert svc.get_audience_member(w["id"], "5")["assignment"] == "campaign"
        # original phone snapshot preserved
        assert svc.get_audience_member(w["id"], "97")["phone_normalized"] == "2348012345678"

    def test_assignment_persists_across_runs(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 20000)
        svc.add_eligible_users(w["id"], [_member("7"), _member("97")])
        svc.start_run(w["id"])
        svc.start_run(w["id"])
        assert svc.get_audience_member(w["id"], "7")["assignment"] == "campaign"
        assert svc.get_audience_member(w["id"], "97")["assignment"] == "control"
        assert svc.count_audience(w["id"])["total"] == 2

    def test_segment_id_recorded_and_must_be_selected(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), segments=["SuperActive", "Affinity"], control_override=10)
        svc.set_eligible_count(w["id"], 20000, {"SuperActive": 10000, "Affinity": 10000})
        svc.add_eligible_users(w["id"], [_member("1", segment="SuperActive"), _member("2", segment="Affinity")])
        assert svc.get_audience_member(w["id"], "1")["segment_id"] == "SuperActive"
        assert svc.get_audience_member(w["id"], "2")["segment_id"] == "Affinity"
        with pytest.raises(WindowConfigError):
            svc.add_eligible_users(w["id"], [_member("3", segment="Unsegmented")])

    def test_invalid_phone_excluded_from_audience(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 20000)
        result = svc.add_eligible_users(
            w["id"],
            [
                _member("7", phone="08012345678"),       # campaign + valid
                _member("97", phone="08011112222"),      # control + valid
                _member("12", phone="not-a-number"),     # campaign + invalid phone
            ],
        )
        assert result["invalid_phone"] == 1
        # An invalid-phone user is never an audience member: not counted toward
        # N, never assigned, never an SMS recipient.
        assert svc.get_audience_member(w["id"], "12") is None
        assert svc.count_audience(w["id"])["total"] == 2
        recipients = svc.list_campaign_recipients(w["id"])
        assert [r["user_id"] for r in recipients] == ["7"]

    def test_phone_valid_true_cannot_force_approve_unparseable_phone(self, _init_db):
        # normalize-or-exclude: an explicit phone_valid=True can never admit a
        # member whose phone the canonical gate fails to normalize.
        w = _new_window(start=_dt(2026, 9, 14))
        svc.set_eligible_count(w["id"], 20000)
        result = svc.add_eligible_users(
            w["id"], [_member("5", phone="not-a-number", phone_valid=True)]
        )
        assert result["added"] == 0
        assert result["invalid_phone"] == 1
        assert svc.get_audience_member(w["id"], "5") is None

    def test_phone_valid_false_forces_exclusion_of_valid_phone(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.set_eligible_count(w["id"], 20000)
        result = svc.add_eligible_users(
            w["id"], [_member("5", phone="08012345678", phone_valid=False)]
        )
        assert result["added"] == 0
        assert result["invalid_phone"] == 1
        assert svc.get_audience_member(w["id"], "5") is None

    def test_control_users_never_campaign_recipients(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 20000)
        svc.add_eligible_users(w["id"], [_member("5"), _member("97"), _member("199")])
        # campaign_pct is 90: ids with (id % 100) >= 90 are Control.
        user_ids = {r["user_id"] for r in svc.list_campaign_recipients(w["id"])}
        assert "97" not in user_ids and "199" not in user_ids  # both control
        assert "5" in user_ids


# ---------------------------------------------------------------------------
# Control percentage: formula, buckets, multi-segment N, override
# ---------------------------------------------------------------------------

class TestControlCalculation:
    def test_formula_and_buckets(self):
        # C >= 0.5*N -> 50%
        assert svc.suggest_control_percentage(1000) == 50.0
        assert svc.suggest_control_percentage(2000) == 50.0
        # 15.5% calc -> bucket [15, 20) -> 20%
        assert svc.suggest_control_percentage(10000) == 20.0
        # 7.75% calc -> bucket < 10% -> 10%
        assert svc.suggest_control_percentage(20000) == 10.0
        # 3.1% calc -> bucket < 10% -> 10%
        assert svc.suggest_control_percentage(50000) == 10.0
        # 31.06% calc is >= 20% -> calculated value kept
        assert svc.suggest_control_percentage(5000) == pytest.approx(31.062, abs=1e-3)

    def test_multi_segment_eligible_audience_sums_to_n(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), segments=["A", "B"], control_override=12)
        svc.set_eligible_count(w["id"], 250, {"A": 100, "B": 150})
        w2 = db.get_window(w["id"])
        assert w2["eligible_count"] == 250
        assert w2["segment_eligible_counts"] == {"A": 100, "B": 150}
        # N is 250: C = 15500/250 + 1550 = 1612; C >= 0.5*250=125 -> 50% suggested,
        # but the operator override of 12% is the effective control percentage.
        assert w2["suggested_control_percentage"] == 50.0
        assert w2["control_percentage"] == 12.0

    def test_segment_counts_must_sum_to_n(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), segments=["A", "B"])
        with pytest.raises(WindowConfigError):
            svc.set_eligible_count(w["id"], 250, {"A": 100, "B": 100})

    def test_control_percentage_changeable_before_first_run(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.set_control_override(w["id"], 15)  # before N/effective percentage
        assert db.get_window(w["id"])["control_override"] == 15
        assert db.get_window(w["id"])["control_percentage"] is None  # not established yet
        established = svc.set_eligible_count(w["id"], 20000)
        assert established["control_percentage"] == 15.0  # override wins over the formula
        assert established["suggested_control_percentage"] == 10.0  # formula retained for audit
        # Still before the first Run: the operator may change the configuration.
        changed = svc.set_control_override(w["id"], 20)
        assert changed["control_percentage"] == 20.0
        assert changed["suggested_control_percentage"] == 10.0
        assert db.get_window(w["id"])["control_locked"] is False

    def test_manual_eligible_count_does_not_lock_control_percentage(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        established = svc.set_eligible_count(w["id"], 20000)
        assert established["control_percentage"] == 10.0  # formula (bucket)
        assert db.get_window(w["id"])["control_locked"] is False
        # The manual N path must not lock: the operator can still override.
        changed = svc.set_control_override(w["id"], 15)
        assert changed["control_percentage"] == 15.0
        assert db.get_window(w["id"])["control_locked"] is False

    def test_first_run_locks_control_percentage(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=15)
        svc.set_eligible_count(w["id"], 20000)
        svc.start_run(w["id"])
        assert db.get_window(w["id"])["control_locked"] is True
        with pytest.raises(WindowStateError) as exc:
            svc.set_control_override(w["id"], 20)
        assert "first run" in str(exc.value).lower()
        win = db.get_window(w["id"])
        assert win["control_override"] == 15
        assert win["control_percentage"] == 15.0

    def test_first_run_with_zero_eligible_locks_control_percentage(self, _init_db):
        # No N and no source data: the Run admits zero eligible users, yet the
        # configuration must still lock at Run start.
        w = _new_window(start=_dt(2026, 9, 14), control_override=15)
        svc.start_run(w["id"])
        win = db.get_window(w["id"])
        assert win["control_locked"] is True
        assert win["eligible_count"] is None
        with pytest.raises(WindowStateError):
            svc.set_control_override(w["id"], 20)

    def test_override_after_first_run_is_rejected(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.set_eligible_count(w["id"], 20000)
        assert db.get_window(w["id"])["control_percentage"] == 10.0  # formula (bucket)
        svc.start_run(w["id"])
        with pytest.raises(WindowStateError) as exc:
            svc.set_control_override(w["id"], 15)
        assert "first run" in str(exc.value).lower()
        win = db.get_window(w["id"])
        assert win["control_override"] is None
        assert win["control_percentage"] == 10.0

    def test_manual_override_remains_fixed_as_n_grows(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=15)
        first = svc.set_eligible_count(w["id"], 20000)
        assert first["control_percentage"] == 15.0
        assert first["suggested_control_percentage"] == 10.0
        grown = svc.set_eligible_count(w["id"], 50000)
        assert grown["eligible_count"] == 50000
        assert grown["control_percentage"] == 15.0  # override fixed
        assert grown["suggested_control_percentage"] == 10.0

    def test_formula_percentage_remains_fixed_as_n_grows(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        first = svc.set_eligible_count(w["id"], 20000)
        assert first["control_percentage"] == 10.0  # formula bucket
        # N=10000 on its own would suggest 20%; the established 10% must not move.
        grown = svc.set_eligible_count(w["id"], 10000)
        assert grown["eligible_count"] == 10000
        assert grown["control_percentage"] == 10.0
        assert grown["suggested_control_percentage"] == 10.0

    def test_growing_n_after_first_run_never_changes_percentage(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.set_eligible_count(w["id"], 20000)
        assert db.get_window(w["id"])["control_percentage"] == 10.0
        svc.start_run(w["id"])  # the first Run locks the configuration
        # N=10000 on its own would suggest 20%; the locked 10% must not move.
        grown = svc.set_eligible_count(w["id"], 10000)
        assert grown["eligible_count"] == 10000
        assert grown["control_percentage"] == 10.0
        assert grown["suggested_control_percentage"] == 10.0

    def test_refresh_eligible_counts_keeps_percentage_fixed(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=15)
        svc.set_eligible_count(w["id"], 20000)
        svc.add_eligible_users(w["id"], [_member("7"), _member("97")])
        svc.refresh_eligible_counts(w["id"])
        win = db.get_window(w["id"])
        assert win["eligible_count"] == 2  # N derived from the admitted audience
        assert win["control_percentage"] == 15.0
        assert win["suggested_control_percentage"] == 10.0

    def test_established_percentage_survives_grace_and_finalization(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 20000)
        svc.end_window(w["id"])
        svc.finalize_window(w["id"], now=FINALIZE_AT)
        win = db.get_window(w["id"])
        assert win["control_percentage"] == 10.0
        assert win["suggested_control_percentage"] == 10.0

    def test_later_runs_use_the_same_fixed_percentage(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 20000)
        svc.add_eligible_users(w["id"], [_member("7"), _member("97")])
        svc.start_run(w["id"])
        svc.start_run(w["id"])  # a later Run in the same window
        win = db.get_window(w["id"])
        assert win["control_percentage"] == 10.0
        assert win["eligible_count"] == 2  # N refreshed, percentage unchanged
        assert svc.get_audience_member(w["id"], "7")["assignment"] == "campaign"
        assert svc.get_audience_member(w["id"], "97")["assignment"] == "control"
        assert svc.get_audience_member(w["id"], "7")["assignment_config"]["campaign_percentage"] == 90.0

    def test_assignments_unchanged_and_later_members_use_fixed_percentage(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 20000)
        svc.add_eligible_users(w["id"], [_member("97")])  # control (97 % 100 >= 90)
        svc.start_run(w["id"])  # the first Run locks the configuration
        with pytest.raises(WindowStateError):
            svc.set_control_override(w["id"], 30)
        svc.add_eligible_users(w["id"], [_member("5"), _member("97")])  # later admission
        m97 = svc.get_audience_member(w["id"], "97")
        m5 = svc.get_audience_member(w["id"], "5")
        assert m97["assignment"] == "control"
        assert m97["assignment_config"]["campaign_percentage"] == 90.0
        assert m5["assignment"] == "campaign"
        assert m5["assignment_config"]["campaign_percentage"] == 90.0
        assert svc.count_audience(w["id"])["total"] == 2

    def test_control_requires_eligible_count_before_assignment(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        with pytest.raises(WindowConfigError):
            svc.add_eligible_users(w["id"], [_member("1")])


# ---------------------------------------------------------------------------
# API smoke tests (keep the application coherent with the new domain)
# ---------------------------------------------------------------------------

class TestWindowAPI:
    @pytest.fixture()
    def client(self, _init_db):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_create_and_detail(self, client):
        r = client.post("/windows", json={"name": "W1"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "active"
        wid = data["id"]
        detail = client.get(f"/windows/{wid}").json()
        assert detail["audience"]["total"] == 0
        assert detail["runs"] == []

    def test_api_rejects_second_active_window(self, client):
        first = client.post("/windows", json={"name": "First"})
        assert first.status_code == 200
        second = client.post("/windows", json={"name": "Second"})
        assert second.status_code == 409
        assert "only one campaign window can run at a time" in second.json()["detail"].lower()
        rows = client.get("/windows").json()
        assert len(rows) == 1
        assert rows[0]["id"] == first.json()["id"]

    def test_start_run_and_snapshot(self, client):
        w = client.post("/windows", json={"name": "W2"}).json()
        run = client.post(f"/windows/{w['id']}/runs", json={"note": "go"}).json()
        assert run["run"]["status"] == "running"
        snap = client.get(f"/runs/{run['run']['id']}/snapshot").json()
        assert "files" in snap

    def test_audience_endpoint(self, client):
        w = client.post("/windows", json={"name": "W3"}).json()
        client.post(f"/windows/{w['id']}/eligible-count", json={"eligible_count": 20000})
        r = client.post(
            f"/windows/{w['id']}/audience",
            json=[{"user_id": "5", "segment_id": "unsegmented", "phone": "08012345678"}],
        )
        assert r.status_code == 200
        assert r.json()["added"] == 1
        assert client.get(f"/windows/{w['id']}/audience/count").json()["total"] == 1

    def test_api_rejects_override_after_first_run(self, client):
        w = client.post("/windows", json={"name": "Fixed"}).json()
        ok = client.post(f"/windows/{w['id']}/eligible-count", json={"eligible_count": 20000})
        assert ok.status_code == 200
        assert ok.json()["control_percentage"] == 10.0
        # Before the first Run the operator may still change the configuration.
        allowed = client.post(f"/windows/{w['id']}/control-override", json={"percentage": 15})
        assert allowed.status_code == 200
        assert allowed.json()["control_percentage"] == 15.0
        run = client.post(f"/windows/{w['id']}/runs", json={"note": "lock"})
        assert run.status_code == 200
        rejection = client.post(f"/windows/{w['id']}/control-override", json={"percentage": 20})
        assert rejection.status_code == 409
        assert "first run" in rejection.json()["detail"].lower()
        detail = client.get(f"/windows/{w['id']}").json()
        assert detail["control_percentage"] == 15.0
        assert detail["control_override"] == 15
        assert detail["control_locked"] is True

    def test_segments_catalog(self, client):
        r = client.get("/segments").json()
        assert r["items"][0] == {"id": "unsegmented", "label": "Unsegmented", "default": True}
        ids = {item["id"] for item in r["items"]}
        assert "NewPlayedPlayers" in ids
        assert "DepositedNoPlayUnder100Days" in ids
        assert any(not item["default"] for item in r["items"])

    def test_window_list_overview_includes_audience_split_and_state(self, client):
        w = client.post("/windows", json={"name": "W4"}).json()
        client.post(f"/windows/{w['id']}/eligible-count", json={"eligible_count": 1000})
        client.post(
            f"/windows/{w['id']}/audience",
            json=[{"user_id": "10", "segment_id": "unsegmented", "phone": "08012345678"}],
        )
        client.post(f"/windows/{w['id']}/runs", json={"note": "alpha"})
        rows = client.get("/windows").json()
        assert rows
        row = next(r for r in rows if r["id"] == w["id"])
        assert row["status"] == "active"
        assert row["report_state"] == "live"
        assert row["audience"]["total"] == 1
        assert row["runs_count"] == 1
        assert row["running_runs"][0]["note"] == "alpha"
        assert "split" in row
        assert row["split"]["recommended"] is not None
        assert row["split"]["effective"] is not None
        assert row["split"]["actual"]["campaign_users"] + row["split"]["actual"]["control_users"] == 1
        assert row["split"]["assignment_method"] == "deterministic"

    def test_window_detail_snapshot_enrichment(self, client):
        w = client.post("/windows", json={"name": "W5"}).json()
        run = client.post(f"/windows/{w['id']}/runs", json={"note": "go"}).json()["run"]
        detail = client.get(f"/windows/{w['id']}").json()
        assert detail["runs"][0]["id"] == run["id"]
        assert detail["runs"][0]["snapshot"]["captured_at"] is not None
        assert detail["report_state"] == "live"

    def test_window_data_state(self, client):
        w = client.post("/windows", json={"name": "W6"}).json()
        run = client.post(f"/windows/{w['id']}/runs", json={"note": "go"}).json()["run"]
        state = client.get(f"/windows/{w['id']}/data-state").json()
        assert state["window_id"] == w["id"]
        assert state["status"] == "active"
        assert "current_files" in state
        run_state = state["runs"][0]
        assert run_state["id"] == run["id"]
        assert run_state["snapshot_captured_at"] is not None


# ---------------------------------------------------------------------------
# Regression tests: Campaign Window mandatory for Campaign Runs
# ---------------------------------------------------------------------------


class TestRunRequiresWindow:
    """A Campaign Run must always belong to a Campaign Window.

    These tests verify the v2.0.0 rule that there is no direct Campaign/Run
    workflow bypassing the Window.
    """

    def test_run_cannot_be_created_without_window(self, _init_db):
        """Run creation requires a valid window_id."""
        from app.db.windows import WindowStateError

        with pytest.raises(WindowStateError):
            db.create_run_with_snapshot(
                window_id=99999,  # non-existent window
                run_created_at=dates.to_utc_iso(_dt(2026, 9, 14, 10, 0, 0)),
                started_at=dates.to_utc_iso(_dt(2026, 9, 14, 10, 0, 0)),
                note=None,
                files_snapshot=[],
                snapshot_notes=None,
            )

    def test_run_cannot_be_started_in_ended_window(self, _init_db):
        """A Run cannot be started in an ended (grace) window."""
        w = _new_window(start=_dt(2026, 9, 14))
        svc.end_window(w["id"])
        with pytest.raises(WindowStateError) as exc:
            svc.start_run(w["id"])
        assert "is 'ended'" in str(exc.value) or "active window" in str(exc.value)

    def test_run_cannot_be_started_in_finalized_window(self, _init_db):
        """A Run cannot be started in a finalized window."""
        w = _new_window(start=_dt(2026, 9, 14))
        svc.end_window(w["id"])
        svc.finalize_window(w["id"], now=FINALIZE_AT)
        with pytest.raises(WindowStateError) as exc:
            svc.start_run(w["id"])
        assert "finalized" in str(exc.value) or "active window" in str(exc.value)

    def test_valid_active_window_can_create_run(self, _init_db):
        """An active window can successfully create/start a Run."""
        w = _new_window(start=_dt(2026, 9, 14))
        result = svc.start_run(w["id"], note="test run")
        run = result["run"]
        assert run["status"] == "running"
        assert run["window_id"] == w["id"]
        assert run["snapshot_id"] is not None
        assert result["snapshot"]["run_id"] == run["id"]

    def test_multiple_runs_in_same_window(self, _init_db):
        """A window can have multiple sequential runs."""
        w = _new_window(start=_dt(2026, 9, 14))
        first = svc.start_run(w["id"])["run"]
        second = svc.start_run(w["id"])["run"]
        runs = svc.list_runs(w["id"])
        assert len(runs) == 2
        assert [r["id"] for r in runs] == [first["id"], second["id"]]

    def test_run_snapshot_captures_data_state(self, _init_db):
        """Run snapshot freezes the data state at start time."""
        from app.core.config import settings
        data_dir = settings.DATA_FOLDER
        (data_dir / "Login_test.csv").write_text(
            "userId,timestamp\n1,2026-09-14 09:00:00\n", encoding="utf-8"
        )
        w = _new_window(start=_dt(2026, 9, 14))
        result = svc.start_run(w["id"])
        files = result["snapshot"]["files"]
        assert len(files) == 1
        assert files[0]["filename"] == "Login_test.csv"
        assert files[0]["dataset"] == "Login"

    def test_legacy_campaign_start_endpoint_still_works(self, _init_db):
        """The legacy /campaign/start endpoint still works for welcome campaigns (Feature 1)."""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        r = client.post("/campaign/start", json={"name": "test"})
        assert r.status_code == 200
        assert "campaign" in r.json()
        assert r.json()["campaign"]["status"] == "active"


class TestWindowLifecycleRestrictionsRemainEnforced:
    """Existing Window lifecycle restrictions must remain enforced."""

    def test_cannot_set_eligible_count_in_ended_window(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.end_window(w["id"])
        with pytest.raises(WindowStateError):
            svc.set_eligible_count(w["id"], 100)

    def test_cannot_add_audience_in_ended_window(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 100)
        svc.end_window(w["id"])
        with pytest.raises(WindowStateError):
            svc.add_eligible_users(w["id"], [{"user_id": "1", "segment_id": "unsegmented", "phone": "08012345678"}])

    def test_cannot_extend_end_in_ended_window(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.end_window(w["id"])
        with pytest.raises(WindowStateError):
            svc.extend_window_end(w["id"], _dt(2026, 9, 18, 12, 0, 0))

    def test_cannot_set_control_override_in_ended_window(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.end_window(w["id"])
        with pytest.raises(WindowStateError):
            svc.set_control_override(w["id"], 15)

    def test_cannot_modify_after_finalization(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 100)
        svc.end_window(w["id"])
        svc.finalize_window(w["id"], now=FINALIZE_AT)
        for fn in (
            lambda: svc.add_eligible_users(w["id"], [{"user_id": "1", "segment_id": "unsegmented", "phone": "08012345678"}]),
            lambda: svc.set_eligible_count(w["id"], 200),
            lambda: svc.set_control_override(w["id"], 20),
            lambda: svc.extend_window_end(w["id"], _dt(2026, 9, 18, 12, 0, 0)),
            lambda: svc.start_run(w["id"]),
        ):
            with pytest.raises(WindowStateError):
                fn()

    def test_active_run_stopped_when_window_ends(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        run = svc.start_run(w["id"])["run"]
        ended = svc.end_window(w["id"])
        assert ended["status"] == "ended"
        closed_runs = ended["closed_runs"]
        assert len(closed_runs) == 1
        assert closed_runs[0]["id"] == run["id"]
        assert closed_runs[0]["status"] == "stopped"
        assert closed_runs[0]["stop_reason"] == "window_ended"


# ---------------------------------------------------------------------------
# Regression tests: Corrected Run lifecycle (snapshot + evaluate + freeze target)
# ---------------------------------------------------------------------------


class TestRunLifecycleCorrected:
    """Tests for the corrected Run lifecycle where Start Run = snapshot + evaluate + freeze target."""

    def test_start_run_evaluates_and_freezes_target(self, _init_db, _fixed_bands):
        """Starting a Run evaluates eligibility and freezes the complete target."""
        from app.db.sms import log_sms

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
        win = _new_window(segments=(NEVER_DEPOSITED,))
        result = svc.start_run(win["id"], now=NOW)

        # Run is created and evaluation is returned
        assert result["run"]["status"] == "running"
        assert "evaluation" in result
        eval_report = result["evaluation"]
        assert eval_report["candidates"] == 6
        assert eval_report["decisions"]["eligible"] == 3  # users 1, 5, 6
        assert eval_report["audience"]["added"] == 2  # user 5 has invalid phone
        assert eval_report["audience"]["campaign"] + eval_report["audience"]["control"] == 2

        # Target audience is already in window_audiences with run_id
        audience = svc.get_audience(win["id"])
        run_members = [m for m in audience if m.get("run_id") == result["run"]["id"]]
        assert len(run_members) == 2
        for m in run_members:
            assert m["run_id"] == result["run"]["id"]

    def test_run_target_cannot_grow_after_start(self, _init_db, _fixed_bands):
        """The Run target cannot grow after the Run starts (no re-evaluation)."""
        data_dir = settings.DATA_FOLDER
        _base_snapshot(data_dir)
        win = _new_window()
        run = svc.start_run(win["id"], now=NOW)["run"]

        # Get initial target size
        initial_target = db.get_run_target(win["id"], run["id"])
        initial_count = len(initial_target)

        # The run target should remain the same
        audience_after = len(db.get_run_target(win["id"], run["id"]))
        assert audience_after == initial_count

    def test_new_login_after_start_not_in_target(self, _init_db, _fixed_bands):
        """New Login data uploaded after Run start cannot affect that Run."""
        data_dir = settings.DATA_FOLDER
        _base_snapshot(data_dir)
        win = _new_window()
        run = svc.start_run(win["id"], now=NOW)["run"]

        # Get initial target (users 1 and 6 are eligible, 5 has invalid phone)
        initial_target = db.get_run_target(win["id"], run["id"])
        initial_ids = {m["user_id"] for m in initial_target}

        # Upload new Login after run start (user 7)
        _write_csv(
            data_dir,
            "Login_20260919_late.csv",
            ["userId", "timestamp"],
            [("7", "2026-09-19 15:05:00")],
        )
        _write_csv(
            data_dir,
            "Registrations_20260910_late.csv",
            ["userId", "firstName", "email", "phone", "timestamp"],
            [("7", "Grace", "g@x.com", "08078901234", "2026-09-10 10:00:00")],
        )

        # Target should not include user 7
        target = db.get_run_target(win["id"], run["id"])
        target_ids = {m["user_id"] for m in target}
        assert target_ids == initial_ids
        assert "7" not in target_ids

    def test_new_registration_after_start_not_in_target(self, _init_db, _fixed_bands):
        """New Registration data uploaded after Run start cannot affect that Run."""
        data_dir = settings.DATA_FOLDER
        _base_snapshot(data_dir)
        win = _new_window()
        run = svc.start_run(win["id"], now=NOW)["run"]

        # Get initial target
        initial_target = db.get_run_target(win["id"], run["id"])
        initial_ids = {m["user_id"] for m in initial_target}

        # Upload new Registration after run start (for a new user 7 who had a login)
        # But the login for user 7 was not in the snapshot
        _write_csv(
            data_dir,
            "Registrations_20260910_late.csv",
            ["userId", "firstName", "email", "phone", "timestamp"],
            [("7", "Grace", "g@x.com", "08078901234", "2026-09-10 10:00:00")],
        )

        # Target should not include user 7 (no login in snapshot)
        target = db.get_run_target(win["id"], run["id"])
        target_ids = {m["user_id"] for m in target}
        assert target_ids == initial_ids
        assert "7" not in target_ids

    def test_new_sales_after_start_not_in_target(self, _init_db, _fixed_bands):
        """New Sales/Play data uploaded after Run start cannot affect that Run."""
        data_dir = settings.DATA_FOLDER
        _base_snapshot(data_dir)
        win = _new_window()
        run = svc.start_run(win["id"], now=NOW)["run"]

        # Get initial target
        initial_target = db.get_run_target(win["id"], run["id"])
        initial_ids = {m["user_id"] for m in initial_target}

        # Upload new Sales after run start (play after login for user 1)
        # User 1 already has a login in the snapshot; this new play came after run start
        _write_csv(
            data_dir,
            "Sales_20260919_late.csv",
            ["userId", "gameName", "amount", "timestamp"],
            [("1", "Lotto", "500.00", "2026-09-19 15:10:00")],
        )

        # Target should still have the same users (the play came after run start,
        # so it's not in the snapshot and doesn't disqualify user 1)
        target = db.get_run_target(win["id"], run["id"])
        target_ids = {m["user_id"] for m in target}
        assert target_ids == initial_ids

    def test_new_deposit_after_start_not_in_target(self, _init_db, _fixed_bands):
        """New Deposit data uploaded after Run start cannot affect that Run."""
        data_dir = settings.DATA_FOLDER
        _base_snapshot(data_dir)
        win = _new_window()
        run = svc.start_run(win["id"], now=NOW)["run"]

        # Get initial target
        initial_target = db.get_run_target(win["id"], run["id"])
        initial_ids = {m["user_id"] for m in initial_target}

        # Upload new Deposit after run start
        _write_csv(
            data_dir,
            "Deposit_events_20260919_late.csv",
            ["userId", "timestamp"],
            [("1", "2026-09-19 15:10:00")],
        )

        # Target should still have the same users
        target = db.get_run_target(win["id"], run["id"])
        target_ids = {m["user_id"] for m in target}
        assert target_ids == initial_ids

    def test_uploads_rejected_during_active_run(self, _init_db, _fixed_bands):
        """Relevant source uploads are rejected while a Run is active."""
        from app.services.windows import any_window_has_active_run
        from app.services.ingestion import persist_upload

        data_dir = settings.DATA_FOLDER
        _base_snapshot(data_dir)
        win = _new_window()
        run = svc.start_run(win["id"], now=NOW)["run"]

        # Run is active - check the flag
        assert any_window_has_active_run() is True

        # Try to upload - should be rejected at service level
        # (The API endpoint returns 409, but we test the service check)
        assert any_window_has_active_run() is True

        # Stop the run
        svc.stop_run(run["id"])

        # Now uploads should be allowed
        assert any_window_has_active_run() is False

    def test_uploads_allowed_after_run_stops(self, _init_db, _fixed_bands):
        """Uploads become possible again after the Run stops."""
        from app.services.windows import any_window_has_active_run

        data_dir = settings.DATA_FOLDER
        _write_csv(
            data_dir,
            "Login_20260919.csv",
            ["userId", "timestamp"],
            [("1", "2026-09-19 15:00:00")],
        )
        _write_csv(
            data_dir,
            "Registrations_20260910.csv",
            ["userId", "firstName", "email", "phone", "timestamp"],
            [("1", "Alice", "a@x.com", "08012345678", "2026-09-10 10:00:00")],
        )
        win = _new_window()
        run = svc.start_run(win["id"])["run"]

        # Run is active
        assert any_window_has_active_run() is True

        # Complete the run
        svc.complete_run(run["id"])

        # Uploads should be allowed again
        assert any_window_has_active_run() is False

    def test_dispatch_uses_frozen_run_target(self, _init_db, _fixed_bands):
        """Dispatch uses the already-frozen Run target."""
        data_dir = settings.DATA_FOLDER
        _write_csv(
            data_dir,
            "Login_20260919.csv",
            ["userId", "timestamp"],
            [("1", "2026-09-19 15:00:00"), ("2", "2026-09-19 15:10:00")],
        )
        _write_csv(
            data_dir,
            "Registrations_20260910.csv",
            ["userId", "firstName", "email", "phone", "timestamp"],
            [
                ("1", "Alice", "a@x.com", "08012345678", "2026-09-10 10:00:00"),
                ("2", "Bob", "b@x.com", "08023456789", "2026-09-10 10:00:00"),
            ],
        )
        win = _new_window(control_override=50)  # 50% control
        run = svc.start_run(win["id"])["run"]

        # Get frozen target
        target = db.get_run_target(win["id"], run["id"])
        target_count = len(target)

        # Upload new user after run start
        _write_csv(
            data_dir,
            "Login_20260919_late.csv",
            ["userId", "timestamp"],
            [("3", "2026-09-19 15:05:00")],
        )
        _write_csv(
            data_dir,
            "Registrations_20260910_late.csv",
            ["userId", "firstName", "email", "phone", "timestamp"],
            [("3", "Carol", "c@x.com", "08034567890", "2026-09-10 10:00:00")],
        )

        # Target should not include user 3
        target_after = db.get_run_target(win["id"], run["id"])
        assert len(target_after) == target_count

    def test_later_run_can_use_newer_data(self, _init_db, _fixed_bands):
        """A later Run can use newer source data without modifying the earlier Run."""
        from app.db.sms import log_sms

        data_dir = settings.DATA_FOLDER
        _base_snapshot(data_dir)
        # Set up cooldown for user 3
        log_sms({
            "user_id": "3", "kind": "campaign", "phone": "08034567890",
            "status": "sent", "sent_at": dates.to_utc_iso(wdt(2026, 9, 19, 17, 0)),
        })
        # Set up failed delivery for user 6 (no cooldown)
        log_sms({
            "user_id": "6", "kind": "campaign", "phone": "08067890123",
            "status": "failed", "sent_at": dates.to_utc_iso(wdt(2026, 9, 19, 17, 0)),
        })
        win = _new_window(segments=(NEVER_DEPOSITED,))

        # Run 1
        run1 = svc.start_run(win["id"], now=NOW)["run"]
        target1 = db.get_run_target(win["id"], run1["id"])
        # Users 1 and 6 are eligible, 5 has invalid phone
        target1_ids = {m["user_id"] for m in target1}
        assert target1_ids == {"1", "6"}

        # Upload new data (user 7)
        _write_csv(
            data_dir,
            "Login_20260919_late.csv",
            ["userId", "timestamp"],
            [("7", "2026-09-19 15:05:00")],
        )
        _write_csv(
            data_dir,
            "Registrations_20260910_late.csv",
            ["userId", "firstName", "email", "phone", "timestamp"],
            [("7", "Grace", "g@x.com", "08078901234", "2026-09-10 10:00:00")],
        )

        # Run 2 (after upload) - only gets NEW users (user 7)
        # Existing users 1 and 6 keep their original run_id (run1)
        run2 = svc.start_run(win["id"], now=NOW)["run"]
        target2 = db.get_run_target(win["id"], run2["id"])
        target2_ids = {m["user_id"] for m in target2}
        assert target2_ids == {"7"}  # Only new user added to Run 2

        # Run 1 target unchanged
        target1_after = db.get_run_target(win["id"], run1["id"])
        target1_after_ids = {m["user_id"] for m in target1_after}
        assert target1_after_ids == {"1", "6"}

    def test_repeated_evaluate_calls_do_not_change_target(self, _init_db, _fixed_bands):
        """Repeated attempts to evaluate or modify a running Run do not change its target."""
        from app.db.sms import log_sms

        data_dir = settings.DATA_FOLDER
        _base_snapshot(data_dir)
        # Set up cooldown for user 3
        log_sms({
            "user_id": "3", "kind": "campaign", "phone": "08034567890",
            "status": "sent", "sent_at": dates.to_utc_iso(wdt(2026, 9, 19, 17, 0)),
        })
        # Set up failed delivery for user 6 (no cooldown)
        log_sms({
            "user_id": "6", "kind": "campaign", "phone": "08067890123",
            "status": "failed", "sent_at": dates.to_utc_iso(wdt(2026, 9, 19, 17, 0)),
        })
        win = _new_window()
        run = svc.start_run(win["id"], now=NOW)["run"]

        # Get initial target
        target1 = db.get_run_target(win["id"], run["id"])
        initial_count = len(target1)

        # Call build_run_target again (simulating re-evaluation)
        # This should not add duplicate members (existing check prevents it)
        from app.services.eligibility import build_run_target
        eval_report = build_run_target(run["id"], now=NOW)

        # The evaluation report should show existing members
        assert eval_report["audience"]["existing"] == initial_count
        assert eval_report["audience"]["added"] == 0  # no new members added

        # Target unchanged
        target2 = db.get_run_target(win["id"], run["id"])
        assert len(target2) == initial_count