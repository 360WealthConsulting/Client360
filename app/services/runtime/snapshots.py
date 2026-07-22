"""Immutable effective-configuration snapshots (Phase D.28).

A snapshot is a stable, comparable, auditable record of the effective configuration + active features
+ edition/license at a point in time. Snapshots are **immutable** (``runtime_config_snapshots`` is
trigger-blocked) and versioned monotonically. Major snapshots (startup / manual / refresh / scheduler)
are persisted; per-request context references the current persisted snapshot rather than writing a
new row per request. The engine composes a snapshot deterministically from the D.27 metadata (read
only) and stores its ``config_hash`` for stale/drift detection and snapshot comparison.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.db import engine, runtime_config_snapshots

from . import editions as edition_eval
from . import features as feature_eval
from . import metadata_reader, resolution
from .common import canonical_hash, record_event

_SNAPSHOT_NAMESPACE = uuid.UUID("6f3a1c2e-9b4d-4e7a-8c1f-2a3b4c5d6e28")


def build_effective(*, environment="production", emergency=None):
    """Compute the tenant-level effective config + active features + edition/license from the current
    D.27 metadata (deterministic, read-only). Returns a dict used for a snapshot or comparison."""
    items = metadata_reader.read_active_items()
    overrides = metadata_reader.read_active_overrides()
    preferences = metadata_reader.read_preferences()
    flags = metadata_reader.read_flags()
    active_rollouts = metadata_reader.read_active_rollouts()
    editions = metadata_reader.read_editions()
    assignments = metadata_reader.read_edition_assignments()
    edition_caps = metadata_reader.read_edition_capabilities()
    licenses = metadata_reader.read_license_policies()

    effective_config = resolution.resolve_effective_config(
        environment=environment, emergency=emergency, items=items, overrides=overrides,
        preferences=preferences)
    edition = edition_eval.resolve_edition(editions=editions, assignments=assignments)
    edition_code = edition["code"] if edition else None
    caps = edition_eval.edition_capabilities(edition["id"], edition_caps=edition_caps) if edition else set()
    lic = edition_eval.license_for(edition["id"], licenses=licenses) if edition else None
    active_features = feature_eval.evaluate_all(
        edition_code=edition_code, edition_capabilities=caps, flags=flags, active_rollouts=active_rollouts)
    return {"effective_config": effective_config, "active_features": active_features,
            "edition_code": edition_code, "license_code": (lic["code"] if lic else None),
            "item_count": len(items), "feature_count": len(flags)}


def build_snapshot(principal=None, *, scope="manual", environment="production", emergency=None,
                   source=None, actor_user_id=None) -> dict:
    """Compose and persist an immutable effective-configuration snapshot. Returns the snapshot row."""
    payload = build_effective(environment=environment, emergency=emergency)
    config_hash = canonical_hash({"config": payload["effective_config"],
                                  "features": payload["active_features"],
                                  "edition": payload["edition_code"], "environment": environment})
    snapshot_uid = str(uuid.uuid5(_SNAPSHOT_NAMESPACE, f"{scope}:{config_hash}:{uuid.uuid4()}"))
    with engine.begin() as c:
        version = (c.scalar(select(func.max(runtime_config_snapshots.c.version))) or 0) + 1
        row = c.execute(runtime_config_snapshots.insert().values(
            snapshot_uid=snapshot_uid, scope=scope, version=version, config_hash=config_hash,
            effective_config=payload["effective_config"], active_features=payload["active_features"],
            edition_code=payload["edition_code"], license_code=payload["license_code"],
            item_count=payload["item_count"], feature_count=payload["feature_count"],
            source=source, created_by_user_id=actor_user_id).returning(*runtime_config_snapshots.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="snapshot", entity_id=row["id"],
                     event_type=("snapshot_refreshed" if scope == "refresh" else "snapshot_created"),
                     actor_user_id=actor_user_id, payload={"scope": scope, "version": version})
    return row


def current_snapshot() -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(runtime_config_snapshots)
                        .order_by(runtime_config_snapshots.c.version.desc()).limit(1)).mappings().first()
        return dict(row) if row else None


def get_snapshot(snapshot_uid: str) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(runtime_config_snapshots)
                        .where(runtime_config_snapshots.c.snapshot_uid == snapshot_uid)).mappings().first()
        return dict(row) if row else None


def list_snapshots(*, limit=50):
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(runtime_config_snapshots).order_by(runtime_config_snapshots.c.version.desc())
            .limit(min(200, max(1, limit)))).mappings()]


def compare_snapshots(uid_a: str, uid_b: str) -> dict:
    """Deterministic diff of two snapshots' effective config + features. Never mutates anything."""
    a, b = get_snapshot(uid_a), get_snapshot(uid_b)
    if a is None or b is None:
        return {"error": "snapshot not found"}
    ca, cb = a["effective_config"] or {}, b["effective_config"] or {}
    fa, fb = a["active_features"] or {}, b["active_features"] or {}
    changed_config = {k: {"from": ca.get(k), "to": cb.get(k)}
                      for k in set(ca) | set(cb) if ca.get(k) != cb.get(k)}
    changed_features = {k: {"from": fa.get(k), "to": fb.get(k)}
                        for k in set(fa) | set(fb) if fa.get(k) != fb.get(k)}
    return {"identical": a["config_hash"] == b["config_hash"],
            "version_a": a["version"], "version_b": b["version"],
            "changed_config": changed_config, "changed_features": changed_features}


def is_stale(snapshot: dict, *, environment="production") -> bool:
    """A snapshot is stale if a freshly-computed hash of the current metadata differs from it."""
    if snapshot is None:
        return True
    payload = build_effective(environment=environment)
    fresh = canonical_hash({"config": payload["effective_config"], "features": payload["active_features"],
                            "edition": payload["edition_code"], "environment": environment})
    return fresh != snapshot["config_hash"]
