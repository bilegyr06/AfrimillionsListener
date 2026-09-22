"""Cycle orchestrator.

Owns when cycles run and what holds across all cycles: the cycle lifecycle
(run / begin), per-cycle deadline, cancellation, and the deferred-work queue a
feature draws from and hands back to when a cycle is interrupted or finishes.

The state reset and dispatch mechanics for each feature are not this module's
business — Welcome lives in app.services.campaigns and Inactive in
app.services.inactivity, both behind the same
``async pipeline(deadline, cycle_id, logins_df=None) -> dict`` contract the
feature registry (app.core.features) exposes. The orchestrator only provisions
the lifecycle; wallet balance snapshots are deliberately NOT a cycle side
effect and are recorded on demand via the stats wallet endpoint.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from app.core import state
from app.core.config import settings
from app.core.features import enabled_feature_kinds, registered_features
from app.db.players import add_pending, clear_pending, get_pending


def enabled_features() -> set[str]:
    """The kinds of the features currently eligible for a cycle."""
    return enabled_feature_kinds()


# ---------------------------------------------------------------------------
# Deferred-work queue (cycle lifecycle)
# ---------------------------------------------------------------------------

def deferred_work(kind: str) -> list[dict]:
    """Return and clear the work a previous interrupted cycle deferred.

    Records are opaque dicts keyed by the feature kind; the consuming feature
    interprets their shape. Queue continuity is cycle lifecycle, so it lives
    here rather than inside a feature's pipeline.
    """
    queued = get_pending(kind)
    if queued:
        clear_pending(kind)
    return queued


def defer_work(kind: str, records: list[dict]) -> None:
    """Persist work this cycle could not finish for a future cycle."""
    if records:
        add_pending(records, kind)


# ---------------------------------------------------------------------------
# Cycle orchestration
# ---------------------------------------------------------------------------

async def run_cycle_async(deadline: datetime, features: set[str] | None = None) -> dict:
    """Run a notification cycle as an async task. Cancellable via task.cancel()."""
    state.cancel_requested = False

    try:
        return await _run_features(deadline, features)
    except asyncio.CancelledError:
        print("Notification cycle cancelled.")
        return {"message": "Notification cycle cancelled.", "count": 0, "cancelled": True}


async def _run_features(deadline: datetime, features: set[str] | None = None) -> dict:
    """Run the given feature subset (default: all enabled) and aggregate results."""
    features = (features & settings.ENABLED_FEATURES) if features else enabled_features()
    if not features:
        print("Cycle: no features enabled; nothing to run.")
        return {"message": "No features enabled.", "count": 0}

    cycle_id = uuid.uuid4().hex[:16]
    print(f"Cycle {cycle_id} started at {datetime.now().strftime('%H:%M:%S')}, running features: {', '.join(sorted(features))}")

    results = {}
    for feature in registered_features():
        if feature.kind not in features:
            continue
        results[feature.kind] = await feature.pipeline(deadline, cycle_id)

    total = sum(r.get("sent", 0) for r in results.values())
    print(f"Cycle {cycle_id} finished: total sent={total}. Per-feature: {results}")
    return {
        "message": f"Cycle complete: {total} message(s) sent.",
        "count": total,
        "cycle_id": cycle_id,
        "features": results,
        "cancelled": False,
    }


def begin_cycle(features: set[str] | None = None) -> bool:
    """Start a new cycle as an asyncio task. Returns False if one is already running.

    If features is None, all enabled features run.
    """
    if state.current_task is not None and not state.current_task.done():
        return False

    now = datetime.now()
    deadline = now.replace(hour=settings.CYCLE_END_HOUR, minute=0, second=0, microsecond=0)

    task = asyncio.create_task(run_cycle_async(deadline, features))
    state.current_task = task

    def _on_done(t: asyncio.Task):
        if state.current_task is t:
            state.current_task = None

    task.add_done_callback(_on_done)
    return True