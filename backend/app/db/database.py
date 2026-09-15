import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.core.config import settings


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notified_users (
            user_id TEXT PRIMARY KEY,
            phone TEXT NOT NULL,
            notification_count INTEGER DEFAULT 1,
            last_notified_at TEXT NOT NULL,
            next_available_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            phone TEXT NOT NULL,
            is_new INTEGER NOT NULL DEFAULT 1,
            kind TEXT NOT NULL DEFAULT 'inactive',
            last_login_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS welcome_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS welcome_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            first_name TEXT,
            phone_raw TEXT,
            phone_normalized TEXT,
            login_at TEXT NOT NULL,
            eval_delay_hours REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'created',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (campaign_id, user_id, login_at)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS welcome_interventions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opportunity_id INTEGER NOT NULL UNIQUE,
            campaign_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            login_at TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            message_id TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            play_at TEXT,
            response_seconds REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sms_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            phone TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'sent',
            cost REAL DEFAULT 0,
            balance_after REAL,
            cycle_id TEXT,
            sent_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wallet_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            balance REAL NOT NULL,
            currency TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            dataset TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            uploaded_by TEXT,
            status TEXT NOT NULL DEFAULT 'received',
            row_count INTEGER,
            parse_error TEXT,
            processed_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    _ensure_column(conn, "pending_queue", "kind", "TEXT NOT NULL DEFAULT 'inactive'")
    _ensure_column(conn, "pending_queue", "last_login_at", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "welcome_campaigns", "config", "TEXT")

    # Legacy welcome tables are no longer sources of truth; the state now lives
    # in campaigns/opportunities/interventions. Dropping them is the migration.
    conn.execute("DROP TABLE IF EXISTS welcome_sent")
    conn.execute("DROP TABLE IF EXISTS welcome_analytics")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_opps_campaign_status "
        "ON welcome_opportunities (campaign_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_interventions_campaign "
        "ON welcome_interventions (campaign_id, status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_interventions_user "
        "ON welcome_interventions (user_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_interventions_opp "
        "ON welcome_interventions (opportunity_id)"
    )
    conn.commit()
    conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def get_all_notified() -> dict[str, dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM notified_users").fetchall()
    conn.close()
    return {str(row["user_id"]): dict(row) for row in rows}


def get_notified_user(user_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM notified_users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_notified_records(records: list[dict], cooldown_hours: int):
    """Batch insert-or-update notified users.

    records: list of {"user_id", "phone", "is_new"}
    """
    now = datetime.now(timezone.utc)
    next_available = now + timedelta(hours=cooldown_hours)

    conn = get_connection()
    for rec in records:
        if rec["is_new"]:
            conn.execute(
                """
                INSERT OR REPLACE INTO notified_users
                    (user_id, phone, notification_count, last_notified_at, next_available_at)
                VALUES (?, ?, 1, ?, ?)
                """,
                (
                    rec["user_id"],
                    rec["phone"],
                    now.isoformat(),
                    next_available.isoformat(),
                ),
            )
        else:
            conn.execute(
                """
                UPDATE notified_users
                SET notification_count = notification_count + 1,
                    last_notified_at = ?,
                    next_available_at = ?
                WHERE user_id = ?
                """,
                (now.isoformat(), next_available.isoformat(), rec["user_id"]),
            )
    conn.commit()
    conn.close()


def insert_notified_user(user_id: str, phone: str, cooldown_hours: int):
    now = datetime.now(timezone.utc)
    next_available = now + timedelta(hours=cooldown_hours)
    conn = get_connection()
    conn.execute(
        """
        INSERT OR REPLACE INTO notified_users
            (user_id, phone, notification_count, last_notified_at, next_available_at)
        VALUES (?, ?, 1, ?, ?)
        """,
        (user_id, phone, now.isoformat(), next_available.isoformat()),
    )
    conn.commit()
    conn.close()


def update_notified_user(user_id: str, cooldown_hours: int):
    now = datetime.now(timezone.utc)
    next_available = now + timedelta(hours=cooldown_hours)
    conn = get_connection()
    conn.execute(
        """
        UPDATE notified_users
        SET notification_count = notification_count + 1,
            last_notified_at = ?,
            next_available_at = ?
        WHERE user_id = ?
        """,
        (now.isoformat(), next_available.isoformat(), user_id),
    )
    conn.commit()
    conn.close()


def reset_user(user_id: str):
    conn = get_connection()
    conn.execute("DELETE FROM notified_users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_pending(kind: str) -> list[dict]:
    """Return queued users of a given kind awaiting a future cycle, oldest first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT user_id, phone, is_new, last_login_at FROM pending_queue WHERE kind = ? ORDER BY id",
        (kind,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def add_pending(records: list[dict], kind: str):
    """Queue users whose messages were deferred by the cycle cutoff.

    records: list of {"user_id", "phone", "is_new", "last_login_at"};
    kind: "welcome" or "inactive".
    """
    if not records:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.executemany(
        """
        INSERT INTO pending_queue (user_id, phone, is_new, kind, last_login_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (r["user_id"], r["phone"], 1 if r["is_new"] else 0, kind, r.get("last_login_at", ""), now)
            for r in records
        ],
    )
    conn.commit()
    conn.close()


def clear_pending(kind: str):
    conn = get_connection()
    conn.execute("DELETE FROM pending_queue WHERE kind = ?", (kind,))
    conn.commit()
    conn.close()


def has_pending(kind: str) -> bool:
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM pending_queue WHERE kind = ?", (kind,)
    ).fetchone()
    conn.close()
    return row["n"] > 0


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
    """Paged per-customer view of a campaign (opportunities + their outcome)."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT o.user_id, o.first_name, o.phone_raw, o.phone_normalized,
               o.login_at, o.status AS opportunity_status,
               i.status AS intervention_status, i.sent_at, i.play_at,
               i.response_seconds
        FROM welcome_opportunities o
        LEFT JOIN welcome_interventions i ON i.opportunity_id = o.id
        WHERE o.campaign_id = ?
        ORDER BY o.login_at DESC
        LIMIT ? OFFSET ?
        """,
        (campaign_id, limit, offset),
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
# SMS log
# ---------------------------------------------------------------------------

def log_sms(record: dict):
    """Insert a single SMS log record.

    record keys: message_id (optional), user_id, kind, phone, status,
                 cost, balance_after, cycle_id, sent_at.
    """
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO sms_log
            (message_id, user_id, kind, phone, status, cost, balance_after, cycle_id, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.get("message_id"),
            record["user_id"],
            record["kind"],
            record["phone"],
            record.get("status", "sent"),
            record.get("cost", 0),
            record.get("balance_after"),
            record.get("cycle_id"),
            record["sent_at"],
        ),
    )
    conn.commit()
    conn.close()


def log_sms_batch(records: list[dict]):
    """Bulk-insert SMS log records."""
    if not records:
        return
    conn = get_connection()
    conn.executemany(
        """
        INSERT INTO sms_log
            (message_id, user_id, kind, phone, status, cost, balance_after, cycle_id, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r.get("message_id"),
                r["user_id"],
                r["kind"],
                r["phone"],
                r.get("status", "sent"),
                r.get("cost", 0),
                r.get("balance_after"),
                r.get("cycle_id"),
                r["sent_at"],
            )
            for r in records
        ],
    )
    conn.commit()
    conn.close()


def update_sms_status(message_id: str, status: str, cost: float | None = None):
    """Update the delivery status (and optionally cost) for a sent SMS."""
    conn = get_connection()
    if cost is not None:
        conn.execute(
            "UPDATE sms_log SET status = ?, cost = ? WHERE message_id = ?",
            (status, cost, message_id),
        )
    else:
        conn.execute(
            "UPDATE sms_log SET status = ? WHERE message_id = ?",
            (status, message_id),
        )
    conn.commit()
    conn.close()


def get_unsynced_sms(limit: int = 100) -> list[dict]:
    """Return sent SMS records that have a message_id but haven't reached a
    terminal delivery status yet."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, message_id, status FROM sms_log
        WHERE message_id IS NOT NULL
          AND status NOT IN ('delivered', 'dnd', 'rejected', 'expired', 'failed')
        ORDER BY id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _sms_kind_clauses(kind: str | None, kinds: set[str] | None) -> tuple[list[str], list]:
    """WHERE clauses for sms_log kind scoping.

    `kinds` (a set) is used for active reporting scope (e.g. enabled features
    plus manual); `kind` (a single value) is used for explicit feature-specific
    or historical inspection. When both are given both apply; callers normally
    pass exactly one.
    """
    clauses: list[str] = []
    params: list = []
    if kinds:
        placeholders = ",".join("?" * len(kinds))
        clauses.append(f"kind IN ({placeholders})")
        params.extend(sorted(kinds))
    elif kind:
        clauses.append("kind = ?")
        params.append(kind)
    return clauses, params


def get_sms_logs(
    kind: str | None = None,
    kinds: set[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    """Query SMS logs with optional kind/kinds and date-range filters."""
    clauses, params = _sms_kind_clauses(kind, kinds)
    if since:
        clauses.append("sent_at >= ?")
        params.append(since)
    if until:
        clauses.append("sent_at <= ?")
        params.append(until)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM sms_log{where} ORDER BY id"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(offset)
    conn = get_connection()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def count_sms_logs(
    kind: str | None = None,
    kinds: set[str] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> int:
    """Count SMS log rows matching the same filters as get_sms_logs."""
    clauses, params = _sms_kind_clauses(kind, kinds)
    if since:
        clauses.append("sent_at >= ?")
        params.append(since)
    if until:
        clauses.append("sent_at <= ?")
        params.append(until)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = get_connection()
    row = conn.execute(f"SELECT COUNT(*) AS n FROM sms_log{where}", params).fetchone()
    conn.close()
    return row["n"]


def get_stats_summary(
    kind: str | None = None,
    kinds: set[str] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Return aggregated statistics for the sms_log table."""
    clauses, params = _sms_kind_clauses(kind, kinds)
    if since:
        clauses.append("sent_at >= ?")
        params.append(since)
    if until:
        clauses.append("sent_at <= ?")
        params.append(until)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = get_connection()

    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status IN ('sent', 'delivered') THEN 1 ELSE 0 END) AS sent,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) AS delivered,
            SUM(CASE WHEN status = 'dnd' THEN 1 ELSE 0 END) AS dnd,
            SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
            SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END) AS expired,
            SUM(CASE WHEN status = 'deferred' THEN 1 ELSE 0 END) AS deferred,
            COALESCE(SUM(cost), 0) AS total_cost,
            COALESCE(AVG(CASE WHEN cost > 0 THEN cost END), 0) AS avg_cost
        FROM sms_log{where}
        """,
        params,
    ).fetchone()

    conn.close()
    return dict(row) if row else {}


def get_cycle_stats(
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    """Return per-cycle aggregated statistics."""
    clauses: list[str] = ["cycle_id IS NOT NULL"]
    params: list = []
    if since:
        clauses.append("sent_at >= ?")
        params.append(since)
    if until:
        clauses.append("sent_at <= ?")
        params.append(until)

    where = " WHERE " + " AND ".join(clauses)
    conn = get_connection()
    rows = conn.execute(
        f"""
        SELECT
            cycle_id,
            COUNT(*) AS total,
            SUM(CASE WHEN status IN ('sent', 'delivered') THEN 1 ELSE 0 END) AS sent,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN status = 'delivered' THEN 1 ELSE 0 END) AS delivered,
            SUM(CASE WHEN status = 'deferred' THEN 1 ELSE 0 END) AS deferred,
            COALESCE(SUM(cost), 0) AS total_cost,
            MIN(sent_at) AS started_at,
            MAX(sent_at) AS ended_at
        FROM sms_log{where}
        GROUP BY cycle_id
        ORDER BY MIN(sent_at) DESC
        """,
        params,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Wallet log
# ---------------------------------------------------------------------------

def log_wallet_snapshot(balance: float, currency: str):
    """Record a wallet balance snapshot."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT INTO wallet_log (balance, currency, fetched_at) VALUES (?, ?, ?)",
        (balance, currency, now),
    )
    conn.commit()
    conn.close()


def get_wallet_history() -> list[dict]:
    """Return all wallet balance snapshots, newest first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM wallet_log ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_latest_wallet() -> dict | None:
    """Return the most recent wallet snapshot, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM wallet_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Uploaded files registry
# ---------------------------------------------------------------------------

def insert_file(record: dict) -> int:
    """Register an uploaded data file. Returns the new row id."""
    conn = get_connection()
    cursor = conn.execute(
        """
        INSERT INTO files
            (original_filename, stored_filename, dataset, uploaded_at,
             uploaded_by, status, row_count, parse_error, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record["original_filename"],
            record["stored_filename"],
            record["dataset"],
            record["uploaded_at"],
            record.get("uploaded_by"),
            record.get("status", "received"),
            record.get("row_count"),
            record.get("parse_error"),
            record.get("processed_at"),
        ),
    )
    conn.commit()
    conn.close()
    return cursor.lastrowid


def get_file(file_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_files(limit: int = 100) -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM files ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_file_outcome(file_id: int, status: str, *, row_count: int | None = None,
                        parse_error: str | None = None):
    """Finalize an upload's ingestion outcome."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    conn.execute(
        "UPDATE files SET status = ?, row_count = ?, parse_error = ?, processed_at = ? "
        "WHERE id = ?",
        (status, row_count, parse_error, now, file_id),
    )
    conn.commit()
    conn.close()