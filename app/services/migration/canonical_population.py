"""Canonical-population remediation — READ-ONLY preview.

The linkage forensics showed the real blocker is canonical-entity population, not document-resolver
coverage: thousands of ``source_contacts`` were never promoted/linked into canonical ``people``, business/
trust entities were never canonicalized into ``relationship_entities``, and households were never derived.
This preview classifies every UNLINKED source_contact into a deterministic proposed action and projects
how many of the linkage-exception folders would become resolvable after the repair — WITHOUT writing
anything.

Classification (deterministic; mirrors the promotion rules in app/matching/promote.py, but never promotes
on name alone where duplicates exist):

  safe_person_promotion         unlinked, unique email/phone identity, no candidate person, no collision
  existing_person_link          unlinked, email/phone matches EXACTLY ONE existing person
  business_company_candidate    source raw_data marks it a business/trust/estate (Wealthbox type=Company,
                                Drake return_type 1120/1120S/1065/1041/990) -> relationship_entities
  household_derivation_candidate joint exception folder whose BOTH members are exactly one canonical person
  ambiguous_identity            >1 candidate people, or shared email/phone with another unlinked contact
  duplicate_canonical_candidate canonical people that collide (reported as groups; never merged here)
  unresolved                    name-only, no deterministic identity

Canonical destinations use the EXISTING architecture only: people (via promote), relationship_entities
with entity_type in {business,trust,estate,...} for organizations, households via derivation. No new model.

STRICTLY READ-ONLY: SELECT only (+ session read-only); no insert/update/delete/merge, no household or
organization creation, no document linking, no storage_uri/document_sources changes, no apply.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from app.importers.taxdome_drive import _folder_person_keys, _name_key
from app.services.migration.base import MigrationJob, Mode, Outcome

# raw_data signals for a non-individual entity, per source system.
_WEALTHBOX_BUSINESS_TYPES = {"company", "business", "organization"}
_DRAKE_ENTITY_BY_RETURN = {"1120": "business", "1120s": "business", "1065": "business",
                           "990": "business", "1041": "trust"}


def business_kind(source_system, raw_data):
    """Return 'business'/'trust'/'estate' if the source record is a non-individual entity, else None.
    Deterministic, from raw_data only."""
    if not isinstance(raw_data, dict):
        return None
    if source_system == "Wealthbox":
        t = str(raw_data.get("type") or raw_data.get("contact_type") or "").strip().lower()
        return "business" if t in _WEALTHBOX_BUSINESS_TYPES else None
    if source_system == "Drake":
        rt = str(raw_data.get("return_type") or "").strip().lower()
        return _DRAKE_ENTITY_BY_RETURN.get(rt)
    return None


def classify_contact(sc, people_by_email, people_by_phone, email_counts, phone_counts):
    """Classify one UNLINKED source_contact. Returns (action, proposed_target, evidence, ambiguity_reason)."""
    kind = business_kind(sc["source_system"], sc.get("raw_data"))
    if kind:
        return ("business_company_candidate", f"relationship_entities:{kind}",
                f"{sc['source_system']} raw_data entity signal", "")
    ne, np = sc.get("normalized_email"), sc.get("normalized_phone")
    candidates = set(people_by_email.get(ne, ())) | set(people_by_phone.get(np, ())) if (ne or np) else set()
    if len(candidates) == 1:
        return ("existing_person_link", f"person:{next(iter(candidates))}", "unique email/phone match", "")
    if len(candidates) > 1:
        return ("ambiguous_identity", "", "", f"{len(candidates)} candidate people by email/phone")
    if (ne and email_counts.get(ne, 0) > 1) or (np and phone_counts.get(np, 0) > 1):
        return ("ambiguous_identity", "", "", "shared email/phone with another unlinked contact")
    if ne or np:
        return ("safe_person_promotion", "person:new", "unique email/phone identity", "")
    return ("unresolved", "", "", "name-only, no deterministic identity")


class CanonicalPopulationPreviewJob(MigrationJob):
    """Read-only preview of canonical-population repair. PREVIEW only; apply is never built here."""

    source_system = "Canonical Population"
    supported_modes = frozenset({Mode.PREVIEW})

    def _load(self):
        from sqlalchemy import select, text

        from app.db import engine, metadata
        source_contacts = metadata.tables["source_contacts"]
        person_source_links = metadata.tables["person_source_links"]
        people = metadata.tables["people"]
        with engine.connect() as conn:
            conn.execute(text("SET default_transaction_read_only = on"))
            linked = set(conn.execute(select(person_source_links.c.source_contact_id)).scalars())
            scs = [dict(m) for m in conn.execute(select(
                source_contacts.c.id, source_contacts.c.source_system, source_contacts.c.source_record_id,
                source_contacts.c.full_name, source_contacts.c.normalized_email,
                source_contacts.c.normalized_phone, source_contacts.c.raw_data)).mappings()]
            ppl = [dict(m) for m in conn.execute(select(
                people.c.id, people.c.full_name, people.c.normalized_email,
                people.c.normalized_phone)).mappings()]
        return linked, scs, ppl

    def _preview(self, preview_dir=None, **_opts) -> Outcome:
        linked, scs, ppl = self._load()

        # canonical people indexes
        people_by_email: dict = defaultdict(list)
        people_by_phone: dict = defaultdict(list)
        people_by_name: dict = defaultdict(list)
        for p in ppl:
            if p["normalized_email"]:
                people_by_email[p["normalized_email"]].append(p["id"])
            if p["normalized_phone"]:
                people_by_phone[p["normalized_phone"]].append(p["id"])
            k = _name_key(p["full_name"])
            if k:
                people_by_name[k].append(p["id"])

        unlinked = [s for s in scs if s["id"] not in linked]
        email_counts = Counter(s["normalized_email"] for s in unlinked if s["normalized_email"])
        phone_counts = Counter(s["normalized_phone"] for s in unlinked if s["normalized_phone"])

        rows: list[dict] = []
        actions: Counter = Counter()
        wealthbox_types: Counter = Counter()
        drake_return_types: Counter = Counter()
        # names that WOULD become uniquely resolvable people after promotion/link
        post_person_names: dict = defaultdict(int)
        business_names: set = set()

        for s in unlinked:
            rd = s.get("raw_data") if isinstance(s.get("raw_data"), dict) else {}
            if s["source_system"] == "Wealthbox":
                wealthbox_types[str(rd.get("type") or rd.get("contact_type") or "(none)")] += 1
            elif s["source_system"] == "Drake":
                drake_return_types[str(rd.get("return_type") or "(none)")] += 1
            action, target, evidence, ambiguity = classify_contact(
                s, people_by_email, people_by_phone, email_counts, phone_counts)
            actions[action] += 1
            nk = _name_key(s["full_name"])
            if action in ("safe_person_promotion", "existing_person_link") and nk:
                post_person_names[nk] += 1
            elif action == "business_company_candidate" and nk:
                business_names.add(nk)
            rows.append({
                "source_contact_id": s["id"], "source_system": s["source_system"],
                "source_record_id": s["source_record_id"] or "", "source_name": s["full_name"] or "",
                "proposed_action": action, "proposed_canonical_target": target,
                "evidence": evidence, "ambiguity_reason": ambiguity,
            })

        # F: duplicate canonical people groups (report groups + evidence only)
        dup_name_groups = {k: v for k, v in people_by_name.items() if len(v) > 1}
        dup_email_groups = {k: v for k, v in people_by_email.items() if len(v) > 1}
        dup_phone_groups = {k: v for k, v in people_by_phone.items() if len(v) > 1}
        largest_name_group = max((len(v) for v in dup_name_groups.values()), default=0)

        # D + folder projection (needs the linkage exceptions)
        household_candidates = 0
        newly_person = newly_business = newly_household = 0
        folders_total = 0
        if preview_dir:
            from scripts.migration.analyze_linkage_exceptions import read_exception_folders
            folders = read_exception_folders(preview_dir)
            folders_total = len(folders)
            # existing unique people names + names promotable to unique people
            unique_existing = {k for k, v in people_by_name.items() if len(v) == 1}
            promotable_unique = {k for k, v in post_person_names.items() if v == 1}
            for folder in folders:
                fk = _name_key(folder)
                member_keys = _folder_person_keys(folder)
                joint = len(member_keys) > 1
                if joint:
                    if all(len(people_by_name.get(k, [])) == 1 for k in member_keys):
                        household_candidates += 1
                        newly_household += 1
                    continue
                if fk and fk not in unique_existing and fk in promotable_unique:
                    newly_person += 1
                elif fk and fk in business_names:
                    newly_business += 1

        counts = {
            "source_contacts_total": len(scs), "linked": len(linked), "unlinked": len(unlinked),
            "safe_person_promotion": actions["safe_person_promotion"],
            "existing_person_link": actions["existing_person_link"],
            "business_company_candidate": actions["business_company_candidate"],
            "ambiguous_identity": actions["ambiguous_identity"],
            "unresolved": actions["unresolved"],
            "household_derivation_candidates": household_candidates,
            "duplicate_person_groups_by_name": len(dup_name_groups),
            "duplicate_person_groups_by_email": len(dup_email_groups),
            "duplicate_person_groups_by_phone": len(dup_phone_groups),
            "largest_duplicate_name_group": largest_name_group,
            "wealthbox_type_distribution": dict(wealthbox_types),
            "drake_return_type_distribution": dict(drake_return_types),
            "exception_folders_total": folders_total,
            "folders_newly_resolvable_person": newly_person,
            "folders_newly_resolvable_business": newly_business,
            "folders_newly_resolvable_household": newly_household,
            "folders_newly_resolvable_total": newly_person + newly_business + newly_household,
        }
        notes = [
            "PREVIEW ONLY — no promotion, linking, entity/household creation, merge, document linkage, "
            "storage_uri/document_sources change, or apply. Read-only SELECTs.",
            "Canonical destinations use the existing architecture only: people (promotion), "
            "relationship_entities {business,trust,estate,...} for organizations, households via derivation.",
            "safe_person_promotion requires a unique email/phone identity — never name-only where "
            "duplicates exist. Ambiguous identities are held for Match Review, never guessed.",
            "duplicate_canonical_candidate groups are reported for review only — no person is merged here.",
            f"Duplicate canonical people: {len(dup_name_groups)} name-groups "
            f"(largest {largest_name_group}) — the cause of the exact-key ambiguity.",
        ]
        return Outcome(counts=counts, exceptions=[], reconciliation=rows, notes=notes)
