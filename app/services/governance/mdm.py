"""Master data management governance (Phase D.23) — duplicates, merges, survivorship, lineage.

Governance may identify duplicates, record candidate merges, record reviewed merge decisions,
record survivorship policy, and reference golden records — but it **NEVER performs an unsafe merge
itself**. It **reuses** the existing deterministic matching/merge infrastructure: it detects
ambiguity via ``promote.list_ambiguous_unlinked`` and applies an approved merge only through
``person_merge.merge_source_contacts`` (which refuses to merge records that already resolve to
different people). Lineage is **read** from the existing ``person_source_links`` for people;
governance-owned lineage rows are added only for non-person entities.
"""
from __future__ import annotations

from sqlalchemy import and_, func, select

from app.database.governance_tables import GOV_ENTITY_TYPES, MERGE_DECISIONS
from app.db import (
    engine,
    governance_duplicate_candidates,
    governance_lineage,
    governance_merge_decisions,
    person_source_links,
    source_contacts,
)

from .common import (
    GovernanceError,
    GovernanceNotFound,
    now,
    publish_timeline,
    record_event,
    write_audit,
)

_candidates = governance_duplicate_candidates
_merges = governance_merge_decisions


# --- duplicate candidates ----------------------------------------------------

def list_candidates(principal, *, status=None, page=1, page_size=50) -> dict:
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        conds = []
        if status:
            conds.append(_candidates.c.status == status)
        where = and_(*conds) if conds else None
        base = select(func.count()).select_from(_candidates)
        total = c.scalar(base.where(where) if where is not None else base)
        stmt = select(_candidates)
        if where is not None:
            stmt = stmt.where(where)
        rows = [dict(r) for r in c.execute(
            stmt.order_by(_candidates.c.id.desc()).limit(page_size)
            .offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size}


def get_candidate(principal, candidate_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(_candidates).where(_candidates.c.id == candidate_id)).mappings().first()
        return dict(row) if row else None


def create_candidate(principal, *, source_contact_ids, entity_type="source_contact", group_key=None,
                     match_method=None, match_score=None, person_id=None, detected_by="manual",
                     actor_user_id=None) -> dict:
    if not source_contact_ids or len(source_contact_ids) < 2:
        raise GovernanceError("a duplicate candidate needs at least two source contacts")
    with engine.begin() as c:
        row = c.execute(_candidates.insert().values(
            entity_type=entity_type, source_contact_ids=list(source_contact_ids), group_key=group_key,
            match_method=match_method, match_score=match_score, person_id=person_id, status="open",
            detected_by=detected_by, created_by_user_id=actor_user_id).returning(*_candidates.c)).mappings().one()
        row = dict(row)
        record_event(c, entity_type="candidate", entity_id=row["id"], event_type="candidate_created",
                     to_status="open", actor_user_id=actor_user_id, payload={"detected_by": detected_by})
        return row


def scan_duplicates(principal, *, actor_user_id=None, limit=500) -> dict:
    """Detect duplicate candidates from the EXISTING ambiguity queue (never recomputes matching)."""
    from app.matching.promote import list_ambiguous_unlinked
    with engine.connect() as c:
        ambiguous = list_ambiguous_unlinked(conn=c)
    created = 0
    for item in ambiguous[:limit]:
        sc_id = item.get("source_contact_id") or item.get("id")
        candidate_ids = [cp.get("id") for cp in (item.get("candidates") or []) if cp.get("id")]
        if sc_id is None or not candidate_ids:
            continue
        with engine.begin() as c:
            existing = c.scalar(select(_candidates.c.id).where(
                _candidates.c.status == "open",
                _candidates.c.duplicate_entity_id == sc_id))
            if existing is not None:
                continue
            c.execute(_candidates.insert().values(
                entity_type="source_contact", duplicate_entity_id=sc_id,
                primary_entity_id=candidate_ids[0], match_method="ambiguous_unlinked",
                source_contact_ids=[sc_id], status="open", detected_by="matching",
                created_by_user_id=actor_user_id))
        created += 1
    return {"ambiguous": len(ambiguous), "candidates_created": created}


# --- merge decisions (reuse the safe merge; never an unsafe merge) -----------

def record_merge_decision(principal, candidate_id: int, *, decision, survivorship_rule_id=None,
                          notes=None, apply=False, actor_user_id=None) -> dict:
    if decision not in MERGE_DECISIONS:
        raise GovernanceError(f"invalid decision {decision!r}")
    with engine.connect() as c:
        cand = c.execute(select(_candidates).where(_candidates.c.id == candidate_id)).mappings().first()
    if cand is None:
        raise GovernanceNotFound(str(candidate_id))
    cand = dict(cand)

    merged_person_id = golden_type = golden_id = None
    if decision == "approved" and apply:
        # Approving + applying a merge requires review authority and uses ONLY the safe merge.
        if not (principal.can("governance.review") or principal.can("governance.admin")):
            raise GovernanceError("applying a merge requires governance.review or governance.admin")
        source_ids = cand.get("source_contact_ids") or []
        if len(source_ids) < 2:
            raise GovernanceError("cannot apply a merge without at least two source contacts")
        try:
            from app.services.person_merge import merge_source_contacts
            merged_person_id = merge_source_contacts(source_ids)   # refuses unsafe cross-person merges
        except ValueError as exc:
            raise GovernanceError(f"unsafe merge refused: {exc}") from exc
        golden_type, golden_id = "person", merged_person_id

    ts = now()
    with engine.begin() as c:
        row = c.execute(_merges.insert().values(
            duplicate_candidate_id=candidate_id, survivorship_rule_id=survivorship_rule_id,
            decision=decision, golden_record_entity_type=golden_type, golden_record_entity_id=golden_id,
            merged_person_id=merged_person_id, source_contact_ids=cand.get("source_contact_ids"),
            group_key=cand.get("group_key"), notes=notes, decided_by_user_id=actor_user_id,
            decided_at=ts, created_by_user_id=actor_user_id).returning(*_merges.c)).mappings().one()
        row = dict(row)
        new_status = "merged" if merged_person_id else ("rejected" if decision == "rejected" else "open")
        c.execute(_candidates.update().where(_candidates.c.id == candidate_id)
                  .values(status=new_status, merge_decision_id=row["id"], person_id=merged_person_id,
                          updated_at=ts))
        record_event(c, entity_type="candidate", entity_id=candidate_id,
                     event_type=f"merge_{decision}", to_status=new_status, actor_user_id=actor_user_id,
                     payload={"merged_person_id": merged_person_id})
    if merged_person_id:
        write_audit("governance.merge_approved", entity_type="person", entity_id=merged_person_id,
                    actor_user_id=actor_user_id, metadata={"candidate_id": candidate_id})
        publish_timeline({"id": row["id"], "person_id": merged_person_id}, "merge_approved",
                         title="Merge approved")
    return row


# --- lineage -----------------------------------------------------------------

def person_lineage(principal, person_id: int) -> list[dict]:
    """Read the EXISTING person lineage (person_source_links + source_contacts) — not duplicated."""
    with engine.connect() as c:
        rows = c.execute(
            select(person_source_links.c.id, person_source_links.c.match_method,
                   person_source_links.c.match_score, person_source_links.c.confirmed,
                   source_contacts.c.source_system, source_contacts.c.source_file,
                   source_contacts.c.source_record_id, source_contacts.c.source_hash,
                   source_contacts.c.imported_at)
            .select_from(person_source_links.join(
                source_contacts, person_source_links.c.source_contact_id == source_contacts.c.id))
            .where(person_source_links.c.person_id == person_id)
            .order_by(source_contacts.c.imported_at)).mappings()
        return [dict(r) for r in rows]


def list_lineage(entity_type: str, entity_id: int) -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(
            select(governance_lineage).where(governance_lineage.c.entity_type == entity_type,
                                             governance_lineage.c.entity_id == entity_id)
            .order_by(governance_lineage.c.id)).mappings()]


def record_lineage(principal, *, entity_type, entity_id, source_system, source_reference=None,
                   source_hash=None, source_contact_id=None, actor_user_id=None) -> dict:
    if entity_type not in GOV_ENTITY_TYPES:
        raise GovernanceError(f"invalid entity_type {entity_type!r}")
    if entity_type == "person":
        raise GovernanceError("person lineage is read from person_source_links; not duplicated here")
    with engine.begin() as c:
        row = c.execute(governance_lineage.insert().values(
            entity_type=entity_type, entity_id=entity_id, source_system=source_system,
            source_reference=source_reference, source_hash=source_hash,
            source_contact_id=source_contact_id, created_by_user_id=actor_user_id)
            .returning(*governance_lineage.c)).mappings().one()
        return dict(row)
