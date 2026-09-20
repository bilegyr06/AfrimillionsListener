"""Campaign Window report (v2 attribution + Campaign/Control reporting).

The Window is the reporting unit for v2.0.0. build_report(window_id) computes
the full Window report from persisted state only:

  * the window's final campaignable audience (window_audiences, phone-valid)
    - Campaign and Control kept strictly apart, never mixed;
  * the window evaluation period [start_time, end_time] Africa/Lagos;
  * accepted Welcome interventions: sms_log rows kind='welcome',
    cycle_id "run:{run_id}" for THIS window's Runs, accepted status
    (sent/delivered), sent within the window period;
  * persistent activity facts: plays, deposits and logins whose source
    timestamps fall in the window period (relabelled source-frame boundaries).

Attribution rule (Campaign only; Control never has conversions):
  * a qualifying play is a play record inside the window evaluation period;
  * the relevant intervention for a play is the MOST RECENT accepted Welcome
    SMS (of the window's Runs) that precedes the play in source time;
  * the first qualifying play after an accepted intervention is that
    intervention's conversion event; later plays attributed to the same
    intervention are activity, never extra conversions;
  * a play at/before an SMS is never converted and never attributed to a Run.

The report is a pure projection of persisted facts (no CSV rescans except the
idempotent ingestion of newly uploaded files before a non-finalized compute),
so a finalized Window's frozen snapshot is reproducible and immutable:
get_report never recomputes the numbers for a finalized window - it serves the
snapshot written at finalization, and later uploads cannot change it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.core import dates
from app.core.dates import BUSINESS_TIMEZONE
from app.db import windows as db
from app.db import window_report as wr
from app.db.window_report import ACCEPTED_SMS_STATUSES, REPORT_SCHEMA_VERSION

RATE_NONE = None  # rate/unit metric with a zero denominator (never 0.0)


def _rate(numerator: float, denominator: float) -> float | None:
    """A rate with a zero denominator is None, never a misleading 0.0."""
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _money(value: float) -> float:
    return round(value, 2)


def _ingest_pending_files() -> None:
    """Ingest newly uploaded Sales/Deposit/Login files into the fact tables.

    Idempotent (files ledger guards each file), so grace-period uploads land
    in plays/deposits/logins and the next live report reflects them. Fact
    scope is the window evaluation period, so an upload whose event reaches
    outside the period never changes the report.
    """
    from app.services.deposits import ingest_new_deposit_files
    from app.services.logins import ingest_new_login_files
    from app.services.plays import ingest_new_sales_files

    ingest_new_sales_files()
    ingest_new_deposit_files()
    ingest_new_login_files()


def _fact_boundaries(window: dict) -> tuple[str, str]:
    """Window period as [low, high] in the relabelled fact frame.

    Reinterprets the stored UTC boundary instants as Africa/Lagos wall-clock
    and relabels them like plays/deposits/logins, so lexicographic SQL ranges
    match the stored fact labels exactly.
    """
    start_lagos = dates.as_business(dates.parse_utc_iso(window["start_time"]))
    end_lagos = dates.as_business(dates.parse_utc_iso(window["end_time"]))
    return dates.to_source_fact(start_lagos), dates.to_source_fact(end_lagos)


def _audience_map(window_id: int) -> dict[str, str]:
    """user_id -> assignment for the window's phone-valid audience members."""
    mapping: dict[str, str] = {}
    for row in db.get_audience(window_id):
        if row.get("phone_valid"):
            mapping[str(row["user_id"])] = row["assignment"]
    return mapping


def _in_window_period(run_sms: list[dict], low_utc: str, high_utc: str) -> list[dict]:
    """Run SMS ledger rows whose send instant falls inside the window period."""
    out = []
    for row in run_sms:
        sent = dates.parse_utc_iso(row["sent_at"])
        if low_utc <= sent.isoformat() <= high_utc:
            out.append(row)
    return out


def _attribution(campaign_users, plays, interventions):
    """Run-level attribution for the window's Campaign plays.

    Returns (attributed, run_metrics) where attributed maps a play id/index to
    a run_id and run_metrics carries per-run accepted/contacted/converted/
    attributed aggregations, plus window-level converted/contacted sets.
    """
    plays_by_user: dict[str, list[dict]] = {}
    for p in plays:
        plays_by_user.setdefault(p["user_id"], []).append(p)

    interventions_by_user: dict[str, list[dict]] = {}
    for i in interventions:
        interventions_by_user.setdefault(i["user_id"], []).append(i)

    converted_set: set[str] = set()          # window-level converted users
    contacted_set: set[str] = set()          # window-level contacted users
    converted_run_events: dict[int, int] = {}    # accepted interventions w/ conversion
    converted_run_users: dict[int, set[str]] = {}
    attributed_plays_run: dict[int, int] = {}
    attributed_amount_run: dict[int, float] = {}
    unattributed_plays = 0
    unattributed_amount = 0.0

    for user_id in (u for u in campaign_users if u in plays_by_user):
        user_plays = sorted(plays_by_user[user_id], key=lambda p: p["dt"])
        user_ints = sorted(
            (i for i in interventions_by_user.get(user_id, [])),
            key=lambda i: i["sent_dt"],
        )
        if user_ints:
            contacted_set.add(user_id)
        for p in user_plays:
            # Most recent accepted Welcome SMS preceding this play.
            relevant = None
            for i in user_ints:
                if i["sent_dt"] < p["dt"]:
                    relevant = i
                else:
                    break
            if relevant is None:
                unattributed_plays += 1
                unattributed_amount = _money(unattributed_amount + p["amount"])
                continue
            run_id = relevant["run_id"]
            attributed_plays_run[run_id] = attributed_plays_run.get(run_id, 0) + 1
            attributed_amount_run[run_id] = _money(
                attributed_amount_run.get(run_id, 0.0) + p["amount"]
            )

        # Conversion: the first qualifying play after an accepted intervention.
        for i in user_ints:
            converted = any(p["dt"] > i["sent_dt"] for p in user_plays)
            if converted:
                converted_set.add(user_id)
                run_id = i["run_id"]
                converted_run_events[run_id] = converted_run_events.get(run_id, 0) + 1
                converted_run_users.setdefault(run_id, set()).add(user_id)

    # Accepted interventions are sent to Campaign users only (Control is never
    # a Run target); per-user additions below are already campaign-scoped.
    for i in interventions:
        if i["user_id"] in campaign_users:
            contacted_set.add(i["user_id"])

    return (
        {
            "converted_users": converted_set,
            "contacted_users": contacted_set,
            "converted_run_events": converted_run_events,
            "converted_run_users": converted_run_users,
            "attributed_plays_run": attributed_plays_run,
            "attributed_amount_run": attributed_amount_run,
            "unattributed_plays": unattributed_plays,
            "unattributed_amount": unattributed_amount,
        }
    )


def _group_metrics(
    assignment: str,
    user_ids: set[str],
    plays: list[dict],
    deposits: list[dict],
    logins: list[dict],
    attribution: dict,
    plays_by_user: dict,
) -> dict:
    """The metrics block for one assignment group (Campaign or Control)."""
    targeted = len(user_ids)
    played = {p["user_id"] for p in plays}
    deposited = {d["user_id"] for d in deposits}
    logged_in = {l["user_id"] for l in logins}

    played_in_group = played & user_ids
    deposited_in_group = deposited & user_ids
    logged_in_group = logged_in & user_ids

    total_plays = sum(1 for p in plays if p["user_id"] in user_ids)
    total_sales = _money(sum(p["amount"] for p in plays if p["user_id"] in user_ids))

    # Distinct Lagos play dates (dates only, evaluated in Africa/Lagos).
    play_dates_by_user: dict[str, set[str]] = {}
    for p in plays:
        if p["user_id"] in user_ids:
            play_dates_by_user.setdefault(p["user_id"], set()).add(p["dt"].date().isoformat())
    active_days = len({d for dates_set in play_dates_by_user.values() for d in dates_set})
    multi_day = sum(1 for arr in play_dates_by_user.values() if len(arr) >= 2)

    # Deposit -> play: user deposited (in period) before a qualifying play.
    deposit_then_play = 0
    deposits_by_user: dict[str, list] = {}
    for d in deposits:
        if d["user_id"] in user_ids:
            deposits_by_user.setdefault(d["user_id"], []).append(d["dt"])
    for uid, plist in plays_by_user.items():
        if uid not in user_ids or uid not in deposits_by_user:
            continue
        if any(
            any(dep < p["dt"] for dep in deposits_by_user[uid])
            for p in plist
        ):
            deposit_then_play += 1

    both_login_play = len(logged_in_group & played_in_group)

    single_play = sum(
        1 for uid in played_in_group
        if uid in plays_by_user and len(plays_by_user[uid]) == 1
    )
    multi_play = max(0, len(played_in_group) - single_play)

    is_campaign = assignment == "campaign"
    converted = attribution["converted_users"] & user_ids if is_campaign else set()
    contacted = attribution["contacted_users"] & user_ids if is_campaign else set()

    converted_plays = sum(len(plays_by_user[uid]) for uid in converted if uid in plays_by_user)
    converted_amount = _money(
        sum(p["amount"] for uid in converted for p in plays_by_user.get(uid, []))
    )
    contacted_plays = sum(len(plays_by_user[uid]) for uid in contacted if uid in plays_by_user)
    contacted_amount = _money(
        sum(p["amount"] for uid in contacted for p in plays_by_user.get(uid, []))
    )

    metrics = {
        "assignment": assignment,
        "total_targeted_audience": targeted,
        "total_logged_in_users": len(logged_in_group),
        "total_played_users": len(played_in_group),
        "total_deposited_users": len(deposited_in_group),
        "total_sales": total_sales,
        "total_plays": total_plays,
        "login_rate": _rate(len(logged_in_group), targeted),
        "play_rate": _rate(len(played_in_group), targeted),
        "deposit_rate": _rate(len(deposited_in_group), targeted),
        "arpu": _rate(total_sales, targeted),
        "arppu": _rate(total_sales, len(played_in_group)),
        "plays_per_player": _rate(total_plays, len(played_in_group)),
        "active_days": active_days,
        "active_days_per_player": _rate(active_days, len(played_in_group)),
        "multi_day_players": multi_day,
        "multi_day_player_rate": _rate(multi_day, len(played_in_group)),
        "deposit_to_play_rate": _rate(deposit_then_play, len(deposited_in_group)),
        "login_to_play_rate": _rate(both_login_play, len(logged_in_group)),
        "deposited_no_play_users": len(deposited_in_group - played_in_group),
        "logged_in_no_play_users": len(logged_in_group - played_in_group),
        "single_play_players": single_play,
        "multiple_play_players": multi_play,
    }

    if is_campaign:
        metrics.update(
            {
                "converted_users": len(converted),
                "contacted_users": len(contacted),
                "conversion_rate": _rate(len(converted), len(contacted)),
                "avg_plays_per_converted": _rate(converted_plays, len(converted)),
                "avg_plays_per_contacted": _rate(contacted_plays, len(contacted)),
                "avg_amount_per_converted": _rate(converted_amount, len(converted)),
                "avg_amount_per_contacted": _rate(contacted_amount, len(contacted)),
            }
        )
    else:
        # Control has no Campaign SMS attribution model - explicitly N/A.
        metrics.update(
            {
                "converted_users": None,
                "contacted_users": None,
                "conversion_rate": None,
                "avg_plays_per_converted": None,
                "avg_plays_per_contacted": None,
                "avg_amount_per_converted": None,
                "avg_amount_per_contacted": None,
                "conversion_not_applicable": True,
            }
        )
    return metrics


def _game_stats(user_ids: set[str], plays: list[dict]) -> list[dict]:
    """Per-game aggregates for one group: plays, customers, amount, avg."""
    by_game: dict[str, dict] = {}
    for p in plays:
        if p["user_id"] not in user_ids:
            continue
        g = by_game.setdefault(
            p["game_name"],
            {"game_name": p["game_name"], "plays": 0, "customers": set(), "amount": 0.0},
        )
        g["plays"] += 1
        g["customers"].add(p["user_id"])
        g["amount"] = _money(g["amount"] + p["amount"])
    rows = []
    for g in by_game.values():
        rows.append(
            {
                "game_name": g["game_name"],
                "plays": g["plays"],
                "customers": len(g["customers"]),
                "amount": g["amount"],
                "avg_amount": _rate(g["amount"], g["plays"]),
            }
        )
    rows.sort(key=lambda r: (-r["plays"], -r["amount"], r["game_name"]))
    return rows


def build_report(window_id: int, *, ingest: bool = True) -> dict:
    """Compute the Window report from persisted facts.

    `ingest=True` (default) also syncs newly uploaded Sales/Deposit/Login files
    into the fact tables first, so a grace-period upload updates a live report.
    Pass ingest=False to compute purely from already-persisted state.
    """
    window = db.get_window(window_id)
    if window is None:
        raise db.WindowStateError(f"Campaign Window #{window_id} not found.")

    if ingest:
        _ingest_pending_files()

    low, high = _fact_boundaries(window)
    low_utc = window["start_time"]
    high_utc = window["end_time"]

    audience = _audience_map(window_id)
    campaign_ids = {uid for uid, a in audience.items() if a == "campaign"}
    control_ids = {uid for uid, a in audience.items() if a == "control"}

    plays = wr.select_window_plays(window_id, low, high)
    deposits = wr.select_window_deposits(window_id, low, high)
    logins = wr.select_window_logins(window_id, low, high)

    for p in plays:
        p["dt"] = dates.read_source_fact(p["played_at"])
    for d in deposits:
        d["dt"] = dates.read_source_fact(d["deposited_at"])
    for l in logins:
        l["dt"] = dates.read_source_fact(l["logged_at"])

    runs = db.list_runs(window_id)
    run_ids = [r["id"] for r in runs]
    run_sms = wr.select_run_welcome_sms(run_ids)
    accepted = _in_window_period(
        [row for row in run_sms if row["status"] in ACCEPTED_SMS_STATUSES],
        low_utc,
        high_utc,
    )
    for i in accepted:
        i["sent_dt"] = dates.as_business(dates.parse_utc_iso(i["sent_at"]))
        i["run_id"] = int(str(i["cycle_id"]).split(":", 1)[1])

    plays_by_user: dict[str, list[dict]] = {}
    for p in plays:
        plays_by_user.setdefault(p["user_id"], []).append(p)

    attribution = _attribution(campaign_ids, plays, accepted)
    runs_by_id = {r["id"]: r for r in runs}

    # Per-run summary (attribution context for the full window).
    run_summaries: list[dict] = []
    for run in runs:
        run_id = run["id"]
        target = {m["user_id"] for m in db.get_run_target(window_id, run_id)}
        cycle = f"run:{run_id}"
        cycle_rows = [row for row in run_sms if row["cycle_id"] == cycle and row["sent_at"] >= low_utc and row["sent_at"] <= high_utc]
        accepted_count = sum(1 for r in cycle_rows if r["status"] in ACCEPTED_SMS_STATUSES)
        failed_count = sum(1 for r in cycle_rows if r["status"] == "failed")
        contacted = {r["user_id"] for r in cycle_rows if r["status"] in ACCEPTED_SMS_STATUSES}
        converted = attribution["converted_run_users"].get(run_id, set())
        # Follow-on activity: plays by the run's target users inside the window.
        target_plays = [p for p in plays if p["user_id"] in target]
        run_summaries.append(
            {
                "run_id": run_id,
                "status": run["status"],
                "started_at": run["started_at"],
                "ended_at": run.get("ended_at"),
                "stop_reason": run.get("stop_reason"),
                "note": run.get("note"),
                "targeted_users": len(target),
                "accepted_interventions": accepted_count,
                "failed_sends": failed_count,
                "contacted_users": len(contacted),
                "converted_users": len(converted),
                "conversion_events": attribution["converted_run_events"].get(run_id, 0),
                "attributed_plays": attribution["attributed_plays_run"].get(run_id, 0),
                "attributed_amount": _money(attribution["attributed_amount_run"].get(run_id, 0.0)),
                "plays_by_target_users": len(target_plays),
                "amount_by_target_users": _money(sum(p["amount"] for p in target_plays)),
            }
        )

    campaign_metrics = _group_metrics(
        "campaign", campaign_ids, plays, deposits, logins, attribution, plays_by_user
    )
    control_metrics = _group_metrics(
        "control", control_ids, plays, deposits, logins, attribution, plays_by_user
    )

    period_start_lagos = dates.as_business(dates.parse_utc_iso(window["start_time"]))
    period_end_lagos = dates.as_business(dates.parse_utc_iso(window["end_time"]))

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "window": {
            "id": window["id"],
            "name": window.get("name"),
            "status": window["status"],
            "start_time": window["start_time"],
            "end_time": window["end_time"],
            "finalization_deadline": window["finalization_deadline"],
            "finalized_at": window.get("finalized_at"),
            "ended_at": window.get("ended_at"),
            "business_timezone": BUSINESS_TIMEZONE,
            "assignment_method": window["assignment_method"],
            "control_percentage": window["control_percentage"],
            "eligible_count": window["eligible_count"],
        },
        "evaluation_period": {
            "start": dates.to_utc_iso(period_start_lagos),
            "end": dates.to_utc_iso(period_end_lagos),
            "timezone": BUSINESS_TIMEZONE,
            "fact_start": low,
            "fact_end": high,
            "description": (
                "All Window activity metrics use the evaluation period "
                f"[{period_start_lagos.isoformat()}, {period_end_lagos.isoformat()}] "
                "Africa/Lagos, and only activity whose source timestamp falls inside "
                "it (an upload arriving during grace never extends the period)."
            ),
        },
        "audience": {
            "campaignable_total": len(campaign_ids) + len(control_ids),
            "campaign": len(campaign_ids),
            "control": len(control_ids),
        },
        "groups": {"campaign": campaign_metrics, "control": control_metrics},
        "games": {
            "campaign": _game_stats(campaign_ids, plays),
            "control": _game_stats(control_ids, plays),
        },
        "runs": run_summaries,
        "attribution": {
            "model": (
                "For a Campaign user, the relevant intervention is the most recent "
                "accepted Welcome SMS (of this window's Runs) preceding the play. The "
                "first qualifying play after an accepted intervention is the "
                "conversion event; later plays are activity, not extra conversions. "
                "Control users have no Campaign SMS conversion model and never "
                "acquire Campaign conversions; their conversion fields are reported "
                "as not applicable (null)."
            ),
            "accepted_interventions": len(accepted),
            "contacted_users_total": len(attribution["contacted_users"]),
            "converted_users_total": len(attribution["converted_users"]),
            "conversion_events": sum(attribution["converted_run_events"].values()),
            "attributed_plays": sum(attribution["attributed_plays_run"].values()),
            "attributed_amount": _money(sum(attribution["attributed_amount_run"].values())),
            "unattributed_plays": attribution["unattributed_plays"],
            "unattributed_amount": _money(attribution["unattributed_amount"]),
        },
        "computed_at": dates.to_utc_iso(dates.now_business()),
    }


def freeze_report(window_id: int, report: dict) -> dict:
    """Persist a computed report as the window's frozen snapshot."""
    wr.save_frozen_report(window_id, report)
    return report


def get_report(window_id: int) -> dict:
    """The report for a window - frozen snapshot if finalized, else live.

    For finalized windows this serves the frozen snapshot and never recomputes,
    so the report cannot change after finalization (enforcement lives behind
    the window_reports persistence boundary). If a finalized window somehow has
    no snapshot yet, one is computed from persisted facts and frozen now.
    """
    window = db.get_window(window_id)
    if window is None:
        raise db.WindowStateError(f"Campaign Window #{window_id} not found.")

    if window["status"] == "finalized":
        frozen = wr.get_frozen_report(window_id)
        if frozen and frozen.get("schema_version") == REPORT_SCHEMA_VERSION:
            return frozen
        report = build_report(window_id, ingest=False)
        # A finalized window must never re-derive from freshly uploaded files:
        # freeze what the persisted facts already contain.
        wr.save_frozen_report(window_id, report)
        return report

    return build_report(window_id, ingest=True)