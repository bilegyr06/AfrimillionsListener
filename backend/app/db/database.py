"""Shared database infrastructure: connection handling and schema/migration.

The per-domain data modules in this package (players, campaigns, sms, wallet,
files, settings) own their domain queries and import `get_connection` from
here. Only connection handling and migration-related plumbing belong in this
module; domain operations live in the narrow aggregate modules.
"""
import sqlite3

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
    # Persistent player activity (Phase 2) - the durable source of truth for
    # "what the customer did". welcome_interventions keeps play_at /
    # response_seconds as a denormalized view of the campaign conversion; the
    # plays table carries the full history (games, amounts, repeat plays).
    #
    # The Sales source has NO stable play/transaction identifier, so the
    # idempotency key is derived deterministically from the source row itself:
    # (user_id, played_at, game_name, amount). Documented assumption: identical
    # same-second bets are indistinguishable from file duplication, and the
    # database therefore collapses them into one play record. If a real play ID
    # ever appears in the source, migrate source_key to it.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            played_at TEXT NOT NULL,
            game_name TEXT NOT NULL,
            amount REAL NOT NULL,
            source_file TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            source_key TEXT NOT NULL UNIQUE
        )
        """
    )
    _ensure_column(conn, "pending_queue", "kind", "TEXT NOT NULL DEFAULT 'inactive'")
    _ensure_column(conn, "pending_queue", "last_login_at", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "welcome_campaigns", "config", "TEXT")

    # Persistent deposit activity (segment evaluation). Deposit_events exports
    # userId + timestamp only (there is no deposit amount), so the fact carries
    # recency/count aggregates only. Like plays, there is no stable deposit id:
    # the idempotency key is derived deterministically from the source row
    # (user_id, deposited_at). Deposit timestamps are stored with the same
    # "+00:00 relabelled wall-clock" convention as plays.played_at so the two
    # facts compare on one frame.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            deposited_at TEXT NOT NULL,
            source_file TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            source_key TEXT NOT NULL UNIQUE
        )
        """
    )

    # ------------------------------------------------------------------ v2.0.0
    # Campaign Window domain foundation. Version 2 starts from a clean slate:
    # these tables are NOT populated from legacy v1 welcome campaign history,
    # and nothing in the v1 pipeline writes to them (the v1 welcome tables are
    # untouched). Once the v2 SMS execution workflow lands, the v2 tables become
    # the source of truth for Campaign Windows, Runs, audience/assignment state
    # and finalized results; the legacy welcome tables remain frozen history.
    #
    # All v2 timestamps are stored as UTC-aware ISO strings (Africa/Lagos wall
    # clock defined boundaries converted to their UTC instant; see
    # app.core.dates), consistent with plays.played_at / sms_log.sent_at so
    # lexical SQL comparisons stay valid.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_windows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'ended', 'finalized')),
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            finalization_deadline TEXT NOT NULL,
            finalized_at TEXT,
            ended_at TEXT,
            business_timezone TEXT NOT NULL DEFAULT 'Africa/Lagos',
            selected_segments TEXT NOT NULL DEFAULT '[]',
            assignment_method TEXT NOT NULL DEFAULT 'deterministic'
                CHECK (assignment_method IN ('deterministic', 'random')),
            suggested_control_percentage REAL,
            control_percentage REAL,
            control_override REAL,
            eligible_count INTEGER,
            segment_eligible_counts TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            window_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'completed', 'stopped')),
            started_at TEXT NOT NULL,
            ended_at TEXT,
            stop_reason TEXT,
            note TEXT,
            snapshot_id INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (window_id) REFERENCES campaign_windows (id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            window_id INTEGER NOT NULL,
            run_id INTEGER UNIQUE,
            captured_at TEXT NOT NULL,
            files TEXT NOT NULL DEFAULT '[]',
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (window_id) REFERENCES campaign_windows (id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS window_audiences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            window_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            segment_id TEXT NOT NULL,
            assignment TEXT NOT NULL
                CHECK (assignment IN ('campaign', 'control')),
            assignment_method TEXT NOT NULL
                CHECK (assignment_method IN ('deterministic', 'random')),
            assignment_config TEXT NOT NULL DEFAULT '{}',
            entered_at TEXT NOT NULL,
            eligibility_state TEXT NOT NULL DEFAULT '{}',
            phone_raw TEXT,
            phone_normalized TEXT,
            phone_valid INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE (window_id, user_id),
            FOREIGN KEY (window_id) REFERENCES campaign_windows (id)
        )
        """
    )
    for index_ddl in (
        "CREATE INDEX IF NOT EXISTS idx_windows_status ON campaign_windows (status)",
        "CREATE INDEX IF NOT EXISTS idx_windows_start ON campaign_windows (start_time)",
        "CREATE INDEX IF NOT EXISTS idx_runs_window_status ON campaign_runs (window_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_runs_window ON campaign_runs (window_id)",
        "CREATE INDEX IF NOT EXISTS idx_audience_window ON window_audiences (window_id)",
        "CREATE INDEX IF NOT EXISTS idx_audience_window_assignment "
        "ON window_audiences (window_id, assignment)",
    ):
        conn.execute(index_ddl)

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
    # sms_log is the delivery-funnel source. These cover the message_id lookups
    # (update_sms_status, get_unsynced_sms, statistics joins) and the
    # (kind, status, sent_at) filter aggregates used by SMS reporting.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sms_message_id ON sms_log (message_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sms_kind_status_sent "
        "ON sms_log (kind, status, sent_at)"
    )
    # plays coverage for attribution (user + played_at range scans), time-series
    # scoping, and game aggregation. source_key is unique via the table
    # constraint (its backing index serves dedup lookups).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plays_user_played "
        "ON plays (user_id, played_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plays_played_at ON plays (played_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plays_game ON plays (game_name)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_deposits_user_dep "
        "ON deposits (user_id, deposited_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_deposits_source ON deposits (source_file)"
    )
    conn.commit()
    conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")