"""The engine's sole reader of the D.27 configuration metadata (Phase D.28).

Per ADR-033, no component reads the ``configuration_*`` metadata tables directly except through the
runtime engine. This module is that reader: it performs **read-only** SELECTs against the D.27
metadata (it never writes it) and returns plain dicts for the resolver / feature evaluator / edition
evaluator to consume. Startup hydration has no principal, so this reads the raw metadata directly
(the engine is the trusted, single reader) rather than through the D.27 service layer's
principal-scoped, sensitive-stripping read paths.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import (
    configuration_edition_assignments,
    configuration_edition_capabilities,
    configuration_editions,
    configuration_environment_overrides,
    configuration_feature_flags,
    configuration_feature_rollouts,
    configuration_items,
    configuration_license_policies,
    configuration_preferences,
    engine,
)


def read_active_items():
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(configuration_items).where(configuration_items.c.status.in_(("active", "approved")))
            .order_by(configuration_items.c.code)).mappings()]


def read_active_overrides():
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(configuration_environment_overrides)
            .where(configuration_environment_overrides.c.active.is_(True))).mappings()]


def read_preferences():
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(configuration_preferences)).mappings()]


def read_flags():
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(configuration_feature_flags).order_by(configuration_feature_flags.c.code)).mappings()]


def read_active_rollouts():
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(configuration_feature_rollouts)
            .where(configuration_feature_rollouts.c.status == "active")).mappings()]


def read_editions():
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(configuration_editions)).mappings()]


def read_edition_assignments():
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(configuration_edition_assignments)
            .where(configuration_edition_assignments.c.status == "active")).mappings()]


def read_edition_capabilities():
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(configuration_edition_capabilities)).mappings()]


def read_license_policies():
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(configuration_license_policies)).mappings()]
