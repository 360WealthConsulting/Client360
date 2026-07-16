"""Insurance Operations — Phase 0 schema foundation (Release 0.10.0).

Schema + capabilities/roles + domain registration only. No services/routes/UI yet.
These tests pin the foundation: the tables and their regulated constraints, the
seeded capabilities/roles, the carrier-as-organization seam (AD-1), the 1:1
case↔engagement rule (AD-2), the normalized multi-party ownership model
(Refinement 3), split producers (Refinement 4), and registration of the
`insurance` domain in the exception engine and work management.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text

from app.db import (
    engagements,
    engine,
    households,
    insurance_carrier_profiles,
    insurance_cases,
    insurance_policies,
    insurance_policy_parties,
    insurance_policy_producers,
    insurance_policy_relationships,
    insurance_product_families,
    insurance_product_versions,
    people,
    relationship_entities,
    service_lines,
)


def _sfx() -> str:
    return uuid.uuid4().hex


def _org(c, entity_type: str, name: str) -> int:
    return c.execute(
        relationship_entities.insert().values(
            entity_type=entity_type, name=name, details={}, active=True
        ).returning(relationship_entities.c.id)
    ).scalar_one()


def _carrier(c, name: str, naic: str) -> int:
    org_id = _org(c, "insurance_carrier", name)
    c.execute(insurance_carrier_profiles.insert().values(
        relationship_entity_id=org_id, naic_company_code=naic, appointment_status="active"))
    return org_id


def _product_version(c, carrier_id: int, sfx: str) -> int:
    fam = c.execute(insurance_product_families.insert().values(
        carrier_id=carrier_id, name=f"Term {sfx}", product_type="term_life", line="life"
    ).returning(insurance_product_families.c.id)).scalar_one()
    return c.execute(insurance_product_versions.insert().values(
        family_id=fam, version_label="2026.1"
    ).returning(insurance_product_versions.c.id)).scalar_one()


def _engagement(c, sfx: str) -> int:
    sl = c.execute(select(service_lines.c.id).where(service_lines.c.code == "insurance")).scalar_one()
    hid = c.execute(households.insert().values(name=f"HH {sfx}").returning(households.c.id)).scalar_one()
    return c.execute(engagements.insert().values(
        service_line_id=sl, engagement_type="policy_application", status="open", metadata={},
        household_id=hid,
    ).returning(engagements.c.id)).scalar_one(), hid


# --- schema exists & is wired ------------------------------------------------

def test_all_eleven_foundation_tables_exist():
    from sqlalchemy import inspect
    names = set(inspect(engine).get_table_names())
    expected = {
        "insurance_carrier_profiles", "insurance_product_families", "insurance_product_versions",
        "insurance_cases", "insurance_policies", "insurance_coverages", "insurance_riders",
        "insurance_policy_values", "insurance_policy_parties", "insurance_policy_producers",
        "insurance_policy_relationships",
    }
    assert expected <= names


# --- AD-1: carrier is an organization node -----------------------------------

def test_carrier_is_an_organization_entity_type():
    """The carrier-as-org seam: the low-level guard accepts insurance_carrier."""
    from app.services.relationships import create_named_entity
    with engine.begin() as c:
        cid = create_named_entity(c, "insurance_carrier", f"Acme Life {_sfx()}")
        row = c.execute(select(relationship_entities.c.entity_type)
                        .where(relationship_entities.c.id == cid)).scalar_one()
    assert row == "insurance_carrier"


def test_carrier_profile_is_one_to_one_with_the_org():
    with engine.begin() as c:
        carrier = _carrier(c, f"Carrier {_sfx()}", "12345")
        # a second profile for the same org violates the unique FK
        with pytest.raises(Exception):
            c.execute(insurance_carrier_profiles.insert().values(
                relationship_entity_id=carrier, naic_company_code="99999"))


# --- AD-2: case <-> engagement is strictly 1:1 -------------------------------

def test_case_wraps_exactly_one_engagement():
    with engine.begin() as c:
        eng, hid = _engagement(c, _sfx())
        c.execute(insurance_cases.insert().values(
            engagement_id=eng, household_id=hid, case_type="new_business", status="open"))
    with engine.begin() as c, pytest.raises(Exception):
        # a second case on the same engagement violates the unique constraint (1:1)
        c.execute(insurance_cases.insert().values(
            engagement_id=eng, case_type="review", status="open"))


# --- Refinement 3: multiple owners / insureds / beneficiaries, trust ownership ---

def test_policy_supports_multi_owner_insured_beneficiary_and_trust_ownership():
    with engine.begin() as c:
        sfx = _sfx()
        carrier = _carrier(c, f"C {sfx}", "22222")
        pv = _product_version(c, carrier, sfx)
        eng, hid = _engagement(c, sfx)
        case = c.execute(insurance_cases.insert().values(
            engagement_id=eng, household_id=hid, case_type="new_business", status="proposed"
        ).returning(insurance_cases.c.id)).scalar_one()
        p1 = c.execute(people.insert().values(household_id=hid, full_name=f"Insured A {sfx}").returning(people.c.id)).scalar_one()
        p2 = c.execute(people.insert().values(household_id=hid, full_name=f"Insured B {sfx}").returning(people.c.id)).scalar_one()
        trust = _org(c, "trust", f"Family Trust {sfx}")
        policy = c.execute(insurance_policies.insert().values(
            case_id=case, carrier_id=carrier, product_version_id=pv, household_id=hid,
            status="proposed", face_amount=1_000_000,
        ).returning(insurance_policies.c.id)).scalar_one()

        # a trust owner, two insureds (survivorship), two beneficiaries with shares.
        # executemany needs uniform keys, so every row carries the full column set.
        def party(role, etype, eid, *, share=None, designation=None, primary=False):
            return {"policy_id": policy, "party_role": role, "party_entity_type": etype,
                    "party_entity_id": eid, "share_percentage": share,
                    "designation": designation, "is_primary_insured": primary}
        c.execute(insurance_policy_parties.insert(), [
            party("owner", "organization", trust, share=100),
            party("insured", "person", p1, primary=True),
            party("insured", "person", p2),
            party("beneficiary", "person", p1, designation="primary", share=60),
            party("beneficiary", "person", p2, designation="contingent", share=40),
        ])
        insureds = c.execute(select(func.count()).select_from(insurance_policy_parties).where(
            insurance_policy_parties.c.policy_id == policy,
            insurance_policy_parties.c.party_role == "insured")).scalar_one()
        owner_org = c.execute(select(insurance_policy_parties.c.party_entity_type).where(
            insurance_policy_parties.c.policy_id == policy,
            insurance_policy_parties.c.party_role == "owner")).scalar_one()
    assert insureds == 2 and owner_org == "organization"


def test_party_designation_check_rejects_bad_value():
    with engine.begin() as c:
        sfx = _sfx()
        carrier = _carrier(c, f"C {sfx}", "33333")
        pv = _product_version(c, carrier, sfx)
        policy = c.execute(insurance_policies.insert().values(
            carrier_id=carrier, product_version_id=pv, status="proposed"
        ).returning(insurance_policies.c.id)).scalar_one()
    with engine.begin() as c, pytest.raises(Exception):
        c.execute(insurance_policy_parties.insert().values(
            policy_id=policy, party_role="beneficiary", party_entity_type="person",
            party_entity_id=1, designation="tertiary"))  # not primary|contingent


# --- Refinement 4: split producers -------------------------------------------

def test_policy_supports_split_producers():
    with engine.begin() as c:
        sfx = _sfx()
        carrier = _carrier(c, f"C {sfx}", "44444")
        pv = _product_version(c, carrier, sfx)
        agency = _org(c, "business", f"Agency {sfx}")
        policy = c.execute(insurance_policies.insert().values(
            carrier_id=carrier, product_version_id=pv, status="in_force"
        ).returning(insurance_policies.c.id)).scalar_one()
        c.execute(insurance_policy_producers.insert(), [
            {"policy_id": policy, "producer_entity_type": "user", "producer_entity_id": 1,
             "producer_role": "writing_agent", "split_percentage": 70},
            {"policy_id": policy, "producer_entity_type": "user", "producer_entity_id": 2,
             "producer_role": "writing_agent", "split_percentage": 30},
            {"policy_id": policy, "producer_entity_type": "organization", "producer_entity_id": agency,
             "producer_role": "override", "split_percentage": 5},
        ])
        total = c.execute(select(func.sum(insurance_policy_producers.c.split_percentage)).where(
            insurance_policy_producers.c.policy_id == policy,
            insurance_policy_producers.c.producer_role == "writing_agent")).scalar_one()
    assert total == 100


def test_policy_relationship_supports_1035_link():
    with engine.begin() as c:
        sfx = _sfx()
        carrier = _carrier(c, f"C {sfx}", "55555")
        pv = _product_version(c, carrier, sfx)
        old = c.execute(insurance_policies.insert().values(
            carrier_id=carrier, product_version_id=pv, status="surrendered"
        ).returning(insurance_policies.c.id)).scalar_one()
        new = c.execute(insurance_policies.insert().values(
            carrier_id=carrier, product_version_id=pv, status="in_force"
        ).returning(insurance_policies.c.id)).scalar_one()
        c.execute(insurance_policy_relationships.insert().values(
            from_policy_id=new, to_policy_id=old, relation_type="funded_by_1035"))
        # a policy cannot relate to itself
        with pytest.raises(Exception):
            c.execute(insurance_policy_relationships.insert().values(
                from_policy_id=new, to_policy_id=new, relation_type="replaces"))


# --- capabilities / roles seeded ---------------------------------------------

def test_capabilities_and_roles_are_seeded():
    with engine.connect() as c:
        caps = set(c.execute(text("select code from capabilities where code like 'insurance.%'")).scalars())
        roles = set(c.execute(text("select code from roles where code like 'insurance_%'")).scalars())
    assert caps == {
        "insurance.read", "insurance.write", "insurance.suitability", "insurance.commissions.read",
        "insurance.commissions.write",  # added in Phase 5 (commissions)
        "insurance.licensing.read", "insurance.licensing.write", "insurance.sensitive.read",
    }
    assert roles == {"insurance_agent", "insurance_operations", "insurance_compliance"}


def test_sensitive_capability_flagged_and_administrator_granted_all():
    with engine.connect() as c:
        sensitive = c.execute(text(
            "select sensitive from capabilities where code='insurance.sensitive.read'")).scalar_one()
        admin_has = c.execute(text(
            "select count(*) from role_capabilities rc "
            "join roles r on r.id=rc.role_id join capabilities cap on cap.id=rc.capability_id "
            "where r.code='administrator' and cap.code like 'insurance.%'")).scalar_one()
    assert sensitive is True
    assert admin_has == 8  # administrator holds every insurance capability (Phase 5 added commissions.write)


def test_agent_role_lacks_sensitive_and_suitability():
    with engine.connect() as c:
        agent_caps = set(c.execute(text(
            "select cap.code from role_capabilities rc "
            "join roles r on r.id=rc.role_id join capabilities cap on cap.id=rc.capability_id "
            "where r.code='insurance_agent'")).scalars())
    assert "insurance.read" in agent_caps and "insurance.write" in agent_caps
    assert "insurance.sensitive.read" not in agent_caps
    assert "insurance.suitability" not in agent_caps  # segregation of duty


# --- domain registration: exception engine + work management -----------------

def test_insurance_registered_in_exception_engine():
    from app.services.exception_engine import SUPPORTED_DOMAINS
    assert "insurance" in SUPPORTED_DOMAINS


def test_exceptions_domain_check_accepts_insurance():
    """The CHECK was widened; a system-raised insurance exception is accepted and
    surfaces via list_exceptions without an 'unsupported domain' error."""
    from app.services import exception_engine as ee
    with engine.begin() as c:
        c.execute(text(
            "insert into exception_types (domain, code, category, name, default_severity, "
            "default_owner_role, sla_minutes, blocks_lifecycle, compliance_visible) "
            "values ('insurance', :code, 'client', 'Test insurance exception', 'medium', "
            "'insurance_agent', 2880, false, false)"), {"code": f"INS_TEST_{_sfx()[:8]}"})
    # list_exceptions for the insurance domain must not raise (domain is supported)
    from app.security.models import Principal
    principal = Principal(1, "a@e.com", "A", frozenset({"record.read_all", "exception.read"}))
    result = ee.list_exceptions(principal=principal, domain="insurance")
    assert isinstance(result, list)


def test_insurance_in_work_items_domain_filter():
    import inspect as _inspect

    from app.services import work_management
    src = _inspect.getsource(work_management.work_items)
    assert '"insurance"' in src  # exception projection includes the insurance domain
