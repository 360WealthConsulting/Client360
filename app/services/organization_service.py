"""Organization canonical service (Release 0.9.11, Phase 2 — ADR-18).

The single write/read path for Organizations and their operational profile, service
lines, permanent relationship roles, and ownership relationships. An Organization **is**
a ``relationship_entities`` row (reused, not rebuilt); operational fields live in the 1:1
``organization_profiles``; permanent roles reuse the ``relationship_types`` vocabulary;
ownership is a ``relationships`` edge with a typed ``relationship_ownership`` 1:1 detail.

Authorization: creation is capability-gated (``organization.write``); reads/mutations are
Organization-anchored record scope (``organization_in_scope``). The ``ein`` is encrypted at
rest and only returned decrypted with ``benefits.sensitive.read``. Every mutation writes an
audit event and reuses the canonical assignment/relationship services (no duplication).
"""
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import (engine, organization_profiles, organization_service_lines,
    organization_service_roles, record_assignments, relationship_entities,
    relationship_types, relationships, relationship_ownership, service_lines)
from app.security.audit import write_audit_event
from app.security.authorization import organization_in_scope, team_ids
from app.security import benefits_crypto
from app.services.relationships import (create_named_entity, ensure_household_entity,
    ensure_person_entity)
from app.services.work_management import assign_work

ORG_ENTITY_TYPES = frozenset({"business", "trust", "estate", "professional", "insurance_carrier"})
_ACTIVE_STATUSES = frozenset({"prospect", "active", "inactive"})


class OrganizationError(RuntimeError):
    """Bad input for an organization operation."""


class OrganizationNotFound(OrganizationError):
    """Organization (or a child record) does not exist."""


def _rid(request_id):
    return request_id or f"benefits-{uuid.uuid4()}"


def _require(principal, capability):
    if not principal.can(capability):
        raise PermissionError(f"Missing capability: {capability}")


def _require_scope(principal, organization_id, *, write, connection):
    if not organization_in_scope(principal, organization_id, write=write, connection=connection):
        raise PermissionError("Organization is outside your record scope")


def _service_line_id(connection, code):
    sid = connection.scalar(select(service_lines.c.id).where(service_lines.c.code == code))
    if sid is None:
        raise OrganizationError(f"Unknown service line: {code}")
    return sid


def _role_type_id(connection, code):
    rid = connection.scalar(select(relationship_types.c.id).where(relationship_types.c.code == code))
    if rid is None:
        raise OrganizationError(f"Unknown relationship/role type: {code}")
    return rid


# --- organization CRUD -------------------------------------------------------

def create_organization(principal, *, name, entity_type="business", legal_name=None,
                        ein=None, industry=None, naics_code=None, entity_form=None,
                        employee_count_band=None, renewal_month=None, status="prospect",
                        address=None, assign_owner=True, request_id=None):
    """Create an Organization (relationship entity) + its profile. Capability-gated
    (``organization.write``); optionally assigns the creator as the primary record owner
    so subsequent record-scoped reads/writes succeed."""
    _require(principal, "organization.write")
    if not (name or "").strip():
        raise OrganizationError("Organization name is required")
    if entity_type not in ORG_ENTITY_TYPES:
        raise OrganizationError(f"Unsupported organization entity type: {entity_type}")
    if status not in _ACTIVE_STATUSES:
        raise OrganizationError(f"Unsupported status: {status}")
    if renewal_month is not None and not (1 <= int(renewal_month) <= 12):
        raise OrganizationError("renewal_month must be 1-12")
    ein_cipher = benefits_crypto.encrypt(ein) if ein else None

    with engine.begin() as c:
        org_id = create_named_entity(c, entity_type, name.strip())
        c.execute(organization_profiles.insert().values(
            relationship_entity_id=org_id, legal_name=legal_name, ein=ein_cipher,
            industry=industry, naics_code=naics_code, entity_form=entity_form,
            employee_count_band=employee_count_band, renewal_month=renewal_month,
            status=status, address_json=address or {}, created_by_user_id=principal.user_id))

    if assign_owner and principal.user_id is not None:
        try:
            assign_work(entity_type="organization", entity_id=org_id, assignment_role="primary",
                        user_id=principal.user_id, actor_user_id=principal.user_id,
                        reason="Organization created", request_id=_rid(request_id))
        except ValueError:
            pass  # a primary already exists (idempotent create paths)

    write_audit_event(action="organization.created", entity_type="organization", entity_id=org_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"entity_type": entity_type, "status": status})
    return get_organization(org_id, principal=principal)


def get_organization(organization_id, *, principal, include_sensitive=False, connection=None):
    _require(principal, "organization.read")

    def _load(c):
        _require_scope(principal, organization_id, write=False, connection=c)
        ent = c.execute(select(relationship_entities.c.id, relationship_entities.c.entity_type,
                               relationship_entities.c.name, relationship_entities.c.active)
                        .where(relationship_entities.c.id == organization_id)).mappings().one_or_none()
        if ent is None:
            raise OrganizationNotFound(f"Organization {organization_id} not found")
        prof = c.execute(select(organization_profiles)
                         .where(organization_profiles.c.relationship_entity_id == organization_id)).mappings().one_or_none()
        row = {"organization_id": ent["id"], "entity_type": ent["entity_type"],
               "name": ent["name"], "active": ent["active"]}
        if prof:
            row.update({k: prof[k] for k in ("legal_name", "industry", "naics_code",
                        "entity_form", "employee_count_band", "renewal_month", "status", "address_json")})
            if prof["ein"]:
                if include_sensitive and principal.can("benefits.sensitive.read"):
                    row["ein"] = benefits_crypto.decrypt(prof["ein"])
                else:
                    row["ein"] = None
                    row["ein_present"] = True
        return row

    if connection is not None:
        return _load(connection)
    with engine.connect() as c:
        return _load(c)


def update_organization(organization_id, *, principal, request_id=None, **fields):
    _require(principal, "organization.write")
    allowed = {"legal_name", "industry", "naics_code", "entity_form",
               "employee_count_band", "renewal_month", "status", "address_json"}
    values = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if "status" in values and values["status"] not in _ACTIVE_STATUSES:
        raise OrganizationError(f"Unsupported status: {values['status']}")
    if "renewal_month" in values and not (1 <= int(values["renewal_month"]) <= 12):
        raise OrganizationError("renewal_month must be 1-12")
    if fields.get("ein"):
        values["ein"] = benefits_crypto.encrypt(fields["ein"])
    with engine.begin() as c:
        _require_scope(principal, organization_id, write=True, connection=c)
        exists = c.scalar(select(organization_profiles.c.id)
                          .where(organization_profiles.c.relationship_entity_id == organization_id))
        if exists is None:
            raise OrganizationNotFound(f"Organization {organization_id} not found")
        if values:
            c.execute(organization_profiles.update()
                      .where(organization_profiles.c.relationship_entity_id == organization_id).values(**values))
    write_audit_event(action="organization.updated", entity_type="organization", entity_id=organization_id,
                      actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"fields": sorted(k for k in values if k != "ein"),
                                "ein_changed": bool(fields.get("ein"))})
    return get_organization(organization_id, principal=principal)


def list_organizations(principal, *, status=None):
    _require(principal, "organization.read")
    query = (select(relationship_entities.c.id, relationship_entities.c.name,
                    organization_profiles.c.status, organization_profiles.c.industry)
             .select_from(relationship_entities.join(
                 organization_profiles,
                 organization_profiles.c.relationship_entity_id == relationship_entities.c.id)))
    if status:
        query = query.where(organization_profiles.c.status == status)
    with engine.connect() as c:
        if not principal.can("record.read_all"):
            tids = team_ids(c, principal)
            from sqlalchemy import or_, false as sql_false
            from datetime import date as _date
            today = _date.today()
            scope = or_(record_assignments.c.user_id == principal.user_id,
                        record_assignments.c.team_id.in_(tids) if tids else sql_false())
            org_ids = set(c.scalars(select(record_assignments.c.entity_id).where(
                record_assignments.c.entity_type == "organization",
                record_assignments.c.effective_date <= today,
                (record_assignments.c.inactive_date.is_(None)) | (record_assignments.c.inactive_date >= today),
                scope)))
            if not org_ids:
                return []
            query = query.where(relationship_entities.c.id.in_(org_ids))
        return [dict(r) for r in c.execute(query.order_by(relationship_entities.c.name)).mappings()]


# --- service lines -----------------------------------------------------------

def add_service_line(organization_id, service_line_code, *, principal, status="active",
                     since_date=None, renewal_owner_user_id=None, request_id=None):
    _require(principal, "organization.write")
    if status not in _ACTIVE_STATUSES:
        raise OrganizationError(f"Unsupported status: {status}")
    with engine.begin() as c:
        _require_scope(principal, organization_id, write=True, connection=c)
        sid = _service_line_id(c, service_line_code)
        try:
            new_id = c.execute(organization_service_lines.insert().values(
                organization_id=organization_id, service_line_id=sid, status=status,
                since_date=since_date or date.today(), renewal_owner_user_id=renewal_owner_user_id
            ).returning(organization_service_lines.c.id)).scalar_one()
        except IntegrityError:
            raise OrganizationError(f"Service line {service_line_code} already attached")
    write_audit_event(action="organization.service_line.added", entity_type="organization",
                      entity_id=organization_id, actor_user_id=principal.user_id,
                      request_id=_rid(request_id), metadata={"service_line": service_line_code, "status": status})
    return new_id


def set_service_line_status(organization_id, service_line_code, status, *, principal, request_id=None):
    _require(principal, "organization.write")
    if status not in _ACTIVE_STATUSES:
        raise OrganizationError(f"Unsupported status: {status}")
    with engine.begin() as c:
        _require_scope(principal, organization_id, write=True, connection=c)
        sid = _service_line_id(c, service_line_code)
        changed = c.execute(organization_service_lines.update().where(
            organization_service_lines.c.organization_id == organization_id,
            organization_service_lines.c.service_line_id == sid).values(status=status)).rowcount
    if not changed:
        raise OrganizationNotFound("Service line not attached to this organization")
    write_audit_event(action="organization.service_line.updated", entity_type="organization",
                      entity_id=organization_id, actor_user_id=principal.user_id,
                      request_id=_rid(request_id), metadata={"service_line": service_line_code, "status": status})


def list_service_lines(organization_id, *, principal):
    _require(principal, "organization.read")
    with engine.connect() as c:
        _require_scope(principal, organization_id, write=False, connection=c)
        rows = c.execute(select(service_lines.c.code, organization_service_lines.c.status,
                                organization_service_lines.c.since_date,
                                organization_service_lines.c.renewal_owner_user_id)
                         .select_from(organization_service_lines.join(
                             service_lines, service_lines.c.id == organization_service_lines.c.service_line_id))
                         .where(organization_service_lines.c.organization_id == organization_id)).mappings().all()
    return [dict(r) for r in rows]


# --- permanent relationship roles -------------------------------------------

def assign_role(organization_id, *, principal, user_id, role_code, service_line_code=None,
                is_primary=False, effective_date=None, request_id=None):
    """Permanent relationship-role ownership (Advisor, Renewal Owner, …). Distinct from
    Work Management assignments; reuses the ``relationship_types`` vocabulary."""
    _require(principal, "organization.write")
    with engine.begin() as c:
        _require_scope(principal, organization_id, write=True, connection=c)
        role_type_id = _role_type_id(c, role_code)
        sid = _service_line_id(c, service_line_code) if service_line_code else None
        new_id = c.execute(organization_service_roles.insert().values(
            organization_id=organization_id, user_id=user_id, role_type_id=role_type_id,
            service_line_id=sid, is_primary=is_primary, effective_date=effective_date or date.today(),
            created_by_user_id=principal.user_id).returning(organization_service_roles.c.id)).scalar_one()
    write_audit_event(action="organization.role.assigned", entity_type="organization",
                      entity_id=organization_id, actor_user_id=principal.user_id, request_id=_rid(request_id),
                      metadata={"role": role_code, "user_id": user_id, "service_line": service_line_code,
                                "is_primary": is_primary})
    return new_id


def end_role(role_id, *, principal, inactive_date=None, request_id=None):
    _require(principal, "organization.write")
    with engine.begin() as c:
        row = c.execute(select(organization_service_roles.c.organization_id)
                        .where(organization_service_roles.c.id == role_id)).mappings().one_or_none()
        if row is None:
            raise OrganizationNotFound("Role not found")
        _require_scope(principal, row["organization_id"], write=True, connection=c)
        c.execute(organization_service_roles.update().where(organization_service_roles.c.id == role_id)
                  .values(inactive_date=inactive_date or date.today()))
    write_audit_event(action="organization.role.ended", entity_type="organization",
                      entity_id=row["organization_id"], actor_user_id=principal.user_id,
                      request_id=_rid(request_id), metadata={"role_id": role_id})


def list_roles(organization_id, *, principal, active_only=True):
    _require(principal, "organization.read")
    with engine.connect() as c:
        _require_scope(principal, organization_id, write=False, connection=c)
        query = (select(organization_service_roles.c.id, organization_service_roles.c.user_id,
                        relationship_types.c.code.label("role_code"),
                        organization_service_roles.c.is_primary,
                        organization_service_roles.c.effective_date,
                        organization_service_roles.c.inactive_date)
                 .select_from(organization_service_roles.join(
                     relationship_types, relationship_types.c.id == organization_service_roles.c.role_type_id))
                 .where(organization_service_roles.c.organization_id == organization_id))
        if active_only:
            query = query.where(organization_service_roles.c.inactive_date.is_(None))
        return [dict(r) for r in c.execute(query).mappings()]


# --- ownership ---------------------------------------------------------------

def _owner_entity_id(c, *, owner_person_id, owner_household_id, owner_organization_id):
    provided = [x for x in (owner_person_id, owner_household_id, owner_organization_id) if x is not None]
    if len(provided) != 1:
        raise OrganizationError("Exactly one owner (person, household, or organization) is required")
    if owner_person_id is not None:
        return ensure_person_entity(c, owner_person_id)
    if owner_household_id is not None:
        return ensure_household_entity(c, owner_household_id)
    return owner_organization_id


def record_ownership(*, principal, owned_organization_id, owner_person_id=None,
                     owner_household_id=None, owner_organization_id=None, relationship_code="owns",
                     ownership_percentage=None, voting_percentage=None, ownership_type=None,
                     is_direct=True, evidence_source=None, effective_date=None, inactive_date=None,
                     notes=None, request_id=None):
    """Record an ownership (or org-structure) edge + typed 1:1 detail. Reuses the existing
    ``relationships`` graph; percentages are on the detail (never on the org record)."""
    _require(principal, "organization.write")
    for pct in (ownership_percentage, voting_percentage):
        if pct is not None and not (0 <= float(pct) <= 100):
            raise OrganizationError("Percentages must be between 0 and 100")
    with engine.begin() as c:
        _require_scope(principal, owned_organization_id, write=True, connection=c)
        type_id = _role_type_id(c, relationship_code)
        owner_id = _owner_entity_id(c, owner_person_id=owner_person_id,
                                    owner_household_id=owner_household_id,
                                    owner_organization_id=owner_organization_id)
        if owner_id == owned_organization_id:
            raise OrganizationError("An entity cannot own itself")
        try:
            rel_id = c.execute(relationships.insert().values(
                from_entity_id=owner_id, to_entity_id=owned_organization_id,
                relationship_type_id=type_id, effective_date=effective_date,
                inactive_date=inactive_date, notes=notes, source="benefits",
                created_by=str(principal.user_id)).returning(relationships.c.id)).scalar_one()
        except IntegrityError:
            rel_id = c.scalar(select(relationships.c.id).where(
                relationships.c.from_entity_id == owner_id,
                relationships.c.to_entity_id == owned_organization_id,
                relationships.c.relationship_type_id == type_id))
        # upsert the 1:1 ownership detail
        existing = c.scalar(select(relationship_ownership.c.id)
                            .where(relationship_ownership.c.relationship_id == rel_id))
        detail = dict(ownership_percentage=ownership_percentage, voting_percentage=voting_percentage,
                      ownership_type=ownership_type, is_direct=is_direct,
                      evidence_source=evidence_source, notes=notes)
        if existing:
            c.execute(relationship_ownership.update()
                      .where(relationship_ownership.c.id == existing).values(**detail))
            ownership_id = existing
        else:
            ownership_id = c.execute(relationship_ownership.insert()
                                     .values(relationship_id=rel_id, **detail)
                                     .returning(relationship_ownership.c.id)).scalar_one()
    write_audit_event(action="organization.ownership.recorded", entity_type="organization",
                      entity_id=owned_organization_id, actor_user_id=principal.user_id,
                      request_id=_rid(request_id),
                      metadata={"relationship": relationship_code, "owner_entity_id": owner_id,
                                "ownership_percentage": (float(ownership_percentage)
                                                         if ownership_percentage is not None else None),
                                "is_direct": is_direct})
    return {"relationship_id": rel_id, "ownership_id": ownership_id, "owner_entity_id": owner_id}


def list_owners(organization_id, *, principal):
    """Organization → owners (incoming ownership edges) with typed detail. Both-direction
    navigation: use :func:`list_owned` for owner → organizations."""
    _require(principal, "organization.read")
    with engine.connect() as c:
        _require_scope(principal, organization_id, write=False, connection=c)
        rows = c.execute(
            select(relationships.c.id.label("relationship_id"), relationships.c.from_entity_id.label("owner_entity_id"),
                   relationship_entities.c.entity_type.label("owner_entity_type"),
                   relationship_entities.c.name.label("owner_name"),
                   relationship_types.c.code.label("relationship_code"),
                   relationship_ownership.c.ownership_percentage, relationship_ownership.c.voting_percentage,
                   relationship_ownership.c.ownership_type, relationship_ownership.c.is_direct,
                   relationship_ownership.c.evidence_source)
            .select_from(relationships
                .join(relationship_types, relationship_types.c.id == relationships.c.relationship_type_id)
                .join(relationship_entities, relationship_entities.c.id == relationships.c.from_entity_id)
                .outerjoin(relationship_ownership, relationship_ownership.c.relationship_id == relationships.c.id))
            .where(relationships.c.to_entity_id == organization_id,
                   relationship_types.c.category.in_(("ownership", "org_structure")))).mappings().all()
    return [dict(r) for r in rows]


def _owned_businesses(c, owner_entity_id):
    """Businesses (entity_type='business') on outgoing ownership/org-structure edges from
    ``owner_entity_id``, joined to their organization profile + typed ownership detail.
    Read-only; no ``ensure_*`` write side-effect."""
    rows = c.execute(
        select(relationship_entities.c.id.label("business_id"),
               relationship_entities.c.name.label("business_name"),
               relationship_entities.c.active.label("entity_active"),
               relationships.c.id.label("relationship_id"),
               relationships.c.effective_date, relationships.c.inactive_date,
               relationships.c.active.label("relationship_active"),
               relationships.c.source.label("edge_source"),
               relationships.c.confidence_level,
               relationship_types.c.code.label("relationship_code"),
               relationship_ownership.c.ownership_percentage,
               relationship_ownership.c.voting_percentage,
               relationship_ownership.c.ownership_type, relationship_ownership.c.is_direct,
               relationship_ownership.c.evidence_source,
               organization_profiles.c.legal_name, organization_profiles.c.entity_form,
               organization_profiles.c.industry, organization_profiles.c.naics_code,
               organization_profiles.c.employee_count_band, organization_profiles.c.status,
               organization_profiles.c.ein)
        .select_from(relationships
            .join(relationship_types, relationship_types.c.id == relationships.c.relationship_type_id)
            .join(relationship_entities, relationship_entities.c.id == relationships.c.to_entity_id)
            .outerjoin(relationship_ownership,
                       relationship_ownership.c.relationship_id == relationships.c.id)
            .outerjoin(organization_profiles,
                       organization_profiles.c.relationship_entity_id == relationships.c.to_entity_id))
        .where(relationships.c.from_entity_id == owner_entity_id,
               relationship_types.c.category.in_(("ownership", "org_structure")),
               relationship_entities.c.entity_type == "business")
        .order_by(relationship_entities.c.name)).mappings().all()
    return [dict(r) for r in rows]


def list_person_business_ownership(person_id):
    """PURE READ (Phase D.12): businesses a PERSON owns, via the ownership graph, with each
    business's organization profile and typed ownership detail. Returns ``[]`` if the person
    has no relationship entity or owns nothing. Unlike :func:`list_owned`, this never calls
    ``ensure_person_entity`` — so rendering the workspace cannot create a person entity row
    as a side effect. The caller enforces person record scope."""
    with engine.connect() as c:
        entity_id = c.scalar(select(relationship_entities.c.id).where(
            relationship_entities.c.person_id == person_id,
            relationship_entities.c.entity_type == "person"))
        if entity_id is None:
            return []
        return _owned_businesses(c, entity_id)


def list_household_business_ownership(household_id):
    """PURE READ (Phase D.12): businesses owned DIRECTLY by a household entity (not by its
    members). Returns ``[]`` if no household entity or no owned businesses. No write side
    effect. The caller enforces household record scope."""
    with engine.connect() as c:
        entity_id = c.scalar(select(relationship_entities.c.id).where(
            relationship_entities.c.household_id == household_id,
            relationship_entities.c.entity_type == "household"))
        if entity_id is None:
            return []
        return _owned_businesses(c, entity_id)


def list_owned(*, principal, owner_person_id=None, owner_household_id=None, owner_organization_id=None):
    """Owner (person/household/organization) → organizations it owns (outgoing edges)."""
    _require(principal, "organization.read")
    with engine.connect() as c:
        owner_id = _owner_entity_id(c, owner_person_id=owner_person_id,
                                    owner_household_id=owner_household_id,
                                    owner_organization_id=owner_organization_id)
        rows = c.execute(
            select(relationships.c.to_entity_id.label("organization_id"),
                   relationship_entities.c.name.label("organization_name"),
                   relationship_types.c.code.label("relationship_code"),
                   relationship_ownership.c.ownership_percentage, relationship_ownership.c.is_direct)
            .select_from(relationships
                .join(relationship_types, relationship_types.c.id == relationships.c.relationship_type_id)
                .join(relationship_entities, relationship_entities.c.id == relationships.c.to_entity_id)
                .outerjoin(relationship_ownership, relationship_ownership.c.relationship_id == relationships.c.id))
            .where(relationships.c.from_entity_id == owner_id,
                   relationship_types.c.category.in_(("ownership", "org_structure")))).mappings().all()
    return [dict(r) for r in rows]
