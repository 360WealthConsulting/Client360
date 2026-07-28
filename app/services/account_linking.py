"""Account → person linking (read-derived backfill).

The Schwab importer loads ``accounts`` (custodian / number / value) and, separately,
``source_contacts`` (``Schwab Profile``, keyed by account number). Identity matching then
links those source contacts to canonical people in ``person_source_links``. Nothing,
however, projects that authoritative person↔source link onto ``accounts.person_id`` — the
column the portfolio / wealth reads aggregate on (see ``app.services.portfolio``). So a
client whose Schwab profile is "linked" still reads **$0** on the Client 360 summary tiles
and Financial tab, because their account rows carry ``person_id = NULL``.

This module closes that gap by *propagating the EXISTING ``person_source_links`` onto
``accounts.person_id`` / ``household_id``*, matching an account to its profile by account
number (normalized — dashes / spacing removed). It reuses only the existing identity tables;
it never creates a person or an account, never moves money, and never touches the importer.
It sets the person / household foreign keys on an account that identity has already resolved.

Idempotent: a second run makes no changes. Run:  ``python -m app.services.account_linking``
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import func, select

from app.db import accounts, engine, people, person_source_links, source_contacts

PROFILE_SOURCE_SYSTEM = "Schwab Profile"
ACCOUNT_CUSTODIAN = "Schwab"

# The four Schwab profiles whose person_source_link is missing (a known
# consistency gap). Repaired from the EXISTING source_contacts + the canonical
# person ids below — no person or account is created. (account_number, person_id).
KNOWN_SOURCE_LINK_REPAIRS = (
    ("4493-0753", 3821),   # Michael B Lancaster
    ("7987-8768", 2905),   # Kendell Q Lancaster
    ("7997-2151", 2905),   # Kendell Q Lancaster
    ("8384-4640", 3822),   # Matthew Nicholson
)


def _norm(number: str | None) -> str:
    """Normalize an account identifier so ``8188-0181`` (accounts) matches ``81880181``
    (source_contacts.source_record_id). Case-folded, non-alphanumerics dropped."""
    if not number:
        return ""
    return re.sub(r"[^0-9a-z]", "", str(number).lower())


def _profile_person_by_number(conn) -> dict[str, int]:
    """Map a normalized Schwab-Profile account number → the UNIQUE canonical person it is
    linked to. A profile linked to several people is ambiguous and is skipped (never a weak
    or guessed link). Confirmed links win: if any confirmed link exists, only confirmed
    links are considered."""
    rows = conn.execute(
        select(source_contacts.c.source_record_id, person_source_links.c.person_id,
               person_source_links.c.confirmed)
        .select_from(source_contacts.join(
            person_source_links, person_source_links.c.source_contact_id == source_contacts.c.id))
        .where(source_contacts.c.source_system == PROFILE_SOURCE_SYSTEM)
    ).mappings().all()

    by_number: dict[str, list[tuple[int, bool]]] = defaultdict(list)
    for r in rows:
        num = _norm(r["source_record_id"])
        if num:
            by_number[num].append((r["person_id"], bool(r["confirmed"])))

    resolved: dict[str, int] = {}
    for num, links in by_number.items():
        confirmed = {pid for pid, is_confirmed in links if is_confirmed}
        candidates = confirmed or {pid for pid, _ in links}
        if len(candidates) == 1:          # unique → safe to project onto the account
            resolved[num] = next(iter(candidates))
    return resolved


def _link(conn) -> dict:
    resolved = _profile_person_by_number(conn)
    households_by_person: dict[int, int | None] = {}
    if resolved:
        for r in conn.execute(
            select(people.c.id, people.c.household_id).where(people.c.id.in_(set(resolved.values())))
        ).mappings():
            households_by_person[r["id"]] = r["household_id"]

    account_rows = conn.execute(
        select(accounts.c.id, accounts.c.account_number, accounts.c.person_id, accounts.c.household_id)
        .where(accounts.c.custodian == ACCOUNT_CUSTODIAN, accounts.c.account_number.is_not(None))
    ).mappings().all()

    linked = already = unresolved = 0
    for a in account_rows:
        person_id = resolved.get(_norm(a["account_number"]))
        if person_id is None:
            unresolved += 1
            continue
        if a["person_id"] == person_id:
            already += 1
            continue
        values = {"person_id": person_id}
        # Fill household only when the account has none — never override an existing one.
        if a["household_id"] is None and households_by_person.get(person_id) is not None:
            values["household_id"] = households_by_person[person_id]
        conn.execute(accounts.update().where(accounts.c.id == a["id"]).values(**values))
        linked += 1

    return {"accounts_seen": len(account_rows), "linked": linked,
            "already_linked": already, "unresolved": unresolved}


def _repair(conn, pairs) -> dict:
    contacts_by_number: dict[str, int] = {}
    for r in conn.execute(
        select(source_contacts.c.id, source_contacts.c.source_record_id)
        .where(source_contacts.c.source_system == PROFILE_SOURCE_SYSTEM)
    ).mappings():
        num = _norm(r["source_record_id"])
        if num:
            contacts_by_number.setdefault(num, r["id"])

    created = already = skipped_no_contact = skipped_no_person = 0
    for number, person_id in pairs:
        source_contact_id = contacts_by_number.get(_norm(number))
        if source_contact_id is None:
            skipped_no_contact += 1
            continue
        if not conn.scalar(select(people.c.id).where(people.c.id == person_id)):
            skipped_no_person += 1
            continue
        exists = conn.scalar(select(person_source_links.c.id).where(
            person_source_links.c.person_id == person_id,
            person_source_links.c.source_contact_id == source_contact_id))
        if exists:
            already += 1
            continue
        conn.execute(person_source_links.insert().values(
            person_id=person_id, source_contact_id=source_contact_id,
            match_method="manual_repair", match_score=100, confirmed=True))
        created += 1

    return {"created": created, "already_present": already,
            "skipped_no_contact": skipped_no_contact, "skipped_no_person": skipped_no_person}


def repair_source_links(pairs=KNOWN_SOURCE_LINK_REPAIRS, *, conn=None) -> dict:
    """Ensure a ``person_source_link`` exists for each (account_number, person_id) pair,
    reusing the existing Schwab-Profile source_contact and the given canonical person.
    Idempotent; creates no person or account. Pass ``conn`` to run inside a caller's
    transaction, else a private transaction is used."""
    if conn is None:
        with engine.begin() as owned:
            return _repair(owned, pairs)
    return _repair(conn, pairs)


def link_accounts_to_people(*, conn=None) -> dict:
    """Project authoritative ``person_source_links`` onto ``accounts.person_id`` /
    ``household_id`` (by normalized account number). Idempotent; sets only the person /
    household FKs, never creating or moving anything. Pass ``conn`` to run inside a
    caller's transaction, else a private transaction is used."""
    if conn is None:
        with engine.begin() as owned:
            return _link(owned)
    return _link(conn)


def unlinked_profile_count(*, conn=None) -> int:
    """How many Schwab-Profile source_contacts have NO person_source_link (the residual
    identity gap). 0 means every Schwab profile is linked to a canonical person."""
    def _count(c):
        return c.scalar(
            select(func.count()).select_from(
                source_contacts.outerjoin(
                    person_source_links,
                    person_source_links.c.source_contact_id == source_contacts.c.id))
            .where(source_contacts.c.source_system == PROFILE_SOURCE_SYSTEM,
                   person_source_links.c.id.is_(None)))
    if conn is None:
        with engine.connect() as c:
            return _count(c) or 0
    return _count(conn) or 0


def _plan(conn, pairs) -> dict:
    """Compute the exact set of writes an ``--apply`` run would perform, WITHOUT writing."""
    contacts_by_number: dict[str, dict] = {}
    for r in conn.execute(
        select(source_contacts.c.id, source_contacts.c.source_record_id, source_contacts.c.full_name)
        .where(source_contacts.c.source_system == PROFILE_SOURCE_SYSTEM)
    ).mappings():
        num = _norm(r["source_record_id"])
        if num:
            contacts_by_number.setdefault(num, dict(r))

    existing = {(r["person_id"], r["source_contact_id"]) for r in conn.execute(
        select(person_source_links.c.person_id, person_source_links.c.source_contact_id)).mappings()}

    # 1) The source links the repair step would create — and the number→person they unlock.
    source_links_to_create: list[dict] = []
    pending: dict[str, int] = {}
    for number, person_id in pairs:
        num = _norm(number)
        contact = contacts_by_number.get(num)
        if contact is None:                                              # no matching Schwab profile
            continue
        if not conn.scalar(select(people.c.id).where(people.c.id == person_id)):   # canonical person missing
            continue
        if (person_id, contact["id"]) in existing:                      # link already present
            continue
        source_links_to_create.append({
            "source_contact_id": contact["id"], "person_id": person_id,
            "account_number": number, "contact_name": contact["full_name"]})
        pending[num] = person_id

    # 2) Resolvable number→person from existing unique links, overlaid with the pending repairs.
    resolved = _profile_person_by_number(conn)
    for num, person_id in pending.items():
        resolved.setdefault(num, person_id)

    # 3) The accounts that would receive a person_id.
    account_changes: list[dict] = []
    needed: set[int] = set()
    for a in conn.execute(
        select(accounts.c.id, accounts.c.account_number, accounts.c.account_name, accounts.c.person_id)
        .where(accounts.c.custodian == ACCOUNT_CUSTODIAN, accounts.c.account_number.is_not(None))
        .order_by(accounts.c.account_number)
    ).mappings():
        person_id = resolved.get(_norm(a["account_number"]))
        if person_id is None or a["person_id"] == person_id:
            continue
        needed.add(person_id)
        account_changes.append({
            "account_id": a["id"], "account_number": a["account_number"],
            "account_name": a["account_name"], "current_person_id": a["person_id"],
            "new_person_id": person_id, "client_name": None})

    names = {}
    if needed:
        names = {r["id"]: r["full_name"] for r in conn.execute(
            select(people.c.id, people.c.full_name).where(people.c.id.in_(needed))).mappings()}
    for change in account_changes:
        change["client_name"] = names.get(change["new_person_id"])

    return {"source_links_to_create": source_links_to_create, "account_changes": account_changes}


def preview(pairs=KNOWN_SOURCE_LINK_REPAIRS, *, conn=None) -> dict:
    """Compute — WITHOUT writing anything — exactly what an ``--apply`` run would change: the
    Schwab-Profile source links that would be created, and the accounts that would receive a
    ``person_id`` (with current + new person and the canonical client name). Read-only."""
    if conn is None:
        with engine.connect() as c:
            return _plan(c, pairs)
    return _plan(conn, pairs)


def run() -> dict:
    """Repair the known missing source links, then link accounts — atomically."""
    with engine.begin() as conn:
        repair = _repair(conn, KNOWN_SOURCE_LINK_REPAIRS)
        link = _link(conn)
    return {"source_link_repair": repair, "account_link": link}


def _print_preview(plan) -> None:
    links, changes = plan["source_links_to_create"], plan["account_changes"]
    print("DRY RUN — no changes were written.\n")
    print(f"Schwab Profile source links to be created: {len(links)}")
    if links:
        print(f"  {'source_contact_id':>17}  {'person_id':>9}  {'account_number':<16}  contact_name")
        for row in links:
            print(f"  {row['source_contact_id']:>17}  {row['person_id']:>9}  "
                  f"{row['account_number']:<16}  {row['contact_name'] or ''}")
    print()
    print(f"Accounts that will receive a person_id: {len(changes)}")
    if changes:
        print(f"  {'account_id':>10}  {'account_number':<16}  {'current':>7}  {'new':>7}  client_name")
        for row in changes:
            current = "NULL" if row["current_person_id"] is None else row["current_person_id"]
            print(f"  {row['account_id']:>10}  {row['account_number']:<16}  {str(current):>7}  "
                  f"{row['new_person_id']:>7}  {row['client_name'] or row['account_name'] or ''}")


def _print_apply(result) -> None:
    repair, link = result["source_link_repair"], result["account_link"]
    print("Schwab source-link repair:")
    print(f"  created:              {repair['created']}")
    print(f"  already present:      {repair['already_present']}")
    print(f"  skipped (no profile): {repair['skipped_no_contact']}")
    print(f"  skipped (no person):  {repair['skipped_no_person']}")
    print()
    print("Account → person linking:")
    print(f"  accounts seen:        {link['accounts_seen']}")
    print(f"  newly linked:         {link['linked']}")
    print(f"  already linked:       {link['already_linked']}")
    print(f"  unresolved:           {link['unresolved']}")
    print()
    print(f"Schwab profiles still unlinked: {unlinked_profile_count()}")


def main(argv=None) -> int:
    """CLI. Requires an explicit mode so a bare invocation never writes to a live database:
    ``--dry-run`` previews (read-only); ``--apply`` performs the repair + linking."""
    import sys
    args = sys.argv[1:] if argv is None else argv
    if "--dry-run" in args:
        _print_preview(preview())
        return 0
    if "--apply" in args:
        _print_apply(run())
        return 0
    print("Usage: python -m app.services.account_linking [--dry-run | --apply]")
    print("  --dry-run   show exactly what would change (no writes)")
    print("  --apply     perform the source-link repair and account linking")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
