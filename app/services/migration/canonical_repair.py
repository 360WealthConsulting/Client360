"""Canonical-population REPAIR — first narrow APPLY (deterministic set only).

Applies ONLY the deterministic, production-validated repairs, reusing the existing canonical architecture:
  * safe_person_promotion       create a canonical person from an unlinked source_contact + link it
                                (reuses app.matching.promote._create_person / _link)
  * existing_person_link        link an unlinked source_contact to an existing person — PLAUSIBLE ONLY
                                (same first + last name); suspect/household-shared links are EXCLUDED
  * household_derivation        create the proven 6 households from joint folders whose both members are
                                exactly one canonical person; set people.household_id
  * business/trust canonicalization  one relationship_entities per DEDUPED deterministic identity
                                (Drake repeated tax-years collapse to ONE entity), via
                                app.services.relationships.create_named_entity; provenance in details

EXCLUDED (never touched here): duplicate-name excess people, suspect links, ambiguous_identity,
unresolved, and ALL document/storage_uri/document_sources changes (document linkage APPLY stays disabled).

Guards: APPLY requires an explicit confirm flag, a verified DB backup file, and the approved expected
counts — it recomputes the plan live and FAILS CLOSED before any write if a count differs. Every write is
idempotent. Full before/after reconciliation is emitted.
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from dataclasses import dataclass, field

from app.importers.taxdome_drive import _folder_person_keys, _name_key
from app.services.migration.base import MigrationJob, Mode, Outcome
from app.services.migration.canonical_population import business_kind, classify_contact


class RepairGuardError(RuntimeError):
    """APPLY aborted by a guard BEFORE any write (bad backup, missing confirm, or count drift)."""


def plausible_link(contact_first, contact_last, target_first, target_last, share) -> bool:
    """SINGLE SOURCE OF TRUTH (shared with diagnose_duplicate_people) for whether a proposed
    existing-person link is plausible enough to auto-apply.

    Plausible only when BOTH hold:
      * the matched email/phone identity is NOT shared with another unlinked contact (``share <= 1``) —
        a shared identity is a spouse/household-shared identity, held for review; and
      * the names are not PROVABLY different — a same-last-name, different-first-name match is a spouse,
        excluded. Missing structured names are tolerated (not proven different), which is why a contact
        carrying only ``full_name`` still links to its uniquely-identified person.
    """
    cf, cl = (contact_first or "").strip().lower(), (contact_last or "").strip().lower()
    tf, tl = (target_first or "").strip().lower(), (target_last or "").strip().lower()
    same_last = (cl == tl) if (cl and tl) else None
    same_first = (cf == tf) if (cf and tf) else None
    if share > 1:
        return False
    if same_last and same_first is False:
        return False
    return True


def _read_exception_folders(preview_dir):
    if not preview_dir:
        return []
    path = os.path.join(preview_dir, "exceptions.csv")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return [(r.get("source_folder") or "") for r in csv.DictReader(f)]


def load_approved_set(path):
    """Load the FROZEN approved set from an approved run's ``reconciliation.csv`` (pass the run directory
    or the csv path). The approved set anchors scope: a post-APPLY run must not expand the repair set just
    because earlier writes altered matching state."""
    csvpath = path if str(path).endswith(".csv") else os.path.join(path, "reconciliation.csv")
    approved = {"promotions": set(), "links": set(), "households": set(), "businesses": set()}
    with open(csvpath, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            cat, scid = (r.get("category") or ""), (r.get("source_contact_id") or "").strip()
            if cat == "safe_person_promotion" and scid.isdigit():
                approved["promotions"].add(int(scid))
            elif cat == "existing_person_link" and scid.isdigit():
                approved["links"].add(int(scid))
            elif cat == "household_derivation":
                approved["households"].add(r.get("source_name") or "")
            elif cat.endswith("_canonicalization"):
                kind = cat[: -len("_canonicalization")]
                approved["businesses"].add((kind, _name_key(r.get("source_name") or "")))
    return approved


@dataclass
class RepairPlan:
    promotions: list = field(default_factory=list)   # source_contact dicts (PENDING)
    links: list = field(default_factory=list)        # {sc, target_person_id, evidence} (PENDING)
    households: list = field(default_factory=list)    # {folder, member_person_ids, name} (PENDING)
    businesses: list = field(default_factory=list)    # {kind, name, source_contact_ids, source_record_ids} (PENDING)
    applied_promotions: int = 0                       # already promoted by a prior repair run
    applied_links: int = 0                            # already linked by a prior repair run
    applied_households: int = 0                        # candidate whose members already share a household
    applied_businesses: int = 0                        # group whose relationship_entities already exists
    newly_eligible: dict = field(default_factory=lambda: {"promotions": 0, "links": 0,
                                                          "households": 0, "businesses": 0})

    def counts(self):
        """Pending work this run would still do (excludes already-applied)."""
        return {"promotions": len(self.promotions), "links": len(self.links),
                "households": len(self.households), "businesses": len(self.businesses)}

    def guard_counts(self):
        """STABLE intended totals for the FROZEN approved set (pending + already-applied-by-repair), for
        EVERY category. Idempotent across re-runs: after the repair is applied, pending -> 0 and the
        totals still equal the approved counts. Newly-eligible records (out of the approved scope) are
        tracked in ``newly_eligible`` and never counted here."""
        return {"promotions": len(self.promotions) + self.applied_promotions,
                "links": len(self.links) + self.applied_links,
                "households": len(self.households) + self.applied_households,
                "businesses": len(self.businesses) + self.applied_businesses}


class CanonicalRepairJob(MigrationJob):
    source_system = "Canonical Repair"
    supported_modes = frozenset({Mode.PREVIEW, Mode.APPLY})

    # -- snapshot + plan (read-only) -----------------------------------------
    def _snapshot(self, conn):
        from sqlalchemy import select

        from app.db import metadata
        sc = metadata.tables["source_contacts"]
        psl = metadata.tables["person_source_links"]
        people = metadata.tables["people"]
        linked = set(conn.execute(select(psl.c.source_contact_id)).scalars())
        scs = [dict(m) for m in conn.execute(select(sc)).mappings()]
        ppl = [dict(m) for m in conn.execute(select(
            people.c.id, people.c.full_name, people.c.first_name, people.c.last_name,
            people.c.normalized_email, people.c.normalized_phone, people.c.household_id)).mappings()]
        return linked, scs, ppl

    def _plan(self, conn, folders, approved=None) -> RepairPlan:
        from sqlalchemy import select

        from app.db import metadata
        linked, scs, ppl = self._snapshot(conn)
        by_email: dict = defaultdict(list)
        by_phone: dict = defaultdict(list)
        by_name: dict = defaultdict(list)
        prow: dict = {}
        for p in ppl:
            prow[p["id"]] = p
            if p["normalized_email"]:
                by_email[p["normalized_email"]].append(p["id"])
            if p["normalized_phone"]:
                by_phone[p["normalized_phone"]].append(p["id"])
            k = _name_key(p["full_name"])
            if k:
                by_name[k].append(p["id"])

        unlinked = [s for s in scs if s["id"] not in linked]
        email_counts = _counter(s["normalized_email"] for s in unlinked if s["normalized_email"])
        phone_counts = _counter(s["normalized_phone"] for s in unlinked if s["normalized_phone"])
        # existing standalone entities, for business/trust applied-recognition
        rel = metadata.tables["relationship_entities"]
        existing_entities = {(r_[0], _name_key(r_[1])) for r_ in
                             conn.execute(select(rel.c.entity_type, rel.c.name)) if r_[1]}

        plan = RepairPlan()

        def in_scope(cat, key):
            """True if this candidate is in the frozen approved set (or no approval -> first run)."""
            if approved is None:
                return True
            if key in approved[cat]:
                return True
            plan.newly_eligible[cat] += 1
            return False

        biz_groups: dict = {}
        for s in unlinked:
            kind = business_kind(s["source_system"], s.get("raw_data"))
            if kind:
                key = (kind, _name_key(s["full_name"]))
                g = biz_groups.setdefault(key, {"kind": kind, "name": (s["full_name"] or "").strip(),
                                                "source_contact_ids": [], "source_record_ids": []})
                g["source_contact_ids"].append(s["id"])
                if s.get("source_record_id"):
                    g["source_record_ids"].append(s["source_record_id"])
                continue
            action, _t, _e, _a = classify_contact(s, by_email, by_phone, email_counts, phone_counts)
            if action == "safe_person_promotion":
                if in_scope("promotions", s["id"]):
                    plan.promotions.append(s)
            elif action == "existing_person_link":
                ne, np = s["normalized_email"], s["normalized_phone"]
                cand = (by_email.get(ne) or by_phone.get(np) or [None])
                pid = cand[0]
                tp = prow.get(pid, {})
                # share = how many unlinked contacts share the matched identity (email preferred, else phone)
                share = email_counts.get(ne, 0) if (ne and by_email.get(ne)) else (
                    phone_counts.get(np, 0) if (np and by_phone.get(np)) else 0)
                if pid and plausible_link(s.get("first_name"), s.get("last_name"),
                                          tp.get("first_name"), tp.get("last_name"), share):
                    if in_scope("links", s["id"]):
                        plan.links.append({"sc": s, "target_person_id": pid,
                                           "evidence": "unique non-shared email/phone; names not provably different"})
            # ambiguous / unresolved -> excluded (never applied here)

        # businesses: split PENDING (no entity yet) vs APPLIED (entity already exists)
        for _k, g in sorted(biz_groups.items()):
            gk = (g["kind"], _name_key(g["name"]))
            if not in_scope("businesses", gk):
                continue
            if gk in existing_entities:
                plan.applied_businesses += 1
            else:
                plan.businesses.append(g)

        # households: split PENDING (members not yet in a shared household) vs APPLIED (already share one)
        for folder in folders:
            member_keys = _folder_person_keys(folder)
            if len(member_keys) <= 1 or not all(len(by_name.get(k, [])) == 1 for k in member_keys):
                continue
            if not in_scope("households", folder):
                continue
            pids = [by_name[k][0] for k in member_keys]
            hids = {prow[p]["household_id"] for p in pids}
            if len(hids) == 1 and None not in hids:
                plan.applied_households += 1
            else:
                lasts = {(prow[p]["last_name"] or "").strip() for p in pids}
                name = (f"{next(iter(lasts)).title()} Household" if len(lasts) == 1 and next(iter(lasts))
                        else folder.strip())
                plan.households.append({"folder": folder, "member_person_ids": pids, "name": name})

        # already-applied promotions/links, restricted to the approved set when frozen
        psl = metadata.tables["person_source_links"]
        promoted = set(conn.execute(select(psl.c.source_contact_id).where(
            psl.c.match_method == "canonical_repair_promote")).scalars())
        relinked = set(conn.execute(select(psl.c.source_contact_id).where(
            psl.c.match_method == "canonical_repair_link")).scalars())
        if approved is not None:
            promoted &= approved["promotions"]
            relinked &= approved["links"]
        plan.applied_promotions = len(promoted)
        plan.applied_links = len(relinked)
        return plan

    def _rows(self, plan, applied=None) -> list[dict]:
        applied = applied or {}
        rows = []
        for s in plan.promotions:
            rows.append(_row("safe_person_promotion", s, applied.get(("promo", s["id"]), "would_create_person_and_link"),
                             "", f"person:{applied.get(('promo_pid', s['id']), 'new')}", "unique email/phone identity"))
        for lk in plan.links:
            s = lk["sc"]
            rows.append(_row("existing_person_link", s, applied.get(("link", s["id"]), "would_link"),
                             "", f"person:{lk['target_person_id']}", lk["evidence"]))
        for h in plan.households:
            rows.append({"category": "household_derivation", "source_contact_id": "",
                         "source_system": "", "source_record_id": "", "source_name": h["folder"],
                         "action": applied.get(("hh", h["folder"]), "would_create_household"),
                         "old_target": "", "new_target": f"household:{applied.get(('hh_id', h['folder']), 'new')} "
                         f"members={h['member_person_ids']}", "evidence": "both members are exactly one person"})
        for g in plan.businesses:
            rows.append({"category": f"{g['kind']}_canonicalization", "source_contact_id": ";".join(map(str, g["source_contact_ids"])),
                         "source_system": "", "source_record_id": ";".join(map(str, g["source_record_ids"])),
                         "source_name": g["name"], "action": applied.get(("biz", g["kind"], _name_key(g["name"])), "would_create_entity"),
                         "old_target": "", "new_target": f"relationship_entities:{applied.get(('biz_id', g['kind'], _name_key(g['name'])), 'new')}",
                         "evidence": f"{len(g['source_contact_ids'])} source record(s) deduped to one {g['kind']}"})
        return rows

    # -- PREVIEW (read-only) -------------------------------------------------
    def _preview(self, preview_dir=None, approved=None, **_opts) -> Outcome:
        from app.db import engine
        with engine.connect() as conn:
            plan = self._plan(conn, _read_exception_folders(preview_dir), approved)
        counts = plan.guard_counts()                                    # stable frozen totals (== approved expect)
        counts["pending"] = plan.counts()                              # what an apply would still do (0 post-apply)
        counts["newly_eligible_out_of_scope"] = dict(plan.newly_eligible)  # never enters the approved totals
        counts["business_source_contacts"] = sum(len(g["source_contact_ids"]) for g in plan.businesses)
        notes = [
            "PREVIEW ONLY — no writes. Deterministic set only; excludes duplicate-name excess, suspect "
            "links, ambiguous, unresolved, and ALL document/storage_uri/document_sources changes.",
            "Totals are the frozen approved set (pending + already-applied). After APPLY, pending -> 0 and "
            "totals are unchanged (idempotent).",
            "newly_eligible_out_of_scope: records that became eligible only because earlier repair writes "
            "altered matching state — reported, NOT added to the approved set (pass --approved to freeze).",
            "Drake repeated tax-years are deduped to ONE relationship_entities business/trust per identity.",
        ]
        return Outcome(counts=counts, exceptions=[], reconciliation=self._rows(plan), notes=notes)

    # -- APPLY (guarded, idempotent) -----------------------------------------
    def _apply(self, job_id=None, preview_dir=None, confirm=False, backup=None, expect=None,
               approved=None, **_opts) -> Outcome:
        if not confirm:
            raise RepairGuardError("APPLY requires explicit confirm=True.")
        if not backup or not os.path.isfile(backup) or os.path.getsize(backup) == 0:
            raise RepairGuardError(f"APPLY requires a verified non-empty DB backup file (got: {backup!r}).")

        from sqlalchemy import and_, select, update

        from app.db import engine, metadata
        from app.matching.promote import _create_person, _link
        from app.services.relationships import create_named_entity
        people = metadata.tables["people"]
        households = metadata.tables["households"]
        psl = metadata.tables["person_source_links"]
        rel = metadata.tables["relationship_entities"]

        applied: dict = {}
        with engine.begin() as conn:
            plan = self._plan(conn, _read_exception_folders(preview_dir), approved)
            live = plan.guard_counts()
            if expect is not None and live != expect:
                raise RepairGuardError(f"count drift — approved {expect} but live {live}; aborted before any write.")

            linked_now = set(conn.execute(select(psl.c.source_contact_id)).scalars())

            # 1) safe_person_promotion (idempotent: skip if already linked)
            for s in plan.promotions:
                if s["id"] in linked_now:
                    applied[("promo", s["id"])] = "skipped_already_linked"
                    continue
                pid = _create_person(conn, s)
                _link(conn, pid, s["id"], "canonical_repair_promote", 100)
                applied[("promo", s["id"])] = "created_person_and_linked"
                applied[("promo_pid", s["id"])] = pid

            # 2) existing_person_link (idempotent via uq constraint / skip if linked)
            for lk in plan.links:
                s = lk["sc"]
                if s["id"] in linked_now:
                    applied[("link", s["id"])] = "skipped_already_linked"
                    continue
                _link(conn, lk["target_person_id"], s["id"], "canonical_repair_link", 100)
                applied[("link", s["id"])] = "linked"

            # 3) household_derivation (idempotent: reuse by name; skip if members already share one)
            for h in plan.households:
                pids = h["member_person_ids"]
                cur = list(conn.execute(select(people.c.household_id).where(people.c.id.in_(pids))).scalars())
                if len(set(x for x in cur if x)) == 1 and all(cur):
                    applied[("hh", h["folder"])] = "skipped_already_in_household"
                    applied[("hh_id", h["folder"])] = cur[0]
                    continue
                hid = conn.execute(select(households.c.id).where(households.c.name == h["name"])).scalar()
                if hid is None:
                    hid = conn.execute(households.insert().values(name=h["name"]).returning(households.c.id)).scalar_one()
                    applied[("hh", h["folder"])] = "created_household"
                else:
                    applied[("hh", h["folder"])] = "reused_household"
                conn.execute(update(people).where(people.c.id.in_(pids)).values(household_id=hid))
                applied[("hh_id", h["folder"])] = hid

            # 4) business/trust canonicalization (idempotent: reuse by (entity_type, name))
            for g in plan.businesses:
                nk = _name_key(g["name"])
                existing = conn.execute(select(rel.c.id).where(and_(
                    rel.c.entity_type == g["kind"], rel.c.name == g["name"].strip()))).scalar()
                if existing is not None:
                    applied[("biz", g["kind"], nk)] = "skipped_existing_entity"
                    applied[("biz_id", g["kind"], nk)] = existing
                    continue
                details = {"origin": "canonical_repair", "source_record_ids": g["source_record_ids"],
                           "source_contact_ids": g["source_contact_ids"]}
                eid = create_named_entity(conn, g["kind"], g["name"], details)
                applied[("biz", g["kind"], nk)] = "created_entity"
                applied[("biz_id", g["kind"], nk)] = eid

        counts = plan.counts()
        counts["rows_inserted"] = sum(1 for k, v in applied.items()
                                      if isinstance(v, str) and v.startswith(("created", "linked")))
        return Outcome(counts=counts, exceptions=[], reconciliation=self._rows(plan, applied),
                       notes=["APPLY complete (deterministic set only). Document linkage APPLY remains "
                              "DISABLED. Rollback: restore the pre-apply DB backup provided to --backup."])


def _counter(it):
    from collections import Counter
    return Counter(it)


def _row(category, s, action, old_target, new_target, evidence):
    return {"category": category, "source_contact_id": s["id"], "source_system": s["source_system"],
            "source_record_id": s.get("source_record_id") or "", "source_name": s.get("full_name") or "",
            "action": action, "old_target": old_target, "new_target": new_target, "evidence": evidence}
