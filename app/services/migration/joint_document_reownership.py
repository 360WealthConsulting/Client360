"""Stage B — guarded re-ownership of proven joint personal-return documents to a canonical household.

Scope is deliberately narrow and deterministic: a document is re-ownable ONLY when it is a proven JOINT
PERSONAL return document owned by a person who is a member of an already-correct canonical household. It is
NOT sufficient that a document is ``category='tax_document'`` — business / entity returns and signature
packets (e.g. an LLC 1120/1065/1041) are excluded even when they are tax documents in a person's folder.

Deterministic joint-personal-return proof (ALL required):
  * the owner is a person whose ``household_id`` is set (the proposed household), and that household has
    >= 2 members (the established couple);
  * NOT a business/entity return — no corporate suffix in the filename (LLC/INC/CORP/LP/LLP/PLLC/TRUST/
    ESTATE/...) and no business return type (1120/1120S/1065/1041/990) in tags;
  * IS a personal return — a federal/state return doc (Drake ``drake_doc_type``) or a 1040 / "tax return"
    filename;
  * JOINT SIGNATURE — the filename explicitly names BOTH canonical household members (first + last);
  * the document's tax year (Drake ``tags.tax_year`` or a 20xx in the filename) is a year the household's
    couple actually filed jointly (MFJ) per the Drake returns.

Re-ownership changes DOCUMENT OWNERSHIP ONLY (person -> household). It never changes storage_uri or
document_sources and never moves a file — physical relocation is a later, separate guarded step. The APPLY
primitive fails closed: the current person must belong to the proposed household; the document's
household_id and organization_id must still be NULL; the current owner must be unchanged; the exact
provable count must be pinned via ``--expect``; and it is idempotent.
"""
from __future__ import annotations

import os
import re

from sqlalchemy import and_, select, text, update

from scripts.migration.plan_joint_household_remediation import doc_year

_BUSINESS_SUFFIX = re.compile(
    r"\b(l\.?l\.?c|inc|incorporated|corp|corporation|co|company|l\.?p|l\.?l\.?p|p\.?l\.?l\.?c|p\.?c|"
    r"trust|estate|foundation|association|assoc|partners|holdings|enterprises|ministries|church)\b",
    re.IGNORECASE)
_BUSINESS_RETURN_TYPES = {"1120", "1120s", "1065", "1041", "990"}
_PERSONAL_RETURN_TYPES = {"federal_return", "state_return"}


class ReownershipGuardError(RuntimeError):
    """APPLY aborted by a guard BEFORE any write (missing confirm/backup, or count drift)."""


# --------------------------------------------------------------------------- pure classification

def is_business_document(doc) -> bool:
    name = doc.get("original_name") or ""
    if _BUSINESS_SUFFIX.search(name):
        return True
    tags = doc.get("tags") if isinstance(doc.get("tags"), dict) else {}
    rt = str(tags.get("return_type") or tags.get("drake_return_type") or "").strip().lower()
    return rt in _BUSINESS_RETURN_TYPES


def is_personal_return(doc) -> bool:
    if is_business_document(doc):
        return False
    tags = doc.get("tags") if isinstance(doc.get("tags"), dict) else {}
    if tags.get("drake_doc_type") in _PERSONAL_RETURN_TYPES:
        return True
    name = (doc.get("original_name") or "").lower()
    return "1040" in name or "tax return" in name


def joint_signature(name, members) -> bool:
    """True when the filename explicitly names BOTH canonical household members (first + last)."""
    low = (name or "").lower()
    matched = 0
    for m in members:
        first = (m.get("first_name") or "").strip().lower()
        last = (m.get("last_name") or "").strip().lower()
        if first and last and first in low and last in low:
            matched += 1
    return matched >= 2


def classify_document(doc, person, members, mfj_years):
    """Return (verdict, reason). verdict='reownable' or an exclusion reason string."""
    if not person or person.get("household_id") is None:
        return "excluded", "owner_not_in_household"
    if len(members) < 2:
        return "excluded", "household_not_established"
    if is_business_document(doc):
        return "excluded", "business_entity_return"
    if not is_personal_return(doc):
        return "excluded", "not_a_personal_return"
    if not joint_signature(doc.get("original_name"), members):
        return "excluded", "no_joint_signature"
    y = doc_year(doc)
    if y is None or y not in mfj_years:
        return "excluded", "year_not_a_joint_filing"
    return "reownable", "proven_joint_personal_return"


# --------------------------------------------------------------------------- DB layer (read-only load)

def _load(engine):
    from scripts.migration.plan_joint_household_remediation import _load as _planner_load
    joint, id_index, _cand, _prow, _docs = _planner_load(engine)
    with engine.connect() as conn:
        conn.execute(text("SET default_transaction_read_only = on"))
        people = {m["id"]: dict(m) for m in conn.execute(text(
            "SELECT id, household_id, first_name, last_name, full_name FROM people")).mappings()}
        households = {m["id"]: m["name"] for m in conn.execute(text(
            "SELECT id, name FROM households")).mappings()}
        docs = [dict(m) for m in conn.execute(text(
            "SELECT id, person_id, household_id, organization_id, original_name, category, classification, "
            "effective_date, storage_uri, tags FROM documents "
            "WHERE status <> 'deleted' AND person_id IS NOT NULL "
            "AND household_id IS NULL AND organization_id IS NULL")).mappings()]

    members_by_hh: dict = {}
    for p in people.values():
        if p.get("household_id"):
            members_by_hh.setdefault(p["household_id"], []).append(p)
    # person -> canonical Drake hashes
    person_hashes: dict = {}
    for h, ident in id_index.items():
        pid = ident.get("primary_person_id")
        if pid:
            person_hashes.setdefault(pid, set()).add(h)
    # household -> MFJ years (both couple hashes resolve to members of the household)
    mfj_years_by_hh: dict = {}
    for hh, members in members_by_hh.items():
        hh_hashes = set()
        for m in members:
            hh_hashes |= person_hashes.get(m["id"], set())
        years = set()
        for r in joint:
            tp, sp = r["taxpayer_identifier_hash"], r["spouse_identifier_hash"]
            if tp and sp and tp in hh_hashes and sp in hh_hashes and r.get("tax_year"):
                years.add(int(r["tax_year"]))
        mfj_years_by_hh[hh] = years
    return people, households, members_by_hh, mfj_years_by_hh, docs


# --------------------------------------------------------------------------- preview (read-only)

def _proposed_destination(doc, hh_id, households):
    from app.services.migration.config import MigrationConfig
    from app.services.migration.naming import RepositoryNaming
    projected = dict(doc)
    projected["household_id"] = hh_id
    projected["person_id"] = None                 # re-owned to the household
    placed = RepositoryNaming().plan(projected, households={hh_id: households.get(hh_id)})
    return placed.full(MigrationConfig.from_env().migration_dest_root)


def preview(engine=None) -> dict:
    if engine is None:
        from app.db import engine as _engine
        engine = _engine
    people, households, members_by_hh, mfj_years_by_hh, docs = _load(engine)

    reownable, exclusions = [], []
    from collections import Counter
    excl_reasons: Counter = Counter()
    for d in docs:
        person = people.get(d["person_id"])
        hh = (person or {}).get("household_id")
        members = members_by_hh.get(hh, [])
        verdict, reason = classify_document(d, person, members, mfj_years_by_hh.get(hh, set()))
        if verdict == "reownable":
            dest = _proposed_destination(d, hh, households)
            reownable.append({
                "document_id": d["id"], "current_person_id": d["person_id"], "proposed_household_id": hh,
                "evidence": (f"joint personal return: names both household #{hh} members, tax year "
                             f"{doc_year(d)} is a Drake MFJ filing year for the couple; not a business return"),
                "current_storage_uri": d.get("storage_uri"), "proposed_destination": dest,
                "relocation_required": _norm(d.get("storage_uri")) != _norm(dest),
                "original_name": d.get("original_name")})
        else:
            excl_reasons[reason] += 1
            exclusions.append({"document_id": d["id"], "person_id": d["person_id"],
                               "reason": reason, "original_name": d.get("original_name")})
    return {"candidates": len(docs), "reownable": len(reownable),
            "reownable_rows": reownable, "excluded": len(exclusions),
            "exclusions_by_reason": dict(excl_reasons), "exclusion_rows": exclusions}


def _norm(path):
    return os.path.normcase(os.path.normpath(path or ""))


# --------------------------------------------------------------------------- apply (guarded)

def apply(*, confirm=False, backup=None, expect=None, engine=None) -> dict:
    """Guarded APPLY: re-own each proven joint personal-return document to its household (ownership only).

    Fail-closed: requires confirm + verified backup; recomputes the provable set and aborts on count drift
    vs ``expect`` before any write; per document requires the owner to belong to the proposed household,
    household_id/organization_id still NULL, and the current owner unchanged. Idempotent. NEVER changes
    storage_uri/document_sources and NEVER moves a file."""
    if not confirm:
        raise ReownershipGuardError("APPLY requires explicit confirm=True.")
    if not backup or not os.path.isfile(backup) or os.path.getsize(backup) == 0:
        raise ReownershipGuardError(f"APPLY requires a verified non-empty DB backup file (got {backup!r}).")
    if engine is None:
        from app.db import engine as _engine
        engine = _engine

    prev = preview(engine)
    if expect is not None and prev["reownable"] != expect:
        raise ReownershipGuardError(
            f"count drift — approved {expect} but live {prev['reownable']} re-ownable documents; "
            "aborted before any write.")

    from app.db import documents
    from app.db import people as people_tbl
    result = {"reownable": prev["reownable"], "reowned": 0, "already_applied": 0,
              "skipped_conflicts": [], "household_ids": set()}
    with engine.begin() as conn:
        for row in prev["reownable_rows"]:
            did, exp_person, prop_hh = (row["document_id"], row["current_person_id"],
                                        row["proposed_household_id"])
            d = conn.execute(select(documents.c.person_id, documents.c.household_id,
                                    documents.c.organization_id).where(documents.c.id == did)).mappings().first()
            if d is None:
                result["skipped_conflicts"].append((did, "missing_document"))
                continue
            if d["household_id"] == prop_hh and d["person_id"] is None:
                result["already_applied"] += 1
                continue
            if d["organization_id"] is not None or d["household_id"] is not None:
                result["skipped_conflicts"].append((did, "already_owned"))
                continue
            if d["person_id"] != exp_person:
                result["skipped_conflicts"].append((did, "owner_changed"))
                continue
            owner_hh = conn.execute(select(people_tbl.c.household_id).where(
                people_tbl.c.id == exp_person)).scalar()
            if owner_hh != prop_hh:
                result["skipped_conflicts"].append((did, "owner_not_in_proposed_household"))
                continue
            conn.execute(update(documents).where(and_(
                documents.c.id == did, documents.c.person_id == exp_person,
                documents.c.household_id.is_(None), documents.c.organization_id.is_(None))
            ).values(household_id=prop_hh, person_id=None))
            result["reowned"] += 1
            result["household_ids"].add(prop_hh)
    result["household_ids"] = sorted(result["household_ids"])
    return result
