"""Business Owner Planning Workspace tests (Phase D.12).

Covers person-level composition (zero/one/multi business, active/inactive), ownership
validation (percentages, incomplete totals, conflicts), person & business scope + URL-
enumeration blocking, per-section capability gating + EIN/policy redaction, restricted-vs-
missing distinction, provenance, retirement/benefits reuse, owner-compensation "not
available", planning-profile lifecycle + controlled vocabulary + durable timeline event,
Advisor Intelligence reuse (no second engine, grouped by durable recommendation_type),
deterministic missing-information, no source-domain mutation on read, Client 360 + household
integration, dependency direction, and route authorization.
"""
import os
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, insert, select
from starlette.requests import Request

from app.security import benefits_crypto

os.environ.setdefault("BENEFITS_FIELD_KEY", benefits_crypto.generate_key())

from app.db import (  # noqa: E402
    benefit_plans,
    business_planning_profiles,
    engine,
    households,
    organization_profiles,
    people,
    record_assignments,
    relationship_entities,
    relationship_ownership,
    relationship_types,
    relationships,
    timeline_events,
    users,
)
from app.security.models import Principal  # noqa: E402
from app.services import business_owner as svc  # noqa: E402
from app.services.advisor_intelligence import get_client_signals  # noqa: E402

FULL = frozenset({"business_owner.read", "business_owner.update", "business_owner.planning_update",
                  "advisor_work.read", "timeline.read", "compliance.review.read",
                  "annual_review.read", "organization.read", "tax.read", "benefits.read",
                  "insurance.read", "benefits.sensitive.read", "insurance.sensitive.read"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _entity(c, entity_type, *, name, person_id=None, household_id=None):
    return c.execute(insert(relationship_entities).values(
        entity_type=entity_type, name=name, person_id=person_id, household_id=household_id,
        active=True).returning(relationship_entities.c.id)).scalar_one()


def _own_edge(c, *, owner_entity, business_entity, pct=None, voting=None, otype="direct",
              is_direct=True, active=True, inactive_date=None):
    owns_id = c.scalar(select(relationship_types.c.id).where(relationship_types.c.code == "owns"))
    rel_id = c.execute(insert(relationships).values(
        from_entity_id=owner_entity, to_entity_id=business_entity, relationship_type_id=owns_id,
        effective_date=date.today(), inactive_date=inactive_date, source="benefits",
        confidence_level=90, active=active).returning(relationships.c.id)).scalar_one()
    c.execute(insert(relationship_ownership).values(
        relationship_id=rel_id, ownership_percentage=pct, voting_percentage=voting,
        ownership_type=otype, is_direct=is_direct, evidence_source="operating agreement"))
    return rel_id


def _setup(*, with_ein=True, entity_form="s_corp"):
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"bo-{tag}@e.test", normalized_email=f"bo-{tag}@e.test",
            display_name=f"Advisor {tag}", status="active").returning(users.c.id)).scalar_one()
        hh = c.execute(households.insert().values(name=f"HH {tag}").returning(households.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Owner {tag}", primary_email=f"{tag}@e.test", normalized_email=f"{tag}@e.test",
            household_id=hh, active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner",
            effective_date=date.today()))
        person_entity = _entity(c, "person", name=f"Owner {tag}", person_id=pid)
        biz = _entity(c, "business", name=f"Acme {tag} LLC")
        c.execute(insert(organization_profiles).values(
            relationship_entity_id=biz, legal_name=f"Acme {tag} LLC",
            ein=(benefits_crypto.encrypt("12-3456789") if with_ein else None),
            entity_form=entity_form, industry="Consulting", status="active",
            created_by_user_id=uid))
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="organization", entity_id=biz, assignment_type="owner",
            effective_date=date.today()))
        _own_edge(c, owner_entity=person_entity, business_entity=biz, pct=60, voting=60)
        # A retirement plan sponsored by the business (benefits reuse).
        from app.db import benefit_plan_types
        ret_type = c.scalar(select(benefit_plan_types.c.id).where(benefit_plan_types.c.code == "401k"))
        c.execute(insert(benefit_plans).values(
            organization_id=biz, plan_type_id=ret_type, name="Acme 401(k)", status="active",
            funding_type="fully_insured"))
    return {"uid": uid, "pid": pid, "hh": hh, "person_entity": person_entity, "biz": biz, "tag": tag}


def _teardown(ids):
    with engine.begin() as c:
        c.execute(delete(business_planning_profiles).where(
            business_planning_profiles.c.business_id == ids["biz"]))
        c.execute(delete(timeline_events).where(timeline_events.c.person_id == ids["pid"]))
        c.execute(delete(benefit_plans).where(benefit_plans.c.organization_id == ids["biz"]))
        c.execute(delete(relationship_ownership).where(relationship_ownership.c.relationship_id.in_(
            select(relationships.c.id).where(relationships.c.to_entity_id == ids["biz"]))))
        c.execute(delete(relationships).where(relationships.c.to_entity_id == ids["biz"]))
        c.execute(delete(organization_profiles).where(
            organization_profiles.c.relationship_entity_id == ids["biz"]))
        c.execute(delete(relationship_entities).where(relationship_entities.c.id.in_(
            (ids["biz"], ids["person_entity"]))))
        c.execute(delete(record_assignments).where(record_assignments.c.user_id == ids["uid"]))
        c.execute(delete(people).where(people.c.id == ids["pid"]))
        c.execute(delete(households).where(households.c.id == ids["hh"]))


def _principal(ids, caps=FULL):
    return Principal(ids["uid"], "a@e.com", f"Advisor {ids['uid']}", frozenset(caps))


# --- composition + owner status ----------------------------------------------

def test_person_workspace_composition():
    ids = _setup()
    try:
        ws = svc.compose_person_workspace(_principal(ids), ids["pid"])
        assert ws is not None
        assert ws["is_business_owner"] is True
        assert ws["snapshot"]["active_business_count"] == 1
        b = ws["businesses"][0]
        assert b["business_id"] == ids["biz"]
        assert b["entity_form"] == "s_corp"
        assert b["ein"] == "12-3456789"       # decrypted with benefits.sensitive.read
        assert b["ownership_percentage"] == 60
        assert svc.is_business_owner(_principal(ids), ids["pid"]) is True
    finally:
        _teardown(ids)


def test_zero_business_empty_state():
    ids = _setup()
    try:
        # A different in-scope person with no ownership.
        with engine.begin() as c:
            pid2 = c.execute(people.insert().values(
                full_name="No Biz", primary_email=f"nb-{ids['tag']}@e.test",
                normalized_email=f"nb-{ids['tag']}@e.test", active=True).returning(people.c.id)).scalar_one()
            c.execute(insert(record_assignments).values(
                user_id=ids["uid"], entity_type="person", entity_id=pid2,
                assignment_type="owner", effective_date=date.today()))
        ws = svc.compose_person_workspace(_principal(ids), pid2)
        assert ws is not None and ws["businesses"] == [] and ws["is_business_owner"] is False
        assert svc.is_business_owner(_principal(ids), pid2) is False
        # No relationship entity was created as a side effect of the read.
        with engine.connect() as c:
            assert c.scalar(select(relationship_entities.c.id).where(
                relationship_entities.c.person_id == pid2)) is None
        with engine.begin() as c:
            c.execute(delete(record_assignments).where(record_assignments.c.entity_id == pid2,
                                                       record_assignments.c.entity_type == "person"))
            c.execute(delete(people).where(people.c.id == pid2))
    finally:
        _teardown(ids)


def test_inactive_ownership_not_business_owner():
    ids = _setup()
    try:
        # End the ownership edge -> no active ownership.
        with engine.begin() as c:
            c.execute(relationships.update().where(relationships.c.to_entity_id == ids["biz"])
                      .values(active=False, inactive_date=date.today()))
        assert svc.is_business_owner(_principal(ids), ids["pid"]) is False
        ws = svc.compose_person_workspace(_principal(ids), ids["pid"])
        assert ws["snapshot"]["active_business_count"] == 0
    finally:
        _teardown(ids)


# --- scope -------------------------------------------------------------------

def test_person_scope_first():
    ids = _setup()
    try:
        stranger = Principal(999999, "s@e.com", "S", FULL)
        assert svc.compose_person_workspace(stranger, ids["pid"]) is None
    finally:
        _teardown(ids)


def test_business_scope_blocks_enumeration():
    ids = _setup()
    try:
        p = _principal(ids)
        # In-scope person + owned business -> visible.
        assert svc.business_in_scope(p, ids["pid"], ids["biz"]) is True
        assert svc.compose_business_detail(p, ids["pid"], ids["biz"]) is not None
        # A random business id the person does not own and principal has no org scope for.
        assert svc.business_in_scope(p, ids["pid"], 987654) is False
        assert svc.compose_business_detail(p, ids["pid"], 987654) is None
    finally:
        _teardown(ids)


# --- redaction: EIN restricted vs missing ------------------------------------

def test_ein_restricted_not_mislabeled_missing():
    ids = _setup(with_ein=True)
    try:
        base = _principal(ids, {"business_owner.read"})  # no benefits.sensitive.read
        ws = svc.compose_person_workspace(base, ids["pid"])
        b = ws["businesses"][0]
        assert b["ein"] is None and b["ein_present"] is True and b.get("ein_restricted") is True
        # Restricted EIN must NOT appear as "missing".
        assert not any("EIN missing" in m["issue"] for m in ws["missing_information"])
    finally:
        _teardown(ids)


def test_ein_missing_is_flagged():
    ids = _setup(with_ein=False, entity_form=None)
    try:
        ws = svc.compose_person_workspace(_principal(ids), ids["pid"])
        issues = {m["issue"] for m in ws["missing_information"]}
        assert "EIN missing" in issues
        assert "Entity type unknown" in issues
    finally:
        _teardown(ids)


# --- sections gated without owning caps --------------------------------------

def test_sections_gated_without_capabilities():
    ids = _setup()
    try:
        base = _principal(ids, {"business_owner.read"})
        ws = svc.compose_person_workspace(base, ids["pid"])
        assert ws["work"] is None and ws["compliance"] is None and ws["annual_review"] is None
        assert ws["activity"] is None
        detail = svc.compose_business_detail(base, ids["pid"], ids["biz"])
        assert detail["tax"]["status"] == "restricted"
        assert detail["retirement"]["status"] == "restricted"   # no benefits.read
        assert detail["insurance"]["status"] == "restricted"    # no insurance.read
        assert detail["ownership"]["status"] == "restricted"    # no organization.read
    finally:
        _teardown(ids)


# --- business detail composition + ownership analysis ------------------------

def test_business_detail_and_retirement_reuse():
    ids = _setup()
    try:
        detail = svc.compose_business_detail(_principal(ids), ids["pid"], ids["biz"])
        assert detail["tax"]["status"] == "ok" and detail["tax"]["engagements"] == []
        assert detail["owner_compensation"]["status"] == "not_available"
        ret = detail["retirement"]
        assert ret["status"] == "ok" and ret["plans"] and ret["plans"][0]["name"] == "Acme 401(k)"
        assert detail["ownership"]["status"] == "ok"
    finally:
        _teardown(ids)


def test_ownership_totals_incomplete_and_missing_percentage():
    ids = _setup()
    try:
        with engine.begin() as c:
            # Second owner at 30% (total 90 -> incomplete) and a third with NO percentage.
            other_entity = _entity(c, "person", name="Co Owner")
            third_entity = _entity(c, "person", name="Silent Owner")
            _own_edge(c, owner_entity=other_entity, business_entity=ids["biz"], pct=30)
            _own_edge(c, owner_entity=third_entity, business_entity=ids["biz"], pct=None)
        own = svc._ownership_structure(_principal(ids), ids["biz"])
        assert own["status"] == "ok"
        assert own["totals_incomplete"] is True          # 60 + 30 + (missing) != 100
        assert own["missing_percentage_count"] == 1
        assert own["conflict_owner_ids"] == []           # edge-uniqueness prevents dup-edge conflicts
        with engine.begin() as c:
            for ent in (other_entity, third_entity):
                c.execute(delete(relationship_ownership).where(
                    relationship_ownership.c.relationship_id.in_(
                        select(relationships.c.id).where(relationships.c.from_entity_id == ent))))
                c.execute(delete(relationships).where(relationships.c.from_entity_id == ent))
                c.execute(delete(relationship_entities).where(relationship_entities.c.id == ent))
    finally:
        _teardown(ids)


# --- planning profile lifecycle ----------------------------------------------

def test_planning_profile_lifecycle_and_vocab_and_timeline():
    ids = _setup()
    try:
        p = _principal(ids)
        prof = svc.upsert_planning_profile(p, person_id=ids["pid"], business_id=ids["biz"],
                                           fields={"succession_plan_status": "documented",
                                                   "buy_sell_status": "in_progress",
                                                   "notes": "Reviewed with owner",
                                                   "source_type": "advisor_entered"})
        assert prof["succession_plan_status"] == "documented"
        assert prof["notes"] == "Reviewed with owner"
        # A durable timeline event was emitted (reused writer, anchored to the person).
        with engine.connect() as c:
            evs = c.execute(select(timeline_events).where(
                timeline_events.c.person_id == ids["pid"],
                timeline_events.c.source == "business_owner")).mappings().all()
        assert len(evs) == 1
        # Controlled vocabulary is enforced.
        with pytest.raises(svc.PlanningValidationError):
            svc.upsert_planning_profile(p, person_id=ids["pid"], business_id=ids["biz"],
                                        fields={"succession_plan_status": "totally_done"})
    finally:
        _teardown(ids)


def test_planning_profile_scope_enforced():
    ids = _setup()
    try:
        stranger = Principal(999998, "s@e.com", "S", FULL)
        with pytest.raises(svc.BusinessNotInScopeError):
            svc.upsert_planning_profile(stranger, person_id=ids["pid"], business_id=ids["biz"],
                                        fields={"succession_plan_status": "complete"})
    finally:
        _teardown(ids)


# --- Advisor Intelligence reuse (no second engine) ---------------------------

def test_recommendations_reused_not_regenerated():
    ids = _setup()
    try:
        p = _principal(ids)
        ws = svc.compose_person_workspace(p, ids["pid"])
        flat = [s for g in ws["recommendation_groups"] for s in g["items"]]
        expected = [s for s in get_client_signals(p, ids["pid"]) if s.category == "recommendation"]
        assert sorted(x.id for x in flat) == sorted(x.id for x in expected)
    finally:
        _teardown(ids)


# --- household integration ---------------------------------------------------

def test_household_business_ownership_summary():
    ids = _setup()
    try:
        with engine.begin() as c:
            c.execute(insert(record_assignments).values(
                user_id=ids["uid"], entity_type="household", entity_id=ids["hh"],
                assignment_type="owner", effective_date=date.today()))
        summary = svc.household_business_ownership(_principal(ids), ids["hh"])
        assert summary is not None
        assert any(m["person_id"] == ids["pid"] for m in summary["owning_members"])
        assert summary["active_business_count"] >= 1
    finally:
        _teardown(ids)


# --- integration + dependency direction --------------------------------------

def test_client360_link_present():
    from pathlib import Path
    tmpl = (Path(__file__).resolve().parent.parent / "app" / "templates" / "people"
            / "workspace.html").read_text()
    assert "business_owner.read" in tmpl and "/business-owner/{{ person.id }}" in tmpl


def test_route_auth_and_render():
    from app.routes.business_owner import workspace
    ids = _setup()
    try:
        req = Request({"type": "http", "method": "GET",
                       "path": f"/business-owner/{ids['pid']}", "headers": [], "query_string": b""})
        resp = workspace(req, ids["pid"], principal=_principal(ids))
        assert resp.status_code == 200
        assert "Business Owner Planning" in resp.body.decode()
    finally:
        _teardown(ids)


def test_source_domains_do_not_import_business_owner():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app" / "services"
    pattern = re.compile(r"import\s+business_owner\b|from\s+\S*business_owner\s+import|"
                         r"services\s+import\s+.*\bbusiness_owner\b")
    for module in ("advisor_intelligence.py", "advisor_work.py", "compliance/reviews.py",
                   "activity_timeline/service.py", "annual_review.py", "organization_service.py",
                   "insurance.py", "tax_domain.py", "benefits_domain.py"):
        src = (root / module).read_text()
        assert not pattern.search(src), f"{module} must not import business_owner"
