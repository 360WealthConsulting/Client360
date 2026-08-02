"""Household service — the supported API for grouping people into a household.

``assign_people_to_household`` is the single supported entry point for assigning an explicit,
human-verified set of people (e.g. spouses) to one household. It is **UI-first**: the future Household
Management UI calls it directly (passing the acting principal's ``actor_user_id``/``request_id`` for an
audited change and rendering the returned summary / typed errors), and the thin ``main`` CLI at the
bottom is just one caller, kept for deployment and automation. Staff are **not** expected to assign
households from PowerShell — the CLI exists for operators, the service is the API for the product.

This is needed where the automatic :mod:`app.services.household_derivation` engine cannot help — its
policies are "group nothing" (default) or "group by address" (a candidate awaiting firm approval), and
shared surname alone is too weak a signal to auto-merge. Assigning specific people is a human decision,
so the service takes explicit person ids rather than inferring a match.

It uses the same tables and conventions as ``household_derivation``: reuse/create a row in
``households``, set ``people.household_id``, and record a ``household_relationships`` ``member`` row.
No schema change. Safety guarantees (identical whether called from the UI or the CLI): it preserves
every existing person record and every document/source link (only the household relationship is
touched), never creates a duplicate household, is idempotent, and refuses to silently merge people who
already belong to *different* households.

CLI (one caller of the service; for deployment/automation)::

    python -m app.services.households 3824 2008                       # assign both to one household
    python -m app.services.households 3824 2008 --name "White Household"
    python -m app.services.households 3824 2008 --dry-run             # report only, no changes
"""
from __future__ import annotations

import argparse

from sqlalchemy import and_, func, insert, or_, select

from app.db import (
    audit_events,
    documents,
    engine,
    household_relationships,
    households,
    people,
)
from app.services.household_derivation import _household_name


def assign_people_to_household(person_ids, *, name: str | None = None, actor_user_id: int | None = None,
                              request_id: str | None = None, dry_run: bool = False) -> dict:
    """Assign the given people to one common household.

    - If none of them has a household, create one (``name`` or a derived "<Surname> Household").
    - If exactly one household already exists among them, reuse it and fill in the others.
    - If they already span **different** households, raise ValueError (never auto-merge households).
    Returns a summary dict. With ``dry_run`` nothing is written.
    """
    ids = sorted({int(p) for p in person_ids})
    if not ids:
        raise ValueError("at least one person id is required")

    with engine.begin() as conn:
        rows = conn.execute(
            select(people.c.id, people.c.full_name, people.c.last_name, people.c.household_id)
            .where(people.c.id.in_(ids))).mappings().all()
        missing = [i for i in ids if i not in {r["id"] for r in rows}]
        if missing:
            raise ValueError(f"person id(s) not found: {missing}")

        existing = {r["household_id"] for r in rows if r["household_id"] is not None}
        if len(existing) > 1:
            raise ValueError(
                f"people already belong to different households {sorted(existing)}; "
                "refuse to auto-merge — resolve the household conflict explicitly first")

        target = next(iter(existing)) if existing else None
        report = {"household_id": target, "household_created": False,
                  "household_name": None, "members_assigned": 0, "already_members": 0,
                  "membership_rows_added": 0, "dry_run": dry_run, "person_ids": ids}

        if target is None:
            report["household_name"] = name or _household_name(list(rows))
            if not dry_run:
                target = conn.execute(households.insert().values(name=report["household_name"])
                                      .returning(households.c.id)).scalar_one()
                report["household_id"] = target
                report["household_created"] = True
            else:
                report["household_created"] = True          # would create

        for r in rows:
            if target is not None and r["household_id"] == target:
                report["already_members"] += 1
            else:
                report["members_assigned"] += 1
                if not dry_run:
                    conn.execute(people.update().where(people.c.id == r["id"])
                                 .values(household_id=target))

        if not dry_run:
            for r in rows:
                has_membership = conn.execute(
                    select(household_relationships.c.id).where(
                        household_relationships.c.household_id == target,
                        household_relationships.c.person_id == r["id"])).first()
                if not has_membership:
                    conn.execute(insert(household_relationships).values(
                        household_id=target, person_id=r["id"], relationship_type="member"))
                    report["membership_rows_added"] += 1
            if actor_user_id is not None:
                from app.security.audit import write_audit_event
                write_audit_event(action="household.members_assigned", entity_type="household",
                                  entity_id=target, actor_user_id=actor_user_id, request_id=request_id,
                                  metadata={"person_ids": ids, "created": report["household_created"]})
        return report


# --- read API (consumed by the Household Management UI; no new ownership logic) ----------------

def _member_ids(conn, household_id) -> list[int]:
    return list(conn.scalars(select(household_relationships.c.person_id)
                             .where(household_relationships.c.household_id == household_id)))


def search_households(query: str | None = None, *, limit: int = 50) -> list[dict]:
    """Households matching a query by household name/city or a member's name. Blank query -> all
    (name-ordered). Includes a member count for the list view."""
    q = (query or "").strip()
    member_count = func.count(household_relationships.c.person_id).label("member_count")
    stmt = (select(households.c.id, households.c.name, households.c.city, households.c.state,
                   member_count)
            .select_from(households.outerjoin(
                household_relationships, household_relationships.c.household_id == households.c.id))
            .group_by(households.c.id, households.c.name, households.c.city, households.c.state)
            .order_by(households.c.name).limit(limit))
    if q:
        like = f"%{q}%"
        by_member = (select(household_relationships.c.household_id)
                     .select_from(household_relationships.join(
                         people, people.c.id == household_relationships.c.person_id))
                     .where(people.c.full_name.ilike(like)))
        stmt = stmt.where(or_(households.c.name.ilike(like), households.c.city.ilike(like),
                              households.c.id.in_(by_member)))
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings().all()]


def household_members(household_id) -> list[dict]:
    """Members with their household relationship role (spouse/dependent/member), primary flags."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(people.c.id, people.c.full_name, people.c.primary_email, people.c.primary_phone,
                   household_relationships.c.relationship_type, household_relationships.c.is_primary)
            .select_from(household_relationships.join(
                people, people.c.id == household_relationships.c.person_id))
            .where(household_relationships.c.household_id == household_id)
            .order_by(household_relationships.c.is_primary.desc(),
                      people.c.last_name, people.c.first_name)).mappings().all()
    return [dict(r) for r in rows]


def household_documents(household_id, *, limit: int = 500) -> list[dict]:
    """Documents owned by the household OR by any current member — the household view of ownership
    (ADR-072 canonical document, ADR-073 ownership). No new ownership logic: same person-or-household
    resolution the person Documents tab uses, scoped to the whole household."""
    with engine.connect() as conn:
        member_ids = _member_ids(conn, household_id)
        scope = documents.c.household_id == household_id
        if member_ids:
            scope = or_(scope, documents.c.person_id.in_(member_ids))
        rows = conn.execute(
            select(documents.c.id, documents.c.original_name, documents.c.category,
                   documents.c.size_bytes, documents.c.person_id, documents.c.household_id,
                   documents.c.storage_provider, documents.c.created_at,
                   documents.c.tags["taxdome_folder"].astext.label("taxdome_folder"))
            .where(and_(scope, documents.c.archived.is_(False)))
            .order_by(documents.c.created_at.desc(), documents.c.id.desc()).limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def household_audit(household_id, *, limit: int = 50) -> list[dict]:
    """Recent audit events recorded against this household (ownership changes, member assignment)."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(audit_events.c.action, audit_events.c.actor_user_id, audit_events.c.occurred_at,
                   audit_events.c.outcome, audit_events.c.metadata)
            .where(audit_events.c.entity_type == "household",
                   audit_events.c.entity_id == str(household_id))
            .order_by(audit_events.c.occurred_at.desc()).limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def unresolved_taxdome_folders(*, limit: int = 200) -> list[dict]:
    """TaxDome folders whose documents are still unlinked (no person and no household), with the
    candidate people for each — the worklist for the in-product resolve tool. Re-evaluates
    resolution live, so a folder becomes resolvable as soon as its household/people exist."""
    from app.importers import taxdome_drive as td
    folder_col = documents.c.tags["taxdome_folder"].astext
    with engine.connect() as conn:
        rows = conn.execute(
            select(folder_col.label("folder"), func.count().label("files"))
            .where(and_(td.taxdome_filter(documents),
                        documents.c.person_id.is_(None), documents.c.household_id.is_(None)))
            .group_by(folder_col).order_by(folder_col).limit(limit)).mappings().all()
        out = []
        for r in rows:
            if not r["folder"]:
                continue
            household_id, person_id = td.resolve_folder(conn, r["folder"])
            out.append({
                "folder": r["folder"], "files": r["files"],
                "resolves_to": {"household_id": household_id, "person_id": person_id},
                "suggestions": td.suggest_people(conn, r["folder"]),
            })
    return out


def duplicate_candidates(sha256_hex, *, limit: int = 20) -> list[dict]:
    """Documents sharing a content hash — the compare-duplicates candidate set (canonical de-dup by
    SHA-256, ADR-072). Read-only; never merges."""
    if not sha256_hex:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            select(documents.c.id, documents.c.original_name, documents.c.person_id,
                   documents.c.household_id, documents.c.organization_id,
                   documents.c.storage_provider, documents.c.created_at)
            .where(documents.c.sha256 == sha256_hex, documents.c.archived.is_(False))
            .order_by(documents.c.id).limit(limit)).mappings().all()
    return [dict(r) for r in rows]


def resolve_folder_ownership(folder_name, *, household_id=None, person_id=None,
                             actor_user_id=None, request_id=None, dry_run: bool = False) -> dict:
    """Link a TaxDome folder's currently-unlinked documents to an existing household and/or person —
    the in-product, audited replacement for the ``--repair-links`` CLI for a single folder. Fills NULL
    ownership only (never overwrites a manual link, never creates a duplicate row). No new ownership
    logic: reuses the same folder-link the sync uses."""
    if household_id is None and person_id is None:
        raise ValueError("a household_id or person_id is required")
    from app.importers import taxdome_drive as td
    if dry_run:
        base = and_(td.taxdome_filter(documents),
                    documents.c.tags["taxdome_folder"].astext == folder_name)
        with engine.connect() as conn:
            n = 0
            if person_id is not None:
                n += conn.execute(select(func.count()).select_from(documents)
                                  .where(and_(base, documents.c.person_id.is_(None)))).scalar() or 0
            if household_id is not None:
                n += conn.execute(select(func.count()).select_from(documents)
                                  .where(and_(base, documents.c.household_id.is_(None)))).scalar() or 0
        return {"folder": folder_name, "documents_updated": n, "dry_run": True}
    with engine.begin() as conn:
        updated = td._apply_folder_link(conn, documents, folder_name, household_id, person_id)
        if actor_user_id is not None and updated:
            from app.security.audit import write_audit_event
            write_audit_event(action="document.ownership_resolved", entity_type="taxdome_folder",
                              entity_id=folder_name, actor_user_id=actor_user_id, request_id=request_id,
                              metadata={"folder": folder_name, "household_id": household_id,
                                        "person_id": person_id, "documents_updated": updated})
    return {"folder": folder_name, "documents_updated": updated, "dry_run": False}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m app.services.households",
        description="Assign explicit people to one common household (create if needed).")
    parser.add_argument("person_ids", nargs="+", type=int, help="Canonical people.id values to group.")
    parser.add_argument("--name", default=None, help="Household name (default '<Surname> Household').")
    parser.add_argument("--dry-run", action="store_true", help="Report only; make no changes.")
    args = parser.parse_args(argv)
    try:
        report = assign_people_to_household(args.person_ids, name=args.name, dry_run=args.dry_run)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    label = "DRY RUN — no changes made" if args.dry_run else "household assignment complete"
    print(f"Household assignment {label}.")
    for key in ("household_id", "household_name", "household_created", "members_assigned",
                "already_members", "membership_rows_added"):
        print(f"  {key}: {report[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
