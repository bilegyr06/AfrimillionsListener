"""Focused tests for the v2.0.0 Campaign Window domain foundation.

Covers Campaign Window lifecycle (active -> ended/grace -> finalized), Campaign
Runs + source snapshots, the audience/assignment model (Campaign/Control),
control-percentage formula/buckets/override, and the finalization immutability
boundary.

Tests use Africa/Lagos business time explicitly (ZoneInfo('Africa/Lagos'));
they never depend on the machine's local timezone.
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.core import dates
from app.db import windows as db
from app.db.database import get_connection
from app.db.windows import WindowConfigError, WindowStateError
from app.services import windows as svc

LAGOS = ZoneInfo("Africa/Lagos")


def _dt(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=LAGOS)


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
        svc.finalize_window(w["id"])
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
        svc.finalize_window(w["id"])
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
        svc.finalize_window(w["id"])
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

    def test_grace_period_accepts_uploads_contributing_to_state(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.set_eligible_count(w["id"], 10)
        svc.end_window(w["id"])  # grace begins; window still mutable for allowed steps
        added = svc.add_eligible_users(w["id"], [_member("1"), _member("2")])
        assert added["added"] == 2
        assert svc.count_audience(w["id"])["total"] == 2

    def test_finalization_freeze(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.set_eligible_count(w["id"], 10)
        svc.add_eligible_users(w["id"], [_member("1")])
        svc.end_window(w["id"])
        finalized = svc.finalize_window(w["id"])
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
            svc.finalize_window(w["id"])  # still active - must end first
        assert db.get_window(w["id"])["status"] == "active"

    def test_audience_insert_blocked_at_persistence_layer_after_finalize(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.end_window(w["id"])
        svc.finalize_window(w["id"])
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
        svc.finalize_window(w["id"])
        with pytest.raises(WindowStateError):
            svc.add_eligible_users(w["id"], [_member("2", phone="08099998888")])
        assert svc.count_audience(w["id"])["total"] == 1
        assert svc.get_audience_member(w["id"], "2") is None

    def test_finalized_audience_assignment_cannot_change(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 20000)
        svc.add_eligible_users(w["id"], [_member("97")])  # control (97 % 100 >= 90)
        svc.end_window(w["id"])
        svc.finalize_window(w["id"])
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

    def test_invalid_phone_kept_in_audience_but_not_recipient(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14), control_override=10)
        svc.set_eligible_count(w["id"], 20000)
        svc.add_eligible_users(
            w["id"],
            [
                _member("7", phone="08012345678"),       # campaign + valid
                _member("97", phone="08011112222"),      # control + valid
                _member("12", phone="not-a-number"),     # campaign + invalid phone
            ],
        )
        assert svc.get_audience_member(w["id"], "12")["phone_valid"] == 0
        recipients = svc.list_campaign_recipients(w["id"])
        assert [r["user_id"] for r in recipients] == ["7"]

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

    def test_operator_override_persisted_and_effective(self, _init_db):
        w = _new_window(start=_dt(2026, 9, 14))
        svc.set_eligible_count(w["id"], 20000)
        assert db.get_window(w["id"])["control_percentage"] == 10.0  # formula (bucket)
        updated = svc.set_control_override(w["id"], 15)
        assert updated["control_override"] == 15
        assert updated["control_percentage"] == 15.0
        # suggested value retained for audit
        assert updated["suggested_control_percentage"] == 10.0

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