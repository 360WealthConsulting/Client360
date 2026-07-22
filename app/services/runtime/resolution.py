"""Deterministic configuration-resolution engine (Phase D.28).

Computes the effective value of every active configuration item by walking a **fixed, deterministic
precedence** and returning the winning value plus the tier it came from. Precedence (highest first):

    1. runtime emergency override   (in-process, ops break-glass — engine-held)
    2. environment override         (configuration_environment_overrides, active, matching environment)
    3. tenant override              (configuration_preferences scope=tenant, key == item.code)
    4. organization override        (configuration_preferences scope=organization, org matches)
    5. user preference              (configuration_preferences scope=user, user matches)
    6. configuration item value     (item.value)
    7. configuration default        (item.default_value)

(Edition assignment and feature rollout/default sit in the *feature* evaluator, not item resolution.)
Every resolution is pure and deterministic given the metadata snapshot inputs — no environment reads,
no mutation. Preferences are matched to config items by ``preference_key == item.code``.
"""
from __future__ import annotations

from . import metadata_reader

# Precedence tiers, most-significant first (for reporting the resolution source).
TIERS = ("emergency", "environment", "tenant", "organization", "user", "item", "default")


def _index_overrides(overrides):
    idx = {}
    for o in overrides:
        idx.setdefault(o["configuration_item_id"], {})[o["environment"]] = o["value"]
    return idx


def _index_preferences(preferences):
    # {(scope, org_id, user_id, key): value}
    idx = {}
    for p in preferences:
        idx[(p["scope"], p.get("organization_id"), p.get("user_id"), p["preference_key"])] = p["value"]
    return idx


def resolve_item(item, *, environment="production", organization_id=None, user_id=None,
                 emergency=None, override_idx=None, preference_idx=None):
    """Resolve a single config item to (value, source_tier) via the fixed precedence."""
    code = item["code"]
    emergency = emergency or {}
    # 1. emergency override
    if code in emergency:
        return emergency[code], "emergency"
    # 2. environment override (exact environment, else the "all" environment)
    ov = (override_idx or {}).get(item["id"], {})
    if environment in ov:
        return ov[environment], "environment"
    if "all" in ov:
        return ov["all"], "environment"
    # 3. tenant override
    prefs = preference_idx or {}
    if ("tenant", None, None, code) in prefs:
        return prefs[("tenant", None, None, code)], "tenant"
    # 4. organization override
    if organization_id is not None and ("organization", organization_id, None, code) in prefs:
        return prefs[("organization", organization_id, None, code)], "organization"
    # 5. user preference
    if user_id is not None and ("user", None, user_id, code) in prefs:
        return prefs[("user", None, user_id, code)], "user"
    # 6. item value
    if item.get("value") is not None:
        return item["value"], "item"
    # 7. configuration default
    return item.get("default_value"), "default"


def resolve_effective_config(*, environment="production", organization_id=None, user_id=None,
                             emergency=None, items=None, overrides=None, preferences=None) -> dict:
    """Compute {item_code: {"value": ..., "source": tier}} for every active item, deterministically.
    Metadata inputs may be supplied (from a snapshot build) or read fresh from D.27."""
    items = metadata_reader.read_active_items() if items is None else items
    overrides = metadata_reader.read_active_overrides() if overrides is None else overrides
    preferences = metadata_reader.read_preferences() if preferences is None else preferences
    override_idx = _index_overrides(overrides)
    preference_idx = _index_preferences(preferences)
    result = {}
    for item in items:
        value, source = resolve_item(item, environment=environment, organization_id=organization_id,
                                     user_id=user_id, emergency=emergency, override_idx=override_idx,
                                     preference_idx=preference_idx)
        result[item["code"]] = {"value": value, "source": source}
    return result
