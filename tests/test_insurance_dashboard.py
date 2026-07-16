"""Insurance operations dashboard & reporting (Release 0.10.0, Phase 8).

Pins the consolidated firm-internal staff dashboard: correct aggregation from scoped lists,
sections **proportional to the viewer's capabilities**, record-scope applied before aggregation,
reuse of the shared exception/work-queue primitives, and NO compliance determination (AD-5). The
dashboard is a staff surface — it is not part of the policyholder portal.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.db import (
    engine,
    insurance_product_families,
    insurance_product_versions,
    relationship_entities,
    users,
)
from app.security.models import Principal
from app.services import insurance as ins
from app.services import insurance_commissions as com
from app.services import insurance_detectors as det
from app.services import insurance_licensing as lic
from app.services import insurance_reporting as rep

ALL_CAPS = frozenset({"insurance.read", "insurance.write", "insurance.commissions.read",
                      "insurance.commissions.write", "insurance.licensing.read",
                      "insurance.licensing.write", "exception.read", "exception.write",
                      "work.read", "record.read_all", "record.write_all"})


def _p(caps=ALL_CAPS, uid=1):
    return Principal(uid, "a@e.com", "A", caps)


def _sfx():
    return uuid.uuid4().hex[:12]


def _carrier_version():
    s = _sfx()
    with engine.begin() as c:
        carrier = c.execute(relationship_entities.insert().values(
            entity_type="insurance_carrier", name=f"Carrier {s}", details={}, active=True
        ).returning(relationship_entities.c.id)).scalar_one()
        fam = c.execute(insurance_product_families.insert().values(
            carrier_id=carrier, name=f"F {s}", product_type="term_life", line="life"
        ).returning(insurance_product_families.c.id)).scalar_one()
        ver = c.execute(insurance_product_versions.insert().values(
            family_id=fam, version_label="1").returning(insurance_product_versions.c.id)).scalar_one()
    return carrier, ver


def _org(c):
    return c.execute(relationship_entities.insert().values(
        entity_type="organization", name=f"Org {_sfx()}", details={}, active=True
    ).returning(relationship_entities.c.id)).scalar_one()


def _user(c):
    s = _sfx()
    return c.execute(users.insert().values(
        email=f"u-{s}@e.com", normalized_email=f"u-{s}@e.com", display_name="U",
        auth_subject=f"sub-{s}", status="active").returning(users.c.id)).scalar_one()


def _rich_book():
    """One org-owned policy with a producer + commission variance + a licensing record + a
    scheduled overdue review, then a scan to raise the operational exceptions."""
    carrier, ver = _carrier_version()
    with engine.begin() as c:
        org, prod = _org(c), _user(c)
    policy = ins.create_policy(_p(), carrier_id=carrier, product_version_id=ver,
                               organization_id=org, status="issued", face_amount=250000)
    ins.add_producer(_p(), policy["id"], producer_entity_type="user", producer_entity_id=prod,
                     producer_role="writing_agent", split_percentage=100)
    cid = com.generate_expected(_p(), policy_id=policy["id"], basis_amount=1000)["created"][0]["id"]
    com.record_received(_p(), cid, received_amount=600)  # -> variance
    lic.record_license(_p(), producer_user_id=prod, state="CA",
                       expiry_date=date.today() + timedelta(days=30))
    ins.schedule_review(_p(), review_type="annual", due_date=date.today() - timedelta(days=5),
                        policy_id=policy["id"])
    det.run_insurance_scan(actor_user_id=1)  # raise review-overdue + commission-variance
    return policy["id"], org


# --- aggregation correctness -------------------------------------------------

def test_dashboard_aggregates_all_domains():
    _rich_book()
    d = rep.operations_dashboard(_p())
    s = d["sections"]
    assert d["boundary"] == "firm_internal_staff"
    assert s["pipeline"]["policy_count"] >= 1
    assert s["reviews"]["total"] >= 1
    assert s["exceptions"]["open"] >= 1
    assert "INS_COMMISSION_VARIANCE" in s["exceptions"]["by_code"]
    assert s["commissions"]["expected_total"] >= 1000.0
    assert s["licensing"]["license_count"] >= 1
    # work-queue depths reuse the shared work_items + queue criteria
    codes = {q["code"] for q in s["work_queues"]}
    assert "insurance_exceptions" in codes and "insurance_commissions" in codes
    assert next(q for q in s["work_queues"] if q["code"] == "insurance_exceptions")["count"] >= 1


# --- proportional to capabilities --------------------------------------------

def test_sections_are_proportional_to_capabilities():
    full = rep.operations_dashboard(_p(ALL_CAPS))
    assert set(full["sections_included"]) == {"pipeline", "reviews", "exceptions", "work_queues",
                                              "commissions", "licensing", "portal_adoption"}

    base = rep.operations_dashboard(_p(frozenset({"insurance.read"})))
    assert base["sections_included"] == ["pipeline", "reviews"]           # no financial/licensing/etc.

    plus_comm = rep.operations_dashboard(_p(frozenset({"insurance.read", "insurance.commissions.read"})))
    assert "commissions" in plus_comm["sections_included"]
    assert "licensing" not in plus_comm["sections_included"]              # not granted -> omitted


# --- record scope applied before aggregation ---------------------------------

def test_pipeline_is_record_scoped():
    _rich_book()
    firm = rep.operations_dashboard(_p(frozenset({"insurance.read", "record.read_all"})))
    scoped_out = rep.operations_dashboard(
        Principal(987654, "z@e.com", "Z", frozenset({"insurance.read"})))  # no read_all, no assignments
    assert firm["sections"]["pipeline"]["policy_count"] >= 1
    assert scoped_out["sections"]["pipeline"]["policy_count"] == 0        # sees nothing in scope


# --- staff-gated (defense in depth) ------------------------------------------

def test_dashboard_requires_insurance_read():
    with pytest.raises(PermissionError):
        rep.operations_dashboard(Principal(3, "n@e.com", "N", frozenset()))


# --- no AD-5 / compliance-determination content ------------------------------

def test_dashboard_has_no_compliance_or_determination_fields():
    _rich_book()
    blob = repr(rep.operations_dashboard(_p())).lower()
    for bad in ("suitab", "replacement", "1035", "compliance", "determination",
                "validate", "recommend", "certif"):
        assert bad not in blob, f"AD-5-gated concept '{bad}' leaked into the dashboard"
