"""Strict-safe ownership selection — the deterministic plan the guarded batch applies.

WHAT "STRICT-SAFE" MEANS
-----------------------
A HIGH owner proposal alone is not enough to assign ownership without a human. The production audit
that preceded this batch found HIGH is bimodal: 40% of all HIGH proposals name one person, driven by
a shared phone number and a shared email that appear in thousands of unrelated documents, and 46% of
HIGH rows carry no name-based evidence at all. Assigning HIGH wholesale would mis-file roughly two
thousand documents onto a single person.

So this module selects the sub-population where that failure mode cannot occur:

    route == HIGH
    entity_type == person, with an entity_id that still resolves to a live person
    the document's own text names the person EXACTLY, and
    at least TWO independent corroborators among matched email, matched phone, matched address/ZIP

The exact-name requirement is what excludes the mass-match cluster (its evidence is phone/email only,
never a name). The two-corroborator requirement is what stops a single shared contact detail from
being sufficient on its own. Neither is a heuristic tuned to a number — each removes one specific,
observed way the proposal engine goes wrong.

WHY THE SELECTION LIVES HERE AND NOT IN THE SCRIPT
--------------------------------------------------
The apply script must be able to recompute the plan LIVE at execution time and compare it to the
reviewed manifest, so the rule has to exist as callable code rather than as prose in a runbook. The
same function backs the tests. The script contributes transaction policy and gates; it contributes no
ownership rules, and it never decides who owns a document.

THE PLAN DIGEST
---------------
:func:`plan_digest` is a content hash over the whole plan, and it is deliberately sensitive: the
person, the corroborator flags and the evidence lines all feed it. A corpus that has moved — a
re-proposal, a changed email, a person renamed — changes the digest and the apply refuses. That is
the point: the approval is of a specific plan, not of a rule that might select something else today.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import text

from app.db import engine
from app.services.document_owner_proposal import PERMANENT_REJECT_DOCUMENT_IDS

#: Batch identity. Encoded into the confirmation phrases so a phrase cannot be reused elsewhere.
BATCH_ID = "STRICT-SAFE-OWNERSHIP-1"

#: The minimum number of independent corroborators. Two, because one shared contact detail is
#: exactly how the mass-match cluster forms.
MIN_CORROBORATORS = 2

#: The evidence prefixes the proposal engine writes. Matching on the engine's own strings keeps this
#: module honest: if the engine changes its vocabulary the plan changes and the digest catches it.
_EXACT_NAME_PREFIX = "✓ exact name"
_EMAIL_PREFIX = "✓ email"
_PHONE_PREFIX = "✓ phone"
_ADDRESS_PREFIX = "✓ address/ZIP matched"

#: The fields that make up a plan row, in the order a reader would want them.
PLAN_FIELDS = ("document_id", "original_name", "person_id", "person_name", "route", "confidence",
               "corroborator_count", "email_match", "phone_match", "address_match", "evidence")


def corroborators(evidence) -> dict:
    """The three independent corroborator flags, derived from the engine's own evidence lines."""
    lines = [str(e) for e in (evidence or [])]
    return {
        "email_match": any(e.startswith(_EMAIL_PREFIX) and "matched" in e for e in lines),
        "phone_match": any(e.startswith(_PHONE_PREFIX) and "matched" in e for e in lines),
        "address_match": any(e.startswith(_ADDRESS_PREFIX) for e in lines),
    }


def has_exact_name_evidence(evidence) -> bool:
    """Does the document's own text name the proposed person exactly?"""
    return any(str(e).startswith(_EXACT_NAME_PREFIX) for e in (evidence or []))


def is_strict_safe(fact: dict, evidence) -> bool:
    """The whole selection rule, as one predicate over a current owner_proposal fact."""
    if (fact.get("route") or "") != "HIGH":
        return False
    if (fact.get("entity_type") or "") != "person":
        return False
    if fact.get("entity_id") in (None, ""):
        return False
    if not has_exact_name_evidence(evidence):
        return False
    return sum(corroborators(evidence).values()) >= MIN_CORROBORATORS


_PLAN_SQL = """
    SELECT d.id                                          AS document_id,
           coalesce(d.original_name, '')                 AS original_name,
           f.fact_value::jsonb ->> 'entity_type'         AS entity_type,
           f.fact_value::jsonb ->> 'entity_id'           AS entity_id,
           f.fact_value::jsonb ->> 'entity_name'         AS entity_name,
           f.fact_value::jsonb ->> 'route'               AS route,
           f.fact_value::jsonb ->> 'confidence'          AS confidence,
           f.fact_value::jsonb ->  'evidence'            AS evidence
      FROM documents d
      JOIN document_facts f
        ON f.document_id = d.id AND f.fact_type = 'owner_proposal' AND f.is_current
     WHERE d.person_id IS NULL AND d.household_id IS NULL AND d.organization_id IS NULL
       AND d.status <> 'deleted' AND d.deleted_at IS NULL AND d.archived = false
       AND coalesce(d.review_status, '') = 'not_required'
       AND NOT (d.id = ANY(:rejects))
       AND f.fact_value::jsonb ->> 'route' = 'HIGH'
       AND f.fact_value::jsonb ->> 'entity_type' = 'person'
       AND f.fact_value::jsonb ->> 'entity_id' IS NOT NULL
       AND EXISTS (SELECT 1 FROM people p
                    WHERE p.id = (f.fact_value::jsonb ->> 'entity_id')::int)
     ORDER BY d.id
"""


def build_plan(conn=None) -> list[dict]:
    """The strict-safe plan as it stands RIGHT NOW, deterministically ordered by document id.

    Read-only. The SQL narrows to what can be expressed in SQL (unowned, live, not a reject, HIGH,
    person, a person that still exists); the evidence rules are applied in Python because they read
    the engine's prose.
    """
    def _run(c):
        rows = c.execute(text(_PLAN_SQL),
                         {"rejects": sorted(PERMANENT_REJECT_DOCUMENT_IDS)}).mappings().all()
        plan = []
        for r in rows:
            evidence = list(r["evidence"] or [])
            fact = {"route": r["route"], "entity_type": r["entity_type"], "entity_id": r["entity_id"]}
            if not is_strict_safe(fact, evidence):
                continue
            flags = corroborators(evidence)
            plan.append({
                "document_id": int(r["document_id"]),
                "original_name": r["original_name"],
                "person_id": int(r["entity_id"]),
                "person_name": r["entity_name"],
                "route": r["route"],
                "confidence": r["confidence"],
                "corroborator_count": sum(flags.values()),
                **flags,
                "evidence": evidence,
            })
        return sorted(plan, key=lambda x: x["document_id"])

    if conn is not None:
        return _run(conn)
    with engine.connect() as c:
        return _run(c)


def plan_digest(plan: list[dict]) -> str:
    """Content hash over the whole plan.

    Canonical form: the rows sorted by document id, serialised with sorted keys, no whitespace and
    no ASCII escaping — so the digest depends on the plan's content and never on formatting or on
    the order rows came back from the database.
    """
    ordered = sorted(plan, key=lambda r: r["document_id"])
    payload = json.dumps(ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_census(plan: list[dict]) -> dict:
    """The numbers a human approves: rows, distinct people, and the corroborator split."""
    by_count: dict[int, int] = {}
    for r in plan:
        by_count[r["corroborator_count"]] = by_count.get(r["corroborator_count"], 0) + 1
    return {
        "rows": len(plan),
        "distinct_people": len({r["person_id"] for r in plan}),
        "by_corroborator_count": dict(sorted(by_count.items())),
    }
