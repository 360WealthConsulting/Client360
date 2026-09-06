"""Strict-safe ownership BATCH 4 — multi-scope CONFLICT CLEANUP, not an assignment.

HOW THIS DIFFERS FROM BATCHES 1-3, AND WHY IT NEEDS ITS OWN WRITE PATH
----------------------------------------------------------------------
Batches 1-3 give an UNOWNED document an owner, and every one of them routes the write through
``households.resolve_document_ownership``, which assigns only when ``person_id``, ``household_id`` and
``organization_id`` are ALL NULL. Batch 4 does the opposite job: the documents here are already owned
TWICE, and the fix is to REMOVE the stale scope. The canonical resolver cannot express that — it
requires a destination and refuses an already-owned row — so this batch owns a narrow guarded UPDATE
of its own. That is a deliberate architectural difference, stated here rather than hidden, and the
statement is written so it can only ever clear a person id that is still exactly the one reviewed:

    UPDATE documents SET person_id = NULL
     WHERE id = :id AND person_id = :former_person_id AND organization_id = :organization_id
       AND household_id IS NULL AND <still live>

``organization_id`` is not written. It is already correct on every row; the batch only removes the
person scope that is arguing with it. ``household_id`` is required to be NULL before and after.

THE POPULATION, AND WHY IT IS SAFE
-----------------------------------
The corpus contains businesses that were imported as PEOPLE and later repaired into
``relationship_entities``. The repair records its own provenance — ``origin='canonical_type_repair'``
and ``repaired_from_person_id`` — and that is the evidence this batch rests on. It is a statement the
SYSTEM made about its own data, not an inference from a folder name:

    * the organization is active, is a ``business``, and its details name the former person id;
    * that former person record is INACTIVE and has no first or last name — it is not a natural
      person, it is the pre-repair shell of the same business;
    * the two names are the same business name once legal suffixes are set aside
      (``Affordable Measures LLC`` vs ``AFFORDABLE MEASURES CO``);
    * the document's own FILENAME names that business; and
    * an available SharePoint/TaxDome folder names it too.

THE CLAUSE THAT MATTERS MOST IS THE ONE THAT REJECTS
-----------------------------------------------------
Four sibling documents sit in the same business folder, under the same doubly-owned pair, and are NOT
in this batch: their filenames name ``JENKINS RANDALL L and L`` — the LLC's principal, a separate
active client who already owns the same-filename copies. Folder evidence alone cannot tell those
apart from the four that qualify, because the folder is identical. :func:`names_a_different_client`
is what separates them, and it is a hard reject rather than a score: a document whose own filename
names somebody else is not this business's document, however tidy the rest of the evidence looks.

Nothing in Batch 1, 2 or 3 is imported, altered or re-used here beyond two pure text helpers.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy import text

from app.db import engine

# Pure, read-only text helpers shared with the earlier batches so "does this name that" is answered
# one way across the ownership lane. Neither module is modified.
from app.services.document_strict_safe_ownership_batch2 import ascii_tokens, person_name_tokens

#: Batch identity. Encoded into both confirmation phrases.
BATCH_ID = "STRICT-SAFE-OWNERSHIP-4"

#: The provenance marker the canonical type repair writes onto the organization entity.
CANONICAL_REPAIR_ORIGIN = "canonical_type_repair"

#: The entity type an organization must be to receive a business's documents.
REQUIRED_ENTITY_TYPE = "business"

#: Trailing tokens that are legal form, not identity. ``Affordable Measures LLC`` and
#: ``AFFORDABLE MEASURES CO`` are the same business; ``co`` and ``llc`` must not decide that.
LEGAL_SUFFIX_TOKENS = frozenset({
    "llc", "l", "c", "inc", "incorporated", "co", "corp", "corporation", "ltd", "limited",
    "lp", "llp", "pc", "pllc", "company", "plc", "sa", "pa",
})

#: The fewest identity tokens a business name must keep after suffix-stripping. Two, because a
#: single token ("Affordable") would match unrelated filenames across the corpus.
MIN_CORE_TOKENS = 2

#: Only these source systems carry client folder structure worth reading.
FOLDER_SOURCE_SYSTEMS = ("SharePoint", "TaxDome Drive")

#: The fields of a Batch 4 plan row, in report order. The digest hashes exactly these.
PLAN_FIELDS = ("document_id", "original_name", "former_person_id", "former_person_name",
               "organization_id", "organization_name", "repaired_from_person_id",
               "filename_names_organization", "folder_source_id", "folder_segment",
               "twin_document_id", "review_status")

_DRIVE_RE = re.compile(r"^[A-Za-z]:$")
_BACKSLASH = chr(92)


def core_name_tokens(name) -> frozenset[str]:
    """A business name reduced to its identity tokens, legal form removed.

    Suffixes are stripped only from the END and only while the name keeps at least
    :data:`MIN_CORE_TOKENS`, so ``Co-op Ltd`` does not lose the word it is named for.
    """
    tokens = list(ascii_tokens(name))
    while len(tokens) > MIN_CORE_TOKENS and tokens[-1] in LEGAL_SUFFIX_TOKENS:
        tokens.pop()
    return frozenset(tokens)


def names_organization(text_value, org_core: frozenset[str]) -> bool:
    """Does this text carry every identity token of the organization's name?"""
    if len(org_core) < MIN_CORE_TOKENS:
        return False
    return org_core <= set(ascii_tokens(text_value))


def build_known_client_index(people: list[dict], organizations: list[dict]) -> dict[str, list]:
    """token -> [(client tokens, label)] for spotting a DIFFERENT client named in a filename.

    Inverted so the check stays cheap, and restricted to multi-token names so a single common word
    cannot flag half the corpus.
    """
    index: dict[str, list] = {}
    seen: set[tuple] = set()
    for person in people:
        tokens = person_name_tokens(person.get("first_name"), person.get("last_name"))
        if not tokens or len(tokens) < 2 or tuple(sorted(tokens)) in seen:
            continue
        seen.add(tuple(sorted(tokens)))
        for token in tokens:
            index.setdefault(token, []).append((tokens, person.get("full_name")))
    for organization in organizations:
        tokens = core_name_tokens(organization.get("name"))
        if len(tokens) < 2 or tuple(sorted(tokens)) in seen:
            continue
        seen.add(tuple(sorted(tokens)))
        for token in tokens:
            index.setdefault(token, []).append((tokens, organization.get("name")))
    return index


def names_a_different_client(text_value, *, org_core: frozenset[str],
                             known_clients_by_token: dict | None) -> str | None:
    """The name of a DIFFERENT known client this text carries, or None.

    This is the clause that keeps the LLC principal's personal returns out of the LLC's batch.
    """
    tokens = set(ascii_tokens(text_value))
    for token in tokens:
        for candidate, label in (known_clients_by_token or {}).get(token, ()):
            if candidate <= tokens and candidate != org_core:
                return label
    return None


def source_folder_segments(source: dict) -> list[str]:
    """Folder segments of a source reference, filename removed.

    SharePoint keeps its hierarchy in the https ``source_uri``; TaxDome keeps its own in the
    ``source_path`` drive form. Reading the wrong field yields no client folder at all, so an
    http(s) uri wins and otherwise the path does.
    """
    uri, path = source.get("source_uri"), source.get("source_path")
    raw = uri if str(uri or "").lower().startswith(("http://", "https://")) else (path or uri or "")
    if str(raw).lower().startswith(("http://", "https://")):
        decoded = unquote(urlparse(str(raw)).path or "")
    else:
        decoded = unquote(str(raw)).replace(_BACKSLASH, "/")
    parts = [p for p in decoded.split("/") if p]
    if parts and _DRIVE_RE.fullmatch(parts[0]):
        parts = parts[1:]
    for marker in ("TaxDome", "Shared Documents", "Documents"):
        if marker in parts:
            parts = parts[parts.index(marker) + 1:]
            break
    return parts[:-1] if len(parts) > 1 else []


def organization_folder_evidence(sources, org_core: frozenset[str]) -> dict | None:
    """The lowest-id AVAILABLE source whose folder path names the organization, or None."""
    matches = []
    for src in sources:
        if not src.get("available") or src.get("source_system") not in FOLDER_SOURCE_SYSTEMS:
            continue
        for segment in source_folder_segments(src):
            if names_organization(segment, org_core):
                matches.append((int(src["id"]), segment))
                break
    if not matches:
        return None
    source_id, segment = min(matches, key=lambda m: m[0])
    return {"folder_source_id": source_id, "folder_segment": segment}


#: Everything expressible in SQL: the multi-scope person+organization shape, live and not_required,
#: an ACTIVE business organization whose canonical-repair provenance names this document's person,
#: and a former person record that is INACTIVE and not a natural person.
_CANDIDATE_SQL = """
    SELECT d.id                         AS document_id,
           coalesce(d.original_name,'') AS original_name,
           coalesce(d.review_status,'') AS review_status,
           d.person_id                  AS former_person_id,
           p.full_name                  AS former_person_name,
           d.organization_id            AS organization_id,
           o.name                       AS organization_name,
           (o.details::jsonb ->> 'repaired_from_person_id')::int AS repaired_from_person_id
      FROM documents d
      JOIN people p ON p.id = d.person_id
      JOIN relationship_entities o ON o.id = d.organization_id
     WHERE d.status <> 'deleted' AND d.deleted_at IS NULL AND d.archived = false
       AND coalesce(d.review_status,'') = 'not_required'
       AND d.person_id IS NOT NULL
       AND d.organization_id IS NOT NULL
       AND d.household_id IS NULL
       AND o.active = true
       AND o.entity_type = :entity_type
       AND o.details::jsonb ->> 'origin' = :repair_origin
       AND (o.details::jsonb ->> 'repaired_from_person_id')::int = d.person_id
       AND p.active = false
       AND p.first_name IS NULL
       AND p.last_name IS NULL
     ORDER BY d.id
"""

_SOURCES_SQL = """
    SELECT document_id, id, source_system, source_uri, source_path, available
      FROM document_sources WHERE document_id = ANY(:ids) ORDER BY document_id, id
"""

_TWIN_SQL = """
    SELECT id, coalesce(original_name,'') AS original_name, organization_id
      FROM documents
     WHERE organization_id = ANY(:orgs) AND person_id IS NULL AND household_id IS NULL
       AND status <> 'deleted' AND deleted_at IS NULL AND archived = false
     ORDER BY id
"""


def build_plan(conn=None) -> list[dict]:
    """The Batch 4 cleanup plan as it stands RIGHT NOW, ordered by document id. Read-only.

    Rebuilt entirely from live state and from the rule — never from a list of document ids.
    """
    def _run(c):
        rows = c.execute(text(_CANDIDATE_SQL), {
            "entity_type": REQUIRED_ENTITY_TYPE,
            "repair_origin": CANONICAL_REPAIR_ORIGIN}).mappings().all()
        if not rows:
            return []

        ids = [int(r["document_id"]) for r in rows]
        sources: dict[int, list[dict]] = {}
        for s in c.execute(text(_SOURCES_SQL), {"ids": ids}).mappings():
            sources.setdefault(int(s["document_id"]), []).append(dict(s))

        organizations = sorted({int(r["organization_id"]) for r in rows})
        twins: dict[int, list[dict]] = {}
        for t in c.execute(text(_TWIN_SQL), {"orgs": organizations}).mappings():
            twins.setdefault(int(t["organization_id"]), []).append(dict(t))

        people_rows = [dict(p) for p in c.execute(text(
            "SELECT id, first_name, last_name, full_name FROM people ORDER BY id")).mappings()]
        org_rows = [dict(o) for o in c.execute(text(
            "SELECT id, name FROM relationship_entities ORDER BY id")).mappings()]
        known = build_known_client_index(people_rows, org_rows)

        plan = []
        for r in rows:
            document_id = int(r["document_id"])
            org_core = core_name_tokens(r["organization_name"])
            if len(org_core) < MIN_CORE_TOKENS:
                continue
            # The former person record must BE this business, not merely point at it.
            if core_name_tokens(r["former_person_name"]) != org_core:
                continue
            # The document's own filename must name the business...
            if not names_organization(r["original_name"], org_core):
                continue
            # ...and must not name anybody else's.
            if names_a_different_client(r["original_name"], org_core=org_core,
                                        known_clients_by_token=known):
                continue
            folder = organization_folder_evidence(sources.get(document_id, ()), org_core)
            if folder is None:
                continue
            twin = next((t["id"] for t in twins.get(int(r["organization_id"]), ())
                         if t["id"] != document_id
                         and ascii_tokens(t["original_name"]) == ascii_tokens(r["original_name"])),
                        None)
            plan.append({
                "document_id": document_id,
                "original_name": r["original_name"],
                "former_person_id": int(r["former_person_id"]),
                "former_person_name": r["former_person_name"],
                "organization_id": int(r["organization_id"]),
                "organization_name": r["organization_name"],
                "repaired_from_person_id": int(r["repaired_from_person_id"]),
                "filename_names_organization": True,
                "folder_source_id": folder["folder_source_id"],
                "folder_segment": folder["folder_segment"],
                "twin_document_id": twin,
                "review_status": r["review_status"],
            })
        return sorted(plan, key=lambda x: x["document_id"])

    if conn is not None:
        return _run(conn)
    with engine.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        return _run(c)


# --- under-lock re-proof --------------------------------------------------------------------------

_VERIFY_SQL = """
    SELECT d.id, d.person_id, d.household_id, d.organization_id, d.status, d.archived,
           d.deleted_at, coalesce(d.review_status,'') AS review_status,
           coalesce(d.original_name,'') AS original_name,
           p.id AS person_exists, p.active AS person_active, p.first_name, p.last_name,
           p.full_name AS person_full_name,
           o.id AS org_exists, o.active AS org_active, o.entity_type, o.name AS org_name,
           o.details::jsonb ->> 'origin' AS repair_origin,
           (o.details::jsonb ->> 'repaired_from_person_id')::int AS repaired_from_person_id
      FROM documents d
      LEFT JOIN people p ON p.id = d.person_id
      LEFT JOIN relationship_entities o ON o.id = d.organization_id
     WHERE d.id = :document_id
"""


def verify_row(conn, row: dict) -> str | None:
    """Re-prove ONE reviewed row against live state. None means it still holds.

    Called under ``FOR UPDATE`` before any write. Every clause of the rule is re-checked here, not
    just the ones that are cheap: a plan rebuild proves the row is still selectable, this proves the
    SPECIFIC document, person and organization a human reviewed are the ones still in the database.
    """
    live = conn.execute(text(_VERIFY_SQL), {"document_id": row["document_id"]}).mappings().first()
    if live is None:
        return f"document {row['document_id']} no longer exists"
    if live["status"] == "deleted" or live["deleted_at"] is not None:
        return "document is deleted"
    if live["archived"]:
        return "document is archived"
    if live["review_status"] != "not_required":
        return f"review_status is {live['review_status']!r}"
    if live["person_id"] != row["former_person_id"]:
        return (f"person_id is {live['person_id']}, reviewed as {row['former_person_id']}")
    if live["organization_id"] != row["organization_id"]:
        return (f"organization_id is {live['organization_id']}, reviewed as "
                f"{row['organization_id']}")
    if live["household_id"] is not None:
        return f"household_id is now {live['household_id']}, must be NULL"
    if live["person_exists"] is None:
        return f"former person {row['former_person_id']} no longer exists"
    if live["person_active"]:
        return (f"former person {row['former_person_id']} is ACTIVE again — it is no longer a "
                "retired business-as-person shell")
    if live["first_name"] is not None or live["last_name"] is not None:
        return (f"former person {row['former_person_id']} now has a personal name — it may be a "
                "natural person")
    if live["org_exists"] is None:
        return f"organization {row['organization_id']} no longer exists"
    if not live["org_active"]:
        return f"organization {row['organization_id']} is inactive"
    if live["entity_type"] != REQUIRED_ENTITY_TYPE:
        return f"organization {row['organization_id']} is a {live['entity_type']!r}, not a business"
    if live["repair_origin"] != CANONICAL_REPAIR_ORIGIN:
        return "canonical repair provenance is missing from the organization"
    if live["repaired_from_person_id"] != row["former_person_id"]:
        return (f"repaired_from_person_id is {live['repaired_from_person_id']}, reviewed as "
                f"{row['former_person_id']}")
    org_core = core_name_tokens(live["org_name"])
    if core_name_tokens(live["person_full_name"]) != org_core:
        return "the former person's name no longer matches the organization's"
    if not names_organization(live["original_name"], org_core):
        return f"filename {live['original_name']!r} no longer names the organization"
    if live["original_name"] != row["original_name"]:
        return "original_name changed since review"
    return None


def plan_digest(plan: list[dict]) -> str:
    """Canonical BATCH 4 content hash over :data:`PLAN_FIELDS`, sorted by document id."""
    ordered = [{k: r[k] for k in PLAN_FIELDS if k in r}
               for r in sorted(plan, key=lambda r: r["document_id"])]
    payload = json.dumps(ordered, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def plan_census(plan: list[dict]) -> dict[str, Any]:
    """The numbers a human approves."""
    return {
        "rows": len(plan),
        "distinct_organizations": len({r["organization_id"] for r in plan}),
        "distinct_former_people": len({r["former_person_id"] for r in plan}),
        "with_twin_corroboration": sum(1 for r in plan if r["twin_document_id"] is not None),
    }
