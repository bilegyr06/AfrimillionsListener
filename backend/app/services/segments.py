"""Segment membership evaluation for the Campaign Window eligibility pipeline.

Translates the operator's authoritative T-SQL segment definitions
(PlayerLifecycleStages plus the Segment_*_3 tables) into an application-layer
membership engine. The SQL literal windows (e.g. '2026-09-06 00:00:00') were
snapshot artifacts of one past run; every reference window is recomputed here
relative to the evaluation instant `now` in Africa/Lagos:

  * "Last week"  = the Sun 00:00 -> Sun 00:00 seven-day window ending at the
                   most recent Sunday (exactly what the segment sources use).
  * "Today"      = today's 00:00 -> `now` (PlayedLastWeek_NoPlayToday).
  * "Onboarding" = registrations since Monday 00:00 of the current week
                   (the WeekToDate onboarding cohort; equals the default
                   Campaign Window start).

Membership is mutually exclusive: a user belongs to exactly one defined segment
or none. Segments are evaluated in the build order of the source SQL; every
later segment excludes the earlier ones by construction (the SQL's LEFT JOIN
exclusion chains, e.g. StandardLapsing excludes New Played Players, the
Onboarding cohort and last-week segments), so the first match wins exactly as
the exclusion chains do. A user in no defined segment is "unsegmented" - the
eligibility service, not this module, decides that classification.

Fact timestamps (played_at / deposited_at labels) must be decoded with
app.core.dates.read_source_fact before being passed in so every comparison
runs in one Africa/Lagos frame.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core import dates

# ---------------------------------------------------------------------------
# Segment identifiers (build order == exclusion order in the source SQL)
# ---------------------------------------------------------------------------

NEW_PLAYED = "NewPlayedPlayers"
LOGGED_IN_NO_PLAY_LAST_WEEK = "LoggedInNoPlayLastWeek"
PLAYED_LAST_WEEK_NO_PLAY_TODAY = "PlayedLastWeekNoPlayToday"
STANDARD_LAPSING = "StandardLapsing"
NEVER_DEPOSITED_UNDER_400_HRS = "NeverDepositedUnder400Hrs"
MID_TIER_INACTIVE = "MidTierInactive"
DEPOSITED_NO_PLAY_UNDER_100_DAYS = "DepositedNoPlayUnder100Days"

#: Order matches the SQL build order; earlier segments pre-empt later ones.
SEGMENT_IDS: tuple[str, ...] = (
    NEW_PLAYED,
    LOGGED_IN_NO_PLAY_LAST_WEEK,
    PLAYED_LAST_WEEK_NO_PLAY_TODAY,
    STANDARD_LAPSING,
    NEVER_DEPOSITED_UNDER_400_HRS,
    MID_TIER_INACTIVE,
    DEPOSITED_NO_PLAY_UNDER_100_DAYS,
)

#: Human-friendly / reporting labels for each segment id.
SEGMENT_LABELS: dict[str, str] = {
    NEW_PLAYED: "New Played Players (first deposit <= 14 days, has played)",
    LOGGED_IN_NO_PLAY_LAST_WEEK: "Logged In No Play (last week)",
    PLAYED_LAST_WEEK_NO_PLAY_TODAY: "Played Last Week, No Play Today",
    STANDARD_LAPSING: "Standard Lapsing (mass market / mid-tier)",
    NEVER_DEPOSITED_UNDER_400_HRS: "Never Deposited (< 400 hours since registration)",
    MID_TIER_INACTIVE: "Mid-Tier Inactive",
    DEPOSITED_NO_PLAY_UNDER_100_DAYS: "Deposited No Play (< 100 days since last deposit)",
}

#: The fallback segment owned by the Window/eligibility service: an eligible
#: user who matches no defined segment is admitted as "unsegmented". It is the
#: default (and today's main operational) Window selection.
SEGMENT_CATALOG_DEFAULT = "unsegmented"


def list_segments() -> list[dict]:
    """The operator-facing segment catalog for Window configuration.

    The default `unsegmented` population comes first, then the defined segments
    in the source build order. Each entry carries the id the backend stores on
    the window plus a human label; the frontend never duplicates this catalog.
    """
    default = {
        "id": SEGMENT_CATALOG_DEFAULT,
        "label": "Unsegmented",
        "default": True,
    }
    defined = [
        {"id": seg_id, "label": SEGMENT_LABELS.get(seg_id, seg_id), "default": False}
        for seg_id in SEGMENT_IDS
    ]
    return [default, *defined]

# ---------------------------------------------------------------------------
# Lifecycle stage + value tier vocabulary
# ---------------------------------------------------------------------------

STAGE_ONBOARDING = "Onboarding Phase"
STAGE_NEVER_DEPOSITED = "Never Deposited"
STAGE_DEPOSIT_NO_PLAY = "C4 - Deposit No Play"
STAGE_NEW_PLAYED = "New Played Player"
STAGE_ACTIVE = "Active"
STAGE_LAPSING = "Lapsing"
STAGE_INACTIVE = "Inactive"
STAGE_DORMANT = "Dormant"
STAGE_UNCLASSIFIED = "Unclassified"

TIER_VIP = "VIP (High Value)"
TIER_MID = "Mid-Tier"
TIER_MASS_MARKET = "Mass Market"

_HOURS_IN_ONBOARDING = 72
_HOURS_UNDER_NEVER_DEPOSITED = 400
_DAYS_NEW_PLAYED = 14
_DAYS_DEPOSITED_NO_PLAY = 100
_ACTIVE_HIGH = 7
_LAPSING_HIGH = 30
_INACTIVE_HIGH = 90

_WEEK_DAYS = timedelta(days=7)


# ---------------------------------------------------------------------------
# Reference windows (parametrized from `now`, Africa/Lagos)
# ---------------------------------------------------------------------------

def _as_business(dt: datetime) -> datetime:
    return dates.as_business(dt)


def midnight(day: datetime) -> datetime:
    """00:00:00 Africa/Lagos on `day`'s calendar date."""
    day = _as_business(day)
    return day.replace(hour=0, minute=0, second=0, microsecond=0)


def last_sunday(ref: datetime) -> datetime:
    """00:00 Africa/Lagos of the most recent Sunday on or before `ref`."""
    ref = _as_business(ref)
    days_since_sunday = (ref.weekday() + 1) % 7
    return midnight(ref - timedelta(days=days_since_sunday))


def segment_week_bounds(now: datetime) -> tuple[datetime, datetime]:
    """[last Sunday 00:00 - 7d, last Sunday 00:00): the segment "last week"."""
    end = last_sunday(now)
    return end - _WEEK_DAYS, end


def today_start(now: datetime) -> datetime:
    """00:00 Africa/Lagos of the evaluation day (unbounded upper end)."""
    return midnight(now)


def onboarding_start(now: datetime) -> datetime:
    """Monday 00:00 of the current business week (WeekToDate cohort boundary)."""
    now = _as_business(now)
    days_since_monday = now.weekday()
    return midnight(now - timedelta(days=days_since_monday))


# ---------------------------------------------------------------------------
# SQL DATEDIFF semantics
# ---------------------------------------------------------------------------

def hour_boundaries(earlier: datetime, later: datetime) -> int:
    """SQL Server DATEDIFF(HOUR, earlier, later): whole one-hour ticks crossed.

    Counts explicit :00 hour boundaries between the two instants (e.g. 10:30 ->
    13:00 crosses 11:00/12:00/13:00 = 3), i.e. floor each to its hour first.
    """
    a = earlier.replace(minute=0, second=0, microsecond=0)
    b = later.replace(minute=0, second=0, microsecond=0)
    return int((b - a).total_seconds() // 3600)


def calendar_days(earlier: datetime, later: datetime) -> int:
    """SQL Server DATEDIFF(DAY, earlier, later): calendar-date boundary count.

    Equivalent to `(later.date() - earlier.date()).days`; floor to dates first.
    """
    return (later.date() - earlier.date()).days


# ---------------------------------------------------------------------------
# Tier + lifecycle classification
# ---------------------------------------------------------------------------

def value_tier(wagered_lifetime: float) -> str:
    """Player_Value_Tier from lifetime wagers (SQL: >=50000/>=10000/else)."""
    if wagered_lifetime >= 50000:
        return TIER_VIP
    if wagered_lifetime >= 10000:
        return TIER_MID
    return TIER_MASS_MARKET


@dataclass(frozen=True)
class PlayerProfile:
    """Per-player aggregates driving tier + lifecycle + segment predicates.

    `plays` is a list of (played_at Lagos-aware, amount); `deposits` a list of
    deposited_at Lagos-aware datetimes. Computed relative to one evaluation
    instant `now` so classification is self-consistent.
    """

    user_id: str
    registered: bool
    registration_ts: datetime | None
    hours_since_registration: int | None
    has_deposit: bool
    first_deposit_ts: datetime | None
    last_deposit_ts: datetime | None
    days_since_last_deposit: int | None
    days_since_first_deposit: int | None
    has_play: bool
    first_play_ts: datetime | None
    last_play_ts: datetime | None
    days_since_last_play: int | None
    wagered_lifetime: float
    tier: str = TIER_MASS_MARKET
    stage: str = STAGE_UNCLASSIFIED

    def as_dict(self) -> dict:
        """Serializable view for audit / eligibility_state (datetimes -> ISO)."""
        def iso(dt: datetime | None) -> str | None:
            return dates.to_utc_iso(dt) if dt is not None else None

        return {
            "user_id": self.user_id,
            "registered": self.registered,
            "hours_since_registration": self.hours_since_registration,
            "days_since_last_play": self.days_since_last_play,
            "days_since_last_deposit": self.days_since_last_deposit,
            "days_since_first_deposit": self.days_since_first_deposit,
            "wagered_lifetime": round(self.wagered_lifetime, 2),
            "player_value_tier": self.tier,
            "lifecycle_stage": self.stage,
            "last_play_at": iso(self.last_play_ts),
            "last_deposit_at": iso(self.last_deposit_ts),
        }


def _first_last(rows: list) -> tuple[datetime, datetime] | None:
    if not rows:
        return None
    first = rows[0]
    last = rows[0]
    for r in rows[1:]:
        if r < first:
            first = r
        if r > last:
            last = r
    return first, last


def _from_registration(raw: object) -> datetime | None:
    """Coerce a registration timestamp (datetime/str/pandas) to Lagos-aware."""
    if raw is None:
        return None
    try:
        if not isinstance(raw, (str, datetime)):
            raw = str(raw)
        return _as_business(datetime.fromisoformat(str(raw).replace(" ", "T", 1)))
    except (TypeError, ValueError):
        return None


def compute_profile(
    user_id: str,
    reg: dict | None,
    plays: list[tuple],
    deposits: list[datetime],
    now: datetime,
) -> PlayerProfile | None:
    """Build a player profile at `now`, or None when the user is unregistered.

    `reg`: the registrations row {timestamp, firstName, phone, email} or None.
    `plays`: list of (played_at Lagos-aware, amount).
    `deposits`: list of deposited_at Lagos-aware datetimes.
    """
    now = _as_business(now)
    if reg is None:
        return None

    reg_ts = _from_registration(reg.get("timestamp"))
    hours_since_registration = (
        hour_boundaries(reg_ts, now) if reg_ts is not None else None
    )

    if deposits and not isinstance(deposits[0], datetime):
        deposits = [d for d in deposits if isinstance(d, datetime)]
    dep_pair = _first_last(deposits)
    first_deposit_ts = dep_pair[0] if dep_pair else None
    last_deposit_ts = dep_pair[1] if dep_pair else None
    days_since_last_deposit = (
        calendar_days(last_deposit_ts, now) if last_deposit_ts is not None else None
    )
    days_since_first_deposit = (
        calendar_days(first_deposit_ts, now) if first_deposit_ts is not None else None
    )

    plays = [(ts, float(amount)) for ts, amount in plays if isinstance(ts, datetime)]
    play_pair = _first_last([ts for ts, _ in plays])
    last_play_ts = play_pair[1] if play_pair else None
    days_since_last_play = (
        calendar_days(last_play_ts, now) if last_play_ts is not None else None
    )
    wagered_lifetime = sum(amount for _, amount in plays)

    profile = PlayerProfile(
        user_id=user_id,
        registered=True,
        registration_ts=reg_ts,
        hours_since_registration=hours_since_registration,
        has_deposit=dep_pair is not None,
        first_deposit_ts=first_deposit_ts,
        last_deposit_ts=last_deposit_ts,
        days_since_last_deposit=days_since_last_deposit,
        days_since_first_deposit=days_since_first_deposit,
        has_play=bool(plays),
        first_play_ts=play_pair[0] if play_pair else None,
        last_play_ts=last_play_ts,
        days_since_last_play=days_since_last_play,
        wagered_lifetime=wagered_lifetime,
        tier=value_tier(wagered_lifetime),
    )
    return PlayerProfile(
        **{**profile.__dict__, "stage": classify_stage(profile, now)}
    )


def classify_stage(profile: PlayerProfile, now: datetime) -> str:
    """LifecycleStage CASE from the authoritative SQL, verbatim order."""
    hours = profile.hours_since_registration
    if hours is not None and hours <= _HOURS_IN_ONBOARDING:
        return STAGE_ONBOARDING
    if not profile.has_deposit:
        return STAGE_NEVER_DEPOSITED
    if profile.has_deposit and not profile.has_play:
        return STAGE_DEPOSIT_NO_PLAY
    if (
        profile.has_play
        and profile.days_since_first_deposit is not None
        and profile.days_since_first_deposit <= _DAYS_NEW_PLAYED
    ):
        return STAGE_NEW_PLAYED
    days = profile.days_since_last_play
    if days is None:
        return STAGE_UNCLASSIFIED
    if days <= _ACTIVE_HIGH:
        return STAGE_ACTIVE
    if days <= _LAPSING_HIGH:
        return STAGE_LAPSING
    if days <= _INACTIVE_HIGH:
        return STAGE_INACTIVE
    return STAGE_DORMANT


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------

def evaluate_membership(
    now: datetime,
    profile: PlayerProfile | None,
    *,
    logged_in_last_week: bool,
    played_last_week: bool,
    played_today: bool,
    in_onboarding_cohort: bool,
) -> str | None:
    """First matching segment in authoritative build order (or None).

    None means "no defined segment": the user is unsegmented. The Onboarding
    cohort is excluded from every segment (the SQL's WTD_Tracker exclusions),
    so it short-circuits to None.
    """
    if in_onboarding_cohort:
        return None
    if profile is None or not profile.registered:
        return None

    stage = profile.stage
    tier = profile.tier

    if stage == STAGE_NEW_PLAYED:
        return NEW_PLAYED
    if logged_in_last_week and not played_last_week:
        return LOGGED_IN_NO_PLAY_LAST_WEEK
    if played_last_week and not played_today:
        return PLAYED_LAST_WEEK_NO_PLAY_TODAY
    if stage == STAGE_LAPSING and tier in (TIER_MASS_MARKET, TIER_MID):
        return STANDARD_LAPSING
    if (
        stage == STAGE_NEVER_DEPOSITED
        and profile.hours_since_registration is not None
        and profile.hours_since_registration < _HOURS_UNDER_NEVER_DEPOSITED
    ):
        return NEVER_DEPOSITED_UNDER_400_HRS
    if tier == TIER_MID and stage == STAGE_INACTIVE:
        return MID_TIER_INACTIVE
    if (
        stage == STAGE_DEPOSIT_NO_PLAY
        and profile.days_since_last_deposit is not None
        and profile.days_since_last_deposit < _DAYS_DEPOSITED_NO_PLAY
    ):
        return DEPOSITED_NO_PLAY_UNDER_100_DAYS
    return None


__all__ = [
    "DEPOSITED_NO_PLAY_UNDER_100_DAYS",
    "LOGGED_IN_NO_PLAY_LAST_WEEK",
    "MID_TIER_INACTIVE",
    "NEVER_DEPOSITED_UNDER_400_HRS",
    "NEW_PLAYED",
    "PLAYED_LAST_WEEK_NO_PLAY_TODAY",
    "SEGMENT_IDS",
    "SEGMENT_LABELS",
    "STANDARD_LAPSING",
    "STAGE_ACTIVE",
    "STAGE_DEPOSIT_NO_PLAY",
    "STAGE_DORMANT",
    "STAGE_INACTIVE",
    "STAGE_LAPSING",
    "STAGE_NEVER_DEPOSITED",
    "STAGE_NEW_PLAYED",
    "STAGE_ONBOARDING",
    "STAGE_UNCLASSIFIED",
    "TIER_MASS_MARKET",
    "TIER_MID",
    "TIER_VIP",
    "PlayerProfile",
    "calendar_days",
    "classify_stage",
    "compute_profile",
    "evaluate_membership",
    "hour_boundaries",
    "last_sunday",
    "midnight",
    "onboarding_start",
    "segment_week_bounds",
    "today_start",
    "value_tier",
]