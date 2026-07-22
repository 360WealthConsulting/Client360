"""Runtime safety detectors (Phase D.28) — never raise; a config problem must never crash startup.

Analyzes the current D.27 metadata (read-only) and the current snapshot for problems that could make
runtime resolution ambiguous or wrong, and returns a structured report. Detectors:
- invalid configuration      — active item referencing a runtime setting but with no value/default
- circular dependency        — a feature ``replacement_feature_id`` chain that cycles
- conflicting override        — an item with active overrides for both a specific env and ``all``
- rollout conflict            — a flag with multiple active staged rollouts at different percentages
- invalid edition             — an active edition assignment pointing at a missing/retired edition
- orphan capability           — an edition capability referencing a capability code not in RBAC
- stale snapshot              — the current snapshot's hash differs from freshly-computed metadata

``validate()`` returns ``{"ok": bool, "issues": [...]}`` and never raises.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import capabilities, engine

from . import metadata_reader, snapshots


def _invalid_configuration(items):
    issues = []
    for it in items:
        if it.get("runtime_setting_reference") and it.get("value") is None and it.get("default_value") is None:
            issues.append({"type": "invalid_configuration", "item": it["code"],
                           "detail": "references a runtime setting but has no value or default"})
    return issues


def _circular_dependency(flags):
    by_id = {f["id"]: f for f in flags}
    issues = []
    for f in flags:
        seen, cur = set(), f
        while cur is not None and cur.get("replacement_feature_id") is not None:
            if cur["id"] in seen:
                issues.append({"type": "circular_dependency", "feature": f["code"]})
                break
            seen.add(cur["id"])
            cur = by_id.get(cur["replacement_feature_id"])
    return issues


def _conflicting_overrides(overrides):
    by_item = {}
    for o in overrides:
        by_item.setdefault(o["configuration_item_id"], set()).add(o["environment"])
    return [{"type": "conflicting_override", "configuration_item_id": iid}
            for iid, envs in by_item.items() if "all" in envs and len(envs) > 1]


def _rollout_conflicts(rollouts):
    by_flag = {}
    for r in rollouts:
        by_flag.setdefault(r["feature_flag_id"], set()).add(r["percentage"])
    return [{"type": "rollout_conflict", "feature_flag_id": fid, "percentages": sorted(pcts)}
            for fid, pcts in by_flag.items() if len(pcts) > 1]


def _invalid_editions(assignments, editions):
    valid = {e["id"] for e in editions if e["status"] != "retired"}
    return [{"type": "invalid_edition", "assignment_id": a["id"], "edition_id": a["edition_id"]}
            for a in assignments if a["edition_id"] not in valid]


def _orphan_capabilities(edition_caps):
    with engine.connect() as c:
        known = set(c.scalars(select(capabilities.c.code)))
    return [{"type": "orphan_capability", "capability_code": ec["capability_code"],
             "edition_id": ec["edition_id"]}
            for ec in edition_caps if ec["capability_code"] not in known]


def validate(*, environment="production") -> dict:
    """Run every detector and return a report. Never raises — failures degrade to a reported issue."""
    issues = []
    try:
        items = metadata_reader.read_active_items()
        overrides = metadata_reader.read_active_overrides()
        flags = metadata_reader.read_flags()
        rollouts = metadata_reader.read_active_rollouts()
        editions = metadata_reader.read_editions()
        assignments = metadata_reader.read_edition_assignments()
        edition_caps = metadata_reader.read_edition_capabilities()
        issues += _invalid_configuration(items)
        issues += _circular_dependency(flags)
        issues += _conflicting_overrides(overrides)
        issues += _rollout_conflicts(rollouts)
        issues += _invalid_editions(assignments, editions)
        issues += _orphan_capabilities(edition_caps)
        cur = snapshots.current_snapshot()
        if cur is not None and snapshots.is_stale(cur, environment=environment):
            issues.append({"type": "stale_snapshot", "snapshot_version": cur["version"]})
    except Exception as exc:   # never raise into a caller — report and continue
        issues.append({"type": "safety_check_error", "detail": str(exc)})
    return {"ok": len(issues) == 0, "issue_count": len(issues), "issues": issues}
