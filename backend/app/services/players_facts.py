"""Per-user fact assembly for segment evaluation.

Turns raw (source_file-scoped) fact rows into the per-user aggregates the
segment engine consumes, decoding the "+00:00 relabelled wall-clock" labels
back into Africa/Lagos-aware datetimes so plays and deposits compare on one
frame with the segment windows.
"""
from __future__ import annotations

from datetime import datetime

from app.core import dates


def build_player_facts(
    play_rows: list[dict],
    deposit_rows: list[dict],
) -> tuple[dict[str, list[tuple]], dict[str, list[datetime]]]:
    """Group + decode fact rows per user.

    play_rows: select_plays() rows {user_id, played_at, amount, source_file}.
    deposit_rows: select_deposits() rows {user_id, deposited_at, source_file}.

    Returns (plays_by_user, deposits_by_user):
      plays_by_user[uid]    = list of (played_at Lagos-aware, amount)
      deposits_by_user[uid] = list of Lagos-aware deposited_at datetimes
    """
    plays_by_user: dict[str, list[tuple]] = {}
    for row in play_rows:
        user_id = str(row["user_id"])
        played_at = dates.read_source_fact(row["played_at"])
        plays_by_user.setdefault(user_id, []).append(
            (played_at, float(row.get("amount") or 0.0))
        )

    deposits_by_user: dict[str, list[datetime]] = {}
    for row in deposit_rows:
        user_id = str(row["user_id"])
        deposits_by_user.setdefault(user_id, []).append(
            dates.read_source_fact(row["deposited_at"])
        )

    return plays_by_user, deposits_by_user