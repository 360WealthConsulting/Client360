"""Strict-safe ownership BATCH 3 — a third, independently reviewed selection rule.

WHERE BATCH 3 SITS
------------------
Batch 1 assigns on TWO independent contact corroborators (email / phone / address). Batch 2 lowers
that to ONE and makes up the difference with two filing signals: the document's own filename names
the proposed person, AND an available SharePoint folder segment names them too.

Batch 3 is the population where the filename still names the proposed person but the FOLDER DOES
NOT — because the folder names somebody else. That is not a failure of filing; it is how a practice
files a household. A 1098-T for Cornelis Craye lives in ``CRAYE, CORNELIUS & MARGARET``; a record
change form for Dolores Hogan lives in ``Hogan, Robert & Delores``. Batch 2's same-person folder rule
correctly refuses these, and refusing them is the right default: a folder naming a DIFFERENT person
is, on its own, evidence AGAINST the proposal.

So Batch 3 does not accept "a different person" as evidence. It accepts exactly two situations where
the different person is not a stranger, and each is proved against live database state, never against
the folder string alone:

    HOUSEHOLD_PATH_MEMBER
        The folder segment names a person who is CURRENTLY a member of the SAME Client360 household
        as the proposed person. The household link is the evidence; the folder is only how we found
        it. If that membership is dissolved tomorrow, the row stops qualifying.

    SAME_FILENAME_SAME_OWNER
        Another live document with the SAME normalized filename is ALREADY OWNED by the proposed
        person. A human already made this exact filing decision about this exact file; this row
        follows the decision that was already made, and follows nothing else.

Everything Batch 2 required about the document itself still holds, and one clause is TIGHTER: live,
unowned, ``not_required``, exactly one current owner_proposal, HIGH/person, exact-name evidence, a
filename that names the proposed person, and — where Batch 2 accepts any one of email / phone /
address — an ADDRESS corroborator with NO email and NO phone match (see
:data:`REQUIRED_CORROBORATOR`). Batch 3 replaces one clause of Batch 2, that the folder must name the
proposed person, with one of the two supports above. It does not weaken any other clause, it never
lowers the contact-corroborator bar below one, and it narrows which corroborator counts.

WHY THE SUPPORT TRAVELS IN THE PLAN AND THE MANIFEST
----------------------------------------------------
Each row carries the support that justified it — which household, which member, which source, or
which duplicate document. That is what makes the row reviewable by a human and re-checkable by the
apply script under lock: :func:`verify_support` re-proves the recorded support against live rows
before a single ownership write, so a row whose household membership or duplicate has moved cannot
be applied on the strength of a manifest that remembers how things used to be.

Batch 1 and Batch 2 are untouched. This module imports their PURE, read-only predicates so all three
batches read the proposal engine's evidence and normalize names identically; nothing here mutates or
reconfigures either.
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import text

from app.db import engine
from app.services.document_owner_proposal import PERMANENT_REJECT_DOCUMENT_IDS

# Read-only reuse of the Batch 1 / Batch 2 predicates. Sharing them is deliberate: if the proposal
# engine changes its evidence vocabulary, or the ASCII normalization is corrected, all three batches
# move together instead of drifting into three slightly different notions of "names this person".
from app.services.document_strict_safe_ownership import corroborators, has_exact_name_evidence
from app.services.document_strict_safe_ownership_batch2 import (
    CORROBORATOR_KINDS,
    ascii_tokens,
    filename_names_person,
    parent_folder_segments,
    person_name_tokens,
    segment_names_person,
    sole_corroborator,
)

#: Batch identity. Encoded into the confirmation phrases so no other batch's phrase can reach here.
BATCH_ID = "STRICT-SAFE-OWNERSHIP-3"

#: The folder segment names a CURRENT member of the proposed person's household.
RULE_HOUSEHOLD_PATH_MEMBER = "HOUSEHOLD_PATH_MEMBER"

#: A live document with the same normalized filename is already owned by the proposed person.
RULE_SAME_FILENAME_SAME_OWNER = "SAME_FILENAME_SAME_OWNER"

#: Evaluation order, and the order a census reports. Household support is evaluated first because it
#: is the stronger claim (a standing relationship, not a filing coincidence); a row that satisfies
#: both is recorded under the household rule so the rule attribution is deterministic.
RULES = (RULE_HOUSEHOLD_PATH_MEMBER, RULE_SAME_FILENAME_SAME_OWNER)

#: Only this source system carries the folder evidence this batch reads.
SHAREPOINT_SOURCE_SYSTEM = "SharePoint"

#: The ONE contact corroborator a Batch 3 row may rest on. This is part of the SELECTION RULE, not
#: merely a property of the reviewed manifest: Batch 3 accepts a folder that names somebody else, so
#: the contact evidence that survives has to be the kind that is hardest to share by accident within
#: the very relationships this batch leans on. A household shares a phone number and often an email
#: address, so an EMAIL or PHONE match between a document and a person tells you little about WHICH
#: member of that household owns the document — precisely the question Batch 3 is answering. A
#: street/ZIP match is no more identifying in isolation, but it is the only one of the three the
#: reviewed population actually rests on, and admitting the other two would widen the batch into
#: exactly the ambiguity the household rule exists to resolve.
REQUIRED_CORROBORATOR = "ADDRESS"

#: The fields that make up a Batch 3 plan row. The digest hashes exactly these, in this shape.
PLAN_FIELDS = ("document_id", "proposed_person_id", "proposed_person_name", "original_name",
               "corroborator", "rule", "support")

#: The support keys each rule records. A support block with different keys is not this rule's.
HOUSEHOLD_SUPPORT_FIELDS = ("household_id", "matching_folder", "member_name", "member_person_id",
                            "source_id")
DUPLICATE_SUPPORT_FIELDS = ("duplicate_document_id", "duplicate_person_id")

__all__ = [
    "BATCH_ID", "CORROBORATOR_KINDS", "DUPLICATE_SUPPORT_FIELDS", "HOUSEHOLD_SUPPORT_FIELDS",
    "PERMANENT_REJECT_DOCUMENT_IDS", "PLAN_FIELDS", "REQUIRED_CORROBORATOR", "RULES",
    "RULE_HOUSEHOLD_PATH_MEMBER", "RULE_SAME_FILENAME_SAME_OWNER", "ascii_tokens",
    "batch3_corroborator", "build_plan", "corroborators", "duplicate_owner_support",
    "has_exact_name_evidence", "household_path_support", "is_batch3_candidate",
    "normalized_filename", "person_name_tokens", "plan_census", "plan_digest",
    "sole_corroborator", "verify_support",
]


def normalized_filename(original_name) -> tuple[str, ...]:
    """A filename reduced to its ASCII word tokens, in order.

    Two filenames are "the same file" for :data:`RULE_SAME_FILENAME_SAME_OWNER` when these tuples are
    equal — so case, punctuation, accents and repeated separators do not make two copies of one
    document look like two different documents, while word ORDER still has to agree.
    """
    return tuple(ascii_tokens(original_name))


def batch3_corroborator(evidence) -> str | None:
    """``"ADDRESS"`` when the evidence carries an ADDRESS match and NEITHER email NOR phone.

    Stricter than Batch 2's :func:`sole_corroborator`, and deliberately spelled out rather than
    expressed as "the single corroborator happens to be ADDRESS": the three conditions below are the
    rule. ADDRESS must be present; EMAIL must be absent; PHONE must be absent. Zero corroborators is
    not enough evidence, and ADDRESS+EMAIL or ADDRESS+PHONE is a Batch 1 row — two independent
    contact signals — which is not the population Batch 3 was reviewed as.
    """
    flags = corroborators(evidence)
    if not flags["address_match"]:
        return None
    if flags["email_match"] or flags["phone_match"]:
        return None
    return REQUIRED_CORROBORATOR


def is_batch3_candidate(fact: dict, evidence) -> bool:
    """The proposal-only half of the rule, shared by both Batch 3 supports.

    HIGH, a person, exact-name evidence, and an ADDRESS corroborator with no email or phone match.
    Batch 2 accepts any one of the three corroborators because its folder names the proposed person;
    Batch 3's folder names somebody else, so it narrows the contact evidence to ADDRESS (see
    :data:`REQUIRED_CORROBORATOR`) rather than widening anything to compensate.
    """
    if (fact.get("route") or "") != "HIGH":
        return False
    if (fact.get("entity_type") or "") != "person":
        return False
    if fact.get("entity_id") in (None, ""):
        return False
    if not has_exact_name_evidence(evidence):
        return False
    return batch3_corroborator(evidence) is not None


def household_path_support(sources, *, tokens, household_id, proposed_person_id,
                           household_members) -> dict | None:
    """The first folder segment that names a CURRENT household co-member, or None.

    Deterministic by construction: sources ascending by id, segments in path order, members ascending
    by id — so a document filed under several qualifying folders always reports the same one.

    A segment that names the PROPOSED person is skipped rather than accepted. That case is Batch 2's,
    and letting it through here would quietly re-file a Batch 2 row under a Batch 3 justification.
    """
    if household_id is None or not tokens:
        return None
    for src in sorted(sources, key=lambda s: int(s["id"])):
        if not src.get("available") or src.get("source_system") != SHAREPOINT_SOURCE_SYSTEM:
            continue
        for segment in parent_folder_segments(src.get("source_uri")):
            if segment_names_person(segment, tokens):
                continue
            for member in household_members:
                if int(member["id"]) == int(proposed_person_id):
                    continue
                member_tokens = person_name_tokens(member["first_name"], member["last_name"])
                if member_tokens and segment_names_person(segment, member_tokens):
                    return {"household_id": int(household_id),
                            "matching_folder": segment,
                            "member_name": member["full_name"],
                            "member_person_id": int(member["id"]),
                            "source_id": int(src["id"])}
    return None


def duplicate_owner_support(document_id, *, proposed_person_id, owned_same_name) -> dict | None:
    """The lowest-id live document with this normalized filename already owned by the proposed person.

    ``owned_same_name`` is the set of live, unarchived, undeleted OWNED documents sharing the
    candidate's normalized filename. The document itself is never its own support.
    """
    for other in sorted(owned_same_name, key=lambda d: int(d["id"])):
        if int(other["id"]) == int(document_id):
            continue
        if other["person_id"] is not None and int(other["person_id"]) == int(proposed_person_id):
            return {"duplicate_document_id": int(other["id"]),
                    "duplicate_person_id": int(other["person_id"])}
    return None


# --- under-lock re-proof of a recorded support ---------------------------------------------------

_HOUSEHOLD_CHECK_SQL = """
    select p.id            as proposed_id,
           p.first_name    as proposed_first,
           p.last_name     as proposed_last,
           p.household_id  as proposed_household,
           m.id            as member_id,
           m.first_name    as member_first,
           m.last_name     as member_last,
           m.full_name     as member_full_name,
           m.household_id  as member_household
      from people p
      left join people m on m.id = :member_person_id
     where p.id = :proposed_person_id
"""

_SOURCE_CHECK_SQL = """
    select id, document_id, source_system, source_uri, available
      from document_sources where id = :source_id
"""

_DUPLICATE_CHECK_SQL = """
    select id, coalesce(original_name, '') as original_name, person_id, status, archived, deleted_at
      from documents where id = :duplicate_document_id
"""


def _verify_household_support(conn, row) -> str | None:
    support = row.get("support") or {}
    missing = [k for k in HOUSEHOLD_SUPPORT_FIELDS if k not in support]
    if missing:
        return f"household support is missing {missing}"

    people_row = conn.execute(text(_HOUSEHOLD_CHECK_SQL), {
        "proposed_person_id": row["proposed_person_id"],
        "member_person_id": support["member_person_id"]}).mappings().first()
    if people_row is None:
        return f"proposed person {row['proposed_person_id']} no longer exists"
    if people_row["member_id"] is None:
        return f"household member {support['member_person_id']} no longer exists"
    if people_row["proposed_household"] is None:
        return "proposed person is no longer in any household"
    if int(people_row["proposed_household"]) != int(support["household_id"]):
        return (f"proposed person moved household {support['household_id']} -> "
                f"{people_row['proposed_household']}")
    if people_row["member_household"] is None \
            or int(people_row["member_household"]) != int(support["household_id"]):
        return (f"member {support['member_person_id']} is no longer in household "
                f"{support['household_id']}")
    if int(people_row["member_id"]) == int(row["proposed_person_id"]):
        return "the household member and the proposed person are the same person"

    source = conn.execute(text(_SOURCE_CHECK_SQL),
                          {"source_id": support["source_id"]}).mappings().first()
    if source is None:
        return f"source {support['source_id']} no longer exists"
    if int(source["document_id"]) != int(row["document_id"]):
        return f"source {support['source_id']} belongs to another document"
    if not source["available"]:
        return f"source {support['source_id']} is no longer available"
    if source["source_system"] != SHAREPOINT_SOURCE_SYSTEM:
        return f"source {support['source_id']} is no longer a SharePoint source"
    if support["matching_folder"] not in parent_folder_segments(source["source_uri"]):
        return (f"folder {support['matching_folder']!r} is no longer a parent segment of "
                f"source {support['source_id']}")

    member_tokens = person_name_tokens(people_row["member_first"], people_row["member_last"])
    if not member_tokens or not segment_names_person(support["matching_folder"], member_tokens):
        return (f"folder {support['matching_folder']!r} no longer names member "
                f"{support['member_person_id']}")
    proposed_tokens = person_name_tokens(people_row["proposed_first"], people_row["proposed_last"])
    if proposed_tokens and segment_names_person(support["matching_folder"], proposed_tokens):
        return (f"folder {support['matching_folder']!r} now names the proposed person; "
                "that is a batch 2 row, not a batch 3 row")
    if people_row["member_full_name"] != support["member_name"]:
        return (f"member name drifted {support['member_name']!r} -> "
                f"{people_row['member_full_name']!r}")
    return None


def _verify_duplicate_support(conn, row) -> str | None:
    support = row.get("support") or {}
    missing = [k for k in DUPLICATE_SUPPORT_FIELDS if k not in support]
    if missing:
        return f"duplicate support is missing {missing}"
    if int(support["duplicate_document_id"]) == int(row["document_id"]):
        return "a document cannot be its own duplicate"

    dup = conn.execute(text(_DUPLICATE_CHECK_SQL), {
        "duplicate_document_id": support["duplicate_document_id"]}).mappings().first()
    if dup is None:
        return f"duplicate document {support['duplicate_document_id']} no longer exists"
    if dup["status"] == "deleted" or dup["deleted_at"] is not None:
        return f"duplicate document {support['duplicate_document_id']} is deleted"
    if dup["archived"]:
        return f"duplicate document {support['duplicate_document_id']} is archived"
    if dup["person_id"] is None:
        return f"duplicate document {support['duplicate_document_id']} is no longer owned"
    if int(dup["person_id"]) != int(row["proposed_person_id"]):
        return (f"duplicate document {support['duplicate_document_id']} is owned by "
                f"{dup['person_id']}, not the proposed {row['proposed_person_id']}")
    if int(dup["person_id"]) != int(support["duplicate_person_id"]):
        return (f"duplicate owner drifted {support['duplicate_person_id']} -> {dup['person_id']}")

    target = conn.execute(text(
        "select coalesce(original_name, '') as original_name from documents where id = :i"),
        {"i": row["document_id"]}).mappings().first()
    if target is None:
        return f"document {row['document_id']} no longer exists"
    if normalized_filename(target["original_name"]) != normalized_filename(dup["original_name"]):
        return (f"filenames no longer match: {target['original_name']!r} vs "
                f"{dup['original_name']!r}")
    return None


def verify_support(conn, row) -> str | None:
    """Re-prove one plan row's recorded support against live rows. None means it still holds.

    This is what the apply script calls under ``FOR UPDATE``. It deliberately re-reads the people,
    sources and duplicate documents rather than trusting the plan that was just rebuilt: the plan
    proves the row is selectable, this proves the SPECIFIC support a human reviewed is the support
    that still exists.
    """
    rule = row.get("rule")
    if rule == RULE_HOUSEHOLD_PATH_MEMBER:
        return _verify_household_support(conn, row)
    if rule == RULE_SAME_FILENAME_SAME_OWNER:
        return _verify_duplicate_support(conn, row)
    return f"unknown rule {rule!r}"


# --- the live plan -------------------------------------------------------------------------------

#: Everything expressible in SQL: live, unowned, not_required, not a permanent reject, HIGH/person
#: with a person that still exists, and EXACTLY ONE current owner_proposal.
_CANDIDATE_SQL = """
    SELECT d.id                                  AS document_id,
           coalesce(d.original_name, '')         AS original_name,
           f.fact_value::jsonb ->> 'entity_id'   AS entity_id,
           f.fact_value::jsonb ->> 'entity_name' AS entity_name,
           f.fact_value::jsonb ->  'evidence'    AS evidence,
           f.fact_value::jsonb ->> 'route'       AS route,
           f.fact_value::jsonb ->> 'entity_type' AS entity_type,
           p.first_name                          AS first_name,
           p.last_name                           AS last_name,
           p.household_id                        AS household_id
      FROM documents d
      JOIN document_facts f
        ON f.document_id = d.id AND f.fact_type = 'owner_proposal' AND f.is_current
      JOIN people p
        ON p.id = (f.fact_value::jsonb ->> 'entity_id')::int
     WHERE d.person_id IS NULL AND d.household_id IS NULL AND d.organization_id IS NULL
       AND d.status <> 'deleted' AND d.deleted_at IS NULL AND d.archived = false
       AND coalesce(d.review_status, '') = 'not_required'
       AND NOT (d.id = ANY(:rejects))
       AND f.fact_value::jsonb ->> 'route' = 'HIGH'
       AND f.fact_value::jsonb ->> 'entity_type' = 'person'
       AND f.fact_value::jsonb ->> 'entity_id' IS NOT NULL
       AND (SELECT count(*) FROM document_facts f2
             WHERE f2.document_id = d.id AND f2.fact_type = 'owner_proposal' AND f2.is_current) = 1
     ORDER BY d.id
"""

_SOURCES_SQL = """
    SELECT document_id, id, source_system, source_uri, source_external_id, available
      FROM document_sources WHERE document_id = ANY(:ids) ORDER BY document_id, id
"""

_MEMBERS_SQL = """
    SELECT id, first_name, last_name, full_name, household_id
      FROM people WHERE household_id = ANY(:households) ORDER BY id
"""

_OWNED_LIVE_SQL = """
    SELECT id, coalesce(original_name, '') AS original_name, person_id
      FROM documents
     WHERE person_id IS NOT NULL AND status <> 'deleted' AND deleted_at IS NULL
       AND archived = false
     ORDER BY id
"""


def build_plan(conn=None) -> list[dict]:
    """The Batch 3 plan as it stands RIGHT NOW, deterministically ordered by document id.

    Read-only, and rebuilt entirely from live state — no manifest is read here — so the apply script
    can compare what a human approved against what the corpus says today.
    """
    def _run(c):
        rows = c.execute(text(_CANDIDATE_SQL),
                         {"rejects": sorted(PERMANENT_REJECT_DOCUMENT_IDS)}).mappings().all()

        # Narrow to the proposal/filename bar before touching sources, members or duplicates: the
        # candidate set is small, and the joins below are corpus-wide.
        candidates = []
        for r in rows:
            evidence = list(r["evidence"] or [])
            fact = {"route": r["route"], "entity_type": r["entity_type"],
                    "entity_id": r["entity_id"]}
            if not is_batch3_candidate(fact, evidence):
                continue
            tokens = person_name_tokens(r["first_name"], r["last_name"])
            if tokens is None:
                continue
            if not filename_names_person(r["original_name"], tokens):
                continue
            candidates.append({**dict(r), "tokens": tokens,
                               "corroborator": batch3_corroborator(evidence)})
        if not candidates:
            return []

        ids = [int(x["document_id"]) for x in candidates]
        sources: dict[int, list[dict]] = {}
        for s in c.execute(text(_SOURCES_SQL), {"ids": ids}).mappings():
            sources.setdefault(int(s["document_id"]), []).append(dict(s))

        households = sorted({int(x["household_id"]) for x in candidates
                             if x["household_id"] is not None})
        members: dict[int, list[dict]] = {}
        if households:
            for m in c.execute(text(_MEMBERS_SQL), {"households": households}).mappings():
                members.setdefault(int(m["household_id"]), []).append(dict(m))

        # Duplicate lookup is keyed on the NORMALIZED filename, so it cannot be pushed into SQL
        # without reimplementing the normalization in Postgres. The owned live corpus is small
        # enough to bucket in one pass, and one pass is also what keeps this deterministic.
        wanted = {normalized_filename(x["original_name"]) for x in candidates}
        owned_by_name: dict[tuple, list[dict]] = {}
        for d in c.execute(text(_OWNED_LIVE_SQL)).mappings():
            key = normalized_filename(d["original_name"])
            if key in wanted:
                owned_by_name.setdefault(key, []).append(dict(d))

        plan = []
        for x in candidates:
            document_id, person_id = int(x["document_id"]), int(x["entity_id"])
            support = household_path_support(
                sources.get(document_id, ()), tokens=x["tokens"], household_id=x["household_id"],
                proposed_person_id=person_id,
                household_members=members.get(int(x["household_id"]), ())
                if x["household_id"] is not None else ())
            rule = RULE_HOUSEHOLD_PATH_MEMBER if support else None
            if support is None:
                support = duplicate_owner_support(
                    document_id, proposed_person_id=person_id,
                    owned_same_name=owned_by_name.get(normalized_filename(x["original_name"]), ()))
                rule = RULE_SAME_FILENAME_SAME_OWNER if support else None
            if support is None:
                continue
            plan.append({
                "document_id": document_id,
                "proposed_person_id": person_id,
                "proposed_person_name": x["entity_name"],
                "original_name": x["original_name"],
                "corroborator": x["corroborator"],
                "rule": rule,
                "support": support,
            })
        return sorted(plan, key=lambda r: r["document_id"])

    if conn is not None:
        return _run(conn)
    with engine.connect() as c:
        return _run(c)


def plan_digest(plan: list[dict]) -> str:
    """Canonical BATCH 3 content hash.

    Rows reduced to :data:`PLAN_FIELDS`, sorted by document id, serialised with sorted keys, no
    whitespace and no ASCII escaping — so the digest depends on the plan's content and never on
    formatting, row order, or extra keys a caller attached. The recorded support is part of the
    hash: a row whose household member or duplicate changed is a different plan.

    Not comparable with Batch 1's or Batch 2's digest, and not comparable with a digest produced by
    any tooling that does not use this function.
    """
    ordered = [{k: r[k] for k in PLAN_FIELDS if k in r}
               for r in sorted(plan, key=lambda r: r["document_id"])]
    payload = json.dumps(ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_census(plan: list[dict]) -> dict:
    """The numbers a human approves: rows, distinct people, the rule split and the corroborators."""
    by_rule: dict[str, int] = {}
    by_corroborator: dict[str, int] = {}
    for r in plan:
        by_rule[r["rule"]] = by_rule.get(r["rule"], 0) + 1
        by_corroborator[r["corroborator"]] = by_corroborator.get(r["corroborator"], 0) + 1
    return {
        "rows": len(plan),
        "distinct_people": len({r["proposed_person_id"] for r in plan}),
        "by_rule": dict(sorted(by_rule.items())),
        "by_corroborator": dict(sorted(by_corroborator.items())),
    }
