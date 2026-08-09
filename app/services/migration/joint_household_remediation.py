"""Stage A guarded remediation for the Drake joint-return / household gap — people + households only.

Stage A makes the CANONICAL PEOPLE and HOUSEHOLDS correct for deterministically established joint couples.
It does NOT touch documents, storage_uri, document_sources, or files — joint-document re-ownership is a
separate later stage.

  A1 — household-only:    scope = both_canonical_safe_household couples. Create (or assign to an existing)
                          household and set both canonical people's household_id.
  A2 — promote + household: scope = one_canonical_plus_promotable couples. Promote the missing spouse
                          SOLELY from the stable Drake identifier/provenance (create the person, set
                          drake_identity.primary_person_id, link the Drake source_contacts), then form the
                          household. Atomic per couple.

Reuses existing primitives only (no new framework): the couple classification from
``scripts.migration.plan_joint_household_remediation`` (stable Drake hashes, never names), the person
promotion primitive ``app.matching.promote._create_person`` + the Drake identity-review link mechanism
(``drake_identity.primary_person_id`` + ``person_source_links``), and the canonical-repair household
derivation pattern (``households`` insert + ``people.household_id`` update).

Guards: APPLY requires an explicit confirm flag, a verified non-empty DB backup, and the approved expected
bucket count — it recomputes the plan live and FAILS CLOSED before any write on count drift. Every couple
is applied in its own transaction (no half-remediated couple), is idempotent (re-runs skip completed
couples), and FAILS CLOSED per couple on conflicting person/household/identifier state.
"""
from __future__ import annotations

import os

from sqlalchemy import and_, select, text, update

from scripts.migration.plan_joint_household_remediation import _load as _planner_load
from scripts.migration.plan_joint_household_remediation import classify_couple

_DRAKE_LINK_METHOD = "drake_identity_promotion"


class RemediationGuardError(RuntimeError):
    """APPLY aborted by a guard BEFORE any write (missing confirm/backup, or count drift)."""


# --------------------------------------------------------------------------- plan (read-only)

def _tables():
    from app.db import metadata
    return (metadata.tables["people"], metadata.tables["households"],
            metadata.tables["person_source_links"], metadata.tables["source_contacts"],
            metadata.tables.get("drake_identity"))


def _household_name(prow, person_ids):
    lasts = {((prow.get(p) or {}).get("last_name") or "").strip() for p in person_ids if p}
    lasts = {x for x in lasts if x}
    if len(lasts) == 1:
        return f"{next(iter(lasts)).title()} Household"
    if lasts:
        return " / ".join(sorted(x.title() for x in lasts)) + " Household"
    return f"Household {sorted(pid for pid in person_ids if pid)}"


def _household_action(hh_a, hh_b):
    """Deterministic, fail-closed household action from the two members' current household_id."""
    if hh_a and hh_b:
        return ("skip_already_shared", hh_a) if hh_a == hh_b else ("conflict_different_households", None)
    if hh_a or hh_b:
        return "assign_existing", (hh_a or hh_b)
    return "create_household", None


def _a1_plan(detail, prow):
    tp, sp = detail["taxpayer_person_id"], detail["spouse_person_id"]
    hh_tp = (prow.get(tp) or {}).get("household_id")
    hh_sp = (prow.get(sp) or {}).get("household_id")
    action, hid = _household_action(hh_tp, hh_sp)
    return {"stage": "A1", "person_ids": [tp, sp], "household_action": action, "existing_household_id": hid,
            "taxpayer_person_id": tp, "spouse_person_id": sp}


def _a2_plan(detail, id_index, prow):
    # exactly one canonical person + one promotable hash
    if detail["taxpayer_status"] == "canonical":
        canon_person, canon_hash = detail["taxpayer_person_id"], detail["taxpayer_hash"]
        promo_hash = detail["spouse_hash"]
    else:
        canon_person, canon_hash = detail["spouse_person_id"], detail["spouse_hash"]
        promo_hash = detail["taxpayer_hash"]
    canon_hh = (prow.get(canon_person) or {}).get("household_id")
    promote_name = (id_index.get(promo_hash) or {}).get("taxpayer_name") or ""
    return {"stage": "A2", "canonical_person_id": canon_person, "canonical_hash": canon_hash,
            "promotable_hash": promo_hash, "promote_name": promote_name,
            "canonical_household_id": canon_hh,
            "household_action": ("assign_existing" if canon_hh else "create_household")}


def _build(engine):
    joint, id_index, cand_index, prow, _docs = _planner_load(engine)
    couples: dict = {}
    for r in joint:
        tp, sp = r["taxpayer_identifier_hash"], r["spouse_identifier_hash"]
        if not (tp and sp):
            continue
        couples.setdefault(frozenset((tp, sp)), {"tp": tp, "sp": sp})
    a1, a2 = [], []
    for c in couples.values():
        bucket, detail = classify_couple(c["tp"], c["sp"], id_index, cand_index, prow)
        if bucket == "both_canonical_safe_household":
            a1.append(_a1_plan(detail, prow))
        elif bucket == "one_canonical_plus_promotable":
            a2.append(_a2_plan(detail, id_index, prow))
    return a1, a2, prow


def preview_stage_a(engine=None) -> dict:
    if engine is None:
        from app.db import engine as _engine
        engine = _engine
    a1, a2, _prow = _build(engine)

    def tally(plans):
        from collections import Counter
        return dict(Counter(p["household_action"] for p in plans))

    return {
        "A1": {"couples": len(a1), "actions": tally(a1),
               "actionable": sum(1 for p in a1 if p["household_action"] in
                                 ("create_household", "assign_existing")),
               "conflicts": sum(1 for p in a1 if p["household_action"].startswith("conflict")),
               "already_done": sum(1 for p in a1 if p["household_action"] == "skip_already_shared")},
        "A2": {"couples": len(a2), "actions": tally(a2)},
        "plans_A1": a1, "plans_A2": a2,
    }


# --------------------------------------------------------------------------- apply (guarded)

def _guard(confirm, backup):
    if not confirm:
        raise RemediationGuardError("APPLY requires explicit confirm=True.")
    if not backup or not os.path.isfile(backup) or os.path.getsize(backup) == 0:
        raise RemediationGuardError(f"APPLY requires a verified non-empty DB backup file (got {backup!r}).")


def _assign_household(conn, people, households, prow, person_ids, existing_hid):
    """Create-or-assign a household and set both people's household_id (idempotent). Returns household id."""
    hid = existing_hid
    created = False
    if hid is None:
        name = _household_name(prow, person_ids)
        hid = conn.execute(select(households.c.id).where(households.c.name == name)).scalar()
        if hid is None:
            hid = conn.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
            created = True
    conn.execute(update(people).where(and_(people.c.id.in_([p for p in person_ids if p]),
                                           people.c.household_id.is_(None))).values(household_id=hid))
    return hid, created


def _promote_from_drake(conn, promo_hash, promote_name):
    """Promote a Drake identity to a NEW canonical person using stable provenance only, then link it:
    create the person, set drake_identity.primary_person_id, and link the Drake source_contacts."""
    from app.matching.promote import _create_person
    people, _hh, psl, source_contacts, drake_identity = _tables()
    first, last = (promote_name.split(" ", 1) + [""])[:2]
    record = {"first_name": first or None, "middle_name": None, "last_name": last or None,
              "full_name": promote_name, "email": None, "normalized_email": None, "phone": None,
              "normalized_phone": None, "address_line_1": None, "address_line_2": None,
              "city": None, "state": None, "postal_code": None}
    pid = _create_person(conn, record)
    if drake_identity is not None:
        conn.execute(update(drake_identity).where(drake_identity.c.identifier_hash == promo_hash)
                     .values(primary_person_id=pid, confidence=100))
    # link the Drake source_contacts carrying this identifier hash (same mechanism as the Drake review)
    conn.execute(text(
        "INSERT INTO person_source_links (person_id, source_contact_id, match_method, match_score, confirmed) "
        "SELECT :pid, sc.id, :m, 100, TRUE FROM source_contacts sc "
        "WHERE sc.source_system = 'Drake' AND sc.raw_data->>'identifier_hash' = :h "
        "ON CONFLICT ON CONSTRAINT uq_person_source_link DO NOTHING"),
        {"pid": pid, "m": _DRAKE_LINK_METHOD, "h": promo_hash})
    return pid


def apply_stage_a(stage, *, confirm=False, backup=None, expect=None, engine=None) -> dict:
    """Guarded APPLY of A1 or A2. Recomputes the plan live, fails closed on count drift, applies each
    couple atomically and idempotently, and fails closed per couple on conflicting state."""
    _guard(confirm, backup)
    if engine is None:
        from app.db import engine as _engine
        engine = _engine
    people, households, _psl, _sc, drake_identity = _tables()

    a1, a2, prow = _build(engine)
    plans = a1 if stage == "A1" else a2 if stage == "A2" else None
    if plans is None:
        raise RemediationGuardError(f"unknown stage {stage!r}; expected 'A1' or 'A2'.")
    if expect is not None and len(plans) != expect:
        raise RemediationGuardError(f"count drift — approved {expect} but live {len(plans)} {stage} couples; "
                                    "aborted before any write.")

    result = {"stage": stage, "couples": len(plans), "created_households": 0, "assigned_households": 0,
              "promoted_people": 0, "skipped": 0, "conflicts": [], "household_ids": []}

    for plan in plans:
        try:
            with engine.begin() as conn:
                if stage == "A1":
                    _apply_a1(conn, plan, people, households, prow, result)
                else:
                    _apply_a2(conn, plan, people, households, drake_identity, prow, result)
        except RemediationGuardError as exc:
            result["conflicts"].append(str(exc))
    return result


def _apply_a1(conn, plan, people, households, prow, result):
    pids = [p for p in plan["person_ids"] if p]
    cur = {pid: conn.execute(select(people.c.household_id).where(people.c.id == pid)).scalar()
           for pid in pids}
    action, hid = _household_action(*[cur.get(p) for p in pids])
    if action == "skip_already_shared":
        result["skipped"] += 1
        return
    if action.startswith("conflict"):
        raise RemediationGuardError(f"A1 couple {pids}: members already in different households {cur}.")
    hid, created = _assign_household(conn, people, households, prow, pids, hid)
    result["created_households" if created else "assigned_households"] += 1
    result["household_ids"].append(hid)


def _apply_a2(conn, plan, people, households, drake_identity, prow, result):
    canon = plan["canonical_person_id"]
    promo_hash = plan["promotable_hash"]
    # live re-check of the promotable identity (fail closed on drift)
    ident = None
    if drake_identity is not None:
        ident = conn.execute(select(drake_identity.c.primary_person_id).where(
            drake_identity.c.identifier_hash == promo_hash)).first()
    cands = conn.execute(text("SELECT count(*) FROM drake_identity_match_candidates "
                              "WHERE identifier_hash = :h AND status IN ('pending','deferred')"),
                         {"h": promo_hash}).scalar() if _regclass(conn, "drake_identity_match_candidates") else 0
    if cands:
        raise RemediationGuardError(f"A2 {promo_hash}: existing-person match candidates appeared; hold.")
    promoted = ident[0] if (ident and ident[0]) else None
    if promoted is None:
        if not (plan["promote_name"] or "").strip():
            raise RemediationGuardError(f"A2 {promo_hash}: no Drake name provenance to promote.")
        promoted = _promote_from_drake(conn, promo_hash, plan["promote_name"])
        result["promoted_people"] += 1
    # household for {canonical, promoted}, fail closed if already in different households
    hh_c = conn.execute(select(people.c.household_id).where(people.c.id == canon)).scalar()
    hh_p = conn.execute(select(people.c.household_id).where(people.c.id == promoted)).scalar()
    action, hid = _household_action(hh_c, hh_p)
    if action == "skip_already_shared":
        result["skipped"] += 1
        return
    if action.startswith("conflict"):
        raise RemediationGuardError(f"A2 couple ({canon},{promoted}): already in different households.")
    hid, created = _assign_household(conn, people, households, prow, [canon, promoted], hid)
    result["created_households" if created else "assigned_households"] += 1
    result["household_ids"].append(hid)


def _regclass(conn, name):
    return conn.execute(text("SELECT to_regclass(:n)"), {"n": f"public.{name}"}).scalar() is not None


# --------------------------------------------------------------------------- post-apply verification

def verify_stage_a(engine=None) -> dict:
    """Recompute the plan; after APPLY the target bucket's actionable count should be 0 (all couples now
    share a household / the spouse is canonical)."""
    prev = preview_stage_a(engine)
    return {"A1_remaining_actionable": prev["A1"]["actionable"] + prev["A1"]["conflicts"],
            "A1_couples_left_in_bucket": prev["A1"]["couples"],
            "A2_couples_left_in_bucket": prev["A2"]["couples"],
            "A1_conflicts": prev["A1"]["conflicts"]}
