"""Durable resolution / alias knowledge service (PR-1).

The small service API around the ``folder_resolution_decisions`` ledger — the durable REUSE layer that
records how an ingestion subject was resolved so future ingestion can reuse approved positive resolutions.
All ledger writes go through here; callers never touch the table directly.

Behaviour:
  * ``record_decision`` inserts a new decision. It FAILS CLOSED on a conflicting current resolution: if an
    active decision already exists for the subject it refuses unless ``supersede=True`` is passed, which
    supersedes the prior row (active=false, superseded_at, superseded_by -> the new row) and inserts a new
    active row — history is never overwritten or deleted, and the partial unique index never sees two
    active rows. ``supersede=True`` with no active decision is itself a fail-closed error.
  * Entity-linking decisions must carry a valid, EXISTING canonical target (person / household /
    relationship_entity); an unknown entity type or a non-existent id is rejected. ``firm_material`` is an
    approved positive disposition with no canonical entity; ``reject`` / ``defer`` / ``ambiguous`` carry no
    entity and are never reusable.
  * ``lookup_reusable`` returns the current decision ONLY when it is an approved positive resolution;
    rejected / deferred / ambiguous / superseded decisions are never returned and never auto-applied.

SCOPE OF PR-1: the ledger ONLY. No document linking, no file movement, no storage_uri /
document_sources change, no canonical/document-link APPLY, no Exception Engine queue, no evidence
assembler, no review UI, no AI/ML, and no change to the deterministic matching/linkage rules.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, select, update

from app.database.identity_tables import (
    FRD_DECISIONS,
    FRD_ENTITY_DECISIONS,
    FRD_POSITIVE_DECISIONS,
)

_POSITIVE = frozenset(FRD_POSITIVE_DECISIONS)
_ENTITY = frozenset(FRD_ENTITY_DECISIONS)

# The canonical entity type each entity-linking decision must resolve to, and the table that holds it.
_DECISION_ENTITY_TYPE = {
    "link_person": "person", "create_person": "person",
    "link_household": "household", "create_household": "household",
    "link_business": "relationship_entity", "create_business": "relationship_entity",
}
_ENTITY_TABLE = {"person": "people", "household": "households",
                 "relationship_entity": "relationship_entities"}


class ResolutionKnowledgeError(ValueError):
    """Invalid resolution-decision input (unknown decision, bad/missing entity, or a mismatch)."""


class ResolutionConflictError(ResolutionKnowledgeError):
    """A current active resolution already exists and supersede was not requested (fail closed)."""


def _table():
    from app.db import metadata
    table = metadata.tables.get("folder_resolution_decisions")
    if table is None:
        raise ResolutionKnowledgeError(
            "folder_resolution_decisions table is not present — run the reskn01 migration.")
    return table


def _norm(value) -> str:
    """Normalize a subject-identity component: collapse whitespace + casefold, so lookups are stable
    regardless of spacing/case. Callers pass a domain-normalized key (e.g. the TaxDome name key)."""
    return " ".join((value or "").split()).casefold()


def _clean(value):
    return value.strip() if isinstance(value, str) else value


def _validate_entity(conn, decision, resulting_entity_type, resulting_entity_id) -> tuple:
    """Fail-closed validation of the resulting entity for a decision. Returns the (type, id) to store."""
    if decision in _ENTITY:
        expected = _DECISION_ENTITY_TYPE[decision]
        etype = resulting_entity_type or expected
        if etype != expected:
            raise ResolutionKnowledgeError(
                f"{decision} must resolve to a {expected!r} entity, got {resulting_entity_type!r}.")
        if not isinstance(resulting_entity_id, int) or resulting_entity_id <= 0:
            raise ResolutionKnowledgeError(
                f"{decision} requires a positive integer resulting_entity_id.")
        target = _table_by_name(_ENTITY_TABLE[etype])
        exists = conn.execute(select(target.c.id).where(
            target.c.id == resulting_entity_id)).scalar_one_or_none()
        if exists is None:
            raise ResolutionKnowledgeError(
                f"{etype} id {resulting_entity_id} does not exist (invalid entity id).")
        return etype, resulting_entity_id
    if decision == "firm_material":
        if resulting_entity_id is not None or (resulting_entity_type not in (None, "firm")):
            raise ResolutionKnowledgeError(
                "firm_material carries no canonical entity id and type must be 'firm'.")
        return "firm", None
    # reject / defer / ambiguous
    if resulting_entity_type is not None or resulting_entity_id is not None:
        raise ResolutionKnowledgeError(
            f"{decision} is a non-reusable disposition and must NOT carry a resulting entity.")
    return None, None


def _table_by_name(name):
    from app.db import metadata
    table = metadata.tables.get(name)
    if table is None:
        raise ResolutionKnowledgeError(f"required table {name!r} is not present.")
    return table


def record_decision(*, subject_system, subject_type, subject_key, display_name, decision,
                    resulting_entity_type=None, resulting_entity_id=None, evidence_snapshot=None,
                    match_reason=None, confidence=None, evidence_metadata=None, reviewed_by=None,
                    exception_id=None, supersede=False, conn=None) -> int:
    """Record a resolution decision. Returns the new row id.

    Fails closed: unknown decision, invalid/non-existent entity, or a conflicting current resolution
    (unless ``supersede=True``). A supersession retains the prior row as history."""
    system = _norm(subject_system)
    stype = _norm(subject_type) or "folder"
    key = _norm(subject_key)
    display = _clean(display_name)
    if not (system and key and display):
        raise ResolutionKnowledgeError("subject_system, subject_key and display_name are required.")
    if decision not in FRD_DECISIONS:
        raise ResolutionKnowledgeError(f"unknown decision {decision!r}; expected one of {FRD_DECISIONS}.")

    table = _table()
    now = datetime.now(UTC)

    def _do(c) -> int:
        etype, eid = _validate_entity(c, decision, resulting_entity_type, resulting_entity_id)
        prior = c.execute(select(table.c.id).where(and_(
            table.c.subject_system == system, table.c.subject_type == stype,
            table.c.subject_key == key, table.c.active.is_(True)))).scalar_one_or_none()
        if prior is not None and not supersede:
            raise ResolutionConflictError(
                f"an active resolution already exists for ({system!r}, {stype!r}, {key!r}); "
                "pass supersede=True to correct it (history is retained).")
        if prior is None and supersede:
            raise ResolutionKnowledgeError(
                "supersede=True but no active decision exists for this subject.")
        # Deactivate the prior active row FIRST so the partial unique index never sees two active rows.
        if prior is not None:
            c.execute(update(table).where(table.c.id == prior).values(
                active=False, superseded_at=now, updated_at=now))
        new_id = c.execute(table.insert().values(
            subject_system=system, subject_type=stype, subject_key=key, display_name=display,
            decision=decision, resulting_entity_type=etype, resulting_entity_id=eid,
            evidence_snapshot=evidence_snapshot or {}, match_reason=match_reason, confidence=confidence,
            evidence_metadata=evidence_metadata or {}, reviewed_by=_clean(reviewed_by), reviewed_at=now,
            exception_id=exception_id, active=True).returning(table.c.id)).scalar_one()
        if prior is not None:
            c.execute(update(table).where(table.c.id == prior).values(superseded_by=new_id))
        return new_id

    if conn is not None:
        return _do(conn)
    from app.db import engine
    with engine.begin() as c:
        return _do(c)


def get_current_decision(subject_system, subject_type, subject_key, *, conn=None):
    """The single current/active decision for a subject (any decision type), or None."""
    table = _table()
    stmt = select(table).where(and_(
        table.c.subject_system == _norm(subject_system), table.c.subject_type == _norm(subject_type),
        table.c.subject_key == _norm(subject_key), table.c.active.is_(True)))

    def _do(c):
        row = c.execute(stmt).mappings().first()
        return dict(row) if row else None

    if conn is not None:
        return _do(conn)
    from app.db import engine
    with engine.connect() as c:
        return _do(c)


def get_reusable_resolution(subject_system, subject_type, subject_key, *, conn=None):
    """The current decision ONLY if it is an approved POSITIVE resolution reusable as durable matching
    knowledge. Returns None for reject / defer / ambiguous (and, by construction, for superseded rows)."""
    row = get_current_decision(subject_system, subject_type, subject_key, conn=conn)
    return row if (row and row["decision"] in _POSITIVE) else None


def get_decision_history(subject_system, subject_type, subject_key, *, conn=None) -> list[dict]:
    """Full decision history for a subject (newest first), including superseded rows."""
    table = _table()
    stmt = select(table).where(and_(
        table.c.subject_system == _norm(subject_system), table.c.subject_type == _norm(subject_type),
        table.c.subject_key == _norm(subject_key))).order_by(
        table.c.created_at.desc(), table.c.id.desc())

    def _do(c):
        return [dict(m) for m in c.execute(stmt).mappings()]

    if conn is not None:
        return _do(conn)
    from app.db import engine
    with engine.connect() as c:
        return _do(c)
