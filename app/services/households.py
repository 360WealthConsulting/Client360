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

from sqlalchemy import insert, select

from app.db import engine, household_relationships, households, people
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
