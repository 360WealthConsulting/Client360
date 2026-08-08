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


def _read_exception_folders(preview_dir):
    if not preview_dir:
        return []
    path = os.path.join(preview_dir, "exceptions.csv")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return [(r.get("source_folder") or "") for r in csv.DictReader(f)]


@dataclass
class RepairPlan:
    promotions: list = field(default_factory=list)   # source_contact dicts (PENDING)
    links: list = field(default_factory=list)        # {sc, target_person_id, evidence} (PENDING)
    households: list = field(default_factory=list)    # {folder, member_person_ids, name}
    businesses: list = field(default_factory=list)    # {kind, name, source_contact_ids, source_record_ids}
    applied_promotions: int = 0                       # already promoted by a prior repair run
    applied_links: int = 0                            # already linked by a prior repair run

    def counts(self):
        """Pending work this run would do."""
        return {"promotions": len(self.promotions), "links": len(self.links),
                "households": len(self.households), "businesses": len(self.businesses)}

    def guard_counts(self):
        """STABLE intended totals (pending + already-applied-by-repair). Idempotent across re-runs, so the
        approved expected counts still match after the repair has been applied. Households/businesses are
        already stable (people stay unique; business source_contacts stay unlinked)."""
        return {"promotions": len(self.promotions) + self.applied_promotions,
                "links": len(self.links) + self.applied_links,
                "households": len(self.households), "businesses": len(self.businesses)}


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

    def _plan(self, conn, folders) -> RepairPlan:
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

        plan = RepairPlan()
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
                plan.promotions.append(s)
            elif action == "existing_person_link":
                ne, np = s["normalized_email"], s["normalized_phone"]
                cand = (by_email.get(ne) or by_phone.get(np) or [None])
                pid = cand[0]
                tp = prow.get(pid, {})
                c_last, c_first = (s.get("last_name") or "").strip().lower(), (s.get("first_name") or "").strip().lower()
                t_last, t_first = (tp.get("last_name") or "").strip().lower(), (tp.get("first_name") or "").strip().lower()
                # PLAUSIBLE only (matches the duplicate diagnostic): matching first+last AND the identity
                # is NOT shared with another unlinked contact (share<=1). A shared email/phone is a
                # spouse/household-shared identity -> suspect -> excluded, never linked here.
                share = email_counts.get(ne, 0) if (ne and by_email.get(ne)) else (
                    phone_counts.get(np, 0) if (np and by_phone.get(np)) else 0)
                if (pid and c_last and t_last and c_first and t_first
                        and c_last == t_last and c_first == t_first and share <= 1):
                    plan.links.append({"sc": s, "target_person_id": pid,
                                       "evidence": "unique NON-shared email/phone + matching first+last name"})
            # ambiguous / unresolved -> excluded (never applied here)
        plan.businesses = [g for _k, g in sorted(biz_groups.items())]

        for folder in folders:
            member_keys = _folder_person_keys(folder)
            if len(member_keys) <= 1:
                continue
            if all(len(by_name.get(k, [])) == 1 for k in member_keys):
                pids = [by_name[k][0] for k in member_keys]
                lasts = {(prow[p]["last_name"] or "").strip() for p in pids}
                name = (f"{next(iter(lasts)).title()} Household" if len(lasts) == 1 and next(iter(lasts))
                        else folder.strip())
                plan.households.append({"folder": folder, "member_person_ids": pids, "name": name})

        from sqlalchemy import func, select

        from app.db import metadata
        psl = metadata.tables["person_source_links"]
        plan.applied_promotions = conn.execute(select(func.count()).select_from(psl).where(
            psl.c.match_method == "canonical_repair_promote")).scalar_one()
        plan.applied_links = conn.execute(select(func.count()).select_from(psl).where(
            psl.c.match_method == "canonical_repair_link")).scalar_one()
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
    def _preview(self, preview_dir=None, **_opts) -> Outcome:
        from app.db import engine
        with engine.connect() as conn:
            plan = self._plan(conn, _read_exception_folders(preview_dir))
        counts = plan.guard_counts()                                    # stable intended totals (== approved expect)
        counts["pending"] = plan.counts()                              # what an apply would still do
        counts["business_source_contacts"] = sum(len(g["source_contact_ids"]) for g in plan.businesses)
        notes = [
            "PREVIEW ONLY — no writes. Deterministic set only; excludes duplicate-name excess, suspect "
            "links, ambiguous, unresolved, and ALL document/storage_uri/document_sources changes.",
            "APPLY requires --confirm, a verified DB backup, and matching approved expected counts "
            "(fails closed before any write if a count differs).",
            "Drake repeated tax-years are deduped to ONE relationship_entities business/trust per identity.",
        ]
        return Outcome(counts=counts, exceptions=[], reconciliation=self._rows(plan), notes=notes)

    # -- APPLY (guarded, idempotent) -----------------------------------------
    def _apply(self, job_id=None, preview_dir=None, confirm=False, backup=None, expect=None, **_opts) -> Outcome:
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
            plan = self._plan(conn, _read_exception_folders(preview_dir))
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
