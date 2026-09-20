"""End-to-end tests for Campaign Run -> Welcome SMS execution
(app.services.execution.dispatch_run) wired to the window/run/audience
foundation and the in-memory SMS gateway.

Each scenario builds a small window + run, adds audience members directly
(deterministic assignment: user_id % 100 < campaign_percentage -> Campaign),
then dispatches and asserts: frozen-target semantics (Control users and
run-less members excluded), cooldown + cap gates, the sms_log ledger, failed
attempt retry, deferral-on-deadline, idempotency of repeat dispatch, and the
lifecycle guards.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core import dates
from app.core.config import settings
from app.db.database import get_connection
from app.db.sms import log_sms
from app.db.windows import WindowStateError, get_run_target
from app.integrations.in_memory import InMemorySmsGateway
from app.services import eligibility, execution as exc
from app.services import windows as svc

LAGOS = ZoneInfo("Africa/Lagos")
NOW = datetime(2026, 9, 19, 18, 30, tzinfo=LAGOS)

NEVER_DEPOSITED = "NeverDepositedUnder400Hrs"

# Deterministic assignment with control 50% -> campaign_percentage 50:
# user_id % 100 < 50  =>  Campaign. 51/52 land in Control.
CAMPAIGN_IDS = ("1", "2", "3")
CONTROL_IDS = ("51", "52")


def _phone(uid: str) -> str:
    return f"080{uid:0>8}"


@pytest.fixture()
def _fast_cycle(monkeypatch):
    monkeypatch.setattr(settings, "COOLDOWN_HOURS", 24)
    monkeypatch.setattr(settings, "WELCOME_MAX_MESSAGES", 3)
    monkeypatch.setattr(settings, "WELCOME_POST_LIMIT_SUPPRESS", True)


def _sms_rows():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM sms_log ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _add_members(win_id, run_id, user_ids) -> None:
    members = [
        {
            "user_id": uid,
            "segment_id": NEVER_DEPOSITED,
            "phone": _phone(uid),
            "run_id": run_id,
            "eligibility_state": {
                "run_id": run_id,
                "window_id": win_id,
                "segment": NEVER_DEPOSITED,
                "login_at": dates.to_utc_iso(datetime(2026, 9, 19, 15, 0, tzinfo=LAGOS)),
                "phone": _phone(uid),
                "first_name": f"User{uid}",
            },
        }
        for uid in user_ids
    ]
    result = svc.add_eligible_users(win_id, members)
    assert result["invalid_phone"] == 0
    return result


def _setup(_init_db, _fast_cycle):
    win = svc.create_window(name="test", segments=[NEVER_DEPOSITED])
    svc.set_control_override(win["id"], 50.0)
    svc.set_eligible_count(
        win["id"],
        len(CAMPAIGN_IDS) + len(CONTROL_IDS),
        {NEVER_DEPOSITED: len(CAMPAIGN_IDS) + len(CONTROL_IDS)},
    )
    run_id = svc.start_run(win["id"])["run"]["id"]
    _add_members(win["id"], run_id, CAMPAIGN_IDS + CONTROL_IDS)
    return win, run_id


class TestDispatchTarget:
    async def test_dispatch_sends_only_frozen_campaign_target(self, _init_db, _fast_cycle):
        win, run_id = _setup(_init_db, _fast_cycle)
        target = get_run_target(win["id"], run_id)
        assert {m["user_id"] for m in target} == set(CAMPAIGN_IDS)

        gateway = InMemorySmsGateway()
        report = await exc.dispatch_run(
            run_id, now=NOW, gateway=gateway,
        )

        assert report["target"] == len(CAMPAIGN_IDS)
        assert report["sent"] == len(CAMPAIGN_IDS)
        assert report["failed"] == 0
        assert report["deferred"] == 0
        assert report["already_sent"] == 0
        assert report["skipped_cooldown"] == 0
        assert report["skipped_cap"] == 0

        # Every provider call was a frozen-target member's canonical phone with
        # the Welcome template rendered from the frozen first name.
        target_phones = {m["phone_normalized"] for m in target}
        assert {a["phone"] for a in gateway.attempts} == target_phones
        for m in target:
            first = (m["eligibility_state"] or {})["first_name"]
            message = next(
                a["message"] for a in gateway.attempts
                if a["phone"] == m["phone_normalized"]
            )
            assert f"Hi {first}," in message

        # Ledger: exactly one accepted 'welcome' send per target user, linked
        # to this Run via cycle_id.
        rows = _sms_rows()
        assert len(rows) == len(CAMPAIGN_IDS)
        assert {r["user_id"] for r in rows} == set(CAMPAIGN_IDS)
        assert all(r["kind"] == "welcome" for r in rows)
        assert all(r["cycle_id"] == f"run:{run_id}" for r in rows)
        assert all(r["status"] == "sent" for r in rows)
        assert all(r["message_id"] for r in rows)

    async def test_control_users_never_dispatched(self, _init_db, _fast_cycle):
        win, run_id = _setup(_init_db, _fast_cycle)
        # Control users are audience members (they count toward N) but are
        # not in the Run's target and never receive the SMS.
        target_phones = {m["phone_normalized"] for m in get_run_target(win["id"], run_id)}
        assert len(target_phones) == len(CAMPAIGN_IDS)

        gateway = InMemorySmsGateway()
        report = await exc.dispatch_run(run_id, now=NOW, gateway=gateway)

        assert report["target"] == len(CAMPAIGN_IDS)
        assert report["sent"] == len(CAMPAIGN_IDS)
        assert all(a["phone"] in target_phones for a in gateway.attempts)
        assert len(gateway.attempts) == len(CAMPAIGN_IDS)

    async def test_members_without_run_id_not_in_target(self, _init_db, _fast_cycle):
        win, run_id = _setup(_init_db, _fast_cycle)
        # A Campaign-assigned user admitted without a run_id (e.g. manual
        # audience add) is NOT part of this Run's frozen target.
        _add_members(win["id"], None, ("7",))
        assert "7" not in {m["user_id"] for m in get_run_target(win["id"], run_id)}

        gateway = InMemorySmsGateway()
        report = await exc.dispatch_run(run_id, now=NOW, gateway=gateway)

        assert report["sent"] == len(CAMPAIGN_IDS)
        assert all(a["phone"] != _phone("7") for a in gateway.attempts)


class TestDispatchGates:
    async def test_global_cooldown_skips_recently_accepted(self, _init_db, _fast_cycle):
        win, run_id = _setup(_init_db, _fast_cycle)
        # The vendor ring stops at cooldown even when the last message was not
        # a Campaign Run welcome: cooldown is global per user, any accepted kind.
        for uid in CAMPAIGN_IDS:
            log_sms({
                "user_id": uid, "kind": "inactive", "phone": _phone(uid),
                "status": "sent", "sent_at": dates.to_utc_iso(
                    datetime(2026, 9, 19, 18, 0, tzinfo=LAGOS)
                ),
            })
        rows_before = _sms_rows()

        gateway = InMemorySmsGateway()
        report = await exc.dispatch_run(run_id, now=NOW, gateway=gateway)

        assert report["skipped_cooldown"] == len(CAMPAIGN_IDS)
        assert report["sent"] == 0
        assert gateway.calls["send"] == 0
        # No welcome rows were added for the run: the seeded rows are untouched.
        assert _sms_rows() == rows_before
        assert not any(r["cycle_id"] == f"run:{run_id}" for r in _sms_rows())

    async def test_failed_sending_does_not_start_cooldown(self, _init_db, _fast_cycle):
        win, run_id = _setup(_init_db, _fast_cycle)
        # A failed send writes a 'failed' ledger row but never starts a
        # cooldown, so the same user can be retried immediately.
        log_sms({
            "user_id": "1", "kind": "campaign", "phone": _phone("1"),
            "status": "failed", "sent_at": dates.to_utc_iso(
                datetime(2026, 9, 19, 18, 0, tzinfo=LAGOS)
            ),
        })

        gateway = InMemorySmsGateway()
        report = await exc.dispatch_run(run_id, now=NOW, gateway=gateway)

        assert report["sent"] == len(CAMPAIGN_IDS)
        assert report["skipped_cooldown"] == 0

    async def test_cap_skips_users_at_limit(self, _init_db, _fast_cycle):
        win, run_id = _setup(_init_db, _fast_cycle)
        for uid in CAMPAIGN_IDS:
            for i in range(3):
                log_sms({
                    "user_id": uid, "kind": "welcome", "phone": _phone(uid),
                    "status": "sent", "sent_at": dates.to_utc_iso(
                        datetime(2026, 9, 18, 10 + i, tzinfo=LAGOS)
                    ),
                })
        rows_before = _sms_rows()

        gateway = InMemorySmsGateway()
        report = await exc.dispatch_run(run_id, now=NOW, gateway=gateway)

        assert report["skipped_cap"] == len(CAMPAIGN_IDS)
        assert report["sent"] == 0
        assert gateway.calls["send"] == 0
        assert _sms_rows() == rows_before

    async def test_cap_suppression_off_sends_beyond_cap(self, _init_db, _fast_cycle, monkeypatch):
        monkeypatch.setattr(settings, "WELCOME_POST_LIMIT_SUPPRESS", False)
        win, run_id = _setup(_init_db, _fast_cycle)
        for uid in CAMPAIGN_IDS:
            log_sms({
                "user_id": uid, "kind": "welcome", "phone": _phone(uid),
                "status": "sent", "sent_at": dates.to_utc_iso(
                    datetime(2026, 9, 18, 10, tzinfo=LAGOS)
                ),
            })

        report = await exc.dispatch_run(run_id, now=NOW, gateway=InMemorySmsGateway())

        assert report["skipped_cap"] == 0
        assert report["sent"] == len(CAMPAIGN_IDS)


class TestDispatchOutcomes:
    async def test_failed_send_is_retried_by_next_dispatch(self, _init_db, _fast_cycle):
        win, run_id = _setup(_init_db, _fast_cycle)

        failing = InMemorySmsGateway(send_result=None)
        report1 = await exc.dispatch_run(run_id, now=NOW, gateway=failing)

        assert report1["sent"] == 0
        assert report1["failed"] == len(CAMPAIGN_IDS)
        assert report1["already_sent"] == 0
        rows1 = _sms_rows()
        assert len(rows1) == len(CAMPAIGN_IDS)
        assert all(r["status"] == "failed" for r in rows1)
        assert all(r["cycle_id"] == f"run:{run_id}" for r in rows1)

        # Failures consumed neither cooldown nor cap, and nothing was accepted
        # for the run: a fresh dispatch retries all of them.
        good = InMemorySmsGateway()
        report2 = await exc.dispatch_run(run_id, now=NOW, gateway=good)

        assert report2["already_sent"] == 0
        assert report2["skipped_cooldown"] == 0
        assert report2["skipped_cap"] == 0
        assert report2["sent"] == len(CAMPAIGN_IDS)
        assert good.calls["send"] == len(CAMPAIGN_IDS)

    async def test_deadline_deferral_writes_nothing(self, _init_db, _fast_cycle):
        win, run_id = _setup(_init_db, _fast_cycle)
        now = datetime.now(timezone.utc)

        gateway = InMemorySmsGateway()
        report = await exc.dispatch_run(
            run_id,
            now=NOW,
            deadline=now - timedelta(minutes=1),
            gateway=gateway,
        )

        assert report["deferred"] == len(CAMPAIGN_IDS)
        assert report["sent"] == 0
        assert report["failed"] == 0
        assert gateway.calls["send"] == 0
        assert _sms_rows() == []

    async def test_repeat_dispatch_is_idempotent(self, _init_db, _fast_cycle):
        win, run_id = _setup(_init_db, _fast_cycle)
        await exc.dispatch_run(run_id, now=NOW, gateway=InMemorySmsGateway())
        rows_before = _sms_rows()

        gateway = InMemorySmsGateway()
        report = await exc.dispatch_run(run_id, now=NOW, gateway=gateway)

        assert report["already_sent"] == len(CAMPAIGN_IDS)
        assert report["sent"] == 0
        assert gateway.calls["send"] == 0
        assert _sms_rows() == rows_before


class TestDispatchLifecycle:
    async def test_dispatch_requires_running_run(self, _init_db, _fast_cycle):
        win, run_id = _setup(_init_db, _fast_cycle)
        svc.complete_run(run_id)
        with pytest.raises(WindowStateError):
            await exc.dispatch_run(run_id, now=NOW, gateway=InMemorySmsGateway())

    async def test_dispatch_blocked_after_window_end(self, _init_db, _fast_cycle):
        win, run_id = _setup(_init_db, _fast_cycle)
        svc.end_window(win["id"])
        with pytest.raises(WindowStateError):
            await exc.dispatch_run(run_id, now=NOW, gateway=InMemorySmsGateway())

    async def test_empty_target_dispatches_zero(self, _init_db, _fast_cycle):
        win = svc.create_window(name="test", segments=[NEVER_DEPOSITED])
        svc.set_control_override(win["id"], 50.0)
        svc.set_eligible_count(win["id"], 1, {NEVER_DEPOSITED: 1})
        run_id = svc.start_run(win["id"])["run"]["id"]

        report = await exc.dispatch_run(run_id, now=NOW, gateway=InMemorySmsGateway())

        assert report["target"] == 0
        assert report["sent"] == report["failed"] == report["deferred"] == 0


class TestEvaluationIntegration:
    """The eligibility pipeline itself seeds the frozen target (run_id +
    first_name) that dispatch consumes."""

    def _snapshot(self, data_dir):
        def _csv(name, header, rows):
            with (data_dir / name).open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(header)
                writer.writerows(rows)

        _csv(
            "Login_20260919.csv",
            ["userId", "timestamp"],
            [
                ("1", "2026-09-19 15:00:00"),
                ("3", "2026-09-19 15:10:00"),
                ("6", "2026-09-19 15:02:00"),
            ],
        )
        _csv(
            "Registrations_20260910.csv",
            ["userId", "firstName", "email", "phone", "timestamp"],
            [
                ("1", "Alice", "a@x.com", "08012345678", "2026-09-10 10:00:00"),
                ("3", "Cara", "c@x.com", "08034567890", "2026-09-10 10:00:00"),
                ("6", "Fay", "f@x.com", "08067890123", "2026-09-10 10:00:00"),
            ],
        )
        _csv(
            "Sales_20260919.csv",
            ["userId", "gameName", "amount", "timestamp"],
            [],
        )
        _csv("Deposit_events_20260919.csv", ["userId", "timestamp"], [])

    async def test_evaluation_seeds_target_and_dispatch_renders_names(self, _init_db, _fast_cycle, monkeypatch):
        monkeypatch.setattr(settings, "WELCOME_LOGIN_AGE_HOURS", 3)
        self._snapshot(settings.DATA_FOLDER)
        win = svc.create_window(name="test", segments=[NEVER_DEPOSITED])
        run_id = svc.start_run(win["id"])["run"]["id"]

        report = eligibility.evaluate_run(run_id, now=NOW)
        # users 1, 3, 6 are eligible; with auto-configured 50% control all three
        # land in Campaign (ids 1, 3, 6 are all < 50), so target == all.
        assert report["audience"]["added"] == 3
        target = get_run_target(win["id"], run_id)
        assert {m["user_id"] for m in target} == {"1", "3", "6"}
        assert {m["eligibility_state"]["first_name"] for m in target} == {
            "Alice", "Cara", "Fay",
        }

        gateway = InMemorySmsGateway()
        out = await exc.dispatch_run(run_id, now=NOW, gateway=gateway)

        assert out["sent"] == 3
        names = {m["eligibility_state"]["first_name"] for m in target}
        for attempt in gateway.attempts:
            assert any(f"Hi {name}," in attempt["message"] for name in names)
            assert attempt["phone"] in {m["phone_normalized"] for m in target}