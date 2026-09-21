"""Campaign Window report tests (v2.0.0 attribution + reporting).

Covers app.services.window_report (build_report/freeze_report/get_report) and
its persistence seam in app.db.window_report:

  * Campaign/Control separation and the campaignable audience population;
  * window evaluation-period scoping in the "relabelled Lagos fact frame"
    (a play at Lagos 23:59:59 is inside the window even though its stored label
    differs from the true UTC instant);
  * intervention attribution: most-recent accepted Welcome precedes a play, the
    first qualifying play after an intervention is its conversion, later plays
    are activity, failed sends are never interventions, Control never converts;
  * per-Run summaries (target, accepted, contacted, converted, attribution);
  * durable login facts and idempotent login ingestion;
  * finalization freeze: the report is computed and persisted exactly once and
    a finalized window serves the frozen snapshot - later facts cannot change it.

Timing conventions mirror the report code:
  * fact rows (plays/deposits/logins) store the "+00:00 relabelled wall-clock"
    label via dates.to_source_fact (_fact below);
  * sms_log.sent_at stores the TRUE UTC instant via dates.to_utc_iso.

Deterministic assignment (control 50% -> campaign_percentage 50):
    user_id % 100 < 50 => Campaign (ids 1..49), >= 50 => Control (51..99).
"""
from __future__ import annotations

import csv
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.core import dates
from app.core.config import settings
from app.db.deposits import insert_deposit_records
from app.db.logins import insert_login_records
from app.db.players import insert_play_records
from app.db.sms import log_sms
from app.services import window_report
from app.services import windows as svc
from app.services.logins import (
    ingest_new_login_files,
    login_source_key,
    parse_logins_frame,
)

LAGOS = ZoneInfo("Africa/Lagos")

CAMPAIGN_IDS = ("1", "2", "3")
CONTROL_IDS = ("51", "52")


def _dt(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=LAGOS)


def _fact(dt: datetime) -> str:
    """Serialize a Lagos wall-clock onto the relabelled +00:00 fact label."""
    return dates.to_source_fact(dt)


def _sms_utc(dt: datetime) -> str:
    """Serialize a Lagos wall-clock as its true UTC instant (sms_log style)."""
    return dates.to_utc_iso(dt)


def _phone(uid: str) -> str:
    return f"080{int(uid):0>8}"


# _setup windows run Mon 2026-09-14 -> Sat 2026-09-19 23:59:59 with a Sun
# 2026-09-20 14:00 finalization deadline; this instant sits inside that grace
# period so finalize clock guards are deterministic (never wall-clock).
FINALIZE_AT = _dt(2026, 9, 20, 9, 0, 0)


def _setup(_init_db):
    win = svc.create_window(name="test", start_time=_dt(2026, 9, 14))
    svc.set_control_override(win["id"], 50.0)
    svc.set_eligible_count(win["id"], 1, {"unsegmented": 1})
    return win


def _add_members(win_id, run_id, user_ids) -> None:
    members = [
        {
            "user_id": uid,
            "segment_id": "unsegmented",
            "phone": _phone(uid),
            "run_id": run_id,
            "eligibility_state": {"first_name": f"User{uid}"},
        }
        for uid in user_ids
    ]
    result = svc.add_eligible_users(win_id, members)
    assert result["invalid_phone"] == 0
    return result


def _play(uid, at: datetime, game="Spin", amount=100, file="Sales_x.csv"):
    return {
        "user_id": uid,
        "played_at": _fact(at),
        "game_name": game,
        "amount": float(amount),
        "source_file": file,
        "source_key": f"{uid}|{_fact(at)}|{game}",
    }


def _deposit(uid, at: datetime, file="Deposit_x.csv"):
    return {
        "user_id": uid,
        "deposited_at": _fact(at),
        "source_file": file,
        "source_key": f"{uid}|{_fact(at)}",
    }


def _login(uid, at: datetime, file="Login_x.csv"):
    return {
        "user_id": uid,
        "logged_at": _fact(at),
        "source_file": file,
        "source_key": login_source_key(uid, _fact(at)),
    }


def _welcome(run_id, uid, at: datetime, status="sent"):
    log_sms({
        "user_id": uid, "kind": "welcome", "phone": _phone(uid),
        "status": status, "cycle_id": f"run:{run_id}", "sent_at": _sms_utc(at),
    })


def _group(report, kind="campaign"):
    return report["groups"][kind]


# ---------------------------------------------------------------------------
# Audience + Campaign/Control separation
# ---------------------------------------------------------------------------

class TestAudience:
    def test_campaignable_population_and_groups_are_separate(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS + CONTROL_IDS)

        report = window_report.build_report(win["id"])
        assert report["audience"] == {"campaignable_total": 5, "campaign": 3, "control": 2}
        assert _group(report, "campaign")["total_targeted_audience"] == 3
        assert _group(report, "control")["total_targeted_audience"] == 2

    def test_invalid_phone_members_are_not_in_audience(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        svc.add_eligible_users(win["id"], [
            {"user_id": "1", "segment_id": "unsegmented", "phone": _phone("1"), "run_id": run_id},
            {"user_id": "9", "segment_id": "unsegmented", "phone": "not-a-phone", "run_id": run_id},
        ])
        report = window_report.build_report(win["id"])
        # user 1 is <50 -> Campaign; user 9's unusable phone never reaches N.
        assert report["audience"]["campaignable_total"] == 1
        assert report["audience"]["campaign"] == 1

    def test_control_has_no_campaign_sms_metrics(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS + CONTROL_IDS)
        _welcome(run_id, "1", _dt(2026, 9, 14, 10), status="sent")

        report = window_report.build_report(win["id"])
        ctrl = _group(report, "control")
        assert ctrl["converted_users"] is None
        assert ctrl["contacted_users"] is None
        assert ctrl["conversion_rate"] is None
        assert ctrl["conversion_not_applicable"] is True


# ---------------------------------------------------------------------------
# Durable logins + login ingestion
# ---------------------------------------------------------------------------

class TestLogins:
    def test_logged_in_users_count_login_facts_in_period(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS + CONTROL_IDS)
        insert_login_records([
            _login("1", _dt(2026, 9, 14, 9)),
            _login("2", _dt(2026, 9, 15, 9)),
            _login("3", _dt(2026, 9, 15, 10)),
            _login("51", _dt(2026, 9, 14, 9)),  # control
        ])

        report = window_report.build_report(win["id"])
        assert _group(report, "campaign")["total_logged_in_users"] == 3
        assert _group(report, "control")["total_logged_in_users"] == 1

    def test_logins_outside_period_are_excluded(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS)
        insert_login_records([
            _login("1", _dt(2026, 9, 13, 23, 59)),  # Sunday before the window
            _login("1", _dt(2026, 9, 14, 0, 30)),   # Monday inside the window
        ])
        report = window_report.build_report(win["id"])
        assert _group(report, "campaign")["total_logged_in_users"] == 1

    def test_login_rows_dedup_on_user_and_time(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, ("1",))
        rec = _login("1", _dt(2026, 9, 14, 9))
        # Same (user, logged_at) from another file -> the dedup key keeps one row.
        n = insert_login_records([rec, dict(rec, source_file="Login_y.csv")])
        assert n == 1
        report = window_report.build_report(win["id"])
        assert _group(report, "campaign")["total_logged_in_users"] == 1

    def test_ingest_new_login_files_is_idempotent_and_marks_processed(self, _init_db):
        path = settings.DATA_FOLDER / "Login_20260914.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["userId", "timestamp"])
            writer.writerow(["1", "2026-09-14 09:00:00"])
            writer.writerow(["2", "2026-09-14 10:00:00"])
            writer.writerow(["2", "2026-09-14 10:00:00"])  # duplicate row

        first = ingest_new_login_files()
        second = ingest_new_login_files()

        assert first["files"] == 1
        assert first["inserted"] == 2
        assert first["skipped"] == 1  # the in-file duplicate
        assert second["files"] == 0

        from app.db.files import login_file_processed
        assert login_file_processed("Login_20260914.csv") is True

    def test_parse_logins_frame_handles_microseconds_and_bad_rows(self):
        import pandas as pd
        df = pd.DataFrame({
            "userId": ["1", "2", "", None],
            "timestamp": [
                "2026-09-14 09:00:00",
                "not-a-time",
                "not-a-time-2",
                "2026-09-14 11:00:00",
            ],
        })
        parsed = parse_logins_frame("Login_x.csv", df)
        assert parsed["discovered"] == 4
        assert len(parsed["records"]) == 1
        assert parsed["records"][0]["logged_at"] == "2026-09-14T09:00:00+00:00"
        assert parsed["invalid_reasons"]["invalid_timestamp"] == 2
        assert parsed["invalid_reasons"]["missing_user_id"] == 2

    def test_parse_logins_frame_drops_microseconds(self):
        import pandas as pd
        df = pd.DataFrame({
            "userId": ["1"],
            "timestamp": ["2026-09-14 09:00:00.123"],
        })
        parsed = parse_logins_frame("Login_x.csv", df)
        assert parsed["records"][0]["logged_at"] == "2026-09-14T09:00:00+00:00"


# ---------------------------------------------------------------------------
# Window plays metrics (all qualifying plays in the period)
# ---------------------------------------------------------------------------

class TestPlays:
    def test_played_users_sales_and_rates(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS + CONTROL_IDS)
        insert_play_records([
            _play("1", _dt(2026, 9, 14, 10, 0), amount=100),
            _play("1", _dt(2026, 9, 14, 12, 0), amount=50),
            _play("2", _dt(2026, 9, 15, 9, 0), amount=200),
            _play("51", _dt(2026, 9, 14, 9, 0), amount=75),  # control
        ])

        report = window_report.build_report(win["id"])
        camp = _group(report, "campaign")
        assert camp["total_played_users"] == 2
        assert camp["total_sales"] == 350.0
        assert camp["total_plays"] == 3
        assert camp["arpu"] == round(350.0 / 3, 4)
        assert camp["arppu"] == 175.0
        assert camp["plays_per_player"] == 1.5
        assert camp["play_rate"] == round(2 / 3, 4)

        ctrl = _group(report, "control")
        assert ctrl["total_played_users"] == 1
        assert ctrl["total_sales"] == 75.0

    def test_play_records_dedup_by_source_key(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, ("1",))
        rec = _play("1", _dt(2026, 9, 14, 10), amount=100)
        # Same source_key from a second file is ignored by the UNIQUE constraint.
        n = insert_play_records([rec, dict(rec, source_file="Sales_y.csv", amount=999)])
        assert n == 1

        report = window_report.build_report(win["id"])
        camp = _group(report, "campaign")
        assert camp["total_plays"] == 1
        assert camp["total_sales"] == 100.0

    def test_active_days_multi_day_and_partition(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS)
        insert_play_records([
            _play("1", _dt(2026, 9, 14, 10)),   # day 1
            _play("1", _dt(2026, 9, 14, 11)),   # day 1
            _play("1", _dt(2026, 9, 15, 10)),   # day 2 -> multi-day
            _play("2", _dt(2026, 9, 14, 9)),    # day 1, single-play player
            _play("3", _dt(2026, 9, 16, 9)),    # another single-play player
        ])

        report = window_report.build_report(win["id"])
        camp = _group(report, "campaign")
        assert camp["active_days"] == 3
        assert camp["active_days_per_player"] == 1.0
        assert camp["multi_day_players"] == 1
        assert camp["multi_day_player_rate"] == round(1 / 3, 4)
        assert camp["single_play_players"] == 2
        assert camp["multiple_play_players"] == 1

    def test_game_distribution_sorts_by_plays(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS)
        insert_play_records([
            _play("1", _dt(2026, 9, 14, 10), game="Aviator", amount=10),
            _play("1", _dt(2026, 9, 14, 11), game="Aviator", amount=20),
            _play("2", _dt(2026, 9, 14, 9), game="Dice", amount=5),
        ])

        report = window_report.build_report(win["id"])
        games = report["games"]["campaign"]
        assert [g["game_name"] for g in games] == ["Aviator", "Dice"]
        aviator = games[0]
        assert aviator["plays"] == 2
        assert aviator["customers"] == 1
        assert aviator["amount"] == 30.0
        assert aviator["avg_amount"] == 15.0

    def test_period_boundary_is_inclusive_relabelled_frame(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS)
        # Lagos Saturday 23:59:59 is the window end and IS inside the period;
        # Sunday 00:00:01 is not (a Sunday upload is grace past the window).
        insert_play_records([
            _play("1", _dt(2026, 9, 19, 23, 59, 59), amount=10),
            _play("2", _dt(2026, 9, 20, 0, 0, 1), amount=20),
        ])
        report = window_report.build_report(win["id"])
        assert _group(report, "campaign")["total_played_users"] == 1
        assert _group(report, "campaign")["total_sales"] == 10.0

    def test_zero_denominator_rates_are_none_not_zero(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS)  # no players
        report = window_report.build_report(win["id"])
        camp = _group(report, "campaign")
        # audience denominator (3) is non-zero, so 0/3 == 0.0 is the honest value.
        assert camp["play_rate"] == 0.0
        assert camp["arppu"] is None
        assert camp["plays_per_player"] is None
        assert camp["total_played_users"] == 0
        assert camp["total_sales"] == 0.0


# ---------------------------------------------------------------------------
# Deposits
# ---------------------------------------------------------------------------

class TestDeposits:
    def test_deposit_rate_and_deposit_then_play(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS)
        insert_deposit_records([
            _deposit("1", _dt(2026, 9, 14, 9)),   # deposits before her play
            _deposit("2", _dt(2026, 9, 15, 9)),   # deposits, never plays
        ])
        insert_play_records([
            _play("1", _dt(2026, 9, 14, 10)),     # plays after depositing
            _play("3", _dt(2026, 9, 14, 10)),     # plays, never deposited
        ])

        report = window_report.build_report(win["id"])
        camp = _group(report, "campaign")
        assert camp["total_deposited_users"] == 2
        assert camp["deposit_rate"] == round(2 / 3, 4)
        assert camp["deposit_to_play_rate"] == round(1 / 2, 4)  # user 1 only
        assert camp["deposited_no_play_users"] == 1
        assert camp["logged_in_no_play_users"] == 0

    def test_login_to_play_and_login_no_play(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS)
        insert_login_records([
            _login("1", _dt(2026, 9, 14, 9)),     # logged in and plays
            _login("2", _dt(2026, 9, 15, 9)),     # logged in, no play
            _login("3", _dt(2026, 9, 15, 10)),    # logged in and plays
        ])
        insert_play_records([
            _play("1", _dt(2026, 9, 14, 10)),
            _play("3", _dt(2026, 9, 15, 11)),
        ])

        report = window_report.build_report(win["id"])
        camp = _group(report, "campaign")
        assert camp["total_logged_in_users"] == 3
        assert camp["login_to_play_rate"] == round(2 / 3, 4)
        assert camp["logged_in_no_play_users"] == 1

    def test_deposits_of_control_never_leak_into_campaign(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS + CONTROL_IDS)
        insert_deposit_records([_deposit("51", _dt(2026, 9, 14, 9))])
        report = window_report.build_report(win["id"])
        assert _group(report, "campaign")["total_deposited_users"] == 0
        assert _group(report, "control")["total_deposited_users"] == 1


# ---------------------------------------------------------------------------
# Intervention attribution (Campaign only)
# ---------------------------------------------------------------------------

class TestAttribution:
    def test_play_before_sms_is_not_a_conversion(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS)
        insert_play_records([_play("1", _dt(2026, 9, 14, 9), amount=50)])
        _welcome(run_id, "1", _dt(2026, 9, 14, 10))

        report = window_report.build_report(win["id"])
        camp = _group(report, "campaign")
        # The play remains a qualifying play (counted in played users/sales) but
        # is not attributed and no conversion is credited.
        assert camp["total_played_users"] == 1
        assert camp["total_sales"] == 50.0
        assert camp["converted_users"] == 0
        assert camp["contacted_users"] == 1
        att = report["attribution"]
        assert att["unattributed_plays"] == 1
        assert att["attributed_plays"] == 0
        assert att["conversion_events"] == 0

    def test_failed_sms_is_not_an_intervention(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS)
        _welcome(run_id, "1", _dt(2026, 9, 14, 9), status="failed")
        insert_play_records([_play("1", _dt(2026, 9, 14, 10))])

        report = window_report.build_report(win["id"])
        camp = _group(report, "campaign")
        assert camp["contacted_users"] == 0
        assert camp["converted_users"] == 0
        att = report["attribution"]
        assert att["accepted_interventions"] == 0
        assert att["unattributed_plays"] == 1

    def test_first_play_after_sms_is_conversion_later_plays_are_activity(self, _init_db):
        win = _setup(_init_db)
        run_id = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run_id, CAMPAIGN_IDS)
        _welcome(run_id, "1", _dt(2026, 9, 14, 9))
        insert_play_records([
            _play("1", _dt(2026, 9, 14, 10), amount=100),   # conversion
            _play("1", _dt(2026, 9, 14, 12), amount=50),    # activity
            _play("1", _dt(2026, 9, 15, 9), amount=200),    # activity
        ])

        report = window_report.build_report(win["id"])
        camp = _group(report, "campaign")
        assert camp["converted_users"] == 1
        assert camp["conversion_rate"] == 1.0
        att = report["attribution"]
        assert att["conversion_events"] == 1
        assert att["attributed_plays"] == 3
        assert att["attributed_amount"] == 350.0
        # Averaged over the one converted/contacted user across all her plays.
        assert camp["avg_plays_per_converted"] == 3.0
        assert camp["avg_amount_per_converted"] == 350.0

    def test_most_recent_intervention_wins_and_run_2_beats_run_1(self, _init_db):
        win = _setup(_init_db)
        run1 = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run1, CAMPAIGN_IDS)
        _welcome(run1, "1", _dt(2026, 9, 15, 9))
        insert_play_records([
            _play("1", _dt(2026, 9, 15, 10), amount=100),   # run1 conversion
            _play("1", _dt(2026, 9, 16, 11), amount=200),   # run1 activity
        ])

        run2 = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _welcome(run2, "1", _dt(2026, 9, 17, 9))
        insert_play_records([_play("1", _dt(2026, 9, 17, 10), amount=300)])

        report = window_report.build_report(win["id"])
        camp = _group(report, "campaign")
        # User 1 converts once per Run's intervention; attribution follows the
        # most-recent accepted welcome that precedes each play.
        assert camp["converted_users"] == 1
        assert report["attribution"]["conversion_events"] == 2

        runs = {r["run_id"]: r for r in report["runs"]}
        assert runs[run1]["converted_users"] == 1
        assert runs[run1]["conversion_events"] == 1
        assert runs[run1]["attributed_plays"] == 2
        assert runs[run1]["attributed_amount"] == 300.0
        assert runs[run2]["converted_users"] == 1
        assert runs[run2]["attributed_plays"] == 1
        assert runs[run2]["attributed_amount"] == 300.0

    def test_converted_user_with_later_run_sms_still_distinct(self, _init_db):
        win = _setup(_init_db)
        run1 = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run1, CAMPAIGN_IDS)
        _welcome(run1, "1", _dt(2026, 9, 15, 9))
        insert_play_records([_play("1", _dt(2026, 9, 15, 10))])
        run2 = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _welcome(run2, "1", _dt(2026, 9, 16, 9))
        insert_play_records([_play("1", _dt(2026, 9, 16, 10))])

        report = window_report.build_report(win["id"])
        # Both plays convert (one per Run); the user is counted once per group.
        assert _group(report, "campaign")["converted_users"] == 1
        assert report["attribution"]["conversion_events"] == 2

    def test_unattributed_and_attributed_plays_are_partitioned(self, _init_db):
        win = _setup(_init_db)
        run1 = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run1, CAMPAIGN_IDS)
        _welcome(run1, "1", _dt(2026, 9, 14, 9))
        insert_play_records([
            _play("1", _dt(2026, 9, 14, 8)),    # before the SMS
            _play("1", _dt(2026, 9, 14, 10)),   # after the SMS
            _play("2", _dt(2026, 9, 14, 10)),   # user 2, never contacted
        ])

        report = window_report.build_report(win["id"])
        att = report["attribution"]
        assert att["attributed_plays"] == 1
        assert att["unattributed_plays"] == 2  # pre-SMS play + user 2's play

    def test_run_summary_counts_target_and_activity(self, _init_db):
        win = _setup(_init_db)
        run1 = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run1, CAMPAIGN_IDS)
        _welcome(run1, "1", _dt(2026, 9, 14, 9))
        _welcome(run1, "2", _dt(2026, 9, 14, 9))
        _welcome(run1, "2", _dt(2026, 9, 14, 9), status="failed")
        insert_play_records([
            _play("1", _dt(2026, 9, 14, 10)),
            _play("2", _dt(2026, 9, 14, 10)),
        ])

        report = window_report.build_report(win["id"])
        run = report["runs"][0]
        assert run["targeted_users"] == len(CAMPAIGN_IDS)
        assert run["accepted_interventions"] == 2
        assert run["failed_sends"] == 1
        assert run["contacted_users"] == 2
        assert run["converted_users"] == 2
        assert run["conversion_events"] == 2
        assert run["attributed_plays"] == 2
        assert run["plays_by_target_users"] == 2

    def test_control_never_attributed_even_with_stray_welcome(self, _init_db):
        win = _setup(_init_db)
        run1 = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run1, CAMPAIGN_IDS + CONTROL_IDS)
        # A control user is never a Run target and would never be sent to, but
        # even a stray ledger row cannot make Control convert.
        _welcome(run1, "51", _dt(2026, 9, 14, 9))
        insert_play_records([_play("51", _dt(2026, 9, 14, 10))])

        report = window_report.build_report(win["id"])
        assert _group(report, "control")["converted_users"] is None
        assert report["attribution"]["converted_users_total"] == 0
        assert report["attribution"]["contacted_users_total"] == 0


# ---------------------------------------------------------------------------
# Finalization freeze + live reports
# ---------------------------------------------------------------------------

class TestFinalization:
    def test_finalize_freezes_report_snapshot(self, _init_db):
        win = _setup(_init_db)
        run1 = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run1, CAMPAIGN_IDS)
        _welcome(run1, "1", _dt(2026, 9, 14, 9))
        insert_play_records([_play("1", _dt(2026, 9, 14, 10), amount=100)])

        svc.end_window(win["id"])
        finalized = svc.finalize_window(win["id"], now=FINALIZE_AT)
        assert finalized["status"] == "finalized"
        assert finalized["finalized_at"] is not None

        frozen = window_report.get_report(win["id"])
        assert frozen["window"]["status"] == "finalized"
        assert frozen["groups"]["campaign"]["converted_users"] == 1

    def test_finalized_report_immutable_when_facts_change_after(self, _init_db):
        win = _setup(_init_db)
        run1 = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run1, CAMPAIGN_IDS)
        _welcome(run1, "1", _dt(2026, 9, 14, 9))
        insert_play_records([_play("1", _dt(2026, 9, 14, 10), amount=100)])

        svc.end_window(win["id"])
        svc.finalize_window(win["id"], now=FINALIZE_AT)
        before = window_report.get_report(win["id"])
        assert before["groups"]["campaign"]["total_played_users"] == 1

        # Facts arriving AFTER finalization must never change the frozen report.
        insert_play_records([_play("1", _dt(2026, 9, 15, 10), amount=999)])
        insert_login_records([_login("2", _dt(2026, 9, 15, 10))])

        after = window_report.get_report(win["id"])
        assert after == before
        assert after["groups"]["campaign"]["total_played_users"] == 1
        assert after["groups"]["campaign"]["total_sales"] == 100.0

    def test_finalize_requires_ended_window(self, _init_db):
        win = _setup(_init_db)
        run1 = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run1, CAMPAIGN_IDS)
        with pytest.raises(Exception):
            svc.finalize_window(win["id"], now=FINALIZE_AT)  # still active

    def test_live_report_counts_grace_period_uploads(self, _init_db):
        win = _setup(_init_db)
        run1 = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run1, CAMPAIGN_IDS)
        _welcome(run1, "1", _dt(2026, 9, 14, 9))
        insert_play_records([_play("1", _dt(2026, 9, 14, 10), amount=100)])

        svc.end_window(win["id"])
        # A qualifying play uploaded during the grace period (before the
        # report was frozen) is counted on the pre-finalization live path.
        insert_play_records([_play("1", _dt(2026, 9, 15, 10), amount=200)])
        before = window_report.get_report(win["id"])
        assert before["groups"]["campaign"]["total_sales"] == 300.0

        svc.finalize_window(win["id"], now=FINALIZE_AT)
        frozen = window_report.get_report(win["id"])
        assert frozen["groups"]["campaign"]["total_sales"] == 300.0

    def test_report_skips_out_of_period_uploads_even_in_grace(self, _init_db):
        win = _setup(_init_db)
        run1 = svc.start_run(win["id"], segments=["unsegmented"])["run"]["id"]
        _add_members(win["id"], run1, CAMPAIGN_IDS)
        insert_play_records([_play("1", _dt(2026, 9, 20, 0, 1), amount=500)])

        svc.end_window(win["id"])
        report = window_report.get_report(win["id"])
        assert report["groups"]["campaign"]["total_played_users"] == 0
        assert report["groups"]["campaign"]["total_sales"] == 0.0

    def test_zero_target_and_no_facts_report(self, _init_db):
        win = svc.create_window(name="empty", start_time=_dt(2026, 9, 14))
        svc.set_control_override(win["id"], 50.0)
        svc.set_eligible_count(win["id"], 1, {"unsegmented": 1})

        report = window_report.build_report(win["id"])
        assert report["audience"]["campaignable_total"] == 0
        assert report["runs"] == []
        assert _group(report, "campaign")["total_targeted_audience"] == 0
        assert report["attribution"]["accepted_interventions"] == 0