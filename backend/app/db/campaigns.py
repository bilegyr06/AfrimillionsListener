"""Welcome campaign data module.

Owns the welcome_campaigns, welcome_opportunities and welcome_interventions
tables plus the campaign-scoped statistics aggregations (audience, delivery
funnel, response and the plays-backed activity groups). Consumers:
app.services.campaigns and app.services.statistics.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db.database import get_connection


def get_welcome_sms_state() -> dict[str, dict]:
    """Welcome campaign state per user, derived from successful interventions.

    Returns {user_id: {"count": n, "last_sent_at": iso}}. Only interventions
    are counted here — they exist solely for Termii-accepted sends, so failed
    or deferred attempts never consume a cooldown or cap slot. The cap is
    cumulative across all campaigns and never resets.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT user_id, COUNT(*) AS count, MAX(sent_at) AS last_sent_at
        FROM welcome_interventions
        GROUP BY user_id
        """
    ).fetchall()
    conn.close()
    return {str(row["user_id"]): dict(row) for row in rows}


# ---------------------------------------------------------------------------
# Welcome campaigns
# ---------------------------------------------------------------------------

def create_campaign(name: str | None = None, config: str | None = None) -> dict:
    """Start a new campaign, closing any currently active one at its end.

    Starting a campaign is the only way a campaign window is opened; campaigns
    are never auto-created. `config` is an optional JSON snapshot of the
    operator settings in force at start time (kept for audit; the engine reads
    live settings). Returns the new campaign plus the campaign that was
    closed by this transition (if any).
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    closed = None
    active = conn.execute(
        "SELECT * FROM welcome_campaigns WHERE status = 'active' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if active:
        conn.execute(
            "UPDATE welcome_campaigns SET ended_at = ?, status = 'closed' WHERE id = ?",
            (now, active["id"]),
        )
        closed = dict(active)

    cursor = conn.execute(
        "INSERT INTO welcome_campaigns (name, started_at, status, created_at, config) "
        "VALUES (?, ?, 'active', ?, ?)",
        (name, now, now, config),
    )
    new = dict(
        conn.execute("SELECT * FROM welcome_campaigns WHERE id = ?", (cursor.lastrowid,)).fetchone()
    )
    conn.commit()
    conn.close()
    return {"campaign": new, "closed_campaign": closed}


def close_active_campaign() -> dict | None:
    """Close the active campaign (if any). Returns the closed campaign or None."""
    conn = get_connection()
    active = conn.execute(
        "SELECT * FROM welcome_campaigns WHERE status = 'active' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not active:
        conn.close()
        return None
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE welcome_campaigns SET ended_at = ?, status = 'closed' WHERE id = ?",
        (now, active["id"]),
    )
    closed = dict(
        conn.execute("SELECT * FROM welcome_campaigns WHERE id = ?", (active["id"],)).fetchone()
    )
    conn.commit()
    conn.close()
    return closed


def get_active_campaign() -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM welcome_campaigns WHERE status = 'active' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_campaign(campaign_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM welcome_campaigns WHERE id = ?", (campaign_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_campaigns(limit: int = 50) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM welcome_campaigns ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Welcome opportunities (one per login within a campaign)
# ---------------------------------------------------------------------------

def upsert_opportunities(records: list[dict]):
    """Insert login opportunities for a campaign, ignoring duplicates.

    records: list of {"campaign_id", "user_id", "first_name", "phone_raw",
                      "phone_normalized", "login_at", "eval_delay_hours"}
    Idempotency key: (campaign_id, user_id, login_at).
    """
    if not records:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.executemany(
        """
        INSERT OR IGNORE INTO welcome_opportunities
            (campaign_id, user_id, first_name, phone_raw, phone_normalized,
             login_at, eval_delay_hours, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)
        """,
        [
            (
                r["campaign_id"],
                r["user_id"],
                r.get("first_name"),
                r.get("phone_raw"),
                r.get("phone_normalized"),
                r["login_at"],
                r["eval_delay_hours"],
                now,
                now,
            )
            for r in records
        ],
    )
    conn.commit()
    conn.close()


def get_open_opportunities(campaign_id: int) -> list[dict]:
    """Opportunities still awaiting evaluation (status 'created')."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM welcome_opportunities
        WHERE campaign_id = ? AND status = 'created'
        ORDER BY login_at
        """,
        (campaign_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_opportunity_status(opportunity_id: int, status: str):
    conn = get_connection()
    conn.execute(
        "UPDATE welcome_opportunities SET status = ?, updated_at = ? WHERE id = ?",
        (status, datetime.now(timezone.utc).isoformat(), opportunity_id),
    )
    conn.commit()
    conn.close()


def mark_opportunities_expired(campaign_id: int):
    """Expire every un-evaluated opportunity of a closed campaign.

    A login whose evaluation never ran before the campaign ended cannot be
    carried into the next campaign, so it becomes terminal 'expired'.
    """
    conn = get_connection()
    conn.execute(
        "UPDATE welcome_opportunities SET status = 'expired', updated_at = ? "
        "WHERE campaign_id = ? AND status = 'created'",
        (datetime.now(timezone.utc).isoformat(), campaign_id),
    )
    conn.commit()
    conn.close()


def count_opportunities(campaign_id: int) -> dict:
    """Opportunity breakdown for a campaign: {status: count, total: n}."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM welcome_opportunities "
        "WHERE campaign_id = ? GROUP BY status",
        (campaign_id,),
    ).fetchall()
    conn.close()
    counts = {str(row["status"]): row["n"] for row in rows}
    return {**counts, "total": sum(counts.values())}


# ---------------------------------------------------------------------------
# Welcome interventions (one per successful send)
# ---------------------------------------------------------------------------

def create_intervention(
    opportunity_id: int,
    campaign_id: int,
    user_id: str,
    login_at: str,
    sent_at: str,
    message_id: str | None,
) -> int:
    """Record a successful send. One intervention per opportunity.

    The unique opportunity_id guarantees a login is never rewarded twice.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO welcome_interventions
            (opportunity_id, campaign_id, user_id, login_at, sent_at, message_id,
             status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)
        """,
        (opportunity_id, campaign_id, user_id, login_at, sent_at, message_id, now, now),
    )
    conn.commit()
    conn.close()
    return cursor.lastrowid


def get_open_interventions(campaign_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM welcome_interventions
        WHERE campaign_id = ? AND status = 'open'
        ORDER BY id
        """,
        (campaign_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def record_intervention_response(intervention_id: int, play_at: str, response_seconds: float):
    conn = get_connection()
    conn.execute(
        "UPDATE welcome_interventions SET status = 'responded', play_at = ?, "
        "response_seconds = ?, updated_at = ? WHERE id = ?",
        (play_at, response_seconds, datetime.now(timezone.utc).isoformat(), intervention_id),
    )
    conn.commit()
    conn.close()


def close_open_interventions(campaign_id: int):
    """Mark every still-open intervention of a campaign as 'no_response'."""
    conn = get_connection()
    conn.execute(
        "UPDATE welcome_interventions SET status = 'no_response', updated_at = ? "
        "WHERE campaign_id = ? AND status = 'open'",
        (datetime.now(timezone.utc).isoformat(), campaign_id),
    )
    conn.commit()
    conn.close()


def get_cap_usage(user_id: str) -> int:
    """Successful welcome sends for a user, cumulative across campaigns."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM welcome_interventions WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["n"]


def get_last_welcome_sent(user_id: str) -> str | None:
    """ISO sent_at of the user's most recent successful welcome send, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT MAX(sent_at) AS last_sent_at FROM welcome_interventions WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["last_sent_at"]


def count_interventions(campaign_id: int) -> dict:
    """Intervention breakdown for a campaign: {status: count, total: n}."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM welcome_interventions "
        "WHERE campaign_id = ? GROUP BY status",
        (campaign_id,),
    ).fetchall()
    conn.close()
    counts = {str(row["status"]): row["n"] for row in rows}
    return {**counts, "total": sum(counts.values())}


# ---------------------------------------------------------------------------
# Campaign dashboard aggregates
# ---------------------------------------------------------------------------

def get_campaign_stats(campaign_id: int) -> dict:
    """Aggregate dashboard numbers for a campaign."""
    opportunities = count_opportunities(campaign_id)
    interventions = count_interventions(campaign_id)
    conn = get_connection()
    row = conn.execute(
        "SELECT AVG(response_seconds) AS avg_response_seconds, "
        "COUNT(*) AS total FROM welcome_interventions "
        "WHERE campaign_id = ? AND response_seconds IS NOT NULL",
        (campaign_id,),
    ).fetchone()
    conn.close()

    sent = interventions.get("total", 0)
    responded = interventions.get("responded", 0)
    return {
        "campaign_id": campaign_id,
        "opportunities": opportunities,
        "interventions": interventions,
        "response_rate": round(responded / sent, 4) if sent else None,
        "avg_response_seconds": row["avg_response_seconds"],
    }


def list_campaign_customers(campaign_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
    """Paged per-customer view of a campaign (opportunities + their outcome).

    Each row adds the delivery status of that accepted SMS and the customer's
    qualifying activity inside the campaign attribution window (attributed to
    the window the same way conversion is): qualifying play count, attributed
    play amount, and distinct games played. The play sub-aggregate is computed
    once per campaign, so pagination never rescans the plays table per row.
    """
    conn = get_connection()
    end = conn.execute(
        "SELECT ended_at FROM welcome_campaigns WHERE id = ?", (campaign_id,)
    ).fetchone()
    if end is None:
        conn.close()
        return []
    window_end = end["ended_at"] or datetime.now(timezone.utc).isoformat()
    rows = conn.execute(
        """
        WITH per_user_play AS (
            SELECT p.user_id,
                   COUNT(p.id) AS qualifying_plays,
                   COALESCE(SUM(p.amount), 0) AS attributed_amount,
                   COUNT(DISTINCT p.game_name) AS games_played
            FROM plays p
            JOIN (SELECT user_id, MIN(sent_at) AS first_sent_at
                  FROM welcome_interventions
                  WHERE campaign_id = ? GROUP BY user_id) c ON c.user_id = p.user_id
            WHERE p.played_at > c.first_sent_at AND p.played_at <= ?
            GROUP BY p.user_id
        )
        SELECT o.user_id, o.first_name, o.phone_raw, o.phone_normalized,
               o.login_at, o.status AS opportunity_status,
               i.status AS intervention_status, i.sent_at, i.play_at,
               i.response_seconds,
               s.status AS delivery_status,
               COALESCE(pu.qualifying_plays, 0) AS qualifying_plays,
               COALESCE(pu.attributed_amount, 0) AS attributed_amount,
               COALESCE(pu.games_played, 0) AS games_played
        FROM welcome_opportunities o
        LEFT JOIN welcome_interventions i ON i.opportunity_id = o.id
        LEFT JOIN sms_log s ON s.message_id = i.message_id
        LEFT JOIN per_user_play pu ON pu.user_id = o.user_id
        WHERE o.campaign_id = ?
        ORDER BY o.login_at DESC
        LIMIT ? OFFSET ?
        """,
        (campaign_id, window_end, campaign_id, limit, offset),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def count_campaign_customers(campaign_id: int) -> int:
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM welcome_opportunities WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchone()
    conn.close()
    return row["n"]


# ---------------------------------------------------------------------------
# Campaign statistics (reporting contract, see app.services.statistics)
# ---------------------------------------------------------------------------
#
# These functions only aggregate; all presentation framing lives in
# app.services.statistics. They intentionally never fetch per-customer rows
# into the caller.

def get_campaign_audience(campaign_id: int) -> dict:
    """Opportunity counts per status plus distinct customers for a campaign.

    `opportunities` counts welcome_opportunities rows (one login inside the
    window); `unique_customers` counts distinct user_id across them.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS rows, COUNT(DISTINCT user_id) AS users
        FROM welcome_opportunities
        WHERE campaign_id = ?
        GROUP BY status
        """,
        (campaign_id,),
    ).fetchall()
    total = conn.execute(
        "SELECT COUNT(DISTINCT user_id) AS users "
        "FROM welcome_opportunities WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchone()["users"]
    conn.close()
    statuses = {row["status"]: {"rows": row["rows"], "users": row["users"]} for row in rows}
    return {
        "opportunities": sum(s["rows"] for s in statuses.values()),
        "unique_customers": total,
        "statuses": statuses,
    }


def get_campaign_intervention_counts(campaign_id: int) -> dict:
    """Accepted-send (intervention) counts per status plus distinct customers.

    `contacted_customers` = distinct users with an accepted Welcome SMS;
    `converted_customers` = distinct users with a responded intervention;
    `conversion_events` = responded interventions (a customer's first
    qualifying play; later plays are not stored yet); `pending_outcome` =
    interventions still awaiting a response.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS rows, COUNT(DISTINCT user_id) AS users
        FROM welcome_interventions
        WHERE campaign_id = ?
        GROUP BY status
        """,
        (campaign_id,),
    ).fetchall()
    contacted = conn.execute(
        "SELECT COUNT(DISTINCT user_id) AS users "
        "FROM welcome_interventions WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchone()["users"]
    conn.close()
    statuses = {row["status"]: {"rows": row["rows"], "users": row["users"]} for row in rows}
    responded = statuses.get("responded", {"rows": 0, "users": 0})
    return {
        "accepted": sum(s["rows"] for s in statuses.values()),
        "contacted_customers": contacted,
        "converted_customers": responded["users"],
        "conversion_events": responded["rows"],
        "pending_outcome": statuses.get("open", {}).get("rows", 0),
        "statuses": statuses,
    }


def get_campaign_sms_funnel(campaign_id: int) -> dict:
    """Termii delivery outcomes for a campaign's accepted Welcome SMS.

    Acceptance lives in welcome_interventions; provider delivery status lives
    in sms_log. The two are joined on message_id (both set from the same Termii
    dispatch). `unmatched` counts accepted sends with no sms_log row.
    """
    conn = get_connection()
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS accepted,
            COUNT(DISTINCT i.user_id) AS contacted_customers,
            COALESCE(SUM(CASE WHEN s.id IS NULL THEN 1 ELSE 0 END), 0) AS unmatched,
            COALESCE(SUM(CASE WHEN s.status = 'delivered' THEN 1 ELSE 0 END), 0) AS delivered,
            COALESCE(SUM(CASE WHEN s.status = 'failed' THEN 1 ELSE 0 END), 0) AS failed,
            COALESCE(SUM(CASE WHEN s.status = 'rejected' THEN 1 ELSE 0 END), 0) AS rejected,
            COALESCE(SUM(CASE WHEN s.status = 'expired' THEN 1 ELSE 0 END), 0) AS expired,
            COALESCE(SUM(CASE WHEN s.status = 'dnd' THEN 1 ELSE 0 END), 0) AS dnd,
            COALESCE(SUM(CASE WHEN s.status = 'deferred' THEN 1 ELSE 0 END), 0) AS deferred,
            COALESCE(SUM(CASE WHEN s.status = 'sent' THEN 1 ELSE 0 END), 0) AS sent,
            COALESCE(SUM(s.cost), 0) AS cost
        FROM welcome_interventions i
        LEFT JOIN sms_log s ON s.message_id = i.message_id
        WHERE i.campaign_id = ?
        """,
        (campaign_id,),
    ).fetchone()
    conn.close()
    return dict(row)


def get_campaign_response_plays(campaign_id: int) -> list[dict]:
    """First-qualifying-play records for a campaign's converted customers.

    Returns rows with `play_at` (ISO timestamp or None) and `response_seconds`
    (float or None), ordered by response time.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT play_at, response_seconds
        FROM welcome_interventions
        WHERE campaign_id = ? AND status = 'responded'
        ORDER BY response_seconds
        """,
        (campaign_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_campaigns_audience() -> dict[int, dict]:
    """Audience aggregates for every campaign, keyed by campaign_id."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT campaign_id, COUNT(*) AS rows, COUNT(DISTINCT user_id) AS users "
        "FROM welcome_opportunities GROUP BY campaign_id"
    ).fetchall()
    conn.close()
    return {
        row["campaign_id"]: {
            "opportunities": row["rows"],
            "unique_customers": row["users"],
        }
        for row in rows
    }


def get_campaigns_interventions() -> dict[int, dict]:
    """Intervention aggregates for every campaign, keyed by campaign_id."""
    conn = get_connection()
    per_status = conn.execute(
        "SELECT campaign_id, status, COUNT(*) AS rows, COUNT(DISTINCT user_id) AS users "
        "FROM welcome_interventions GROUP BY campaign_id, status"
    ).fetchall()
    contacted = conn.execute(
        "SELECT campaign_id, COUNT(DISTINCT user_id) AS users "
        "FROM welcome_interventions GROUP BY campaign_id"
    ).fetchall()
    avg = conn.execute(
        "SELECT campaign_id, AVG(response_seconds) AS avg_response_seconds "
        "FROM welcome_interventions "
        "WHERE status = 'responded' AND response_seconds IS NOT NULL "
        "GROUP BY campaign_id"
    ).fetchall()
    conn.close()
    out: dict[int, dict] = {}
    for row in per_status:
        d = out.setdefault(
            row["campaign_id"],
            {
                "accepted": 0,
                "contacted_customers": 0,
                "converted_customers": 0,
                "conversion_events": 0,
                "still_open": 0,
                "avg_response_seconds": None,
            },
        )
        d["accepted"] += row["rows"]
        if row["status"] == "responded":
            d["conversion_events"] += row["rows"]
            d["converted_customers"] += row["users"]
        elif row["status"] == "open":
            d["still_open"] += row["rows"]
    for row in contacted:
        d = out.setdefault(row["campaign_id"], {})
        d["contacted_customers"] = row["users"]
    for row in avg:
        d = out.setdefault(row["campaign_id"], {})
        d["avg_response_seconds"] = (
            round(row["avg_response_seconds"], 1)
            if row["avg_response_seconds"] is not None
            else None
        )
    return out


def get_campaigns_sms_funnel() -> dict[int, dict]:
    """Delivery-funnel aggregates for every campaign, keyed by campaign_id."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT i.campaign_id,
               COUNT(*) AS accepted,
               COUNT(DISTINCT i.user_id) AS contacted_customers,
               COALESCE(SUM(CASE WHEN s.status = 'delivered' THEN 1 ELSE 0 END), 0) AS delivered,
               COALESCE(SUM(CASE WHEN s.status = 'deferred' THEN 1 ELSE 0 END), 0) AS deferred,
               COALESCE(SUM(s.cost), 0) AS cost
        FROM welcome_interventions i
        LEFT JOIN sms_log s ON s.message_id = i.message_id
        GROUP BY i.campaign_id
        """
    ).fetchall()
    conn.close()
    return {
        row["campaign_id"]: {
            "accepted": row["accepted"],
            "contacted_customers": row["contacted_customers"],
            "delivered": row["delivered"],
            "deferred": row["deferred"],
            "cost": round(row["cost"], 2),
        }
        for row in rows
    }


def get_converted_customers(campaign_id: int) -> set[str]:
    """Distinct customers with a responded intervention in the campaign."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT user_id FROM welcome_interventions "
        "WHERE campaign_id = ? AND status = 'responded'",
        (campaign_id,),
    ).fetchall()
    conn.close()
    return {str(row["user_id"]) for row in rows}


def get_campaign_play_stats(campaign_id: int, window_end: str) -> list[dict]:
    """Per-customer qualifying-play counts and amounts for a campaign.

    A play qualifies when it falls inside the campaign's attribution window:
    strictly after the customer's first accepted Welcome SMS and at or before
    `window_end`. Rows are grouped per customer so aggregation over converted /
    contacted splits stays a constant-size operation.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        WITH contact AS (
            SELECT user_id, MIN(sent_at) AS first_sent_at
            FROM welcome_interventions
            WHERE campaign_id = ?
            GROUP BY user_id
        )
        SELECT p.user_id,
               COUNT(*) AS play_count,
               COALESCE(SUM(p.amount), 0) AS amount
        FROM plays p
        JOIN contact c ON c.user_id = p.user_id
        WHERE p.played_at > c.first_sent_at AND p.played_at <= ?
        GROUP BY p.user_id
        """,
        (campaign_id, window_end),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_campaign_game_stats(campaign_id: int, window_end: str, limit: int = 10) -> list[dict]:
    """Qualifying activity grouped by game, most-played first (bounded)."""
    conn = get_connection()
    rows = conn.execute(
        """
        WITH contact AS (
            SELECT user_id, MIN(sent_at) AS first_sent_at
            FROM welcome_interventions
            WHERE campaign_id = ?
            GROUP BY user_id
        )
        SELECT p.game_name,
               COUNT(*) AS plays,
               COUNT(DISTINCT p.user_id) AS customers,
               COALESCE(SUM(p.amount), 0) AS amount,
               COALESCE(AVG(p.amount), 0) AS avg_amount
        FROM plays p
        JOIN contact c ON c.user_id = p.user_id
        WHERE p.played_at > c.first_sent_at AND p.played_at <= ?
        GROUP BY p.game_name
        ORDER BY plays DESC, amount DESC, game_name
        LIMIT ?
        """,
        (campaign_id, window_end, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_campaign_game_count(campaign_id: int, window_end: str) -> int:
    """Distinct games with at least one qualifying play in the window."""
    conn = get_connection()
    row = conn.execute(
        """
        WITH contact AS (
            SELECT user_id, MIN(sent_at) AS first_sent_at
            FROM welcome_interventions WHERE campaign_id = ? GROUP BY user_id
        )
        SELECT COUNT(DISTINCT p.game_name) AS game_count
        FROM plays p JOIN contact c ON c.user_id = p.user_id
        WHERE p.played_at > c.first_sent_at AND p.played_at <= ?
        """,
        (campaign_id, window_end),
    ).fetchone()
    conn.close()
    return row["game_count"]


def get_campaign_play_edges(campaign_id: int, window_end: str) -> dict:
    """Before/outside activity of a campaign's contacted customers.

    `before_sms` counts plays that did NOT follow a campaign Welcome SMS
    (played_at <= first sent); `after_end` counts plays beyond the attribution
    window. These are contextual, never conversion data.
    """
    conn = get_connection()
    row = conn.execute(
        """
        WITH contact AS (
            SELECT user_id, MIN(sent_at) AS first_sent_at
            FROM welcome_interventions
            WHERE campaign_id = ?
            GROUP BY user_id
        )
        SELECT
            (SELECT COUNT(*) FROM plays p JOIN contact c ON c.user_id = p.user_id
             WHERE p.played_at <= c.first_sent_at) AS before_sms,
            (SELECT COUNT(*) FROM plays p JOIN contact c ON c.user_id = p.user_id
             WHERE p.played_at > ?) AS after_end
        """,
        (campaign_id, window_end),
    ).fetchone()
    conn.close()
    return dict(row)


def get_campaigns_play_summary(window_end: str) -> dict[int, dict]:
    """Qualifying-activity aggregates for every campaign, keyed by campaign_id.

    `window_end` is the "now" instant used for still-active campaigns (closed
    campaigns scope to their own ended_at).
    """
    conn = get_connection()
    rows = conn.execute(
        """
        WITH contact AS (
            SELECT campaign_id, user_id, MIN(sent_at) AS first_sent_at
            FROM welcome_interventions
            GROUP BY campaign_id, user_id
        )
        SELECT c.id AS campaign_id,
               COUNT(p.id) AS qualifying_plays,
               COUNT(DISTINCT p.user_id) AS players,
               COALESCE(SUM(p.amount), 0) AS total_amount
        FROM welcome_campaigns c
        LEFT JOIN contact k ON k.campaign_id = c.id
        LEFT JOIN plays p ON p.user_id = k.user_id
            AND p.played_at > k.first_sent_at
            AND p.played_at <= COALESCE(c.ended_at, ?)
        GROUP BY c.id
        """,
        (window_end,),
    ).fetchall()
    conn.close()
    return {
        row["campaign_id"]: {
            "qualifying_plays": row["qualifying_plays"],
            "players": row["players"],
            "total_amount": round(row["total_amount"], 2),
        }
        for row in rows
    }