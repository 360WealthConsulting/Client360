"""Deterministic feature evaluator (Phase D.28) — consumes D.27 feature metadata, never mutates it.

Evaluates whether a feature is ON for a given principal/organization/user by walking a deterministic
sequence: lifecycle status → activation window → deprecation → edition gate → target orgs → target
roles → staged/percentage rollout. Rollout is a deterministic hash bucket (sha256 of
``feature_code:rollout_key``) so the same subject always lands in the same bucket. The evaluator
reads D.27 flags/rollouts through the engine's metadata reader and **never modifies** them.
"""
from __future__ import annotations

import hashlib

from . import metadata_reader
from .common import now


def _bucket(code: str, key: str) -> int:
    """Deterministic 0..99 bucket for percentage rollout."""
    digest = hashlib.sha256(f"{code}:{key}".encode()).hexdigest()
    return int(digest[:8], 16) % 100


def _effective_rollout_pct(flag, active_rollouts) -> int:
    """The effective rollout percentage: the highest active staged-rollout percentage for the flag,
    else the flag's own rollout_percentage."""
    staged = [r["percentage"] for r in active_rollouts if r["feature_flag_id"] == flag["id"]]
    return max(staged) if staged else int(flag.get("rollout_percentage") or 0)


def evaluate_flag(flag, *, organization_id=None, rollout_key=None, edition_code=None,
                  edition_capabilities=None, principal_roles=None, active_rollouts=None) -> dict:
    """Return {"enabled": bool, "reason": str} deterministically. Pure — no mutation, no env reads."""
    active_rollouts = active_rollouts or []
    ts = now()

    status = flag.get("status")
    if status == "archived":
        return {"enabled": False, "reason": "archived"}
    if status == "deprecated":
        return {"enabled": False, "reason": "deprecated"}
    if status != "active" or not flag.get("enabled"):
        return {"enabled": False, "reason": "not_active"}

    # activation window
    starts = flag.get("activation_starts_at")
    ends = flag.get("activation_ends_at")
    if starts is not None and ts < starts:
        return {"enabled": False, "reason": "before_activation_window"}
    if ends is not None and ts > ends:
        return {"enabled": False, "reason": "after_activation_window"}
    if flag.get("deprecation_at") is not None and ts >= flag["deprecation_at"]:
        return {"enabled": False, "reason": "past_deprecation"}

    # capability-aware / edition gate: if the flag targets an edition (flag_metadata.edition),
    # require the resolved edition to include the required capability where declared.
    meta = flag.get("flag_metadata") or {}
    required_cap = meta.get("required_capability")
    if required_cap is not None and required_cap not in (edition_capabilities or set()):
        return {"enabled": False, "reason": "edition_capability_missing"}
    required_edition = meta.get("required_edition")
    if required_edition is not None and required_edition != edition_code:
        return {"enabled": False, "reason": "edition_mismatch"}

    # target organizations
    targets = flag.get("target_organizations")
    if targets:
        if organization_id is None or organization_id not in set(targets):
            return {"enabled": False, "reason": "org_not_targeted"}

    # target roles (capability-aware activation): principal must hold at least one target role
    troles = flag.get("target_roles")
    if troles:
        if not (principal_roles and set(troles) & set(principal_roles)):
            return {"enabled": False, "reason": "role_not_targeted"}

    # percentage / staged rollout
    pct = _effective_rollout_pct(flag, active_rollouts)
    if pct >= 100:
        return {"enabled": True, "reason": "rollout_full"}
    if pct <= 0:
        return {"enabled": False, "reason": "rollout_zero"}
    key = str(rollout_key if rollout_key is not None else (organization_id if organization_id is not None else "global"))
    return ({"enabled": True, "reason": f"rollout_{pct}"} if _bucket(flag["code"], key) < pct
            else {"enabled": False, "reason": f"rollout_excluded_{pct}"})


def evaluate_all(*, organization_id=None, rollout_key=None, edition_code=None,
                 edition_capabilities=None, principal_roles=None, flags=None, active_rollouts=None) -> dict:
    """Evaluate every feature flag → {code: {enabled, reason}} deterministically."""
    flags = metadata_reader.read_flags() if flags is None else flags
    active_rollouts = metadata_reader.read_active_rollouts() if active_rollouts is None else active_rollouts
    return {f["code"]: evaluate_flag(
        f, organization_id=organization_id, rollout_key=rollout_key, edition_code=edition_code,
        edition_capabilities=edition_capabilities, principal_roles=principal_roles,
        active_rollouts=active_rollouts) for f in flags}
