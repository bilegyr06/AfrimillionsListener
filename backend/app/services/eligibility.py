"""Current-Welcome eligibility evaluation for a Campaign Run.

evaluate_run(run_id) implements the audience/eligibility step of the Campaign
Window pipeline against a running Run's frozen source snapshot:

  1. Snapshot-scoped data: partition the Run's captured files by dataset and
     read logins, registrations, plays and deposits exactly from those files.
     Later uploads cannot change an active Run's inputs. Plays/deposits are
     additionally persisted via idempotent ingestion (scoped by source_file)
     so the durable fact tables stay the queryable source of truth.
  2. Candidates: users whose LATEST login in the Login snapshot falls inside
     the fixed one-hour band [now-(H+1)h, now-H) Africa/Lagos (a login older
     than H hours is "not hot anymore"; a login younger than exactly H hours is
     still being evaluated). H = the operator setting WELCOME_LOGIN_AGE_HOURS.
  3. Membership: each candidate's segment is evaluated from its player profile
     (lifecycle stage + tier + registration/today/last-week evidence) in the
     authoritative build order (app.services.segments). A member is only
     admitted when its segment is among the windores selected_segments;
     a candidate in the current WeekToDate onboarding cohort is excluded from
     all defined segments (never added to the audience by this step).
  4. Gates, in order: registered, onboarding cohort, selected segment,
     qualifying play after the login (any play strictly later disqualifies),
     cooldown (no accepted SMS within COOLDOWN_HOURS), and phone validity
     (invalid phones are skipped by the audience service).
  5. Audience + N: eligible members are added via the window service (which
     assigns Campaign/Control and only admits NEW users); N and per-segment
     eligible counts are refreshed from the admitted audience.

This step does NOT complete or stop the Run; the operator does that explicitly.
All reference windows are computed from the evaluation instant in Africa/Lagos,
so a fixed set of source files yields the same outcome however late a Run is
evaluated (login-band semantics, not wall-clock anniversary).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as _dt_timezone
from pathlib import Path

import pandas as pd

from app.core import dates
from app.core.config import settings
from app.db import files as files_db
from app.db import windows as db
from app.db.deposits import select_deposits
from app.db.players import select_plays
from app.db.sms import get_last_accepted_sms
from app.db.windows import WindowStateError
from app.services import segments, windows as svc
from app.services.players_facts import build_player_facts

#: Datasets captured by run_snapshots (c.f. windows._SNAPSHOT_PREFIX_TO_DATASET).
DATASET_LOGIN = "Login"
DATASET_REGISTRATIONS = "Registrations"
DATASET_SALES = "Sales"
DATASET_DEPOSITS = "Deposit_events"

UNSEGMENTED = svc.UNSEGMENTED


def _snapshot_partition(files: list[dict]) -> dict:
    """Partition snapshot entries by dataset, returning filenames (oldest first)."""
    partition: dict[str, list[str]] = {DATASET_LOGIN: [], DATASET_SALES: [], DATASET_DEPOSITS: []}
    reg: list[str] = []
    for entry in files:
        dataset = entry.get("dataset")
        name = entry.get("filename")
        if dataset == DATASET_REGISTRATIONS:
            reg.append(name)
        elif dataset in partition:
            partition[dataset].append(name)
    return {
        DATASET_LOGIN: sorted(partition[DATASET_LOGIN]),
        DATASET_SALES: sorted(partition[DATASET_SALES]),
        DATASET_DEPOSITS: sorted(partition[DATASET_DEPOSITS]),
        DATASET_REGISTRATIONS: sorted(reg),
    }


def _read_login_rows(filenames: list[str]) -> list[dict]:
    """All login rows from the snapshot Login files, timestamps as Lagos-aware.

    Column-less or unparseable files are skipped, mirroring read_login_events.
    """
    rows: list[dict] = []
    folder = settings.DATA_FOLDER
    for name in filenames:
        path = folder / name
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, dtype={"userId": str})
        except Exception:
            continue
        if df.empty or "userId" not in df.columns or "timestamp" not in df.columns:
            continue
        parsed = pd.to_datetime(df["timestamp"], errors="coerce")
        for idx, row in df.iterrows():
            uid = str(row["userId"]).strip() if pd.notna(row["userId"]) else ""
            if not uid:
                continue
            ts = parsed[idx]
            if pd.isna(ts):
                continue
            dt = ts.to_pydatetime()
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            rows.append({"user_id": uid, "logged_at": dates.as_business(dt)})
    return rows


def _read_registrations(filenames: list[str]) -> dict[str, dict]:
    """Newest snapshot Registrations file -> {user_id: row with Lagos timestamp}.

    Registration exports mix timestamp formats, so the timestamp column is
    coerced per-value (unparseable values yield None registration time).
    """
    if not filenames:
        return {}
    folder = settings.DATA_FOLDER
    newest = sorted(filenames, key=lambda n: Path(n))[-1]
    path = folder / newest
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, dtype={"userId": str})
    except Exception:
        return {}
    if df.empty or "userId" not in df.columns:
        return {}
    parsed = pd.to_datetime(df["timestamp"], errors="coerce") if "timestamp" in df.columns else None
    reg_map: dict[str, dict] = {}
    for idx, row in df.iterrows():
        uid = str(row["userId"]).strip() if pd.notna(row["userId"]) else ""
        if not uid:
            continue
        reg_ts = None
        if parsed is not None and not pd.isna(parsed[idx]):
            dt = parsed[idx].to_pydatetime()
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            reg_ts = dates.as_business(dt)
        reg_map[uid] = {
            "userId": uid,
            "timestamp": reg_ts,
            "firstName": str(row["firstName"]) if "firstName" in df.columns and pd.notna(row.get("firstName")) else None,
            "phone": str(row["phone"]).strip() if "phone" in df.columns and pd.notna(row.get("phone")) else None,
            "email": str(row["email"]) if "email" in df.columns and pd.notna(row.get("email")) else None,
        }
    return reg_map


def _ingest_snapshot_sources(partition: dict) -> None:
    """Ingest the snapshot's Sales/Deposit files that exist and are unprocessed.

    Scoped by source_file later, so only files the Run captured ever contribute
    facts to its evaluation.
    """
    from app.services.deposits import ingest_deposit_file
    from app.services.plays import ingest_sales_file

    folder = settings.DATA_FOLDER
    for key, ingester in ((DATASET_SALES, ingest_sales_file), (DATASET_DEPOSITS, ingest_deposit_file)):
        is_processed = (
            files_db.sales_file_processed
            if key == DATASET_SALES
            else files_db.deposit_file_processed
        )
        for name in partition[key]:
            path = folder / name
            if not path.exists() or is_processed(name):
                continue
            ingester(path)


def _scoped(names: list[str]) -> list[str]:
    """Source-file scope markers for empty snapshots.

    An empty list would widen the query to ALL rows (no source_file filter);
    an empty snapshot means the Run literally captured no files of a dataset,
    so the query must return nothing instead of everything.
    """
    return names if names else ["__no_snapshot_files__"]


def _login_band(now: datetime, hours: int) -> tuple[datetime, datetime]:
    """The current-welcome one-hour login band [now-(H+1)h, now-H)."""
    high = now - timedelta(hours=hours)
    low = high - timedelta(hours=1)
    return low, high


def _play_disqualified(plays: list[tuple], login_at) -> bool:
    """A played-at timestamp strictly after the qualifying login disqualifies."""
    return any(played_at > login_at for played_at, _ in plays)


def evaluate_run(run_id: int, *, now: datetime | None = None) -> dict:
    """Evaluate the current-welcome eligibility batch for a running Run.

    Returns a full report (windows used, snapshot scope, candidate counts,
    per-decision tallies, per-user detail, added audience, refreshed N).
    Raises WindowStateError when the run is not 'running' or the run/window is
    missing; raises WindowConfigError through the audience service when the
    window's control percentage cannot be determined.
    """
    now = dates.as_business(now) if now is not None else dates.now_business()

    run = db.get_run(run_id)
    if run is None:
        raise WindowStateError(f"Campaign Run #{run_id} not found.")
    if run["status"] != "running":
        raise WindowStateError(
            f"Cannot evaluate Campaign Run #{run_id}: status is {run['status']!r}, "
            "only running runs may be evaluated."
        )
    window_id = run["window_id"]
    window = db.get_window(window_id)
    if window is None:
        raise WindowStateError(f"Campaign Window #{window_id} not found.")

    snapshot_rows = db.get_run_snapshot(run_id)
    if snapshot_rows is None:
        raise WindowStateError(f"Run snapshot not found for Campaign Run #{run_id}.")
    partition = _snapshot_partition(snapshot_rows.get("files", []))
    _ingest_snapshot_sources(partition)

    # Reference windows (all Africa/Lagos).
    seg_start, seg_end = segments.segment_week_bounds(now)
    today = segments.today_start(now)
    onboarding = segments.onboarding_start(now)
    band_low, band_high = _login_band(now, settings.WELCOME_LOGIN_AGE_HOURS)

    # Snapshot-scoped facts.
    login_rows = _read_login_rows(partition[DATASET_LOGIN])
    reg_map = _read_registrations(partition[DATASET_REGISTRATIONS])
    plays_by_user, deposits_by_user = build_player_facts(
        select_plays(_scoped(partition[DATASET_SALES])),
        select_deposits(_scoped(partition[DATASET_DEPOSITS])),
    )

    selected = set(window["selected_segments"])
    defined = set(segments.SEGMENT_IDS)
    login_age_hours = int(settings.WELCOME_LOGIN_AGE_HOURS)

    # Latest login per user strictly inside the band -> candidates (deduped).
    candidates: dict[str, datetime] = {}
    last_week_login: dict[str, bool] = {}
    for lr in login_rows:
        uid, ts = lr["user_id"], lr["logged_at"]
        if seg_start <= ts < seg_end:
            last_week_login[uid] = True
        if band_low <= ts < band_high:
            prev = candidates.get(uid)
            if prev is None or ts > prev:
                candidates[uid] = ts

    decisions = {
        "not_registered": 0,
        "in_onboarding_cohort": 0,
        "not_in_selected_segment": 0,
        "played_since_login": 0,
        "cooldown_active": 0,
        "eligible": 0,
    }
    detail: list[dict] = []
    members: list[dict] = []
    members_by_segment: dict[str, int] = {}

    for user_id, login_at in candidates.items():
        reg = reg_map.get(user_id)
        reason = None
        segment = None

        if reg is None:
            reason = "not_registered"
            decisions["not_registered"] += 1
        elif reg.get("timestamp") is not None and reg["timestamp"] >= onboarding:
            reason = "in_onboarding_cohort"
            decisions["in_onboarding_cohort"] += 1
        else:
            profile = segments.compute_profile(
                user_id,
                reg,
                plays_by_user.get(user_id, []),
                deposits_by_user.get(user_id, []),
                now,
            )
            segment = segments.evaluate_membership(
                now,
                profile,
                logged_in_last_week=last_week_login.get(user_id, False),
                played_last_week=_in_week(plays_by_user.get(user_id, []), seg_start, seg_end),
                played_today=_in_week(plays_by_user.get(user_id, []), today, None),
                in_onboarding_cohort=False,
            )
            if segment is None:
                segment = UNSEGMENTED

            if segment not in selected:
                reason = "not_in_selected_segment"
                decisions["not_in_selected_segment"] += 1
            elif _play_disqualified(plays_by_user.get(user_id, []), login_at):
                reason = "played_since_login"
                decisions["played_since_login"] += 1
            elif _in_cooldown(user_id, now):
                reason = "cooldown_active"
                decisions["cooldown_active"] += 1
            else:
                reason = "eligible"
                decisions["eligible"] += 1
                members_by_segment[segment] = members_by_segment.get(segment, 0) + 1
                members.append(
                    {
                        "user_id": user_id,
                        "segment_id": segment,
                        "phone": reg.get("phone"),
                        "eligibility_state": _eligibility_state(
                            login_at, segment, profile, reg, window_id, run_id
                        ),
                    }
                )

        detail.append(
            {
                "user_id": user_id,
                "login_at": dates.to_utc_iso(login_at),
                "decision": reason,
                "segment": segment,
            }
        )

    # Auto-config N when control not yet configured, so assignment can run.
    window = db.get_window(window_id)
    if window is not None and window["control_percentage"] is None and members:
        svc.set_eligible_count(window_id, len(members), dict(members_by_segment))

    added: dict = {"added": 0, "existing": 0, "campaign": 0, "control": 0, "invalid_phone": 0}
    if members:
        added = svc.add_eligible_users(window_id, members)

    refreshed = svc.refresh_eligible_counts(window_id)

    return {
        "run_id": run_id,
        "window_id": window_id,
        "window_status": db.get_window(window_id)["status"],
        "evaluated_at": dates.to_utc_iso(now),
        "reference_windows": {
            "timezone": dates.BUSINESS_TIMEZONE,
            "login_band_start": dates.to_utc_iso(band_low),
            "login_band_end": dates.to_utc_iso(band_high),
            "login_age_hours": login_age_hours,
            "segment_week_start": dates.to_utc_iso(seg_start),
            "segment_week_end": dates.to_utc_iso(seg_end),
            "today_start": dates.to_utc_iso(today),
            "onboarding_start": dates.to_utc_iso(onboarding),
        },
        "snapshot_scope": {
            "login_files": len(partition[DATASET_LOGIN]),
            "registrations_file": partition[DATASET_REGISTRATIONS][-1]
            if partition[DATASET_REGISTRATIONS]
            else None,
            "sales_files": len(partition[DATASET_SALES]),
            "deposit_files": len(partition[DATASET_DEPOSITS]),
        },
        "candidates": len(candidates),
        "decisions": dict(decisions),
        "members_by_segment": dict(members_by_segment),
        "audience": added,
        "eligible_count": refreshed["eligible_count"] if refreshed else None,
        "details": detail,
    }


def _in_week(rows: list[tuple], start: datetime, end: datetime | None) -> bool:
    """Any played_at in [start, end) (bounded) or >= start (end=None the SQL's
    unbounded '>= today' Plays-Today predicate). Rows are Lagos-aware."""
    if end is None:
        return any(played_at >= start for played_at, *_ in rows)
    return any(start <= played_at < end for played_at, *_ in rows)


def _in_cooldown(user_id: str, now: datetime) -> bool:
    """Cooldown active when an accepted SMS was sent < COOLDOWN_HOURS ago.

    Compares true UTC instants: sms_log.sent_at is a UTC-aware ISO label while
    `now` is an Africa/Lagos-aware datetime; both convert to the same instant.
    """
    last_sent = get_last_accepted_sms(user_id)
    if not last_sent or settings.COOLDOWN_HOURS <= 0:
        return False
    last_dt = dates.parse_utc_iso(last_sent)
    return now.astimezone(_dt_timezone.utc) < last_dt + timedelta(hours=settings.COOLDOWN_HOURS)


def _eligibility_state(
    login_at: datetime,
    segment: str,
    profile: segments.PlayerProfile | None,
    reg: dict,
    window_id: int,
    run_id: int,
) -> dict:
    """Audit state snapshotted onto the window_audiences row."""
    state = {
        "run_id": run_id,
        "window_id": window_id,
        "segment": segment,
        "login_at": dates.to_utc_iso(login_at),
        "phone": reg.get("phone"),
    }
    if profile is not None:
        state["profile"] = profile.as_dict()
    return state