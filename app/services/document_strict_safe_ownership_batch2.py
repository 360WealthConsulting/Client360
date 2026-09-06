"""Strict-safe ownership BATCH 2 — a second, independently reviewed selection rule.

WHY A SECOND MODULE AND NOT A FLAG ON THE FIRST
-----------------------------------------------
Batch 1 (:mod:`app.services.document_strict_safe_ownership`) is frozen: its rule, its approved
composition ``{3: 104, 2: 437}`` and its 205 people describe a plan a human has already signed off.
Adding a mode to it would mean the same function could return two different populations, and the
digest that protects the first approval would stop meaning what it meant at review time. So Batch 2
gets its own module, its own batch id, its own digest and its own apply script. Nothing here changes
Batch 1; the only thing borrowed from it is the pair of PURE evidence predicates below, so that the
two batches read the proposal engine's evidence with one vocabulary rather than two copies that can
drift apart.

WHAT BATCH 2 SELECTS, AND WHY IT IS SAFE WITH ONLY ONE CONTACT CORROBORATOR
---------------------------------------------------------------------------
Batch 1 required TWO independent contact corroborators (email / phone / address) because one shared
contact detail is exactly how the mass-match cluster forms. Batch 2 lowers that to EXACTLY ONE and
replaces the missing corroborator with two signals that live entirely outside the document's text:

    * SOURCE PATH — the SharePoint parent folder the file actually sits in names the person, and
    * FILENAME    — the file's own ``original_name`` names the person.

Those are filing facts, produced by humans filing the document, not by the extraction engine reading
its content. They cannot be manufactured by a shared email address or a shared phone number, which
is the failure mode the two-corroborator bar existed to stop. They are deliberately NOT counted as
email/phone/address corroborators anywhere in this module or its manifest: the contact corroborator
count for every Batch 2 row is one, and the row carries the corroborator's NAME
(``ADDRESS`` / ``EMAIL`` / ``PHONE``) rather than a number that could be mistaken for Batch 1's.

The folder rule is deliberately strict in two ways that matter:

    * the FILENAME COMPONENT IS REMOVED before matching, so a file called "Jane Doe 2021.pdf" in a
      folder belonging to somebody else cannot borrow its own name as folder evidence; and
    * both name tokens must appear in the SAME folder segment, so an ancestry like
      ``/Doe/2021/Jane/`` — first name in one segment, last name in another — does not qualify.
      Real client folders are ``SURNAME, GIVEN`` or ``Given Surname`` in one segment.

Two documents are permanently excluded (:data:`PERMANENT_EXCLUDED_DOCUMENT_IDS`). Both carry more
than one *conflicting* available SharePoint path, so "which folder is this filed under" has no single
answer for them; each would otherwise pass every clause above. They are excluded by id rather than
by a cleverer rule because the reviewed batch is the batch that was reviewed.

THE PLAN DIGEST
---------------
:func:`plan_digest` is canonical for BATCH 2 and is not comparable with Batch 1's digest or with any
digest computed by ad-hoc tooling: it hashes this module's own row shape, including the resolved
source and folder evidence. If the corpus moves — a re-proposal, a renamed folder, a source that goes
unavailable — the digest changes and the apply refuses. The approval is of a specific plan.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from sqlalchemy import text

from app.db import engine
from app.services.document_owner_proposal import PERMANENT_REJECT_DOCUMENT_IDS

# Read-only reuse of Batch 1's PURE evidence predicates. Importing them (rather than copying the
# engine's evidence prefixes) means both batches always read the same vocabulary; neither call
# mutates anything, and Batch 1's selection is untouched.
from app.services.document_strict_safe_ownership import corroborators, has_exact_name_evidence

#: Batch identity. Encoded into the confirmation phrase so a Batch 1 phrase cannot apply this batch.
BATCH_ID = "STRICT-SAFE-OWNERSHIP-2"

#: The contact corroborators, in the order a census reports them. Folder and filename evidence are
#: NOT in this list and are never counted as one of these.
CORROBORATOR_KINDS = ("ADDRESS", "EMAIL", "PHONE")

#: EXACTLY one contact corroborator. Not "at least" — two would be a Batch 1 row, and Batch 2 was
#: reviewed as the one-corroborator population.
REQUIRED_CORROBORATOR_COUNT = 1

#: Only this source system carries the filing evidence this batch relies on.
SHAREPOINT_SOURCE_SYSTEM = "SharePoint"

#: Permanently excluded from this approved batch: each has multiple, conflicting available SharePoint
#: paths, so its folder evidence does not identify one filing location. Both otherwise qualify.
PERMANENT_EXCLUDED_DOCUMENT_IDS = frozenset({40100, 44247})

#: The fields that make up a Batch 2 plan row, in the order a reader would want them. The digest
#: hashes all of them.
PLAN_FIELDS = ("document_id", "person_id", "person_name", "original_name", "corroborator",
               "matching_folder", "source_id", "source_external_id", "source_uri", "review_status",
               "evidence")


def ascii_tokens(value) -> tuple[str, ...]:
    """Normalized ASCII word tokens: NFKD, accents dropped, lowercased, split on non-alphanumerics.

    ASCII-safe because the corpus mixes accented names, smart apostrophes and mojibake from three
    import paths, and a rule that compares "O'Gorman" to "O’GORMAN" by code point silently drops
    real matches. Note that an apostrophe SPLITS: ``O'Gorman`` is ``("o", "gorman")``, and both halves
    are then required — which is what lets the folder ``O'GORMAN, AMEDEE`` and the filename
    ``(O GORMAN AMEDEE D)`` match the same person without either spelling being privileged.
    """
    s = unicodedata.normalize("NFKD", str(value or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    return tuple(t for t in re.split(r"[^a-z0-9]+", s) if t)


def person_name_tokens(first_name, last_name) -> frozenset[str] | None:
    """Every token of the person's FIRST and LAST name, or None if either side is missing.

    None means "this person cannot be matched by name" — a record with no surname (institutions
    imported as people, for instance) can never satisfy the folder or filename rule.
    """
    first, last = ascii_tokens(first_name), ascii_tokens(last_name)
    if not first or not last:
        return None
    return frozenset(first) | frozenset(last)


def parent_folder_segments(source_uri) -> list[str]:
    """The URL-decoded parent folders of a SharePoint URI, with the FILENAME REMOVED.

    Only the path is read, so the host can never contribute a segment, and every segment is
    percent-decoded (``%20`` -> space, ``%27`` -> apostrophe, ``%26`` -> ``&``) before matching.
    """
    segments = [unquote(s) for s in urlsplit(str(source_uri or "")).path.split("/") if s]
    return segments[:-1]


def segment_names_person(segment, tokens) -> bool:
    """Does ONE folder segment carry every first- and last-name token?"""
    return bool(tokens) and tokens <= set(ascii_tokens(segment))


def filename_names_person(original_name, tokens) -> bool:
    """Does the document's own filename independently carry every first- and last-name token?"""
    return bool(tokens) and tokens <= set(ascii_tokens(original_name))


def sole_corroborator(evidence) -> str | None:
    """The single contact corroborator's NAME, or None unless there is exactly one.

    Zero is not enough evidence; two or more is a Batch 1 row and is not what this batch reviewed.
    """
    flags = corroborators(evidence)
    hits = [kind for kind in CORROBORATOR_KINDS if flags[f"{kind.lower()}_match"]]
    return hits[0] if len(hits) == REQUIRED_CORROBORATOR_COUNT else None


def matching_source(sources, tokens) -> dict | None:
    """The canonical filing source: the LOWEST-id available SharePoint source whose path names them.

    Deterministic by construction — ties in the corpus are broken by source id, never by row order —
    and it returns the matched segment so the plan (and the manifest) can show which folder was read.
    """
    matches = []
    for src in sources:
        if not src.get("available"):
            continue
        if src.get("source_system") != SHAREPOINT_SOURCE_SYSTEM:
            continue
        for segment in parent_folder_segments(src.get("source_uri")):
            if segment_names_person(segment, tokens):
                matches.append((int(src["id"]), segment, src))
                break
    if not matches:
        return None
    source_id, segment, src = min(matches, key=lambda m: m[0])
    return {"source_id": source_id, "matching_folder": segment,
            "source_external_id": src.get("source_external_id"),
            "source_uri": src.get("source_uri")}


def is_batch2_candidate(fact: dict, evidence) -> bool:
    """The document-state-independent half of the rule, as one predicate over a current proposal."""
    if (fact.get("route") or "") != "HIGH":
        return False
    if (fact.get("entity_type") or "") != "person":
        return False
    if fact.get("entity_id") in (None, ""):
        return False
    if not has_exact_name_evidence(evidence):
        return False
    return sole_corroborator(evidence) is not None


#: The SQL half of the rule. Everything expressible in SQL is expressed here — live, unowned,
#: not_required, not a permanent reject, not a Batch 2 exclusion, HIGH/person with a person that
#: still exists, and EXACTLY ONE current owner_proposal. The evidence and path rules run in Python
#: because they read the engine's prose and the filing paths.
_PLAN_SQL = """
    SELECT d.id                                          AS document_id,
           coalesce(d.original_name, '')                 AS original_name,
           coalesce(d.review_status, '')                 AS review_status,
           f.fact_value::jsonb ->> 'entity_type'         AS entity_type,
           f.fact_value::jsonb ->> 'entity_id'           AS entity_id,
           f.fact_value::jsonb ->> 'entity_name'         AS entity_name,
           f.fact_value::jsonb ->> 'route'               AS route,
           f.fact_value::jsonb ->  'evidence'            AS evidence,
           p.first_name                                  AS first_name,
           p.last_name                                   AS last_name
      FROM documents d
      JOIN document_facts f
        ON f.document_id = d.id AND f.fact_type = 'owner_proposal' AND f.is_current
      JOIN people p
        ON p.id = (f.fact_value::jsonb ->> 'entity_id')::int
     WHERE d.person_id IS NULL AND d.household_id IS NULL AND d.organization_id IS NULL
       AND d.status <> 'deleted' AND d.deleted_at IS NULL AND d.archived = false
       AND coalesce(d.review_status, '') = 'not_required'
       AND NOT (d.id = ANY(:rejects))
       AND NOT (d.id = ANY(:excluded))
       AND f.fact_value::jsonb ->> 'route' = 'HIGH'
       AND f.fact_value::jsonb ->> 'entity_type' = 'person'
       AND f.fact_value::jsonb ->> 'entity_id' IS NOT NULL
       AND (SELECT count(*) FROM document_facts f2
             WHERE f2.document_id = d.id AND f2.fact_type = 'owner_proposal' AND f2.is_current) = 1
     ORDER BY d.id
"""

_SOURCES_SQL = """
    SELECT document_id, id, source_system, source_uri, source_external_id, available
      FROM document_sources
     WHERE document_id = ANY(:ids)
     ORDER BY document_id, id
"""


def build_plan(conn=None) -> list[dict]:
    """The Batch 2 plan as it stands RIGHT NOW, deterministically ordered by document id.

    Read-only. Rebuilt entirely from live database state — no manifest is read here — so the apply
    script can compare what a human approved against what the corpus actually says today.
    """
    def _run(c):
        rows = c.execute(text(_PLAN_SQL), {
            "rejects": sorted(PERMANENT_REJECT_DOCUMENT_IDS),
            "excluded": sorted(PERMANENT_EXCLUDED_DOCUMENT_IDS),
        }).mappings().all()
        if not rows:
            return []

        sources: dict[int, list[dict]] = {}
        ids = [int(r["document_id"]) for r in rows]
        for s in c.execute(text(_SOURCES_SQL), {"ids": ids}).mappings():
            sources.setdefault(int(s["document_id"]), []).append(dict(s))

        plan = []
        for r in rows:
            document_id = int(r["document_id"])
            evidence = list(r["evidence"] or [])
            fact = {"route": r["route"], "entity_type": r["entity_type"],
                    "entity_id": r["entity_id"]}
            if not is_batch2_candidate(fact, evidence):
                continue
            tokens = person_name_tokens(r["first_name"], r["last_name"])
            if tokens is None:
                continue
            # Filename evidence is required INDEPENDENTLY of the folder: a document whose folder
            # names the person but whose own name does not is out, and vice versa.
            if not filename_names_person(r["original_name"], tokens):
                continue
            source = matching_source(sources.get(document_id, ()), tokens)
            if source is None:
                continue
            plan.append({
                "document_id": document_id,
                "person_id": int(r["entity_id"]),
                "person_name": r["entity_name"],
                "original_name": r["original_name"],
                "corroborator": sole_corroborator(evidence),
                "matching_folder": source["matching_folder"],
                "source_id": source["source_id"],
                "source_external_id": source["source_external_id"],
                "source_uri": source["source_uri"],
                "review_status": r["review_status"],
                "evidence": evidence,
            })
        return sorted(plan, key=lambda x: x["document_id"])

    if conn is not None:
        return _run(conn)
    with engine.connect() as c:
        return _run(c)


def plan_digest(plan: list[dict]) -> str:
    """Canonical BATCH 2 content hash.

    Canonical form: rows reduced to :data:`PLAN_FIELDS`, sorted by document id, serialised with
    sorted keys, no whitespace and no ASCII escaping — so the digest depends on the plan's content
    and never on formatting, on row order, or on extra keys a caller happened to attach.

    Not comparable with Batch 1's digest, and not comparable with any digest produced by tooling that
    does not use this function: a digest computed elsewhere is not evidence about this plan.
    """
    ordered = [{k: r[k] for k in PLAN_FIELDS if k in r}
               for r in sorted(plan, key=lambda r: r["document_id"])]
    payload = json.dumps(ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_census(plan: list[dict]) -> dict:
    """The numbers a human approves: rows, distinct people, and the named corroborator split."""
    by_kind: dict[str, int] = {}
    for r in plan:
        by_kind[r["corroborator"]] = by_kind.get(r["corroborator"], 0) + 1
    return {
        "rows": len(plan),
        "distinct_people": len({r["person_id"] for r in plan}),
        "by_corroborator": dict(sorted(by_kind.items())),
    }
