"""Insurance policyholder portal surface (Release 0.10.0, Phase 7 — reuse the portal).

A read-only, portal-safe view of a policyholder's OWN policies through the **existing** portal
framework (`portal_scope` / grants) — there is no insurance-specific portal engine, auth,
session, or scope model. Scope is **opt-in**: only a portal grant that allows the ``insurance``
permission sees anything (least privilege).

Proportional disclosure: policy summary + coverages + riders + the policyholder's own
owner/insured/beneficiary **designations**. It NEVER exposes producers, commissions /
compensation / splits, licensing/CE, exceptions, underwriting-internal notes, or internal
metadata — commission/compensation is firm-internal, and client-facing exception visibility is
out of scope. Factual policy data only: no suitability/replacement/1035 determination is shown,
so the AD-5 gate is unaffected.
"""
from __future__ import annotations

from sqlalchemy import or_, select

from app.db import (
    engine,
    insurance_coverages,
    insurance_policies,
    insurance_policy_parties,
    insurance_riders,
)
from app.portal.service import portal_scope
from app.services.insurance import _entity_name


def _scoped_policy_query(scope):
    """A SELECT over the policies this portal scope may see, or None when the scope is empty
    (no ``insurance``-permitted grant → the caller returns nothing / 404s)."""
    clauses = []
    if scope["person_ids"]:
        clauses.append(insurance_policies.c.person_id.in_(scope["person_ids"]))
    if scope["shared_household_ids"]:
        clauses.append(insurance_policies.c.household_id.in_(scope["shared_household_ids"]))
    if scope["organization_ids"]:
        clauses.append(insurance_policies.c.organization_id.in_(scope["organization_ids"]))
    if not clauses:
        return None
    return select(insurance_policies).where(or_(*clauses))


def _summary(c, row):
    """Portal-safe policy summary — no producers, commissions, underwriting internals, or metadata."""
    return {
        "id": row["id"],
        "policy_number": row["policy_number"],
        "status": row["status"],
        "issue_date": row["issue_date"],
        "face_amount": row["face_amount"],
        "premium_amount": row["premium_amount"],
        "premium_mode": row["premium_mode"],
        "carrier_name": _entity_name(c, "organization", row["carrier_id"]),
    }


def portal_policies(principal, scope=None):
    """Every policy in the portal account's ``insurance``-permitted scope (summary projection)."""
    scope = scope or portal_scope(principal.account_id, permission="insurance")
    query = _scoped_policy_query(scope)
    if query is None:
        return []
    with engine.connect() as c:
        return [_summary(c, r) for r in c.execute(
            query.order_by(insurance_policies.c.id.desc())).mappings()]


def portal_policy_detail(principal, policy_id, scope=None):
    """One policy's portal-safe detail, or None when out-of-scope/unknown — the route maps None
    to 404 so existence is never disclosed. Includes coverages, riders, and the policyholder's
    own owner/insured/beneficiary designations (``insurance_policy_parties`` — never the separate
    ``insurance_policy_producers`` table)."""
    scope = scope or portal_scope(principal.account_id, permission="insurance")
    query = _scoped_policy_query(scope)
    if query is None:
        return None
    with engine.connect() as c:
        row = c.execute(query.where(insurance_policies.c.id == policy_id)).mappings().one_or_none()
        if row is None:
            return None

        def children(table):
            return c.execute(select(table).where(table.c.policy_id == policy_id)).mappings().all()

        coverages = [{"coverage_type": x["coverage_type"], "face_amount": x["face_amount"]}
                     for x in children(insurance_coverages)]
        riders = [{"rider_type": x["rider_type"], "description": x["description"],
                   "face_amount": x["face_amount"]} for x in children(insurance_riders)]
        parties = [{"party_role": x["party_role"],
                    "party_name": _entity_name(c, x["party_entity_type"], x["party_entity_id"]),
                    "designation": x["designation"],
                    "share_percentage": x["share_percentage"],
                    "is_primary_insured": x["is_primary_insured"]}
                   for x in children(insurance_policy_parties)]
        return {**_summary(c, row), "coverages": coverages, "riders": riders, "parties": parties}
