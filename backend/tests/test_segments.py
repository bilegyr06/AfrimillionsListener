"""Unit tests for the segment membership engine (transcription of the
authoritative business SQL into app.services.segments).

Covers the parametrized reference windows, the SQL DATEDIFF translations,
lifecycle/tier classification order, and the first-match (mutually exclusive)
membership evaluation in authoritative build order.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services import segments
from app.services.segments import (
    DEPOSITED_NO_PLAY_UNDER_100_DAYS,
    LOGGED_IN_NO_PLAY_LAST_WEEK,
    MID_TIER_INACTIVE,
    NEVER_DEPOSITED_UNDER_400_HRS,
    NEW_PLAYED,
    PLAYED_LAST_WEEK_NO_PLAY_TODAY,
    SEGMENT_IDS,
    STANDARD_LAPSING,
    STAGE_ACTIVE,
    STAGE_DEPOSIT_NO_PLAY,
    STAGE_DORMANT,
    STAGE_INACTIVE,
    STAGE_LAPSING,
    STAGE_NEVER_DEPOSITED,
    STAGE_NEW_PLAYED,
    STAGE_ONBOARDING,
    STAGE_UNCLASSIFIED,
    TIER_MASS_MARKET,
    TIER_MID,
    TIER_VIP,
    PlayerProfile,
    calendar_days,
    classify_stage,
    compute_profile,
    evaluate_membership,
    hour_boundaries,
    last_sunday,
    onboarding_start,
    segment_week_bounds,
    today_start,
    value_tier,
)

LAGOS = ZoneInfo("Africa/Lagos")


def dt(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=LAGOS)


def reg(user_id, when, phone="08012345678"):
    return {"userId": str(user_id), "timestamp": when, "phone": phone}


def profile(user_id, now, *, registered_at=None, plays=(), deposits=()):
    return compute_profile(
        str(user_id),
        reg(user_id, registered_at) if registered_at is not None else None,
        list(plays),
        list(deposits),
        now,
    )


# ---------------------------------------------------------------------------
# Reference windows
# ---------------------------------------------------------------------------

class TestReferenceWindows:
    def test_segment_week_bounds_match_sql_literals(self):
        # Authoritative SQL used [2026-09-06 00:00, 2026-09-13 00:00): the
        # Sun-to-Sun week ending at the last Sunday at/ before the run.
        start, end = segment_week_bounds(dt(2026, 9, 19, 18, 30))
        assert start == dt(2026, 9, 6)
        assert end == dt(2026, 9, 13)

    def test_last_sunday_lands_on_sunday(self):
        assert last_sunday(dt(2026, 9, 13, 23, 59)) == dt(2026, 9, 13)
        assert last_sunday(dt(2026, 9, 19)) == dt(2026, 9, 13)
        assert last_sunday(dt(2026, 9, 7)) == dt(2026, 9, 6)

    def test_onboarding_start_matches_default_window_start(self):
        # WeekToDate cohort: registrations >= Monday 00:00 of the current week.
        assert onboarding_start(dt(2026, 9, 19)) == dt(2026, 9, 14)

    def test_today_start(self):
        assert today_start(dt(2026, 9, 19, 18, 30)) == dt(2026, 9, 19)

    def test_reference_windows_shift_with_reference_instant(self):
        # Audit check: no SQL literal is baked in. The authoritative SQL
        # referenced a fixed 2026-09 week, but every window here is computed
        # from Africa/Lagos reference instants, so a different week
        # reconstructs its own boundaries.
        start, end = segment_week_bounds(dt(2026, 8, 22, 18, 30))
        assert start == dt(2026, 8, 9)      # last Sunday at/before the reference
        assert end == dt(2026, 8, 16)
        assert last_sunday(dt(2026, 8, 22)) == dt(2026, 8, 16)
        assert onboarding_start(dt(2026, 8, 22)) == dt(2026, 8, 17)
        assert today_start(dt(2026, 8, 22, 18, 30)) == dt(2026, 8, 22)

    def test_segment_ids_in_build_order(self):
        assert SEGMENT_IDS == (
            NEW_PLAYED,
            LOGGED_IN_NO_PLAY_LAST_WEEK,
            PLAYED_LAST_WEEK_NO_PLAY_TODAY,
            STANDARD_LAPSING,
            NEVER_DEPOSITED_UNDER_400_HRS,
            MID_TIER_INACTIVE,
            DEPOSITED_NO_PLAY_UNDER_100_DAYS,
        )


# ---------------------------------------------------------------------------
# SQL DATEDIFF translations
# ---------------------------------------------------------------------------

class TestDatediff:
    def test_hours_count_explicit_hour_boundaries(self):
        # 10:30 -> 13:00 crosses 11/12/13 = 3 (NOT floor(2.5h) = 2).
        assert hour_boundaries(dt(2026, 9, 19, 10, 30), dt(2026, 9, 19, 13, 0)) == 3
        assert hour_boundaries(dt(2026, 9, 19, 13, 0), dt(2026, 9, 19, 13, 0)) == 0

    def test_calendar_days_cross_midnight(self):
        assert calendar_days(dt(2026, 9, 6, 23, 59), dt(2026, 9, 7, 0, 0)) == 1
        assert calendar_days(dt(2026, 9, 19, 1, 0), dt(2026, 9, 19, 23, 0)) == 0


# ---------------------------------------------------------------------------
# Tier + lifecycle
# ---------------------------------------------------------------------------

class TestClassification:
    def test_value_tier_thresholds(self):
        assert value_tier(0) == TIER_MASS_MARKET
        assert value_tier(9999.99) == TIER_MASS_MARKET
        assert value_tier(10000) == TIER_MID
        assert value_tier(49999.99) == TIER_MID
        assert value_tier(50000) == TIER_VIP

    def test_onboarding_within_72_hours(self):
        p = profile("9", dt(2026, 9, 19, 10), registered_at=dt(2026, 9, 16, 10))
        assert p.stage == STAGE_ONBOARDING

    def test_never_deposited_after_onboarding(self):
        p = profile("9", dt(2026, 9, 19, 10), registered_at=dt(2026, 9, 16, 9))
        assert p.stage == STAGE_NEVER_DEPOSITED

    def test_deposit_no_play_is_c4(self):
        now = dt(2026, 9, 19, 12)
        p = profile(
            "9", now,
            registered_at=now - timedelta(days=20),
            deposits=[now - timedelta(days=10)],
        )
        assert p.stage == STAGE_DEPOSIT_NO_PLAY

    def test_new_played_player_first_deposit_within_14_days(self):
        now = dt(2026, 9, 19, 12)
        p = profile(
            "9", now,
            registered_at=now - timedelta(days=20),
            deposits=[now - timedelta(days=5)],
            plays=[(now - timedelta(days=1), 500.0)],
        )
        assert p.stage == STAGE_NEW_PLAYED
        assert p.tier == TIER_MASS_MARKET

    def test_active_lapsing_inactive_dormant(self):
        now = dt(2026, 9, 19, 12)
        reg_at = now - timedelta(days=90)
        for days_back, expected in (
            (1, STAGE_ACTIVE),
            (7, STAGE_ACTIVE),
            (8, STAGE_LAPSING),
            (30, STAGE_LAPSING),
            (31, STAGE_INACTIVE),
            (90, STAGE_INACTIVE),
            (91, STAGE_DORMANT),
        ):
            p = profile(
                "9", now,
                registered_at=reg_at,
                deposits=[now - timedelta(days=60)],
                plays=[(now - timedelta(days=days_back), 20000.0)],
            )
            assert p.stage == expected, (days_back, p.stage)

    def test_mid_tier_classification(self):
        now = dt(2026, 9, 19, 12)
        p = profile(
            "9", now,
            registered_at=now - timedelta(days=90),
            deposits=[now - timedelta(days=60)],
            plays=[(now - timedelta(days=45), 15000.0)],
        )
        assert p.tier == TIER_MID

    def test_unregistered_has_no_profile(self):
        assert compute_profile("9", None, [], [], dt(2026, 9, 19, 12)) is None


# ---------------------------------------------------------------------------
# Membership: build order + mutual exclusivity
# ---------------------------------------------------------------------------

class TestMembership:
    NOW = dt(2026, 9, 19, 12)

    def _p(self, now=None, **kwargs):
        now = now or self.NOW
        return profile("9", now, **kwargs)

    def test_onboarding_cohort_excluded_from_everything(self):
        # A New Played Player is still excluded when the registration is inside
        # the current WeekToDate onboarding cohort.
        p = self._p(
            registered_at=self.NOW - timedelta(days=10),
            deposits=[self.NOW - timedelta(days=5)],
            plays=[(self.NOW - timedelta(days=1), 100)],
        )
        assert p.stage == STAGE_NEW_PLAYED
        assert (
            evaluate_membership(
                self.NOW, p,
                logged_in_last_week=True, played_last_week=True,
                played_today=True, in_onboarding_cohort=True,
            )
            is None
        )

    def test_wtd_cohort_excluded_even_for_lapsing(self):
        now = dt(2026, 9, 19, 12)
        p = profile("9", now, registered_at=now - timedelta(days=90),
                    deposits=[now - timedelta(days=60)],
                    plays=[(now - timedelta(days=15), 800.0)])
        assert p.stage == STAGE_LAPSING
        assert evaluate_membership(now, p, logged_in_last_week=False,
                                   played_last_week=False, played_today=False,
                                   in_onboarding_cohort=True) is None

    def test_new_played_preempts_every_other_segment(self):
        now = dt(2026, 9, 19, 12)
        p = profile("9", now, registered_at=now - timedelta(days=20),
                    deposits=[now - timedelta(days=5)],
                    plays=[(now - timedelta(days=1), 300.0)])
        assert p.stage == STAGE_NEW_PLAYED
        assert evaluate_membership(now, p, logged_in_last_week=True,
                                   played_last_week=True, played_today=True,
                                   in_onboarding_cohort=False) == NEW_PLAYED

    def test_logged_in_no_play_precedes_never_deposited(self):
        now = dt(2026, 9, 19, 12)
        p = profile("9", now, registered_at=now - timedelta(hours=100))
        assert p.stage == STAGE_NEVER_DEPOSITED
        # Logged in last week + no play last week -> LoggedInNoPlay, not the
        # never-deposited-<400h segment (whose exclusion chain drops it too).
        assert (
            evaluate_membership(now, p, logged_in_last_week=True,
                                played_last_week=False, played_today=False,
                                in_onboarding_cohort=False)
            == LOGGED_IN_NO_PLAY_LAST_WEEK
        )

    def test_played_last_week_no_play_today(self):
        now = dt(2026, 9, 19, 12)
        p = profile("9", now, registered_at=now - timedelta(hours=100))
        assert (
            evaluate_membership(now, p, logged_in_last_week=True,
                                played_last_week=True, played_today=False,
                                in_onboarding_cohort=False)
            == PLAYED_LAST_WEEK_NO_PLAY_TODAY
        )

    def test_standard_lapsing_mass_market(self):
        now = dt(2026, 9, 19, 12)
        p = profile("9", now, registered_at=now - timedelta(days=90),
                    deposits=[now - timedelta(days=60)],
                    plays=[(now - timedelta(days=15), 800.0)])
        assert p.stage == STAGE_LAPSING
        assert evaluate_membership(now, p, logged_in_last_week=False,
                                   played_last_week=False, played_today=False,
                                   in_onboarding_cohort=False) == STANDARD_LAPSING

    def test_vip_lapsing_is_not_standard(self):
        now = dt(2026, 9, 19, 12)
        p = profile("9", now, registered_at=now - timedelta(days=90),
                    deposits=[now - timedelta(days=60)],
                    plays=[(now - timedelta(days=15), 80000.0)])
        assert p.tier == TIER_VIP
        assert p.stage == STAGE_LAPSING
        # Not Mass/Mid -> the standard-lapsing segment drops VIPs.
        assert evaluate_membership(now, p, logged_in_last_week=False,
                                   played_last_week=False, played_today=False,
                                   in_onboarding_cohort=False) is None

    def test_never_deposited_under_400_hours(self):
        now = dt(2026, 9, 19, 12)
        p = profile("9", now, registered_at=now - timedelta(hours=100))
        assert p.stage == STAGE_NEVER_DEPOSITED
        assert evaluate_membership(now, p, logged_in_last_week=False,
                                   played_last_week=False, played_today=False,
                                   in_onboarding_cohort=False) == NEVER_DEPOSITED_UNDER_400_HRS

    def test_never_deposited_over_400_hours_unsegmented(self):
        now = dt(2026, 9, 19, 12)
        p = profile("9", now, registered_at=now - timedelta(days=30))
        assert p.stage == STAGE_NEVER_DEPOSITED
        assert evaluate_membership(now, p, logged_in_last_week=False,
                                   played_last_week=False, played_today=False,
                                   in_onboarding_cohort=False) is None

    def test_mid_tier_inactive_only(self):
        now = dt(2026, 9, 19, 12)
        mid = profile("9", now, registered_at=now - timedelta(days=120),
                      deposits=[now - timedelta(days=100)],
                      plays=[(now - timedelta(days=45), 15000.0)])
        assert mid.stage == STAGE_INACTIVE and mid.tier == TIER_MID
        assert evaluate_membership(now, mid, logged_in_last_week=False,
                                   played_last_week=False, played_today=False,
                                   in_onboarding_cohort=False) == MID_TIER_INACTIVE
        # A mass-market inactive user belongs to no defined segment.
        mass = profile("9", now, registered_at=now - timedelta(days=120),
                       deposits=[now - timedelta(days=100)],
                       plays=[(now - timedelta(days=45), 800.0)])
        assert mass.stage == STAGE_INACTIVE and mass.tier == TIER_MASS_MARKET
        assert evaluate_membership(now, mass, logged_in_last_week=False,
                                   played_last_week=False, played_today=False,
                                   in_onboarding_cohort=False) is None

    def test_deposited_no_play_under_100_days(self):
        now = dt(2026, 9, 19, 12)
        p = profile("9", now, registered_at=now - timedelta(days=200),
                    deposits=[now - timedelta(days=50)])
        assert p.stage == STAGE_DEPOSIT_NO_PLAY
        assert (
            evaluate_membership(now, p, logged_in_last_week=False,
                                played_last_week=False, played_today=False,
                                in_onboarding_cohort=False)
            == DEPOSITED_NO_PLAY_UNDER_100_DAYS
        )

    def test_deposited_no_play_over_100_days_unsegmented(self):
        now = dt(2026, 9, 19, 12)
        p = profile("9", now, registered_at=now - timedelta(days=400),
                    deposits=[now - timedelta(days=150)])
        assert p.stage == STAGE_DEPOSIT_NO_PLAY
        assert evaluate_membership(now, p, logged_in_last_week=False,
                                   played_last_week=False, played_today=False,
                                   in_onboarding_cohort=False) is None

    def test_unregistered_is_unsegmented(self):
        assert evaluate_membership(self.NOW, None, logged_in_last_week=False,
                                   played_last_week=False, played_today=False,
                                   in_onboarding_cohort=False) is None

    def test_membership_holds_for_different_reference_week(self):
        # Audit check: membership is a pure function of the reference instant,
        # not of literals from the authoritative SQL (which referenced a
        # specific 2026-09 week).
        now = dt(2026, 8, 22, 12)
        p = profile("9", now, registered_at=now - timedelta(hours=100))
        assert p.stage == STAGE_NEVER_DEPOSITED
        assert (
            evaluate_membership(now, p, logged_in_last_week=False,
                                played_last_week=False, played_today=False,
                                in_onboarding_cohort=False)
            == NEVER_DEPOSITED_UNDER_400_HRS
        )