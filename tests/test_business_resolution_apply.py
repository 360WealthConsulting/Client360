"""Bounded SAFE_OWNERSHIP apply — the only writer for derived business ownership.

Proves the gates that make this safe to run against real data: only SAFE_OWNERSHIP +
``apply_eligible`` may write, the resolver is re-run at apply time (a stale preview cannot authorize
an edge), ownership lands on the CANONICAL person and never the stale duplicate, percentages stay
NULL, repeat applies are idempotent, and people / household membership / document ownership are
never touched.

Pullen Homes is the mandatory regression. Production identities are business entity 135 (Pullen
Homes Inc), stale Norm Pullen 3710, canonical Norman Pullen 7783, household 215; the test builds
that exact SHAPE with tag-unique fixtures because the suite runs against a disposable test database
and must never depend on production row ids.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from app.db import (
    audit_events,
    documents,
    engine,
    household_relationships,
    people,
    relationship_entities,
    relationship_ownership,
    relationship_types,
    relationships,
    source_contacts,
)
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import business_resolution_apply as bra
from app.services.business_resolution_apply import (
    EVIDENCE_SOURCE,
    BusinessResolutionApplyError,
    apply_business_resolution,
    review_overview,
)
from app.services.business_workspace import get_business_workspace
from tests._portal_util import fake_request, render
from tests.test_business_resolution import (
    _cleanup,
    _mk_business,
    _mk_doc,
    _mk_household,
    _mk_person,
    _mk_wb,
)

# record.write_all bypasses organization-anchored record scope; organization.write is the capability
# record_ownership itself demands.
ADMIN = Principal(1, "admin@t", "Admin",
                  frozenset({"organization.write", "organization.read",
                             "record.write_all", "record.read_all"}))
READER = Principal(2, "staff@t", "Staff", frozenset({"organization.read", "record.read_all"}))
UNSCOPED = Principal(3, "unscoped@t", "Unscoped", frozenset({"organization.write"}))


def _tag():
    return "BRA" + uuid.uuid4().hex[:8]


def _owner_edges(business_id):
    """(person_id, ownership_percentage, evidence_source) for every active ownership edge in."""
    owner_entity = relationship_entities.alias("oe")
    with engine.connect() as c:
        return sorted(c.execute(
            select(owner_entity.c.person_id, relationship_ownership.c.ownership_percentage,
                   relationship_ownership.c.evidence_source)
            .select_from(
                relationships
                .join(relationship_types,
                      relationship_types.c.id == relationships.c.relationship_type_id)
                .join(owner_entity, owner_entity.c.id == relationships.c.from_entity_id)
                .outerjoin(relationship_ownership,
                           relationship_ownership.c.relationship_id == relationships.c.id))
            .where(relationships.c.to_entity_id == business_id,
                   relationships.c.active.is_(True),
                   relationship_types.c.category.in_(("ownership", "org_structure")))
        ).all(), key=lambda r: (r[0] or 0))


def _person_row(pid):
    with engine.connect() as c:
        return c.execute(select(people).where(people.c.id == pid)).mappings().one()


def _household_rows(pids):
    with engine.connect() as c:
        return sorted(c.execute(
            select(household_relationships.c.person_id, household_relationships.c.household_id,
                   household_relationships.c.relationship_type)
            .where(household_relationships.c.person_id.in_(pids))).all())


def _doc_ownership(business_id):
    with engine.connect() as c:
        return sorted(c.execute(
            select(documents.c.id, documents.c.person_id, documents.c.household_id,
                   documents.c.organization_id)
            .where(documents.c.organization_id == business_id)).all())


def _pullen(c, tag):
    """The Pullen shape: canonical Norman (household) + Julie Ann, a stale Norm with no provenance,
    the business, a corroborating document, and the Wealthbox 'Owner' contact on the STALE person."""
    hh = _mk_household(c, tag, "Norman & Julie Ann Pullen Household")
    norman = _mk_person(c, tag, "Norman", "Pullen", household_id=hh)
    julie = _mk_person(c, tag, "Julie Ann", "Pullen", household_id=hh)
    stale = _mk_person(c, tag, "Norm", "Pullen")
    biz = _mk_business(c, f"Pullen Homes Inc {tag}")
    _mk_doc(c, tag, organization_id=biz, household_id=hh)
    _mk_wb(c, tag, stale, "Norm", "Pullen", f"Pullen Homes Inc {tag}", "Owner")
    return {"hh": hh, "norman": norman, "julie": julie, "stale": stale, "biz": biz}


# --------------------------------------------------------------------- A / E: apply + no percentage
def test_safe_ownership_applies_and_leaves_percentage_null():
    tag = _tag()
    try:
        with engine.begin() as c:
            karl = _mk_person(c, tag, "Karl", "Armbrust", doc=True)
            biz = _mk_business(c, f"Crossbow Services LLC {tag}")
            _mk_wb(c, tag, karl, "Karl", "Armbrust", f"Crossbow Services LLC {tag}", "Owner")
        assert _owner_edges(biz) == []

        result = apply_business_resolution(principal=ADMIN, business_id=biz, dry_run=False,
                                           request_id="t")
        assert result["ok"] is True and result["applied"] is True and result["dry_run"] is False
        assert result["bucket"] == "SAFE_OWNERSHIP" and result["apply_eligible"] is True
        assert result["canonical_person_ids"] == [karl]
        assert result["relationships_created"] == 1 and result["relationships_reused"] == 0
        considered = result["owners_considered"]
        assert len(considered) == 1 and considered[0]["action"] == "created"
        assert considered[0]["relationship_id"] and considered[0]["ownership_id"]

        edges = _owner_edges(biz)
        assert [e[0] for e in edges] == [karl]
        assert edges[0][1] is None                     # (E) ownership_percentage NEVER inferred
        assert edges[0][2] == EVIDENCE_SOURCE          # resolver + Wealthbox evidence recorded

        with engine.connect() as c:                    # existing record_ownership audit preserved
            assert c.scalar(select(audit_events.c.id).where(
                audit_events.c.action == "organization.ownership.recorded",
                audit_events.c.entity_id == str(biz)).limit(1)) is not None
    finally:
        _cleanup(tag)


def test_dry_run_is_the_default_and_writes_nothing():
    tag = _tag()
    try:
        with engine.begin() as c:
            karl = _mk_person(c, tag, "Dana", "Ledbetter", doc=True)
            biz = _mk_business(c, f"Ledbetter Supply LLC {tag}")
            _mk_wb(c, tag, karl, "Dana", "Ledbetter", f"Ledbetter Supply LLC {tag}", "Owner")
        result = apply_business_resolution(principal=ADMIN, business_id=biz)   # no dry_run argument
        assert result["ok"] is True and result["dry_run"] is True and result["applied"] is False
        assert result["owners_considered"][0]["action"] == "would_create"
        assert _owner_edges(biz) == []
    finally:
        _cleanup(tag)


# --------------------------------------------------------------------- B: non-SAFE buckets refused
def test_association_only_bucket_cannot_apply():
    tag = _tag()
    try:
        with engine.begin() as c:
            barry = _mk_person(c, tag, "Barry", "Beckner", doc=True)
            biz = _mk_business(c, f"Cave Spring Painting {tag}")
            _mk_wb(c, tag, barry, "Barry", "Beckner", f"Cave Spring Painting {tag}", "President")
        r = apply_business_resolution(principal=ADMIN, business_id=biz, dry_run=False)
        assert r["ok"] is False and r["bucket"] == "SAFE_ASSOCIATION_ONLY"
        assert "only SAFE_OWNERSHIP" in r["reason"]
        assert _owner_edges(biz) == []
    finally:
        _cleanup(tag)


def test_person_identity_review_bucket_cannot_apply():
    tag = _tag()
    try:
        with engine.begin() as c:
            stale = _mk_person(c, tag, "Andrew", "Grinder")          # stale, no canonical twin
            biz = _mk_business(c, f"Grinder Works LLC {tag}")
            _mk_wb(c, tag, stale, "Andrew", "Grinder", f"Grinder Works LLC {tag}", "Owner")
        r = apply_business_resolution(principal=ADMIN, business_id=biz, dry_run=False)
        assert r["ok"] is False and r["bucket"] == "PERSON_IDENTITY_REVIEW"
        assert _owner_edges(biz) == []
    finally:
        _cleanup(tag)


def test_duplicate_business_review_bucket_cannot_apply():
    tag = _tag()
    try:
        with engine.begin() as c:
            marty = _mk_person(c, tag, "Marty", "Maxwell", doc=True)
            b1 = _mk_business(c, f"Haul-Max Trucking LLC {tag}")
            b2 = _mk_business(c, f"HaulMax Trucking LLC {tag}")
            _mk_wb(c, tag, marty, "Marty", "Maxwell", f"Haul-Max Trucking LLC {tag}", "Owner")
        for biz in (b1, b2):
            r = apply_business_resolution(principal=ADMIN, business_id=biz, dry_run=False)
            assert r["ok"] is False and r["bucket"] == "DUPLICATE_BUSINESS_REVIEW"
            assert _owner_edges(biz) == []
    finally:
        _cleanup(tag)


def test_unresolved_bucket_cannot_apply():
    tag = _tag()
    try:
        with engine.begin() as c:
            biz = _mk_business(c, f"Nobody Knows Inc {tag}")         # no Wealthbox company match
        r = apply_business_resolution(principal=ADMIN, business_id=biz, dry_run=False)
        assert r["ok"] is False and r["bucket"] == "UNRESOLVED"
        assert _owner_edges(biz) == []
    finally:
        _cleanup(tag)


def test_unknown_business_is_refused():
    r = apply_business_resolution(principal=ADMIN, business_id=2_000_000_000, dry_run=False)
    assert r["ok"] is False and r["bucket"] is None
    assert "not a known business entity" in r["reason"]


def test_non_positive_business_id_is_rejected():
    with pytest.raises(BusinessResolutionApplyError):
        apply_business_resolution(principal=ADMIN, business_id=0, dry_run=False)


# --------------------------------------------------------------------- C: apply_eligible gate
def test_apply_eligible_false_cannot_apply_even_in_safe_ownership(monkeypatch):
    """The apply_eligible flag is an INDEPENDENT gate: a record sitting in SAFE_OWNERSHIP with the
    flag cleared must still be refused, so a future resolver change that clears the flag without
    moving the bucket cannot leak a write."""
    tag = _tag()
    try:
        with engine.begin() as c:
            pat = _mk_person(c, tag, "Pat", "Quillen", doc=True)     # noqa: F841 — evidence only
            biz = _mk_business(c, f"Quillen Metalworks LLC {tag}")
            _mk_wb(c, tag, pat, "Pat", "Quillen", f"Quillen Metalworks LLC {tag}", "Owner")

        real = bra.resolve_business_relationships

        def _flag_cleared(**kwargs):
            report = real(**kwargs)
            for rec in report["SAFE_OWNERSHIP"]:
                rec["apply_eligible"] = False
            return report

        monkeypatch.setattr(bra, "resolve_business_relationships", _flag_cleared)
        r = apply_business_resolution(principal=ADMIN, business_id=biz, dry_run=False)
        assert r["ok"] is False and r["bucket"] == "SAFE_OWNERSHIP"
        assert r["reason"] == "record is not apply_eligible"
        assert _owner_edges(biz) == []
    finally:
        _cleanup(tag)


# --------------------------------------------------------------------- D: idempotency
def test_repeated_apply_is_idempotent():
    tag = _tag()
    try:
        with engine.begin() as c:
            a = _mk_person(c, tag, "Alan", "Mignard", doc=True)
            b = _mk_person(c, tag, "Beth", "Mignard", doc=True)
            biz = _mk_business(c, f"Mignard Company LLC {tag}")
            _mk_wb(c, tag, a, "Alan", "Mignard", f"Mignard Company LLC {tag}", "Owner")
            _mk_wb(c, tag, b, "Beth", "Mignard", f"Mignard Company LLC {tag}", "Owner")

        first = apply_business_resolution(principal=ADMIN, business_id=biz, dry_run=False)
        assert first["relationships_created"] == 2 and first["relationships_reused"] == 0
        after_first = _owner_edges(biz)

        second = apply_business_resolution(principal=ADMIN, business_id=biz, dry_run=False)
        assert second["ok"] is True
        assert second["relationships_created"] == 0 and second["relationships_reused"] == 2
        assert all(o["action"] == "reused" for o in second["owners_considered"])
        assert _owner_edges(biz) == after_first            # no duplicate edges, no changed detail
        assert sorted(e[0] for e in after_first) == sorted([a, b])
        assert all(e[1] is None for e in after_first)      # still no percentage
    finally:
        _cleanup(tag)


# --------------------------------------------------------------------- F/G/H/I + M: Pullen
def test_pullen_writes_canonical_only_and_touches_nothing_else():
    tag = _tag()
    try:
        with engine.begin() as c:
            ids = _pullen(c, tag)
        biz, norman, julie, stale, hh = (ids["biz"], ids["norman"], ids["julie"],
                                         ids["stale"], ids["hh"])

        stale_before = dict(_person_row(stale))
        norman_before = dict(_person_row(norman))
        households_before = _household_rows([norman, julie, stale])
        docs_before = _doc_ownership(biz)

        result = apply_business_resolution(principal=ADMIN, business_id=biz, dry_run=False,
                                           request_id="t")
        assert result["ok"] is True and result["bucket"] == "SAFE_OWNERSHIP"

        # (F) ownership written to the canonical person ONLY — never the stale duplicate.
        edges = _owner_edges(biz)
        assert [e[0] for e in edges] == [norman]
        assert stale not in {e[0] for e in edges}
        owner = result["owners_considered"][0]
        assert owner["canonical_person_id"] == norman
        assert owner["requires_reconciliation"] is True and owner["stale_person_id"] == stale

        # (G) the stale person row is not modified in any way (no merge/deactivate/delete).
        assert dict(_person_row(stale)) == stale_before
        assert dict(_person_row(norman)) == norman_before

        # (H) household membership untouched.
        assert _household_rows([norman, julie, stale]) == households_before

        # (I) document ownership untouched.
        assert _doc_ownership(biz) == docs_before

        # (M) the business workspace now resolves through the real ownership graph.
        ws = get_business_workspace(biz)
        assert [o["person_id"] for o in ws["owners"]] == [norman]
        assert ws["owners"][0]["name"] == f"Norman Pullen{tag}"
        assert ws["owners"][0]["workspace_url"] == f"/client/{norman}"
        assert ws["owners"][0]["ownership_percentage"] is None
        assert EVIDENCE_SOURCE in ws["provenance"]
        assert hh in {h["household_id"] for h in ws["related_households"]}
    finally:
        _cleanup(tag)


# --------------------------------------------------------------------- J/K: authorization
def test_missing_organization_write_is_denied():
    tag = _tag()
    try:
        with engine.begin() as c:
            ids = _pullen(c, tag)
        with pytest.raises(PermissionError) as exc:
            apply_business_resolution(principal=READER, business_id=ids["biz"], dry_run=False)
        assert "organization.write" in str(exc.value)
        assert _owner_edges(ids["biz"]) == []
        # denied even for a dry run — the capability is checked before any resolution work
        with pytest.raises(PermissionError):
            apply_business_resolution(principal=READER, business_id=ids["biz"])
    finally:
        _cleanup(tag)


def test_out_of_scope_organization_is_denied_by_record_ownership():
    """The principal holds organization.write but no record scope on the organization; the
    canonical service's scope check must still refuse (no bypass principal here)."""
    tag = _tag()
    try:
        with engine.begin() as c:
            ids = _pullen(c, tag)
        with pytest.raises(PermissionError):
            apply_business_resolution(principal=UNSCOPED, business_id=ids["biz"], dry_run=False)
        assert _owner_edges(ids["biz"]) == []
    finally:
        _cleanup(tag)


def test_routes_are_capability_gated_and_registered():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/admin/business-resolution" in paths
    assert "/admin/business-resolution/{business_id}/apply" in paths

    read_dep = require_capability("organization.read")
    write_dep = require_capability("organization.write")
    assert write_dep(principal=ADMIN) is ADMIN
    assert read_dep(principal=READER) is READER
    with pytest.raises(HTTPException) as exc:
        write_dep(principal=READER)
    assert exc.value.status_code == 403


def test_no_bulk_apply_endpoint_exists():
    from app.main import app
    paths = [p for p in (getattr(r, "path", "") for r in app.routes)
             if p.startswith("/admin/business-resolution")]
    assert sorted(paths) == ["/admin/business-resolution",
                             "/admin/business-resolution/{business_id}/apply"]


# --------------------------------------------------------------------- L: re-resolution at apply time
def test_resolver_is_rerun_so_stale_preview_cannot_authorize_a_write():
    """A page rendered while the evidence said 'Owner' must not be able to write once the evidence
    says 'President'. The apply path re-resolves and refuses on the CURRENT bucket."""
    tag = _tag()
    try:
        with engine.begin() as c:
            gail = _mk_person(c, tag, "Gail", "Thorsby", doc=True)   # noqa: F841 — evidence only
            biz = _mk_business(c, f"Thorsby Roofing LLC {tag}")
            sc = _mk_wb(c, tag, gail, "Gail", "Thorsby", f"Thorsby Roofing LLC {tag}", "Owner")

        preview = apply_business_resolution(principal=ADMIN, business_id=biz)   # the stale preview
        assert preview["ok"] is True and preview["bucket"] == "SAFE_OWNERSHIP"

        with engine.begin() as c:      # evidence changes underneath the rendered page
            c.execute(update(source_contacts).where(source_contacts.c.id == sc)
                      .values(raw_data={"company_name": f"Thorsby Roofing LLC {tag}",
                                        "job_title": "President"}))

        r = apply_business_resolution(principal=ADMIN, business_id=biz, dry_run=False)
        assert r["ok"] is False and r["bucket"] == "SAFE_ASSOCIATION_ONLY"
        assert _owner_edges(biz) == []
    finally:
        _cleanup(tag)


def test_resolver_rerun_catches_a_business_that_became_a_duplicate():
    tag = _tag()
    try:
        with engine.begin() as c:
            sam = _mk_person(c, tag, "Sam", "Ovitt", doc=True)
            biz = _mk_business(c, f"Ovitt-Field Services LLC {tag}")
            _mk_wb(c, tag, sam, "Sam", "Ovitt", f"Ovitt-Field Services LLC {tag}", "Owner")
        assert apply_business_resolution(principal=ADMIN, business_id=biz)["bucket"] == "SAFE_OWNERSHIP"

        with engine.begin() as c:      # a colliding entity appears after the preview
            _mk_business(c, f"OvittField Services LLC {tag}")

        r = apply_business_resolution(principal=ADMIN, business_id=biz, dry_run=False)
        assert r["ok"] is False and r["bucket"] == "DUPLICATE_BUSINESS_REVIEW"
        assert _owner_edges(biz) == []
    finally:
        _cleanup(tag)


# --------------------------------------------------------------------- review surface
def test_review_overview_reports_applied_state_and_writes_nothing():
    tag = _tag()
    try:
        with engine.begin() as c:
            ids = _pullen(c, tag)
        biz = ids["biz"]

        before = review_overview()
        mine = next(c for c in before["candidates"] if c["business_id"] == biz)
        assert mine["applied_state"] == "not_applied"
        assert mine["owners"][0]["canonical_person_id"] == ids["norman"]
        assert mine["reconciliations"][0]["canonical_person_id"] == ids["norman"]
        assert any(h["household_id"] == ids["hh"] for h in mine["household_associations"])
        assert before["summary"]["SAFE_OWNERSHIP"] >= 1
        assert _owner_edges(biz) == []                 # the review page is a pure read

        apply_business_resolution(principal=ADMIN, business_id=biz, dry_run=False)
        after = next(c for c in review_overview()["candidates"] if c["business_id"] == biz)
        assert after["applied_state"] == "applied"
        assert after["owners"][0]["already_applied"] is True
    finally:
        _cleanup(tag)


# --------------------------------------------------------------------- admin surface renders
def test_review_page_renders_candidate_and_apply_form():
    tag = _tag()
    try:
        with engine.begin() as c:
            ids = _pullen(c, tag)
        from app.routes.admin_business_resolution import business_resolution_review
        html = render(business_resolution_review(
            request=fake_request("/admin/business-resolution", state_principal=ADMIN),
            principal=ADMIN))
        assert f"Pullen Homes Inc {tag}" in html
        assert f'action="/admin/business-resolution/{ids["biz"]}/apply"' in html
        assert f'/client/{ids["norman"]}' in html          # canonical owner link
        assert f'/client/{ids["stale"]}' not in html       # stale person is never linked as owner
        assert "not applied" in html
        assert _owner_edges(ids["biz"]) == []              # rendering writes nothing
    finally:
        _cleanup(tag)


def test_apply_route_requires_the_confirmation_value():
    tag = _tag()
    try:
        with engine.begin() as c:
            ids = _pullen(c, tag)
        from app.routes.admin_business_resolution import business_resolution_apply
        resp = business_resolution_apply(
            request=fake_request("/admin/business-resolution", method="POST",
                                 state_principal=ADMIN),
            business_id=ids["biz"], confirm="", principal=ADMIN)
        assert resp.status_code == 303
        assert _owner_edges(ids["biz"]) == []              # unconfirmed POST writes nothing
    finally:
        _cleanup(tag)


def test_apply_route_returns_403_when_scope_is_missing():
    tag = _tag()
    try:
        with engine.begin() as c:
            ids = _pullen(c, tag)
        from app.routes.admin_business_resolution import business_resolution_apply
        with pytest.raises(HTTPException) as exc:
            business_resolution_apply(
                request=fake_request("/admin/business-resolution", method="POST",
                                     state_principal=UNSCOPED),
                business_id=ids["biz"], confirm="yes", principal=UNSCOPED)
        assert exc.value.status_code == 403
        assert _owner_edges(ids["biz"]) == []
    finally:
        _cleanup(tag)
