"""Governance configuration catalog (Phase D.23) — data domains, elements, quality & survivorship
rules. Firm-level governance configuration gated by ``governance.manage``. Deterministic vocabularies
only — no AI, no probabilistic survivorship.
"""
from __future__ import annotations

from sqlalchemy import select

from app.database.governance_tables import (
    DATA_CLASSIFICATIONS,
    QUALITY_RULE_TYPES,
    SEVERITIES,
    SURVIVORSHIP_STRATEGIES,
)
from app.db import engine
from app.db import governance_data_domains as domains_t
from app.db import governance_data_elements as elements_t
from app.db import governance_quality_rules as rules_t
from app.db import governance_survivorship_rules as surv_t

from .common import GovernanceError


def _create_unique(table, code, values):
    code = (code or "").strip()
    if not code:
        raise GovernanceError("code is required")
    with engine.begin() as c:
        if c.scalar(select(table.c.id).where(table.c.code == code)) is not None:
            raise GovernanceError(f"code {code!r} already exists")
        return dict(c.execute(table.insert().values(code=code, **values)
                              .returning(*table.c)).mappings().one())


# --- data domains ------------------------------------------------------------

def list_domains(*, active_only=False):
    with engine.connect() as c:
        stmt = select(domains_t).order_by(domains_t.c.code)
        if active_only:
            stmt = stmt.where(domains_t.c.active.is_(True))
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_domain(*, code):
    with engine.connect() as c:
        row = c.execute(select(domains_t).where(domains_t.c.code == code)).mappings().first()
        return dict(row) if row else None


def create_domain(*, code, name, description=None, steward_user_id=None, actor_user_id=None):
    if not (name or "").strip():
        raise GovernanceError("name is required")
    return _create_unique(domains_t, code, {"name": name.strip(), "description": description,
                                            "steward_user_id": steward_user_id, "active": True,
                                            "created_by_user_id": actor_user_id})


# --- data elements -----------------------------------------------------------

def list_elements(*, data_domain_id=None):
    with engine.connect() as c:
        stmt = select(elements_t).order_by(elements_t.c.code)
        if data_domain_id is not None:
            stmt = stmt.where(elements_t.c.data_domain_id == data_domain_id)
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_element(*, data_domain_id, code, name, entity_type="person", field_name=None,
                   classification="internal", required=False, description=None, actor_user_id=None):
    if not (name or "").strip():
        raise GovernanceError("name is required")
    if classification not in DATA_CLASSIFICATIONS:
        raise GovernanceError(f"invalid classification {classification!r}")
    with engine.begin() as c:
        if c.scalar(select(domains_t.c.id).where(domains_t.c.id == data_domain_id)) is None:
            raise GovernanceError("data domain not found")
    return _create_unique(elements_t, code, {
        "data_domain_id": data_domain_id, "name": name.strip(), "entity_type": entity_type,
        "field_name": field_name, "classification": classification, "required": bool(required),
        "description": description, "created_by_user_id": actor_user_id})


# --- quality rules -----------------------------------------------------------

def list_rules(*, active_only=False, rule_type=None):
    with engine.connect() as c:
        stmt = select(rules_t).order_by(rules_t.c.code)
        if active_only:
            stmt = stmt.where(rules_t.c.active.is_(True))
        if rule_type:
            stmt = stmt.where(rules_t.c.rule_type == rule_type)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_rule(*, code=None, rule_id=None):
    with engine.connect() as c:
        where = rules_t.c.code == code if code is not None else rules_t.c.id == rule_id
        row = c.execute(select(rules_t).where(where)).mappings().first()
        return dict(row) if row else None


def create_rule(*, code, name, rule_type, entity_type="person", data_element_id=None, config=None,
                severity="medium", description=None, actor_user_id=None):
    if not (name or "").strip():
        raise GovernanceError("name is required")
    if rule_type not in QUALITY_RULE_TYPES:
        raise GovernanceError(f"invalid rule_type {rule_type!r}")
    if severity not in SEVERITIES:
        raise GovernanceError(f"invalid severity {severity!r}")
    return _create_unique(rules_t, code, {
        "name": name.strip(), "rule_type": rule_type, "entity_type": entity_type,
        "data_element_id": data_element_id, "config": config, "severity": severity,
        "active": True, "description": description, "created_by_user_id": actor_user_id})


# --- survivorship rules ------------------------------------------------------

def list_survivorship_rules(*, active_only=False):
    with engine.connect() as c:
        stmt = select(surv_t).order_by(surv_t.c.code)
        if active_only:
            stmt = stmt.where(surv_t.c.active.is_(True))
        return [dict(r) for r in c.execute(stmt).mappings()]


def create_survivorship_rule(*, code, name, strategy="most_recent", entity_type="person",
                             data_element_id=None, source_priority=None, description=None,
                             actor_user_id=None):
    if not (name or "").strip():
        raise GovernanceError("name is required")
    if strategy not in SURVIVORSHIP_STRATEGIES:
        raise GovernanceError(f"invalid strategy {strategy!r}")
    if strategy == "source_priority" and not source_priority:
        raise GovernanceError("source_priority strategy requires an ordered source_priority list")
    return _create_unique(surv_t, code, {
        "name": name.strip(), "strategy": strategy, "entity_type": entity_type,
        "data_element_id": data_element_id, "source_priority": source_priority, "active": True,
        "description": description, "created_by_user_id": actor_user_id})
