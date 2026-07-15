"""Release 0.9.11 — Employer Operations / Employee Benefits schema (Phase 1) tests.

Schema-only phase (services land later). Covers the shared Organization/Engagement/
service-line/relationship-role/ownership foundation and the benefits + retirement tables,
the seeded reference data (service lines, 17 plan types, Betterment recordkeeper, benefits
exception types, capabilities/roles), the extended exception `domain` CHECK, and the
additive `organization_id` columns.
"""
import uuid

import pytest
from sqlalchemy import insert, select, text

from app.db import (engine, benefit_plan_types, benefit_providers, benefit_plans,
    benefit_provider_connections, engagements, exception_types, exceptions, households,
    organization_profiles, relationship_ownership, service_lines, service_revenue)


# --- tables exist (import above already binds all 18 globals) ----------------

def test_all_benefits_tables_present():
    with engine.connect() as c:
        n = c.scalar(text(
            "SELECT count(*) FROM information_schema.tables WHERE table_name LIKE 'benefit_%' "
            "OR table_name IN ('organization_profiles','relationship_ownership','service_lines',"
            "'organization_service_lines','organization_service_roles','engagements','service_revenue')"))
    assert n == 18


# --- reference seeds ---------------------------------------------------------

def test_service_lines_seeded():
    with engine.connect() as c:
        codes = set(c.execute(select(service_lines.c.code)).scalars())
    assert {"tax", "wealth", "benefits", "retirement", "insurance", "bookkeeping",
            "payroll", "consulting", "estate_coordination"} <= codes


def test_plan_types_include_all_17_health_and_retirement():
    with engine.connect() as c:
        rows = {r.code: r.line_of_coverage for r in c.execute(
            select(benefit_plan_types.c.code, benefit_plan_types.c.line_of_coverage))}
    expected = {"medical", "dental", "vision", "group_life", "std", "ltd", "accident",
                "critical_illness", "hospital_indemnity", "hsa", "fsa", "hra",
                "401k", "simple_ira", "sep_ira", "cash_balance", "deferred_comp"}
    assert expected <= set(rows)
    # retirement types are on the retirement line of coverage
    for code in ("401k", "simple_ira", "sep_ira", "cash_balance", "deferred_comp"):
        assert rows[code] == "retirement"


def test_betterment_seeded_as_first_recordkeeper():
    with engine.connect() as c:
        row = c.execute(select(benefit_providers).where(benefit_providers.c.code == "betterment")).mappings().one()
    assert row["name"] == "Betterment at Work"
    assert row["provider_type"] == "recordkeeper" and row["line_of_coverage"] == "retirement"


def test_benefits_exception_types_seeded_health_and_retirement():
    with engine.connect() as c:
        codes = set(c.execute(select(exception_types.c.code).where(exception_types.c.domain == "benefits")).scalars())
    assert "BEN_CENSUS_OVERDUE" in codes                 # health
    assert "BEN_FIDUCIARY_REVIEW_DUE" in codes           # retirement
    assert "BEN_PROVIDER_CONNECTION_STALE" in codes       # future/inert
    assert len(codes) == 24


def test_capabilities_and_roles_seeded_least_privilege():
    with engine.connect() as c:
        caps = set(c.execute(text(
            "SELECT code FROM capabilities WHERE code LIKE 'benefits.%' OR code LIKE 'organization.%'")).scalars())
        assert caps == {"organization.read", "organization.write", "benefits.read", "benefits.write",
                        "benefits.enroll", "benefits.compliance", "benefits.sensitive.read"}
        # sensitive flags
        sensitive = set(c.execute(text(
            "SELECT code FROM capabilities WHERE sensitive AND (code LIKE 'benefits.%')")).scalars())
        assert sensitive == {"benefits.compliance", "benefits.sensitive.read"}
        # benefits_compliance holds exception.compliance; not record.read_all (no new firm-wide grant)
        comp_caps = set(c.execute(text(
            "SELECT cp.code FROM roles r JOIN role_capabilities rc ON rc.role_id=r.id "
            "JOIN capabilities cp ON cp.id=rc.capability_id WHERE r.code='benefits_compliance'")).scalars())
        assert "exception.compliance" in comp_caps and "benefits.compliance" in comp_caps
        assert "record.read_all" not in comp_caps


# --- extended exception domain CHECK -----------------------------------------

def test_exception_domain_check_accepts_benefits():
    with engine.begin() as c:
        etype = c.scalar(select(exception_types.c.id).where(exception_types.c.code == "BEN_CENSUS_OVERDUE"))
        new_id = c.execute(exceptions.insert().values(
            exception_type_id=etype, domain="benefits", category="document", severity="high",
            status="open", title="schema probe", source="system").returning(exceptions.c.id)).scalar_one()
        assert new_id
        c.execute(exceptions.delete().where(exceptions.c.id == new_id))  # keep the shared DB clean


# --- ownership: relationship graph + typed 1:1 detail ------------------------

def _org_entity(c, name):
    return c.execute(text(
        "INSERT INTO relationship_entities (entity_type, name) VALUES ('business', :n) RETURNING id"),
        {"n": name}).scalar_one()


def test_ownership_edge_carries_typed_percentages_and_rejects_over_100():
    from app.db import relationship_types
    with engine.begin() as c:
        owns = c.scalar(select(relationship_types.c.id).where(relationship_types.c.code == "owns"))
        assert owns is not None  # ownership vocab seeded
        suffix = uuid.uuid4().hex[:8]
        owner = _org_entity(c, f"Owner {suffix}")
        owned = _org_entity(c, f"Owned {suffix}")
        rel = c.execute(text(
            "INSERT INTO relationships (from_entity_id, to_entity_id, relationship_type_id) "
            "VALUES (:f, :t, :ty) RETURNING id"), {"f": owner, "t": owned, "ty": owns}).scalar_one()
        oid = c.execute(relationship_ownership.insert().values(
            relationship_id=rel, ownership_percentage=60, voting_percentage=60,
            ownership_type="individual", is_direct=True, evidence_source="operating agreement"
        ).returning(relationship_ownership.c.id)).scalar_one()
        assert oid
        # unknown percentage is allowed (NULL)
        rel2 = c.execute(text(
            "INSERT INTO relationships (from_entity_id, to_entity_id, relationship_type_id) "
            "VALUES (:f, :t, :ty) RETURNING id"),
            {"f": owned, "t": owner, "ty": owns}).scalar_one()
        c.execute(relationship_ownership.insert().values(relationship_id=rel2))  # ownership_percentage NULL
    # >100 percentage is rejected by the CHECK
    with pytest.raises(Exception):
        with engine.begin() as c:
            suffix = uuid.uuid4().hex[:8]
            a, b = _org_entity(c, f"A {suffix}"), _org_entity(c, f"B {suffix}")
            owns = c.scalar(text("SELECT id FROM relationship_types WHERE code='owns'"))
            rel = c.execute(text(
                "INSERT INTO relationships (from_entity_id, to_entity_id, relationship_type_id) "
                "VALUES (:f,:t,:ty) RETURNING id"), {"f": a, "t": b, "ty": owns}).scalar_one()
            c.execute(relationship_ownership.insert().values(relationship_id=rel, ownership_percentage=150))


# --- additive organization_id columns ----------------------------------------

def test_organization_id_added_to_portal_grants_and_timeline():
    with engine.connect() as c:
        cols = set(c.execute(text(
            "SELECT table_name || '.' || column_name FROM information_schema.columns "
            "WHERE column_name='organization_id' AND table_name IN ('portal_access_grants','timeline_events')")).scalars())
    assert cols == {"portal_access_grants.organization_id", "timeline_events.organization_id"}
