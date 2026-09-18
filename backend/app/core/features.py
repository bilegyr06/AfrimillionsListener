"""Feature registry: the single source of truth for campaign features.

A feature is declared once as a Feature(kind, gate, template, pipeline) and
registered by the module that owns its pipeline:

  * welcome  -> app.services.campaigns (the welcome-back campaign service)
  * inactive -> app.services.inactivity (the inactivity-reminder pipeline)

Everything that needs to know which features exist -- reporting scope, cycle
selection, the trigger loop -- reads from the registry instead of hardcoding
feature kinds, so a third feature is simply one more registration and a future
developer's answer to "where is a feature declared?" is always this module plus
the pipeline owner.

The kind strings themselves stay in app.core.models (WELCOME = "welcome",
INACTIVE = "inactive"); they are the canonical identifiers the database and the
logs use. This module only knows how features are declared and discovered.

Importing this module never executes a pipeline module: registration is
triggered lazily on the first registry access. That keeps feature knowledge out
of the import graph and makes the module import-order independent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass(frozen=True)
class Feature:
    """One registrable feature declaration.

    kind      -- the feature identifier, as stored in sms_log.kind (gate/report
                 scoping keyed off it).
    gate      -- callable deciding whether the feature is part of the active
                 scope right now (conventionally tests ENABLED_FEATURES).
    template  -- the operator-facing SMS template used by this feature.
    pipeline  -- async callable (deadline, cycle_id, logins_df=None) -> dict.
                 The shared cycle loop invokes it; feature-specific behavior
                 stays inside the pipeline, never in the generic loop.
    """

    kind: str
    gate: Callable[[], bool]
    template: str
    pipeline: Callable[..., Awaitable[dict]]


_FEATURES: dict[str, Feature] = {}
_SEEDED = False


def _ensure_seeded() -> None:
    """Import the pipeline-owning modules so they register their features.

    Idempotent and cycle-safe: it only runs once this module is fully imported
    (first public access), so the pipeline modules -- which may import this
    module -- are always pristine when they are pulled in here.
    """
    global _SEEDED
    if _SEEDED:
        return
    _SEEDED = True
    import app.services.campaigns  # noqa: F401  registers the welcome feature
    import app.services.inactivity  # noqa: F401  registers the inactive feature


def register_feature(feature: Feature) -> None:
    """Declare a feature. Re-registering a kind replaces its declaration."""
    _FEATURES[feature.kind] = feature


def get_feature(kind: str) -> Feature | None:
    """The registered feature for a kind, or None when unknown."""
    _ensure_seeded()
    return _FEATURES.get(kind)


def all_feature_kinds() -> frozenset[str]:
    """Every registered feature kind."""
    _ensure_seeded()
    return frozenset(_FEATURES)


def registered_features() -> tuple[Feature, ...]:
    """Registered features in registration order.

    The generic cycle loop iterates this instead of branching on kinds, so the
    execution order of shipped features is the order they were registered.
    """
    _ensure_seeded()
    return tuple(_FEATURES.values())


def enabled_feature_kinds() -> set[str]:
    """Kinds whose gate currently passes (the active feature scope)."""
    _ensure_seeded()
    return {kind for kind, feature in _FEATURES.items() if feature.gate()}


def __getattr__(name: str):
    # FEATURES is exposed lazily so the very act of reading it seeds the
    # registry; see the module docstring for why seeding is lazy at all.
    if name == "FEATURES":
        _ensure_seeded()
        return _FEATURES
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")