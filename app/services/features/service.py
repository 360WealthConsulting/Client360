"""Client Feature & Access Control — the effective-access engine + audited CRUD.

Precedence (the "firm ceiling" model). Firm-wide state is a CEILING that can only RESTRICT; product
entitlement + per-client override determine access within that ceiling:

    unknown feature ............................. DENY (fail-closed)
    firm DISABLED ............................... DENY (a client override can never re-open it)
    firm INTERNAL_ONLY (client actor) ........... DENY (never exposed to a normal client)
    firm BETA (client actor) .................... DENY unless the subject has an explicit ENABLE override
    per-client override DISABLE ................. DENY
    per-client override ENABLE .................. ALLOW (within the ceiling)
    else product entitlement default ............ ALLOW iff subject holds the feature's product
    else ........................................ DENY (default-deny)

``core`` is the baseline product every client holds, so existing Core functionality keeps working; the
wealth/business features default to firm ``disabled`` in the catalog, so no client reaches an unfinished
feature until an administrator enables it firm-wide. Client STATUS is tracked but never grants access.

Every mutation writes an existing-infrastructure audit event capturing who/target/feature/prev/new/time.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.db import (
    client_feature_overrides,
    client_product_entitlements,
    client_status,
    engine,
    firm_feature_controls,
)
from app.security.audit import write_audit_event
from app.services.features import catalog
from app.services.features.catalog import (
    FIRM_BETA,
    FIRM_DISABLED,
    FIRM_INTERNAL_ONLY,
    OVERRIDE_DISABLE,
    OVERRIDE_ENABLE,
    get_feature,
)


class FrameworkUnavailable(RuntimeError):
    """The caf01 tables are not present (migration not applied). Mutations refuse; reads fail closed."""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    feature: str
    firm_state: str
    source: str          # "override" | "entitlement" | "default" | "firm" | "catalog"
    product: str | None = None


def _now():
    return datetime.now(UTC)


def _require(table, name):
    if table is None:
        raise FrameworkUnavailable(f"{name} table missing — apply migration caf01 first.")
    return table


def _validate_subject(subject_type):
    if subject_type not in catalog.SUBJECT_TYPES:
        raise ValueError(f"invalid subject_type {subject_type!r}")


# --- reads (fail closed if the framework tables are absent) -------------------

def firm_state(feature_key: str) -> str:
    """Firm-wide state for a feature: the persisted control if any, else the catalog default."""
    if firm_feature_controls is not None:
        with engine.connect() as conn:
            row = conn.execute(select(firm_feature_controls.c.state).where(
                firm_feature_controls.c.feature_key == feature_key)).scalar_one_or_none()
        if row is not None:
            return row
    return catalog.default_firm_state(feature_key)


def entitlements(subject_type: str, subject_id: int) -> set[str]:
    """Products the subject holds. ``core`` is always present (baseline); wealth/business need a row."""
    products = {"core"}
    if client_product_entitlements is not None:
        with engine.connect() as conn:
            rows = conn.execute(select(client_product_entitlements.c.product).where(
                client_product_entitlements.c.subject_type == subject_type,
                client_product_entitlements.c.subject_id == subject_id)).scalars().all()
        products |= set(rows)
    return products


def override(subject_type: str, subject_id: int, feature_key: str) -> str | None:
    """The per-subject override state (``enable``/``disable``) or None (INHERIT)."""
    if client_feature_overrides is None:
        return None
    with engine.connect() as conn:
        return conn.execute(select(client_feature_overrides.c.state).where(
            client_feature_overrides.c.subject_type == subject_type,
            client_feature_overrides.c.subject_id == subject_id,
            client_feature_overrides.c.feature_key == feature_key)).scalar_one_or_none()


def get_status(subject_type: str, subject_id: int) -> dict | None:
    if client_status is None:
        return None
    with engine.connect() as conn:
        row = conn.execute(select(client_status.c.status, client_status.c.disposition).where(
            client_status.c.subject_type == subject_type,
            client_status.c.subject_id == subject_id)).mappings().first()
    return dict(row) if row else None


# --- the effective-access engine --------------------------------------------

def effective_access(subject_type: str, subject_id: int, feature_key: str, *, actor: str = "client") -> Decision:
    """Compute effective access for a subject + feature.

    Precedence (approved): Firm control → PRODUCT ENTITLEMENT → feature override → effective access.
    The product entitlement is a HARD BOUNDARY that sits ABOVE per-client overrides, so an override can
    never grant a Wealth/Business feature the subject is not entitled to (a feature override must never
    become a shadow product entitlement). ``core`` is the baseline product every client holds.
    ``actor='client'`` applies the full client ceiling; ``actor='staff'`` is an internal view (sees
    internal_only/beta) but firm DISABLED and the entitlement boundary still apply. Fails closed on an
    unknown feature."""
    feat = get_feature(feature_key)
    if feat is None:
        return Decision(False, "unknown_feature", feature_key, "disabled", "catalog", None)

    fs = firm_state(feature_key)
    ov = override(subject_type, subject_id, feature_key)

    # 1. Firm-wide ceiling (only restricts).
    if fs == FIRM_DISABLED:
        return Decision(False, "firm_disabled", feature_key, fs, "firm", feat.product)
    if fs == FIRM_INTERNAL_ONLY and actor == "client":
        return Decision(False, "internal_only", feature_key, fs, "firm", feat.product)

    # 2. Product entitlement boundary — ABOVE overrides. No override can cross this.
    if feat.product not in entitlements(subject_type, subject_id):
        return Decision(False, "entitlement_required", feature_key, fs, "entitlement", feat.product)

    # 3. Beta eligibility (within the entitled product): reaches only explicitly enabled clients.
    if fs == FIRM_BETA and actor == "client" and ov != OVERRIDE_ENABLE:
        return Decision(False, "beta_not_eligible", feature_key, fs, "firm", feat.product)

    # 4. Feature override within the entitled product.
    if ov == OVERRIDE_DISABLE:
        return Decision(False, "client_disabled", feature_key, fs, "override", feat.product)
    if ov == OVERRIDE_ENABLE:
        return Decision(True, "client_enabled", feature_key, fs, "override", feat.product)

    # 5. INHERIT → product default (entitlement already confirmed).
    return Decision(True, "product_default", feature_key, fs, "entitlement", feat.product)


# --- client lifecycle status → portal-access gate ----------------------------

# Only an effective lifecycle of "active" permits normal client portal access. Everything else
# (inactive/historical, archive, prospect, needs_review) fails closed. The ABSENCE of a status row is
# treated as "active" so existing clients keep working; a non-active state must be explicitly set.
_PORTAL_OPEN_LIFECYCLE = {"active"}


def effective_lifecycle(subject_type: str, subject_id: int) -> str:
    """The subject's effective lifecycle state: the resolved ``disposition`` if set, else ``status``,
    else ``active`` (no row). Drives the portal-access status gate."""
    st = get_status(subject_type, subject_id)
    if not st:
        return "active"
    return st.get("disposition") or st.get("status") or "active"


def portal_status_open(subject_type: str, subject_id: int) -> bool:
    """Whether the subject's lifecycle status permits normal client portal access."""
    return effective_lifecycle(subject_type, subject_id) in _PORTAL_OPEN_LIFECYCLE


# --- reason explanations for staff ("why can't the client see this?") --------

_PRODUCT_LABEL = {"core": "360Plus Core", "wealth": "360Plus Wealth", "business": "360Plus Business"}


def explain(decision: Decision) -> str:
    """Human-readable reason for the staff Access & Features screen."""
    product = _PRODUCT_LABEL.get(decision.product or "", decision.product or "")
    return {
        "unknown_feature": "Unknown feature",
        "firm_disabled": "Disabled firm-wide",
        "internal_only": "Internal only — not available to clients",
        "entitlement_required": f"Requires {product}",
        "beta_not_eligible": "Beta — client not enabled",
        "client_disabled": "Disabled for this client",
        "client_enabled": f"Enabled for this client (included path: {product})",
        "product_default": f"Included with {product}",
    }.get(decision.reason, decision.reason)


def feature_report(subject_type: str, subject_id: int, *, actor: str = "client") -> list[dict]:
    """Per-feature effective decision for a subject — for the staff Access & Features panel. Each row
    carries a human-readable ``explanation`` (why it is / isn't available)."""
    out = []
    for f in catalog.FEATURES.values():
        d = effective_access(subject_type, subject_id, f.key, actor=actor)
        ov = override(subject_type, subject_id, f.key)
        out.append({
            "feature": f.key, "label": f.label, "product": f.product,
            "allowed": d.allowed, "reason": d.reason, "explanation": explain(d),
            "firm_state": d.firm_state, "override": ov or "inherit", "inherited": ov is None,
            "source": d.source,
        })
    return out


def subject_access_summary(subject_type: str, subject_id: int) -> dict:
    """Portal-level status for the staff banner: the effective lifecycle + whether normal portal access
    is open for this subject, with a human reason when it is closed."""
    lifecycle = effective_lifecycle(subject_type, subject_id)
    status_open = lifecycle in _PORTAL_OPEN_LIFECYCLE
    portal_feature = effective_access(subject_type, subject_id, "portal_access", actor="client")
    reasons = []
    if not status_open:
        reasons.append(f"Client status: {lifecycle.replace('_', ' ').title()}")
    if not portal_feature.allowed:
        reasons.append("Portal access disabled firm-wide" if portal_feature.reason == "firm_disabled"
                       else "Portal access disabled for this client")
    return {"lifecycle": lifecycle, "portal_open": status_open and portal_feature.allowed,
            "reasons": reasons}


# --- audited mutations -------------------------------------------------------

def _audit(action, entity_type, entity_id, *, actor_user_id, request_id, metadata):
    write_audit_event(action=action, entity_type=entity_type, entity_id=entity_id,
                      actor_user_id=actor_user_id, request_id=request_id or f"features-{uuid.uuid4()}",
                      metadata=metadata)


def set_firm_state(feature_key, state, *, actor_user_id, request_id=None):
    if not catalog.is_registered(feature_key):
        raise ValueError(f"unknown feature {feature_key!r}")
    if state not in catalog.FIRM_STATES:
        raise ValueError(f"invalid firm state {state!r}")
    table = _require(firm_feature_controls, "firm_feature_controls")
    previous = firm_state(feature_key)
    with engine.begin() as conn:
        existing = conn.execute(select(table.c.id).where(table.c.feature_key == feature_key)).scalar_one_or_none()
        if existing is None:
            conn.execute(table.insert().values(feature_key=feature_key, state=state,
                                               updated_by_user_id=actor_user_id, updated_at=_now()))
        else:
            conn.execute(table.update().where(table.c.id == existing).values(
                state=state, updated_by_user_id=actor_user_id, updated_at=_now()))
    _audit("firm.feature.state_changed", "feature", feature_key, actor_user_id=actor_user_id,
           request_id=request_id, metadata={"feature": feature_key, "previous": previous, "new": state})
    return {"feature": feature_key, "previous": previous, "new": state}


def grant_entitlement(subject_type, subject_id, product, *, actor_user_id, request_id=None):
    _validate_subject(subject_type)
    if product not in catalog.PRODUCTS:
        raise ValueError(f"invalid product {product!r}")
    table = _require(client_product_entitlements, "client_product_entitlements")
    with engine.begin() as conn:
        existing = conn.execute(select(table.c.id).where(
            table.c.subject_type == subject_type, table.c.subject_id == subject_id,
            table.c.product == product)).scalar_one_or_none()
        if existing is None:
            conn.execute(table.insert().values(subject_type=subject_type, subject_id=subject_id,
                                               product=product, granted_by_user_id=actor_user_id))
    _audit("client.entitlement.granted", subject_type, subject_id, actor_user_id=actor_user_id,
           request_id=request_id, metadata={"subject_type": subject_type, "subject_id": subject_id,
                                            "product": product, "previous": "absent", "new": "granted"})
    return {"subject_type": subject_type, "subject_id": subject_id, "product": product}


def revoke_entitlement(subject_type, subject_id, product, *, actor_user_id, request_id=None):
    _validate_subject(subject_type)
    if product == "core":
        raise ValueError("core is the baseline product and cannot be revoked")
    table = _require(client_product_entitlements, "client_product_entitlements")
    with engine.begin() as conn:
        conn.execute(delete(table).where(
            table.c.subject_type == subject_type, table.c.subject_id == subject_id,
            table.c.product == product))
    _audit("client.entitlement.revoked", subject_type, subject_id, actor_user_id=actor_user_id,
           request_id=request_id, metadata={"subject_type": subject_type, "subject_id": subject_id,
                                            "product": product, "previous": "granted", "new": "absent"})
    return {"subject_type": subject_type, "subject_id": subject_id, "product": product}


def set_override(subject_type, subject_id, feature_key, state, *, actor_user_id, request_id=None):
    """Set a per-client override. ``state='inherit'`` clears the override (deletes the row)."""
    _validate_subject(subject_type)
    if not catalog.is_registered(feature_key):
        raise ValueError(f"unknown feature {feature_key!r}")
    if state not in catalog.OVERRIDE_STATES:
        raise ValueError(f"invalid override state {state!r}")
    table = _require(client_feature_overrides, "client_feature_overrides")
    previous = override(subject_type, subject_id, feature_key) or "inherit"
    with engine.begin() as conn:
        existing = conn.execute(select(table.c.id).where(
            table.c.subject_type == subject_type, table.c.subject_id == subject_id,
            table.c.feature_key == feature_key)).scalar_one_or_none()
        if state == catalog.OVERRIDE_INHERIT:
            if existing is not None:
                conn.execute(delete(table).where(table.c.id == existing))
        elif existing is None:
            conn.execute(table.insert().values(subject_type=subject_type, subject_id=subject_id,
                                               feature_key=feature_key, state=state,
                                               updated_by_user_id=actor_user_id))
        else:
            conn.execute(table.update().where(table.c.id == existing).values(
                state=state, updated_by_user_id=actor_user_id, updated_at=_now()))
    _audit("client.feature.override_set", subject_type, subject_id, actor_user_id=actor_user_id,
           request_id=request_id, metadata={"subject_type": subject_type, "subject_id": subject_id,
                                            "feature": feature_key, "previous": previous, "new": state})
    return {"subject_type": subject_type, "subject_id": subject_id, "feature": feature_key, "state": state}


def set_status(subject_type, subject_id, status, disposition=None, *, actor_user_id, request_id=None):
    _validate_subject(subject_type)
    if status not in catalog.CLIENT_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    if disposition is not None and disposition not in catalog.CLIENT_DISPOSITIONS:
        raise ValueError(f"invalid disposition {disposition!r}")
    table = _require(client_status, "client_status")
    prev = get_status(subject_type, subject_id) or {"status": None, "disposition": None}
    with engine.begin() as conn:
        existing = conn.execute(select(table.c.id).where(
            table.c.subject_type == subject_type, table.c.subject_id == subject_id)).scalar_one_or_none()
        if existing is None:
            conn.execute(table.insert().values(subject_type=subject_type, subject_id=subject_id,
                                               status=status, disposition=disposition,
                                               updated_by_user_id=actor_user_id))
        else:
            conn.execute(table.update().where(table.c.id == existing).values(
                status=status, disposition=disposition, updated_by_user_id=actor_user_id, updated_at=_now()))
    _audit("client.status.changed", subject_type, subject_id, actor_user_id=actor_user_id,
           request_id=request_id, metadata={"subject_type": subject_type, "subject_id": subject_id,
                                            "previous": prev.get("status"), "new": status,
                                            "disposition": disposition})
    return {"subject_type": subject_type, "subject_id": subject_id, "status": status,
            "disposition": disposition}


# --- portal principal resolution + client check ------------------------------

def household_subject(principal) -> tuple[str, int] | None:
    """The individual-client subject for a PortalPrincipal: their HOUSEHOLD (else person fallback)."""
    from app.db import people
    with engine.connect() as conn:
        household_id = conn.execute(select(people.c.household_id).where(
            people.c.id == principal.person_id)).scalar_one_or_none()
    if household_id is not None:
        return ("household", household_id)
    if principal.person_id is not None:
        return ("person", principal.person_id)
    return None


def portal_access_state(principal) -> tuple[bool, str]:
    """The MASTER client-portal gate (kill switch): (open, reason). Normal client functionality is
    available only when the client's lifecycle status is active AND the ``portal_access`` feature is
    allowed (which folds in a per-client override and the firm-wide portal_access kill switch). Applied
    to the client's household. Staff access is unaffected — this governs the portal principal only."""
    subj = household_subject(principal)
    if subj is None:
        return (False, "no_subject")
    st, sid = subj
    if not portal_status_open(st, sid):
        return (False, f"status_{effective_lifecycle(st, sid)}")
    d = effective_access(st, sid, "portal_access", actor="client")
    if not d.allowed:
        return (False, d.reason)          # firm_disabled (kill switch) or client_disabled
    return (True, "ok")


def client_can(principal, feature_key: str, *, organization_id: int | None = None) -> bool:
    """Server-side check: may THIS portal client use ``feature_key``? Fail-closed for an unknown
    feature, a closed master portal gate, or an unresolvable subject.

    * The MASTER portal gate (status + portal_access kill switch) is enforced first — a disabled portal
      denies all normal client functionality regardless of individual feature settings.
    * Business features are evaluated PER ORGANIZATION: an explicit ``organization_id`` is required, it
      must be one the client is associated with, and only that organization's entitlement/override is
      consulted (a client's access to one business never leaks to another).
    * Core/Wealth features are evaluated on the client's household.
    """
    feat = get_feature(feature_key)
    if feat is None:
        return False                                     # default-deny for unregistered features
    open_, _reason = portal_access_state(principal)
    if not open_:
        return False                                     # kill switch / status closes everything
    if feature_key == "portal_access":
        return True                                      # already validated by the master gate
    if feat.product == "business":
        if organization_id is None:
            return False                                 # fail-closed: business needs explicit org context
        from app.portal.service import portal_base_scope
        # Relationship resolution INSIDE the feature decision — requiring a permission here would be
        # circular.
        org_ids = portal_base_scope(principal.account_id).get("organization_ids") or set()
        if organization_id not in org_ids:
            return False                                 # not one of this client's organizations
        return effective_access("organization", organization_id, feature_key, actor="client").allowed
    subj = household_subject(principal)
    if subj is None:
        return False
    return effective_access(subj[0], subj[1], feature_key, actor="client").allowed
