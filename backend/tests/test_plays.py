"""Tests for persistent player activity (Phase 2): plays ingestion, dedup,
attribution over the plays table, and the campaign activity/game aggregations
that feed the Statistics surface.

Covers the full data-quality contract:
  * parsing/validation of Sales rows (naive->UTC, 2dp REAL, malformed rows)
  * deterministic dedup (intra-file, cross-file, cumulative exports)
  * the (sent_at, window_end] qualifying rule, first-play-wins attribution
  * activity volume (repeat players, amounts) and per-game ranking
  * zero/None conventions in the activity report
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

os.environ.setdefault("TERMII_API_KEY", "test-key")
os.environ.setdefault("TERMII_BASE_URL", "https://test.api.termii.com/api")
os.environ.setdefault("TERMII_SENDER_ID", "TestSender")

from app.db.database import (
    create_campaign,
    create_intervention,
    get_campaign_game_stats,
    get_campaign_play_edges,
    get_campaign_play_stats,
    get_campaigns_play_summary,
    get_converted_customers,
    get_connection,
    get_open_opportunities,
    insert_play_records,
    sales_file_processed,
    update_opportunity_status,
    upsert_opportunities,
)
from app.services.campaigns import _attribute_interventions
from app.services.plays import ingest_new_sales_files, ingest_sales_file, play_source_key
from app.services.statistics import campaign_statistics, campaign_summaries


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ago(hours: float) -> datetime:
    return _now() - timedelta(hours=hours)


def _slug(dt: datetime) -> str:
    """Naive CSV timestamp string (app-wide CSV timestamps are treated as UTC)."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _write_sales(data_dir, rows, name="Sales_test.csv"):
    pd.DataFrame(rows, columns=["userId", "gameName", "amount", "timestamp"]).to_csv(
        data_dir / name, index=False
    )


def _insert(user_id, played_at, game="Aviator", amount=1.0, source="Sales_test.csv", data_dir=None):
    """Insert one play record directly (skipping the CSV path)."""
    source = source or "Sales_test.csv"
    return insert_play_records(
        [
            {
                "user_id": user_id,
                "played_at": played_at,
                "game_name": game,
                "amount": amount,
                "source_file": source,
                "source_key": play_source_key(user_id, played_at, game, amount),
            }
        ]
    )


def _seed_intervention(campaign_id: int, user_id: str, login_ago=5, sent_ago=2,
                       phone="2348012345678") -> str:
    """Create a successful send (opportunity + intervention); returns sent_at."""
    login_at = _iso(_ago(login_ago))
    sent_at = _iso(_ago(sent_ago))
    upsert_opportunities([{
        "campaign_id": campaign_id,
        "user_id": user_id,
        "first_name": "Ada",
        "phone_raw": "07870123456",
        "phone_normalized": phone,
        "login_at": login_at,
        "eval_delay_hours": 1,
    }])
    opp = next(o for o in get_open_opportunities(campaign_id) if o["user_id"] == user_id)
    update_opportunity_status(opp["id"], "sent")
    create_intervention(
        opportunity_id=opp["id"],
        campaign_id=campaign_id,
        user_id=user_id,
        login_at=opp["login_at"],
        sent_at=sent_at,
        message_id=f"m_{user_id}",
    )
    return sent_at


def _campaign(name="t", start_ago=6, end_ago=None) -> dict:
    """Create a campaign, optionally backdating start / end."""
    campaign = create_campaign(name)["campaign"]
    conn = get_connection()
    if end_ago is not None:
        conn.execute(
            "UPDATE welcome_campaigns SET started_at = ?, ended_at = ? WHERE id = ?",
            (_iso(_ago(start_ago)), _iso(_ago(end_ago)), campaign["id"]),
        )
        campaign["started_at"] = _iso(_ago(start_ago))
        campaign["ended_at"] = _iso(_ago(end_ago))
    else:
        conn.execute(
            "UPDATE welcome_campaigns SET started_at = ? WHERE id = ?",
            (_iso(_ago(start_ago)), campaign["id"]),
        )
        campaign["started_at"] = _iso(_ago(start_ago))
    conn.commit()
    conn.close()
    return campaign


def _count_plays(user_id: str | None = None) -> int:
    conn = get_connection()
    if user_id is None:
        n = conn.execute("SELECT COUNT(*) AS n FROM plays").fetchone()["n"]
    else:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM plays WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]
    conn.close()
    return n


def settings_data_dir():
    from app.core.config import settings
    return settings.DATA_FOLDER


# ---------------------------------------------------------------------------
# Schema + ingestion
# ---------------------------------------------------------------------------

class TestPlaysSchemaAndIngestion:
    def test_plays_table_and_unique_key(self, _init_db):
        conn = get_connection()
        indexes = {
            r["name"] for r in conn.execute("PRAGMA index_list('plays')").fetchall()
        }
        conn.close()
        assert "idx_plays_user_played" in indexes
        assert "idx_plays_played_at" in indexes
        assert "idx_plays_game" in indexes
        assert "sqlite_autoindex_plays_1" in indexes  # UNIQUE(source_key)

    def test_insert_plays_counts_and_stores(self, _init_db):
        n = insert_play_records(
            [
                {"user_id": "1", "played_at": _iso(_ago(2)), "game_name": "Aviator",
                 "amount": 10.0, "source_file": "Sales_test.csv",
                 "source_key": play_source_key("1", _iso(_ago(2)), "Aviator", 10.0)},
                {"user_id": "1", "played_at": _iso(_ago(1)), "game_name": "Mines",
                 "amount": 5.0, "source_file": "Sales_test.csv",
                 "source_key": play_source_key("1", _iso(_ago(1)), "Mines", 5.0)},
            ]
        )
        assert n == 2
        assert _count_plays() == 2
        assert _count_plays("1") == 2

    def test_identical_play_inserted_once(self, _init_db):
        rec = {"user_id": "1", "played_at": _iso(_ago(1)), "game_name": "Aviator",
               "amount": 5.0, "source_file": "Sales_test.csv",
               "source_key": play_source_key("1", _iso(_ago(1)), "Aviator", 5.0)}
        assert insert_play_records([rec]) == 1
        assert insert_play_records([rec]) == 0
        assert _count_plays() == 1

    def test_cumulative_files_do_not_duplicate(self, _init_db):
        _write_sales(
            settings_data_dir(),
            [("1", "Aviator", 10, _slug(_ago(6))),
             ("1", "Mines", 5, _slug(_ago(5))),
             ("1", "Mines", 5, _slug(_ago(4)))],
            name="Sales_a.csv",
        )
        first = ingest_new_sales_files()
        # Overlapping cumulative export re-ships the same rows plus one new one.
        _write_sales(
            settings_data_dir(),
            [("1", "Aviator", 10, _slug(_ago(6))),
             ("1", "Mines", 5, _slug(_ago(5))),
             ("1", "Mines", 5, _slug(_ago(4))),
             ("1", "Aviator", 20, _slug(_ago(3)))],
            name="Sales_b.csv",
        )
        second = ingest_new_sales_files()

        assert first["inserted"] == 3
        assert second["files"] == 1
        assert second["inserted"] == 1
        assert second["skipped"] == 3  # overlap rows
        assert _count_plays() == 4  # deduped union, not 3+4

    def test_processed_file_skipped_on_next_cycle(self, _init_db):
        _write_sales(settings_data_dir(), [("1", "Aviator", 10, _slug(_ago(1)))])
        first = ingest_new_sales_files()
        assert first["inserted"] == 1
        assert sales_file_processed("Sales_test.csv") is True
        second = ingest_new_sales_files()
        assert second["files"] == 0
        assert second["inserted"] == 0
        assert _count_plays() == 1

    def test_duplicate_row_within_file_skipped(self, _init_db):
        _write_sales(
            settings_data_dir(),
            [("1", "Aviator", 10, _slug(_ago(1))),
             ("1", "Aviator", 10, _slug(_ago(1)))],
        )
        report = ingest_new_sales_files()
        assert report["inserted"] == 1
        assert report["skipped"] == 1
        assert _count_plays() == 1

    def test_malformed_rows_rejected(self, _init_db):
        _write_sales(
            settings_data_dir(),
            [("1", "Aviator", 10, _slug(_ago(1))),                 # valid
             ("", "Mines", 5, _slug(_ago(1))),                     # missing user
             ("2", "Mines", "abc", _slug(_ago(1))),                # invalid amount
             ("3", "Mines", 5, "not-a-timestamp"),                 # invalid timestamp
             ("4", "", 5, _slug(_ago(1)))],                        # missing game
        )
        report = ingest_sales_file(settings_data_dir() / "Sales_test.csv")
        assert report["inserted"] == 1
        assert report["invalid"] == 4
        assert report["invalid_reasons"]["missing_user_id"] == 1
        assert report["invalid_reasons"]["invalid_amount"] == 1
        assert report["invalid_reasons"]["invalid_timestamp"] == 1
        assert report["invalid_reasons"]["missing_game_name"] == 1
        assert _count_plays() == 1

    def test_missing_columns_rejected_as_whole_file(self, _init_db):
        pd.DataFrame([("1", "Aviator")], columns=["userId", "gameName"]).to_csv(
            settings_data_dir() / "Sales_test.csv", index=False
        )
        report = ingest_sales_file(settings_data_dir() / "Sales_test.csv")
        assert report["inserted"] == 0
        assert report["invalid_reasons"]["missing_columns"] == 1

    def test_naive_timestamp_treated_as_utc(self, _init_db):
        _write_sales(settings_data_dir(), [("1", "Aviator", 10, "2026-08-14 16:18:07")])
        ingest_new_sales_files()
        conn = get_connection()
        row = conn.execute("SELECT played_at FROM plays").fetchone()
        conn.close()
        assert row["played_at"] == "2026-08-14T16:18:07+00:00"

    def test_amount_parsed_to_real_two_decimal(self, _init_db):
        _write_sales(
            settings_data_dir(),
            [("1", "Aviator", "0.1", _slug(_ago(1))),
             ("2", "Mines", "12.50", _slug(_ago(1)))],
        )
        ingest_new_sales_files()
        conn = get_connection()
        rows = conn.execute("SELECT user_id, amount FROM plays ORDER BY user_id").fetchall()
        conn.close()
        assert {r["user_id"]: r["amount"] for r in rows} == {"1": 0.1, "2": 12.5}

    def test_read_failure_reported(self, _init_db):
        p = settings_data_dir() / "Sales_test.csv"
        p.write_text("")  # empty file -> pandas read fails
        report = ingest_sales_file(p)
        assert report["parse_errors"] == 1
        assert _count_plays() == 0


# ---------------------------------------------------------------------------
# Attribution over the plays table
# ---------------------------------------------------------------------------

class TestAttributionOverPlays:
    def test_play_after_send_attributed(self, _init_db):
        campaign = _campaign(start_ago=6)
        sent_at = _seed_intervention(campaign["id"], "1", sent_ago=3)
        play_at = _iso(_ago(2))  # 1h after send
        _insert("1", play_at)
        result = _attribute_interventions(campaign)
        assert result == {"open": 1, "responded": 1, "no_change": 0}
        conn = get_connection()
        row = conn.execute(
            "SELECT status, play_at, response_seconds FROM welcome_interventions "
            "WHERE campaign_id = ?", (campaign["id"],)
        ).fetchone()
        conn.close()
        assert row["status"] == "responded"
        assert row["play_at"] == play_at
        assert row["response_seconds"] == pytest.approx(3600, abs=120)

    def test_earliest_qualifying_play_wins(self, _init_db):
        campaign = _campaign(start_ago=6)
        sent_at = _seed_intervention(campaign["id"], "1", sent_ago=3)
        earliest_play = _iso(_ago(2.5))  # closest to the SMS
        later_play = _iso(_ago(2))
        _insert("1", later_play)
        _insert("1", earliest_play)
        _attribute_interventions(campaign)
        conn = get_connection()
        row = conn.execute(
            "SELECT play_at, response_seconds FROM welcome_interventions "
            "WHERE campaign_id = ?", (campaign["id"],)
        ).fetchone()
        conn.close()
        assert row["play_at"] == earliest_play
        assert row["response_seconds"] == pytest.approx(1800, abs=120)

    def test_play_before_send_not_attributed(self, _init_db):
        campaign = _campaign(start_ago=6)
        sent_at = _seed_intervention(campaign["id"], "1", sent_ago=2)
        _insert("1", _iso(_ago(3)))  # before the SMS
        result = _attribute_interventions(campaign)
        assert result["responded"] == 0
        conn = get_connection()
        row = conn.execute(
            "SELECT status FROM welcome_interventions WHERE campaign_id = ?",
            (campaign["id"],),
        ).fetchone()
        conn.close()
        assert row["status"] == "open"  # still open

    def test_play_at_sent_second_not_qualifying(self, _init_db):
        campaign = _campaign(start_ago=6)
        sent_at = _seed_intervention(campaign["id"], "1", sent_ago=2)
        _insert("1", sent_at)  # exactly at the SMS instant -> strictly-after is false
        result = _attribute_interventions(campaign)
        assert result["responded"] == 0
        edges = get_campaign_play_edges(campaign["id"], _iso(_now()))
        assert edges["before_sms"] == 1

    def test_play_outside_window_end_excluded(self, _init_db):
        campaign = _campaign(start_ago=6, end_ago=1)  # closed 1h ago
        sent_at = _seed_intervention(campaign["id"], "1", sent_ago=3)
        _insert("1", _iso(_ago(2)))   # inside (sent, ended]
        _insert("1", _iso(_ago(0.5)))  # after campaign ended
        result = _attribute_interventions(campaign, campaign["ended_at"])
        assert result["responded"] == 1
        stats = get_campaign_play_stats(campaign["id"], campaign["ended_at"])
        assert stats[0]["play_count"] == 1
        edges = get_campaign_play_edges(campaign["id"], campaign["ended_at"])
        assert edges["after_end"] == 1


# ---------------------------------------------------------------------------
# Activity + games aggregations
# ---------------------------------------------------------------------------

class TestActivityAggregations:
    def test_repeat_players_and_volume(self, _init_db):
        campaign = _campaign()
        _seed_intervention(campaign["id"], "1", sent_ago=3)
        _seed_intervention(campaign["id"], "2", sent_ago=3)
        _insert("1", _iso(_ago(2)))
        _insert("1", _iso(_ago(1.5)))
        _insert("1", _iso(_ago(1)))
        _insert("2", _iso(_ago(1)))

        report = campaign_statistics(campaign["id"])
        activity = report["activity"]
        assert activity["qualifying_plays"] == 4
        assert activity["players"] == 2
        assert activity["repeat_players"] == 1
        assert activity["max_plays_per_player"] == 3
        assert activity["avg_plays_per_player"] == 2.0

    def test_amounts_aggregated(self, _init_db):
        campaign = _campaign()
        _seed_intervention(campaign["id"], "1", sent_ago=3)
        _seed_intervention(campaign["id"], "2", sent_ago=3)
        _insert("1", _iso(_ago(2)), amount=10)
        _insert("1", _iso(_ago(1)), amount=20)
        _insert("2", _iso(_ago(1)), amount=5)

        report = campaign_statistics(campaign["id"])
        activity = report["activity"]
        assert activity["total_play_amount"] == 35.0
        assert activity["avg_play_amount"] == pytest.approx(35 / 3, abs=0.01)
        assert report["games"][0]["amount"] == 35.0

    def test_game_aggregation_ranked(self, _init_db):
        campaign = _campaign()
        _seed_intervention(campaign["id"], "1", sent_ago=5)
        _seed_intervention(campaign["id"], "2", sent_ago=5)
        _insert("1", _iso(_ago(4)), game="Aviator")
        _insert("1", _iso(_ago(3.5)), game="Aviator")
        _insert("1", _iso(_ago(3)), game="Mines")
        _insert("2", _iso(_ago(3)), game="Mines")
        _insert("2", _iso(_ago(2)), game="Roulette")

        games = get_campaign_game_stats(campaign["id"], _iso(_now()))
        ranking = {g["game_name"]: g for g in games}
        assert ranking["Aviator"]["plays"] == 2
        assert ranking["Aviator"]["customers"] == 1
        assert ranking["Mines"]["plays"] == 2
        assert ranking["Mines"]["customers"] == 2
        assert ranking["Roulette"]["plays"] == 1
        # Aviator before Mines on plays, then amount ties broken by name.
        assert [g["game_name"] for g in games][:2] == ["Aviator", "Mines"]

    def test_games_limit_bounds_ranking(self, _init_db):
        campaign = _campaign()
        _seed_intervention(campaign["id"], "1", sent_ago=5)
        for i, game in enumerate(("Aviator", "Mines", "Roulette")):
            _insert("1", _iso(_ago(4 - i / 2)), game=game)
        limited = get_campaign_game_stats(campaign["id"], _iso(_now()), limit=2)
        assert len(limited) == 2

    def test_converted_players_only_responded(self, _init_db):
        campaign = _campaign()
        _seed_intervention(campaign["id"], "1", sent_ago=3)
        _seed_intervention(campaign["id"], "2", sent_ago=3)
        _insert("1", _iso(_ago(2)))  # qualifying -> attributed
        _insert("2", _iso(_ago(4)))  # before the SMS -> not qualifying
        _attribute_interventions(campaign)
        assert get_converted_customers(campaign["id"]) == {"1"}
        report = campaign_statistics(campaign["id"])
        assert report["activity"]["converted_players"] == 1
        assert report["activity"]["players"] == 1

    def test_zero_play_campaign_conventions(self, _init_db):
        campaign = _campaign()
        _seed_intervention(campaign["id"], "1", sent_ago=3)

        report = campaign_statistics(campaign["id"])
        activity = report["activity"]
        assert activity["qualifying_plays"] == 0
        assert activity["players"] == 0
        assert activity["repeat_players"] == 0
        assert activity["total_play_amount"] == 0.0
        assert activity["avg_plays_per_player"] is None
        assert activity["repeat_rate"] is None
        assert activity["avg_play_amount"] is None
        assert report["response"]["conversion_rate"] == 0.0
        assert report["games"] == []

    def test_summaries_activity_block(self, _init_db):
        a = _campaign(name="a")
        _seed_intervention(a["id"], "1", sent_ago=3)
        _insert("1", _iso(_ago(2)), amount=10)
        _insert("1", _iso(_ago(1)), amount=20)
        b = _campaign(name="b")

        summaries = {s["campaign_id"]: s for s in campaign_summaries()}
        assert summaries[a["id"]]["activity"] == {
            "qualifying_plays": 2,
            "players": 1,
            "total_play_amount": 30.0,
        }
        assert summaries[b["id"]]["activity"] == {
            "qualifying_plays": 0,
            "players": 0,
            "total_play_amount": 0.0,
        }

    def test_future_play_excluded_while_active(self, _init_db):
        campaign = _campaign()
        _seed_intervention(campaign["id"], "1", sent_ago=3)
        _insert("1", _iso(_now() + timedelta(hours=2)))
        report = campaign_statistics(campaign["id"])
        assert report["activity"]["qualifying_plays"] == 0
        assert report["activity"]["after_window"] == 1