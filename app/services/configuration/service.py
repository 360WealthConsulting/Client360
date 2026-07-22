"""Enterprise Configuration service facade (Phase D.27).

Aggregates the configuration submodules (catalog / preferences / features / editions / platform) for
the overview surface and cross-cutting reads. Enterprise Configuration is an authoritative
platform-configuration domain: it owns configuration governance metadata but references operational
records/organizations/users and never owns them. It imports its own submodules and shared
infrastructure — never a composition layer (annual_review/business_owner/reporting).
"""
from __future__ import annotations

from . import catalog, editions, features, platform, preferences
from .common import audit_history  # re-exported for routes


def overview_metrics(principal) -> dict:
    cat = catalog.metrics(principal)
    feat = features.metrics(principal)
    ed = editions.metrics(principal)
    plat = platform.metrics(principal)
    pref = preferences.metrics(principal)
    return {"active_overrides": cat["active_overrides"], "draft_sets": cat["draft_sets"],
            "enabled_feature_flags": feat["enabled_feature_flags"], "active_rollouts": feat["active_rollouts"],
            "active_editions": ed["active_editions"],
            "active_edition_assignments": ed["active_edition_assignments"],
            "pending_changes": plat["pending_changes"], "platform_options": plat["platform_options"],
            "preferences": pref["preferences"]}


__all__ = ["overview_metrics", "audit_history"]
