"""Reviewer authority administration (Phase D.8).

Records and maintains ``reviewer_authorities`` from documented facts, with an
append-only ``reviewer_authority_events`` history. It does NOT decide whether anyone
is legally qualified, validate external licensing, parse documents, or certify
evidence — it records the authority evidence an authorized administrator supplies.

Governance invariants enforced here:
- **Segregation of duties**: an administrator can never create or administer authority
  for themselves (``actor != subject``). Self-recording is blocked, not silently
  allowed.
- **Explicit lifecycle**: draft → active → suspended ↔ active → revoked/superseded; no
  generic state machine. ``expired`` is computed by date at lookup time, never mutated
  on read.
- **Complete evidence** is required to activate (role, non-empty scope, effective date,
  source reference, evidence description).
- **No conflicting active authority**: two active records for the same principal may not
  share overlapping scope.
- **Append-only history**: every material change appends an event; nothing is updated in
  place except the current authority row's lifecycle fields, and prior versions are
  superseded (not overwritten).
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import and_, func, or_, select

from app.db import engine, reviewer_authorities, reviewer_authority_events, users

STATUSES = frozenset({"draft", "active", "suspended", "expired", "revoked", "superseded"})

# Explicit allowed source states per lifecycle action (no generic engine).
_TRANSITIONS = {
    "activate": (frozenset({"draft"}), "active"),
    "suspend": (frozenset({"active"}), "suspended"),
    "restore": (frozenset({"suspended"}), "active"),
    "revoke": (frozenset({"draft", "active", "suspended"}), "revoked"),
}


class AuthorityError(RuntimeError):
    """Base class for reviewer-authority administration errors."""


class SelfAdministrationError(AuthorityError):
    """An administrator may not create or administer their own authority."""


class UnknownPrincipalError(AuthorityError):
    """The subject principal does not reference an existing user."""


class IncompleteEvidenceError(AuthorityError):
    """Activation requires complete authority evidence."""


class InvalidTransitionError(AuthorityError):
    """The action is not allowed from the record's current status."""


class StaleAuthorityError(AuthorityError):
    """The record changed since the caller loaded it; the action was rejected."""


class ScopeConflictError(AuthorityError):
    """An overlapping active authority already exists for this principal."""


def _now() -> datetime:
    return datetime.now(UTC)


def _load_for_update(conn, authority_id, expected_status):
    row = conn.execute(
        select(reviewer_authorities).where(reviewer_authorities.c.id == authority_id).with_for_update()
    ).mappings().first()
    if row is None:
        raise AuthorityError("authority not found")
    if expected_status is not None and row["status"] != expected_status:
        raise StaleAuthorityError(
            f"authority is now {row['status']!r}, not {expected_status!r}; reload and retry")
    return row


def _append_event(conn, authority_id, *, event_type, prior_status, new_status,
                  actor_principal_id, reason, snapshot):
    conn.execute(reviewer_authority_events.insert().values(
        reviewer_authority_id=authority_id, event_type=event_type,
        prior_status=prior_status, new_status=new_status,
        actor_principal_id=actor_principal_id, occurred_at=_now(),
        reason=reason, evidence_snapshot=snapshot or {}))


def _snapshot(row) -> dict:
    return {
        "reviewer_role": row["reviewer_role"], "reviewer_name": row["reviewer_name"],
        "authority_scope": list(row["authority_scope"] or []),
        "effective_date": str(row["effective_date"]) if row["effective_date"] else None,
        "expiration_date": str(row["expiration_date"]) if row["expiration_date"] else None,
        "source_reference": row["source_reference"],
        "evidence_description": row["evidence_description"],
    }


def _evidence_complete(row) -> bool:
    return bool(row["reviewer_role"] and (row["authority_scope"] or [])
               and row["effective_date"] and row["source_reference"]
               and row["evidence_description"])


def _principal_exists(conn, principal_id) -> bool:
    return conn.scalar(select(users.c.id).where(users.c.id == principal_id)) is not None


def _scope_conflict(conn, principal_id, scope, *, exclude_id=None) -> bool:
    """True if an ACTIVE authority for this principal shares an overlapping scope token
    (``*`` overlaps everything)."""
    tokens = set(scope or [])
    stmt = select(reviewer_authorities).where(
        reviewer_authorities.c.principal_id == principal_id,
        reviewer_authorities.c.status == "active")
    if exclude_id is not None:
        stmt = stmt.where(reviewer_authorities.c.id != exclude_id)
    for other in conn.execute(stmt).mappings():
        other_tokens = set(other["authority_scope"] or [])
        if "*" in tokens or "*" in other_tokens or (tokens & other_tokens):
            return True
    return False


# --- create + lifecycle ------------------------------------------------------

def create_draft(actor_principal_id, *, principal_id, reviewer_role, reviewer_name=None,
                 authority_scope, effective_date=None, expiration_date=None,
                 source_reference=None, evidence_description=None):
    """Create a DRAFT authority record from documented facts. SoD: the actor may not be
    the subject. The subject must reference an existing user. Nothing is activated or
    confers approval authority until an explicit ``activate`` with complete evidence."""
    if actor_principal_id == principal_id:
        raise SelfAdministrationError("an administrator cannot record their own authority")
    scope = [s for s in (authority_scope or []) if s]
    with engine.begin() as conn:
        if not _principal_exists(conn, principal_id):
            raise UnknownPrincipalError(f"principal {principal_id} does not exist")
        now = _now()
        row = conn.execute(reviewer_authorities.insert().values(
            principal_id=principal_id, reviewer_role=reviewer_role, reviewer_name=reviewer_name,
            authority_scope=scope, effective_date=effective_date, expiration_date=expiration_date,
            status="draft", source_reference=source_reference, evidence_description=evidence_description,
            recorded_by=actor_principal_id, recorded_at=now, created_at=now,
        ).returning(reviewer_authorities)).mappings().one()
        _append_event(conn, row["id"], event_type="created", prior_status=None,
                      new_status="draft", actor_principal_id=actor_principal_id,
                      reason=None, snapshot=_snapshot(row))
    return dict(row)


def _guard_subject(actor_principal_id, row):
    if actor_principal_id == row["principal_id"]:
        raise SelfAdministrationError("the subject of an authority record may not administer it")


def _transition(actor_principal_id, authority_id, action, *, expected_status, reason=None):
    froms, to = _TRANSITIONS[action]
    with engine.begin() as conn:
        row = _load_for_update(conn, authority_id, expected_status)
        _guard_subject(actor_principal_id, row)
        if row["status"] not in froms:
            raise InvalidTransitionError(f"cannot {action} from status {row['status']}")
        if action == "activate":
            if not _evidence_complete(row):
                raise IncompleteEvidenceError(
                    "activation requires role, scope, effective date, source reference, and evidence description")
            if _scope_conflict(conn, row["principal_id"], row["authority_scope"], exclude_id=row["id"]):
                raise ScopeConflictError("an overlapping active authority already exists for this principal")
        if action == "restore" and _scope_conflict(conn, row["principal_id"], row["authority_scope"], exclude_id=row["id"]):
            raise ScopeConflictError("an overlapping active authority already exists for this principal")
        if action in ("suspend", "revoke") and not reason:
            raise AuthorityError(f"{action} requires a reason")
        values = {"status": to}
        if action == "suspend":
            values["suspended_at"] = _now()
        if action == "restore":
            values["suspended_at"] = None
        if action == "revoke":
            values["revoked_at"] = _now()
            values["revocation_reason"] = reason
        conn.execute(reviewer_authorities.update().where(
            reviewer_authorities.c.id == authority_id).values(**values))
        _append_event(conn, authority_id, event_type=action, prior_status=row["status"],
                      new_status=to, actor_principal_id=actor_principal_id,
                      reason=reason, snapshot=_snapshot(row))
    return {"status": to}


def activate(actor_principal_id, authority_id, *, expected_status="draft"):
    return _transition(actor_principal_id, authority_id, "activate", expected_status=expected_status)


def suspend(actor_principal_id, authority_id, *, reason, expected_status="active"):
    return _transition(actor_principal_id, authority_id, "suspend", expected_status=expected_status, reason=reason)


def restore(actor_principal_id, authority_id, *, expected_status="suspended"):
    return _transition(actor_principal_id, authority_id, "restore", expected_status=expected_status)


def revoke(actor_principal_id, authority_id, *, reason, expected_status):
    return _transition(actor_principal_id, authority_id, "revoke", expected_status=expected_status, reason=reason)


def supersede(actor_principal_id, authority_id, *, expected_status, reviewer_role=None,
              reviewer_name=None, authority_scope=None, effective_date=None,
              expiration_date=None, source_reference=None, evidence_description=None,
              reason=None):
    """Create a NEW active authority version that references the prior via
    ``supersedes_authority_id`` and mark the prior ``superseded``. Fields default to the
    prior record's values; provided fields override. Requires complete evidence and no
    scope conflict; the prior must be active or suspended (never already superseded, so
    no cycles)."""
    with engine.begin() as conn:
        prior = _load_for_update(conn, authority_id, expected_status)
        _guard_subject(actor_principal_id, prior)
        if prior["status"] not in ("active", "suspended"):
            raise InvalidTransitionError(f"cannot supersede from status {prior['status']}")
        new_scope = [s for s in (authority_scope if authority_scope is not None
                                 else (prior["authority_scope"] or [])) if s]
        merged = {
            "reviewer_role": reviewer_role or prior["reviewer_role"],
            "reviewer_name": reviewer_name if reviewer_name is not None else prior["reviewer_name"],
            "authority_scope": new_scope,
            "effective_date": effective_date or prior["effective_date"],
            "expiration_date": expiration_date if expiration_date is not None else prior["expiration_date"],
            "source_reference": source_reference or prior["source_reference"],
            "evidence_description": evidence_description or prior["evidence_description"],
        }
        if not (merged["reviewer_role"] and merged["authority_scope"] and merged["effective_date"]
                and merged["source_reference"] and merged["evidence_description"]):
            raise IncompleteEvidenceError("superseding version requires complete evidence")
        if _scope_conflict(conn, prior["principal_id"], new_scope, exclude_id=prior["id"]):
            raise ScopeConflictError("an overlapping active authority already exists for this principal")
        now = _now()
        new_row = conn.execute(reviewer_authorities.insert().values(
            principal_id=prior["principal_id"], status="active",
            recorded_by=actor_principal_id, recorded_at=now, created_at=now,
            supersedes_authority_id=prior["id"], **merged,
        ).returning(reviewer_authorities)).mappings().one()
        conn.execute(reviewer_authorities.update().where(
            reviewer_authorities.c.id == prior["id"]).values(status="superseded"))
        _append_event(conn, prior["id"], event_type="superseded", prior_status=prior["status"],
                      new_status="superseded", actor_principal_id=actor_principal_id,
                      reason=reason, snapshot=_snapshot(prior))
        _append_event(conn, new_row["id"], event_type="created_superseding", prior_status=None,
                      new_status="active", actor_principal_id=actor_principal_id,
                      reason=reason, snapshot=_snapshot(new_row))
    return dict(new_row)


# --- reads -------------------------------------------------------------------

def _effective_status(row, today: date) -> str:
    """Display status: an active record past its expiration date reads as ``expired``
    (computed, not stored — history is never mutated on view)."""
    if row["status"] == "active" and row["expiration_date"] and row["expiration_date"] < today:
        return "expired"
    return row["status"]


def list_authorities(*, search=None, status=None, sort="recorded_at", descending=True,
                     page=1, page_size=25, today: date | None = None):
    today = today or date.today()
    sort_cols = {
        "recorded_at": reviewer_authorities.c.recorded_at,
        "reviewer_name": reviewer_authorities.c.reviewer_name,
        "reviewer_role": reviewer_authorities.c.reviewer_role,
        "status": reviewer_authorities.c.status,
        "effective_date": reviewer_authorities.c.effective_date,
        "expiration_date": reviewer_authorities.c.expiration_date,
    }
    col = sort_cols.get(sort, reviewer_authorities.c.recorded_at)
    conds = []
    if status:
        conds.append(reviewer_authorities.c.status == status)
    if search:
        like = f"%{search.strip().lower()}%"
        conds.append(or_(
            func.lower(func.coalesce(reviewer_authorities.c.reviewer_name, "")).like(like),
            func.lower(reviewer_authorities.c.reviewer_role).like(like),
            func.lower(func.coalesce(reviewer_authorities.c.source_reference, "")).like(like)))
    where = and_(*conds) if conds else None
    with engine.connect() as conn:
        total = conn.scalar(
            select(func.count()).select_from(reviewer_authorities).where(where)
            if where is not None else select(func.count()).select_from(reviewer_authorities))
        page = max(1, page)
        page_size = max(1, min(page_size, 200))
        stmt = select(reviewer_authorities)
        if where is not None:
            stmt = stmt.where(where)
        stmt = stmt.order_by(col.desc().nullslast() if descending else col.asc().nullslast(),
                             reviewer_authorities.c.id.desc())
        stmt = stmt.limit(page_size).offset((page - 1) * page_size)
        rows = []
        for r in conn.execute(stmt).mappings():
            d = dict(r)
            d["effective_status"] = _effective_status(r, today)
            rows.append(d)
    pages = (total + page_size - 1) // page_size if total else 0
    return {"rows": rows, "total": total, "page": page, "page_size": page_size, "pages": pages}


def get_authority(authority_id, *, today: date | None = None):
    today = today or date.today()
    with engine.connect() as conn:
        row = conn.execute(
            select(reviewer_authorities).where(reviewer_authorities.c.id == authority_id)
        ).mappings().first()
        if row is None:
            return None
        row = dict(row)
        row["effective_status"] = _effective_status(row, today)
        row["events"] = [dict(e) for e in conn.execute(
            select(reviewer_authority_events)
            .where(reviewer_authority_events.c.reviewer_authority_id == authority_id)
            .order_by(reviewer_authority_events.c.occurred_at.asc(), reviewer_authority_events.c.id.asc())
        ).mappings()]
        row["successor"] = conn.scalar(
            select(reviewer_authorities.c.id).where(
                reviewer_authorities.c.supersedes_authority_id == authority_id))
    return row
