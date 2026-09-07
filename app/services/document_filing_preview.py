"""Document filing preview — READ-ONLY. Proposes where each document WOULD be filed. Never writes.

WHY THIS EXISTS, AND WHAT IT DELIBERATELY DOES NOT DO
-----------------------------------------------------
``document_folders`` is empty and ``documents.folder_id`` is NULL on every one of the 121,839 rows in
the corpus. Before a single folder is created, somebody has to be able to read what the filing plan
would actually be, row by row, with the evidence behind each destination. That is this module. It
opens no write path, exposes no apply function, and proposes nothing that is persisted anywhere.

It also does NOT infer ownership. Ownership is settled by the strict-safe ownership batches and their
guardrails; here, existing ownership is EVIDENCE and nothing more. A document with no current owner
gets no client scope and is reported UNRESOLVED — never guessed into somebody's folder.

THE FILING TREE CAME FROM THE CORPUS, NOT FROM AN OPINION
----------------------------------------------------------
The taxonomy below was measured, not designed (reproduce it with :func:`taxdome_taxonomy` and
:func:`sharepoint_taxonomy`). Two systems, two different shapes:

*TaxDome* — 22,848 references, 681 client folders, all parsed. Its top five shapes cover 90.7%::

    <CLIENT>/<YEAR>                                6,279
    <CLIENT>/Client uploaded documents             5,768
    <CLIENT>/Firm docs shared with client/<YEAR>   3,270
    <CLIENT>/Client uploaded documents/<YEAR>      3,045
    <CLIENT>/Client uploaded documents/Unsorted    2,368

The critical reading: TaxDome's level-2 is **not a filing category**. "Client uploaded documents" and
"Firm docs shared with client" say who PUT the file there, not what it IS. Treating them as
categories would file a W-2 and a driving licence in the same place because the same person uploaded
them. They are recorded here as a PROVENANCE category, and they are honestly weaker than SharePoint's
service lines — which is why they alone never produce a year-bearing destination.

*SharePoint* — 71,352 available references. Exactly two roots carry client filing structure::

    360 Tax Solutions, LLC / Clients / <SERVICE LINE> / <SEGMENT> / <CLIENT> / <YEAR?> / ...
    360 Wealth Consulting, LLC / Accounts / <CLIENT> / <SUBFOLDER or YEAR?> / ...

with service lines Tax Preparation (25,795), Sales/Litter & PP Tax (8,029), Payroll (4,969),
Bookkeeping (4,751) and a small tail. Every OTHER root is an operational tree — "AWS Migration
Backup" alone is 64,890 references, 60,654 of them already unavailable — and none of it is client
filing evidence. That is the single biggest false-positive risk in this whole exercise, so
:data:`SHAREPOINT_OPERATIONAL_ROOTS` refuses those roots outright rather than scoring them low.

PRECISION OVER COVERAGE
-----------------------
AUTO_FILE_SAFE means "a human would not have to look at this". Every clause in
:func:`_filing_decision` exists to remove a way that claim could be wrong, and none of them can be
satisfied by weak evidence stacking up: a classifier guess never creates a destination on its own, a
generic folder is never a category, a year is placed in the path only when independently corroborated,
and any disagreement between two AVAILABLE sources drops the row to REVIEW_REQUIRED. A smaller
trustworthy set is the goal; a bigger one is not.

CANONICAL SERVICES ARE REUSED, NOT REIMPLEMENTED
-------------------------------------------------
Document type comes from :func:`app.services.document_classification.classify_document`, the tax year
from :mod:`app.services.document_tax_year`, names from :mod:`app.services.document_naming`, and the
ASCII name-matching helpers from the strict-safe ownership module that already had to solve "does this
folder name this person" against this exact corpus. All are imported read-only and none is modified.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy import text

from app.db import engine
from app.services.document_classification import CLASSIFIER_VERSION, classify_document
from app.services.document_naming import document_display_name, extract_years, safe_document_label

# The ASCII name-token helpers were built for the ownership batches against this same corpus (accents,
# smart apostrophes, "SURNAME, GIVEN" folders). Reusing them keeps "does this folder name this person"
# answered ONE way across the codebase. Imported read-only; the ownership modules are not touched.
from app.services.document_strict_safe_ownership_batch2 import ascii_tokens, person_name_tokens
from app.services.document_tax_year import infer_tax_year

PREVIEW_VERSION = "filing-preview-v1"

#: review_status marking a mechanically proven non-client artifact. Never a client filing candidate.
EXCLUDED_NONCLIENT_REVIEW_STATUS = "excluded_nonclient"

#: SharePoint roots that carry real client filing structure, and the shape each one uses.
#: ``client_depth`` is how many segments sit above the client folder inside the root.
SHAREPOINT_CLIENT_ROOTS: dict[str, dict[str, Any]] = {
    "360 tax solutions, llc": {"label": "360 Tax Solutions, LLC", "kind": "tax",
                               "category_index": 2, "client_index": 4},
    "360 wealth consulting, llc": {"label": "360 Wealth Consulting, LLC", "kind": "wealth",
                                   "category_index": 1, "client_index": 2},
}

#: SharePoint roots that are operational, backup or personal trees. Measured, not guessed: these
#: carry 64,890 (AWS Migration Backup), 12,073 (Documents 1), 4,655 (sites) references and so on, and
#: none of them is a client filing hierarchy. A path under any of these contributes NO client
#: evidence at all — not weak evidence, none.
SHAREPOINT_OPERATIONAL_ROOTS = frozenset({
    "aws migration backup", "documents 1", "sites", "360+(1)", "general", "pictures", "desktop",
    "desktop cloud", "personal", "archive", "apps", "email attachments", "my data sources",
})

#: SharePoint service lines -> the normalized Client360 category. The RAW label is always preserved
#: in evidence; these mappings only collapse labels that unambiguously name the same service.
SHAREPOINT_CATEGORY_MAP = {
    "tax preparation": "Tax Preparation",
    "tax preparation(1)": "Tax Preparation",
    "sales, litter & pp tax": "Sales & Litter Tax",
    "sales & litter tax": "Sales & Litter Tax",
    "payroll": "Payroll",
    "bookkeeping": "Bookkeeping",
    "bookkeeping(1)": "Bookkeeping",
    "client services": "Client Services",
    "1099 processing": "1099 Processing",
    "tax resolution": "Tax Resolution",
    "accounts": "Wealth Accounts",
}

#: TaxDome level-2 buckets. These are PROVENANCE, not document categories — see the module docstring.
TAXDOME_CATEGORY_MAP = {
    "client uploaded documents": "Client Uploads",
    "firm docs shared with client": "Firm Deliverables",
}

#: Categories whose meaning is "where it came from", not "what it is". A destination resting only on
#: one of these is never AUTO_FILE_SAFE.
PROVENANCE_CATEGORIES = frozenset(TAXDOME_CATEGORY_MAP.values())

#: Folder names that carry no filing meaning anywhere in either system. A year or a client name is
#: still read from a path containing these, but they never become a category.
GENERIC_SEGMENTS = frozenset({
    "unsorted", "scans", "scan", "needs to be done", "web upload", "inactive", "active", "archive",
    "archived", "general", "documents", "document", "misc", "miscellaneous", "other", "temp",
    "tmp", "new folder", "untitled", "shared documents", "filehistory", "detail", "dms",
    "_layouts", "separation copy", "forms", "files", "attachments", "downloads", "upload",
    "uploads", "to be filed", "to file", "working", "wip", "backup", "old", "personal", "private",
    "client copy", "copies", "duplicates", "review", "pending",
})

#: A path segment that IS a year, or leads with one. Mirrors ``document_tax_year._YEAR_SEGMENT_RE``;
#: :func:`test_year_segment_rule_agrees_with_the_canonical_engine` pins the two together.
YEAR_SEGMENT_RE = re.compile(r"^(19[89]\d|20[0-4]\d)(?:\s*[-_. ].*)?$")

_DRIVE_RE = re.compile(r"^[A-Za-z]:$")
_BACKSLASH = chr(92)

FILING_STATUSES = ("AUTO_FILE_SAFE", "REVIEW_REQUIRED", "UNRESOLVED")

#: Every field of a proposal, in report order. CSV columns and JSON key order both come from here, so
#: the two reports can never drift apart.
PROPOSAL_FIELDS = (
    "document_id", "original_name", "current_display_name",
    "source", "source_id", "source_identity", "source_uri", "source_path",
    "current_person_id", "current_person_name", "current_household_id", "current_household_name",
    "current_organization_id", "current_organization_name",
    "proposed_client_scope", "proposed_scope_type", "proposed_scope_id", "proposed_scope_name",
    "filing_scope_state",
    "proposed_folder_segments", "proposed_folder_path",
    "proposed_top_level_category", "category_source", "category_confidence",
    "proposed_document_type", "document_type_source", "document_type_confidence",
    "proposed_tax_year", "tax_year_source", "tax_year_confidence", "tax_year_evidence",
    "proposed_display_name", "display_name_source", "display_name_changes",
    "filing_status", "filing_confidence",
    "reasons", "conflicts", "evidence",
)


# --- path reading --------------------------------------------------------------------------------

def path_segments(uri: str | None, path: str | None = None) -> list[str]:
    """The folder segments of a source reference, URL-decoded, filename REMOVED.

    Which field carries the HIERARCHY differs by system, and picking the wrong one silently destroys
    all filing evidence:

    * SharePoint stores the library hierarchy in ``source_uri``
      (``https://…/Shared Documents/360 Tax Solutions, LLC/Clients/…``) while ``source_path`` holds a
      drive-id/GUID form (``1d5e7f6a-…/b!an9e…/General/…``) that no client taxonomy can be read from;
    * TaxDome is the other way round — ``source_path`` is the drive hierarchy (``Z:\\Client\\…``) and
      ``source_uri`` is a local staging path.

    So an http(s) ``uri`` wins, and otherwise ``path`` does. Returns [] when there is nothing to read.
    """
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


def segment_year(segment: str | None) -> int | None:
    """The filing year a folder segment names, or None. ``2023 Receipts`` yes; ``SMITH 2020`` no."""
    match = YEAR_SEGMENT_RE.match(str(segment or "").strip())
    return int(match.group(1)) if match else None


def is_generic_segment(segment: str | None) -> bool:
    """Is this a folder name that carries no filing meaning in either system?"""
    return str(segment or "").strip().lower() in GENERIC_SEGMENTS


def read_taxdome_source(segments: list[str]) -> dict[str, Any]:
    """Read a TaxDome folder path: ``<CLIENT>/<PROVENANCE?>/<YEAR?>/...``."""
    reading: dict[str, Any] = {"taxonomy": "taxdome", "client_segment": None, "raw_category": None,
                               "category": None, "year": None, "operational": False,
                               "segments": list(segments)}
    if not segments:
        return reading
    reading["client_segment"] = segments[0]
    for segment in segments[1:]:
        lowered = segment.strip().lower()
        if reading["category"] is None and lowered in TAXDOME_CATEGORY_MAP:
            reading["raw_category"] = segment
            reading["category"] = TAXDOME_CATEGORY_MAP[lowered]
    years = [y for y in (segment_year(s) for s in segments[1:]) if y is not None]
    if len(set(years)) == 1:
        reading["year"] = years[0]
    elif len(set(years)) > 1:
        reading["year"] = "conflict"
    return reading


def read_sharepoint_source(segments: list[str]) -> dict[str, Any]:
    """Read a SharePoint folder path, refusing operational roots outright."""
    reading: dict[str, Any] = {"taxonomy": None, "client_segment": None, "raw_category": None,
                               "category": None, "year": None, "operational": False,
                               "segments": list(segments)}
    if not segments:
        return reading
    root = segments[0].strip().lower()
    if root in SHAREPOINT_OPERATIONAL_ROOTS:
        reading["operational"] = True
        return reading
    spec = SHAREPOINT_CLIENT_ROOTS.get(root)
    if spec is None:
        # An unrecognised root is not evidence. It is not refused as operational either — it is
        # simply unknown structure, and unknown structure files nothing.
        return reading
    reading["taxonomy"] = f"sharepoint:{spec['kind']}"
    raw_category = segments[spec["category_index"]] if len(segments) > spec["category_index"] else None
    if raw_category is not None:
        mapped = SHAREPOINT_CATEGORY_MAP.get(raw_category.strip().lower())
        if mapped:
            reading["raw_category"] = raw_category
            reading["category"] = mapped
    client = segments[spec["client_index"]] if len(segments) > spec["client_index"] else None
    if client is not None and not is_generic_segment(client) and segment_year(client) is None:
        reading["client_segment"] = client
    years = [y for y in (segment_year(s) for s in segments[1:]) if y is not None]
    if len(set(years)) == 1:
        reading["year"] = years[0]
    elif len(set(years)) > 1:
        reading["year"] = "conflict"
    return reading


def read_source(source: dict) -> dict[str, Any]:
    """Normalize one ``document_sources`` row into filing evidence. Pure; no database access."""
    system = source.get("source_system") or ""
    segments = path_segments(source.get("source_uri"), source.get("source_path"))
    if system == "TaxDome Drive":
        reading = read_taxdome_source(segments)
    elif system == "SharePoint":
        reading = read_sharepoint_source(segments)
    else:
        reading = {"taxonomy": None, "client_segment": None, "raw_category": None, "category": None,
                   "year": None, "operational": False, "segments": list(segments)}
    reading.update({
        "source_system": system,
        "source_id": source.get("id"),
        "source_external_id": source.get("source_external_id"),
        "source_uri": source.get("source_uri"),
        "source_path": source.get("source_path"),
        "available": bool(source.get("available")),
    })
    return reading


# --- client scope --------------------------------------------------------------------------------

def entity_display_name(entity: dict | None) -> str | None:
    """A client entity's name, tolerating the corpus's missing ``full_name``.

    254 active person-owned documents belong to people who exist, are active, and have first and last
    names — but a NULL ``full_name``. That is a data-quality gap in one column, NOT an ownership
    problem, and treating it as one wrongly reported 254 perfectly good client documents as having a
    conflicting owner. Only an entity with no derivable name at all is unusable here.
    """
    if not entity:
        return None
    for candidate in (entity.get("full_name"), entity.get("name")):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    first, last = entity.get("first_name"), entity.get("last_name")
    joined = " ".join(part.strip() for part in (first, last) if part and str(part).strip())
    return joined or None


def client_scope(row: dict, *, people_by_id: dict, households_by_id: dict,
                 organizations_by_id: dict) -> dict[str, Any]:
    """The filing scope implied by EXISTING ownership. Never infers, never guesses.

    Returns a ``filing_scope_state`` of:

    ``resolved``    exactly one ownership scope, naming an entity we can file under
    ``conflict``    more than one scope set, or a scope pointing at an entity that does not exist —
                    the database disagrees with itself about who the client is
    ``unresolved``  no ownership scope at all

    These three are the FILING view and are deliberately not the same partition as the raw database
    ownership census (see :func:`corpus_totals`): a multi-scope document is OWNED in the database and
    UNFILEABLE here, and both statements are true at once.
    """
    person_id = row.get("person_id")
    household_id = row.get("household_id")
    organization_id = row.get("organization_id")
    present = [k for k, v in (("person", person_id), ("household", household_id),
                              ("organization", organization_id)) if v is not None]

    person = people_by_id.get(person_id) if person_id is not None else None
    household = households_by_id.get(household_id) if household_id is not None else None
    organization = organizations_by_id.get(organization_id) if organization_id is not None else None

    scope: dict[str, Any] = {
        "current_person_id": person_id,
        "current_person_name": entity_display_name(person),
        "current_household_id": household_id,
        "current_household_name": entity_display_name(household),
        "current_organization_id": organization_id,
        "current_organization_name": entity_display_name(organization),
        "proposed_scope_type": None,
        "proposed_scope_id": None,
        "proposed_scope_name": None,
        "proposed_client_scope": None,
        "filing_scope_state": "unresolved",
        "conflicts": [],
    }
    if len(present) > 1:
        scope["filing_scope_state"] = "conflict"
        scope["conflicts"].append(f"multiple ownership scopes set: {'+'.join(present)}")
        return scope
    if not present:
        return scope

    kind = present[0]
    entity, scope_id = ({"person": (person, person_id), "household": (household, household_id),
                         "organization": (organization, organization_id)}[kind])
    scope.update({"proposed_scope_type": kind, "proposed_scope_id": scope_id,
                  "proposed_scope_name": entity_display_name(entity)})
    if scope["proposed_scope_name"]:
        scope["proposed_client_scope"] = f"{kind}:{scope_id}"
        scope["filing_scope_state"] = "resolved"
    else:
        # The scope id is set but names nothing that exists — the database points at a client who is
        # not there. That is a conflict, not merely a missing label.
        scope["filing_scope_state"] = "conflict"
        scope["conflicts"].append(f"{kind} {scope_id} does not resolve to a known entity")
    return scope


def _scope_name_tokens(scope: dict, *, people_by_id: dict, household_members: dict) -> tuple[
        frozenset, list[frozenset]]:
    """(owner tokens, household-member token sets) for client-context comparison."""
    kind, scope_id = scope["proposed_scope_type"], scope["proposed_scope_id"]
    owner: frozenset = frozenset()
    members: list[frozenset] = []
    if kind == "person":
        person = people_by_id.get(scope_id) or {}
        owner = person_name_tokens(person.get("first_name"), person.get("last_name")) or frozenset()
        for member in household_members.get(person.get("household_id"), ()):  # same household
            tokens = person_name_tokens(member.get("first_name"), member.get("last_name"))
            if tokens:
                members.append(tokens)
    elif kind == "household":
        for member in household_members.get(scope_id, ()):
            tokens = person_name_tokens(member.get("first_name"), member.get("last_name"))
            if tokens:
                members.append(tokens)
        owner = frozenset()
    else:
        owner = frozenset(ascii_tokens(scope.get("proposed_scope_name")))
    return owner, members


def client_context(segment: str | None, *, owner_tokens: frozenset,
                   member_token_sets: list[frozenset], known_clients_by_token=None) -> str:
    """How a source path's client folder relates to the document's owner.

    ``owner``               the folder names the owner
    ``household_member``    the folder names another member of the SAME household — normal filing,
                            not a conflict. A joint return lives in one folder, and treating
                            ``BROWN, TERRY AND CARLA`` as a conflict for Carla's document would
                            reject the corpus's most ordinary filing pattern.
    ``different_client``    the folder names a DIFFERENT known client — a real conflict, and the
                            distinction this function exists to draw
    ``unknown``             the folder names nobody we can identify — no evidence either way
    """
    if not segment:
        return "unknown"
    tokens = set(ascii_tokens(segment))
    if owner_tokens and owner_tokens <= tokens:
        return "owner"
    for member in member_token_sets:
        if member <= tokens:
            return "household_member"
    # Only a name we can positively identify as SOMEBODY ELSE'S is a conflict. An unrecognised
    # folder name is silence, not disagreement.
    for token in tokens:
        for candidate, _label in (known_clients_by_token or {}).get(token, ()):
            if candidate <= tokens and candidate != owner_tokens:
                return "different_client"
    return "unknown"


# --- the decision --------------------------------------------------------------------------------

def _category_from_sources(readings: list[dict]) -> dict[str, Any]:
    """The normalized category the AVAILABLE client-taxonomy sources agree on, plus any conflict.

    Provenance and service line are DIFFERENT DIMENSIONS, not competing answers. A document that
    TaxDome records under "Client uploaded documents" and SharePoint records under "Tax Preparation"
    is not a document with two categories — it is a tax-preparation document that the client
    uploaded. Reading that as a conflict would suppress the real filing category on the large
    population held in both systems, so only two REAL categories disagreeing is a conflict; the
    provenance bucket is kept as evidence and steps aside when a service line is present.
    """
    usable = [r for r in readings if r["available"] and r["category"]]
    stale = [r for r in readings if not r["available"] and r["category"]]
    service = sorted({r["category"] for r in usable
                      if r["category"] not in PROVENANCE_CATEGORIES})
    provenance = sorted({r["category"] for r in usable if r["category"] in PROVENANCE_CATEGORIES})

    out: dict[str, Any] = {
        "category": None, "source": None, "conflict": None,
        "raw_categories": sorted({str(r["raw_category"]) for r in usable if r["raw_category"]}),
        "provenance_categories": provenance,
        "stale_categories": sorted({str(r["category"]) for r in stale}),
    }
    chosen: str | None = None
    if len(service) > 1:
        out["conflict"] = "available sources disagree on category: " + ", ".join(service)
    elif len(service) == 1:
        chosen = service[0]
    elif len(provenance) > 1:
        out["conflict"] = "available sources disagree on category: " + ", ".join(provenance)
    elif len(provenance) == 1:
        chosen = provenance[0]

    if chosen:
        out["category"] = chosen
        out["source"] = "+".join(sorted({r["taxonomy"] for r in usable
                                         if r["category"] == chosen}))
    return out


def _year_evidence(row: dict, readings: list[dict]) -> dict[str, Any]:
    """Tax-year evidence recorded per independent source, then reconciled.

    Three independent signals: a year already recorded in tags, the filename (via the canonical
    extractor, which already rejects hash fragments and scanner timestamps), and the source folder
    paths. Disagreement is reported, never resolved by preference.
    """
    tags = row.get("tags") if isinstance(row.get("tags"), dict) else {}
    evidence: dict[str, Any] = {}

    recorded = (tags or {}).get("tax_year") or (tags or {}).get("year")
    if recorded is not None and str(recorded).strip()[:4].isdigit():
        evidence["tag"] = int(str(recorded).strip()[:4])

    filename_years = sorted(set(extract_years(row.get("original_name"))))
    if len(filename_years) == 1:
        evidence["filename"] = filename_years[0]
    elif len(filename_years) > 1:
        evidence["filename"] = "conflict"

    path_years = sorted({r["year"] for r in readings
                         if r["available"] and isinstance(r["year"], int)})
    if any(r["available"] and r["year"] == "conflict" for r in readings):
        evidence["source_path"] = "conflict"
    elif len(path_years) == 1:
        evidence["source_path"] = path_years[0]
    elif len(path_years) > 1:
        evidence["source_path"] = "conflict"

    values = [v for v in evidence.values() if isinstance(v, int)]
    # A RESOLVED year short-circuits the vote. ``documents.tax_year`` is not a fourth signal: it is
    # the adjudicated outcome of a validated resolution process, and the evidence that produced it
    # is the document's own content. Letting it agree with the raw signals would count one piece of
    # evidence twice and manufacture "two independent signals" out of one — see
    # :mod:`app.services.document_tax_year_resolution`. So it decides, and the raw signals are
    # reported beside it rather than combined with it.
    resolved = row.get("tax_year")
    if resolved is not None and str(resolved).strip()[:4].isdigit():
        resolved = int(str(resolved).strip()[:4])
        evidence["resolved"] = resolved
        # Silence is not safety: a raw signal that contradicts the resolved year is recorded so a
        # reviewer sees it, even though the resolution is what the filing gate acts on.
        disagreeing = sorted(k for k, v in evidence.items()
                             if k != "resolved" and isinstance(v, int) and v != resolved)
        if disagreeing:
            evidence["resolved_conflicts_with"] = disagreeing
        return {"year": resolved, "confidence": "strong", "source": "resolved",
                "evidence": evidence}

    distinct = sorted(set(values))
    if "conflict" in evidence.values() or len(distinct) > 1:
        return {"year": None, "confidence": "conflict", "source": None, "evidence": evidence}
    if not distinct:
        return {"year": None, "confidence": "none", "source": None, "evidence": evidence}
    agreeing = sorted(k for k, v in evidence.items() if v == distinct[0])
    # STRONG only when a recorded tag says so, or two independent signals agree. One signal alone is
    # moderate — good enough to report, never good enough to put a year in an auto-filed path.
    confidence = "strong" if ("tag" in agreeing or len(agreeing) > 1) else "moderate"
    return {"year": distinct[0], "confidence": confidence, "source": "+".join(agreeing),
            "evidence": evidence}


def _filing_decision(*, scope: dict, category: dict, year: dict, readings: list[dict],
                     contexts: list[str], excluded: bool, doc_type: str,
                     type_confidence: float) -> dict[str, Any]:
    """Filing status, confidence, reasons and conflicts. Conservative by construction."""
    reasons: list[str] = []
    conflicts: list[str] = list(scope["conflicts"])

    if excluded:
        return {"filing_status": "UNRESOLVED", "filing_confidence": 0.0,
                "reasons": ["excluded_nonclient"], "conflicts": conflicts}

    if conflicts:
        return {"filing_status": "UNRESOLVED", "filing_confidence": 0.0,
                "reasons": ["conflicting_ownership_context"], "conflicts": conflicts}

    if not scope["proposed_client_scope"]:
        return {"filing_status": "UNRESOLVED", "filing_confidence": 0.0,
                "reasons": ["no_current_owner"], "conflicts": conflicts}

    available = [r for r in readings if r["available"]]
    client_taxonomy = [r for r in available if r["taxonomy"]]

    if any(c == "different_client" for c in contexts):
        conflicts.append("a source path names a different known client")

    if category["conflict"]:
        conflicts.append(category["conflict"])

    if year["confidence"] == "conflict":
        conflicts.append("tax-year signals disagree: "
                         + ", ".join(f"{k}={v}" for k, v in sorted(year["evidence"].items())))

    # A classifier that disagrees with the filing category is surfaced, never hidden — but it is a
    # REASON to look, not a conflict about the destination, because the destination comes from the
    # source taxonomy and not from the classifier. The type itself lives in the proposal fields, so
    # the reason stays a stable code rather than fragmenting the census into one bucket per form.
    if doc_type != "unknown" and category["category"] in PROVENANCE_CATEGORIES:
        reasons.append("classifier_disagrees_with_provenance_category")

    if not client_taxonomy:
        reasons.append("operational_paths_only" if any(r["operational"] for r in available)
                       else "no_client_taxonomy_source")
        return {"filing_status": "UNRESOLVED",
                "filing_confidence": 0.0, "reasons": reasons, "conflicts": conflicts}

    # A path naming a DIFFERENT client is a disagreement about WHO the client is, not about where to
    # file, and this preview does not adjudicate ownership. It outranks every other conflict.
    if any(c == "different_client" for c in contexts) and not any(c == "owner" for c in contexts):
        reasons.append("conflicting_client_context")
        return {"filing_status": "UNRESOLVED", "filing_confidence": 0.0,
                "reasons": reasons, "conflicts": conflicts}

    # SEVERAL plausible categories is a decision for a human — the evidence is real, it just points
    # two ways. NO category is a different thing entirely: there is nothing to review.
    if category["conflict"]:
        reasons.append("multiple_plausible_categories")
        return {"filing_status": "REVIEW_REQUIRED", "filing_confidence": 0.3,
                "reasons": reasons, "conflicts": conflicts}

    if not category["category"]:
        reasons.append("no_trustworthy_category")
        return {"filing_status": "UNRESOLVED", "filing_confidence": 0.0,
                "reasons": reasons, "conflicts": conflicts}

    if conflicts:
        reasons.append("conflicting_filing_evidence")
        return {"filing_status": "REVIEW_REQUIRED", "filing_confidence": 0.3,
                "reasons": reasons, "conflicts": conflicts}

    if category["category"] in PROVENANCE_CATEGORIES:
        reasons.append("provenance_category_only")
        return {"filing_status": "REVIEW_REQUIRED", "filing_confidence": 0.45,
                "reasons": reasons, "conflicts": conflicts}

    if not any(c in ("owner", "household_member") for c in contexts):
        reasons.append("no_client_confirmation_in_path")
        return {"filing_status": "REVIEW_REQUIRED", "filing_confidence": 0.5,
                "reasons": reasons, "conflicts": conflicts}

    confidence = 0.9 if year["confidence"] == "strong" else 0.8
    if any(c == "household_member" for c in contexts) and not any(c == "owner" for c in contexts):
        reasons.append("household_member_context")
        confidence -= 0.05
    if doc_type == "unknown" or type_confidence < 0.7:
        reasons.append("weak_document_type")
    return {"filing_status": "AUTO_FILE_SAFE", "filing_confidence": round(confidence, 2),
            "reasons": reasons, "conflicts": conflicts}


def evaluate_document(row: dict, sources: list[dict], *, people_by_id: dict, households_by_id: dict,
                      organizations_by_id: dict, household_members: dict,
                      known_clients_by_token: dict | None = None,
                      ocr_text: str | None = None) -> dict[str, Any]:
    """The full filing proposal for ONE document. Pure: no database access, no writes."""
    readings = sorted((read_source(s) for s in sources),
                      key=lambda r: (not r["available"], r["source_system"] or "",
                                     r["source_id"] or 0))
    scope = client_scope(row, people_by_id=people_by_id, households_by_id=households_by_id,
                         organizations_by_id=organizations_by_id)
    owner_tokens, member_tokens = _scope_name_tokens(
        scope, people_by_id=people_by_id, household_members=household_members)
    contexts = [client_context(r["client_segment"], owner_tokens=owner_tokens,
                               member_token_sets=member_tokens,
                               known_clients_by_token=known_clients_by_token)
                for r in readings if r["available"] and r["taxonomy"] and r["client_segment"]]

    category = _category_from_sources(readings)
    year = _year_evidence(row, readings)
    doc_type, type_confidence = classify_document(row.get("original_name"), ocr_text)
    excluded = (row.get("review_status") or "") == EXCLUDED_NONCLIENT_REVIEW_STATUS

    decision = _filing_decision(scope=scope, category=category, year=year, readings=readings,
                                contexts=contexts, excluded=excluded, doc_type=doc_type,
                                type_confidence=type_confidence)

    segments: list[str] = []
    if decision["filing_status"] in ("AUTO_FILE_SAFE", "REVIEW_REQUIRED") \
            and scope["proposed_scope_name"] and category["category"]:
        segments = [scope["proposed_scope_name"], category["category"]]
        # A year joins the PATH only when independently corroborated. A moderate year is reported in
        # the proposal and deliberately left out of the destination.
        if year["confidence"] == "strong" and year["year"]:
            segments.append(str(year["year"]))

    primary = readings[0] if readings else {}
    current_display = document_display_name(row)
    proposed_display = safe_document_label(row, owner=scope.get("proposed_scope_name"))

    canonical_year = infer_tax_year(row)
    evidence = {
        "sources": [{"source_id": r["source_id"], "system": r["source_system"],
                     "available": r["available"], "taxonomy": r["taxonomy"],
                     "client_segment": r["client_segment"], "raw_category": r["raw_category"],
                     "category": r["category"], "year": r["year"],
                     "operational": r["operational"], "segments": r["segments"]}
                    for r in readings],
        "client_contexts": sorted(Counter(contexts).items()),
        "raw_categories": category["raw_categories"],
        "stale_categories": category["stale_categories"],
        "classifier": {"version": CLASSIFIER_VERSION, "type": doc_type,
                       "confidence": type_confidence, "used_ocr_text": ocr_text is not None},
        "canonical_tax_year": {"year": canonical_year.year, "confidence": canonical_year.confidence,
                               "source": canonical_year.source},
    }

    return {
        "document_id": row.get("id"),
        "original_name": row.get("original_name") or "",
        "current_display_name": current_display,
        "source": primary.get("source_system"),
        "source_id": primary.get("source_id"),
        "source_identity": primary.get("source_external_id"),
        "source_uri": primary.get("source_uri"),
        "source_path": primary.get("source_path"),
        "current_person_id": scope["current_person_id"],
        "current_person_name": scope["current_person_name"],
        "current_household_id": scope["current_household_id"],
        "current_household_name": scope["current_household_name"],
        "current_organization_id": scope["current_organization_id"],
        "current_organization_name": scope["current_organization_name"],
        "proposed_client_scope": scope["proposed_client_scope"],
        "proposed_scope_type": scope["proposed_scope_type"],
        "proposed_scope_id": scope["proposed_scope_id"],
        "proposed_scope_name": scope["proposed_scope_name"],
        "filing_scope_state": scope["filing_scope_state"],
        "proposed_folder_segments": segments,
        "proposed_folder_path": "/".join(segments) if segments else None,
        "proposed_top_level_category": category["category"],
        "category_source": category["source"],
        "category_confidence": 0.9 if category["category"] and category["source"] else 0.0,
        "proposed_document_type": doc_type if doc_type != "unknown" else None,
        "document_type_source": CLASSIFIER_VERSION if doc_type != "unknown" else None,
        "document_type_confidence": type_confidence,
        "proposed_tax_year": year["year"],
        "tax_year_source": year["source"],
        "tax_year_confidence": year["confidence"],
        "tax_year_evidence": year["evidence"],
        "proposed_display_name": proposed_display,
        "display_name_source": "document_naming.safe_document_label",
        "display_name_changes": bool(proposed_display and proposed_display != current_display),
        "filing_status": decision["filing_status"],
        "filing_confidence": decision["filing_confidence"],
        "reasons": decision["reasons"],
        "conflicts": decision["conflicts"],
        "evidence": evidence,
    }


# --- corpus-wide preview -------------------------------------------------------------------------

_ACTIVE_CLAUSE = ("d.status <> 'deleted' AND d.deleted_at IS NULL AND d.archived = false")

_DOCUMENTS_SQL = f"""
    SELECT d.id, d.original_name, d.display_name, d.storage_path, d.tags, d.category,
           d.review_status, d.person_id, d.household_id, d.organization_id, d.effective_date,
           d.tax_year
      FROM documents d
     WHERE {_ACTIVE_CLAUSE}
     ORDER BY d.id
"""

_SOURCES_SQL = """
    SELECT document_id, id, source_system, source_uri, source_path, source_external_id, available
      FROM document_sources
     ORDER BY document_id, id
"""


def build_known_client_index(people: list[dict], organizations: list[dict]) -> dict[str, list]:
    """Inverted index token -> [(client tokens, label)], for spotting a DIFFERENT client in a path.

    Inverted rather than a flat list purely for cost: a linear scan would be thousands of set
    comparisons per folder segment across 73k documents. Only multi-token names are indexed — a
    single-token client name would match half the corpus's folders by accident.
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
        tokens = frozenset(ascii_tokens(organization.get("name")))
        if len(tokens) < 2 or tuple(sorted(tokens)) in seen:
            continue
        seen.add(tuple(sorted(tokens)))
        for token in tokens:
            index.setdefault(token, []).append((tokens, organization.get("name")))
    return index


def _reference_data(conn) -> dict[str, Any]:
    people_by_id, household_members = {}, {}
    people_rows = [dict(p) for p in conn.execute(text(
        "SELECT id, first_name, last_name, full_name, household_id FROM people ORDER BY id"
    )).mappings()]
    for person in people_rows:
        people_by_id[person["id"]] = person
        if person["household_id"] is not None:
            household_members.setdefault(person["household_id"], []).append(person)
    households_by_id = {h["id"]: dict(h) for h in conn.execute(text(
        "SELECT id, name FROM households ORDER BY id")).mappings()}
    organization_rows = [dict(o) for o in conn.execute(text(
        "SELECT id, name FROM relationship_entities ORDER BY id")).mappings()]
    organizations_by_id = {o["id"]: o for o in organization_rows}
    return {"people_by_id": people_by_id, "households_by_id": households_by_id,
            "organizations_by_id": organizations_by_id, "household_members": household_members,
            "known_clients_by_token": build_known_client_index(people_rows, organization_rows)}


def build_preview(conn=None, *, document_ids=None, person_id=None, household_id=None,
                  organization_id=None, source=None, limit=None,
                  with_ocr_text=False) -> list[dict]:
    """The filing proposal for every active document that matches the filters. READ-ONLY.

    Deterministically ordered by document id. Filters narrow WHICH documents are evaluated; they
    never change how one is evaluated, so a filtered run and a full run agree row for row.
    """
    def _run(c):
        reference = _reference_data(c)
        sources_by_doc: dict[int, list[dict]] = {}
        for s in c.execute(text(_SOURCES_SQL)).mappings():
            sources_by_doc.setdefault(s["document_id"], []).append(dict(s))

        rows = [dict(r) for r in c.execute(text(_DOCUMENTS_SQL)).mappings()]
        wanted = set(document_ids) if document_ids else None
        selected = []
        for row in rows:
            if wanted is not None and row["id"] not in wanted:
                continue
            if person_id is not None and row["person_id"] != person_id:
                continue
            if household_id is not None and row["household_id"] != household_id:
                continue
            if organization_id is not None and row["organization_id"] != organization_id:
                continue
            if source is not None and not any(
                    s["source_system"] == source for s in sources_by_doc.get(row["id"], ())):
                continue
            selected.append(row)
            if limit is not None and len(selected) >= limit:
                break

        ocr_by_doc: dict[int, str] = {}
        if with_ocr_text and selected:
            ids = [r["id"] for r in selected]
            for o in c.execute(text("SELECT document_id, text FROM document_ocr "
                                    "WHERE document_id = ANY(:ids) AND text IS NOT NULL"),
                               {"ids": ids}).mappings():
                ocr_by_doc.setdefault(o["document_id"], o["text"])

        return [evaluate_document(row, sources_by_doc.get(row["id"], []),
                                  ocr_text=ocr_by_doc.get(row["id"]), **reference)
                for row in sorted(selected, key=lambda r: r["id"])]

    if conn is not None:
        return _run(conn)
    with engine.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        return _run(c)


# --- census --------------------------------------------------------------------------------------

def _counter(values) -> dict:
    """Frequency map, most frequent first, ties broken alphabetically.

    Keys are stringified — ``None`` becomes ``"<none>"`` — because a breakdown mixing ``None`` with
    labels cannot be sorted or JSON-serialised, and "no category" is itself a finding a reviewer
    needs to see counted rather than dropped.
    """
    counts = Counter("<none>" if v is None else str(v) for v in values)
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def summarize(proposals: list[dict], *, totals: dict | None = None) -> dict[str, Any]:
    """The census a reviewer reads. Deterministic: every breakdown is sorted."""
    resolved = [p for p in proposals if p["filing_scope_state"] == "resolved"]
    conflict = [p for p in proposals if p["filing_scope_state"] == "conflict"]
    unresolved_scope = [p for p in proposals if p["filing_scope_state"] == "unresolved"]
    excluded = [p for p in proposals if "excluded_nonclient" in p["reasons"]]
    by_status = _counter(p["filing_status"] for p in proposals)

    summary: dict[str, Any] = {
        "preview_version": PREVIEW_VERSION,
        "classifier_version": CLASSIFIER_VERSION,
        # The RAW production ownership census, straight from the columns. Reported separately and
        # named unmistakably so it can never be read as a filing number, or vice versa.
        "database_ownership": (totals or {}).get("database_ownership"),
        "census": {
            "TOTAL_DOCUMENTS": (totals or {}).get("total_documents"),
            "TOTAL_ACTIVE_DOCUMENTS": (totals or {}).get("total_active_documents"),
            "EVALUATED": len(proposals),
            "AUTO_FILE_SAFE": by_status.get("AUTO_FILE_SAFE", 0),
            "REVIEW_REQUIRED": by_status.get("REVIEW_REQUIRED", 0),
            "UNRESOLVED": by_status.get("UNRESOLVED", 0),
            # FILING scope, not database ownership. These three partition EVALUATED exactly.
            "FILING_SCOPE_RESOLVED": len(resolved),
            "FILING_SCOPE_CONFLICT": len(conflict),
            "FILING_SCOPE_UNRESOLVED": len(unresolved_scope),
            "FILING_SCOPE_RESOLVED_AUTO_FILE_SAFE": sum(
                1 for p in resolved if p["filing_status"] == "AUTO_FILE_SAFE"),
            "FILING_SCOPE_RESOLVED_REVIEW_REQUIRED": sum(
                1 for p in resolved if p["filing_status"] == "REVIEW_REQUIRED"),
            "FILING_SCOPE_RESOLVED_UNRESOLVED": sum(
                1 for p in resolved if p["filing_status"] == "UNRESOLVED"),
            "EXCLUDED_NONCLIENT": len(excluded),
        },
        "by_source": _counter(p["source"] for p in proposals),
        "by_filing_scope_state": _counter(p["filing_scope_state"] for p in proposals),
        "by_scope_type": _counter(p["proposed_scope_type"] for p in proposals),
        "by_filing_status": by_status,
        "by_filing_confidence": _counter(p["filing_confidence"] for p in proposals),
        "by_proposed_category": _counter(p["proposed_top_level_category"] for p in proposals),
        "by_proposed_document_type": _counter(p["proposed_document_type"] for p in proposals),
        "by_proposed_tax_year": _counter(p["proposed_tax_year"] for p in proposals),
        "by_category_source": _counter(p["category_source"] for p in proposals),
        "by_tax_year_source": _counter(p["tax_year_source"] for p in proposals),
        "by_tax_year_confidence": _counter(p["tax_year_confidence"] for p in proposals),
        "review_reasons": _counter(
            r for p in proposals if p["filing_status"] == "REVIEW_REQUIRED" for r in p["reasons"]),
        "unresolved_reasons": _counter(
            r for p in proposals if p["filing_status"] == "UNRESOLVED" for r in p["reasons"]),
        "conflicts": _counter(c for p in proposals for c in p["conflicts"]),
        "path_shapes": _counter(
            "/".join(["<CLIENT>", *p["proposed_folder_segments"][1:]])
            for p in proposals if p["proposed_folder_segments"]),
        "auto_file_safe_by_source": _counter(
            p["source"] for p in proposals if p["filing_status"] == "AUTO_FILE_SAFE"),
        "auto_file_safe_by_scope_type": _counter(
            p["proposed_scope_type"] for p in proposals if p["filing_status"] == "AUTO_FILE_SAFE"),
    }
    return summary


def database_ownership_census(conn) -> dict[str, int]:
    """The RAW database ownership census for active documents. Straight column counts, no judgement.

    This is the production ownership number — what the ownership batches move — and it is NOT the
    filing-scope partition. A document with both ``person_id`` and ``household_id`` is counted here
    as OWNED, because it is; the filing view separately calls it a conflict, because it cannot be
    filed. Keeping the two apart is the whole point of this function existing: the counts differ, and
    each is right about its own question.

    ``PERSON_ONLY + HOUSEHOLD_ONLY + ORGANIZATION_ONLY + MULTI_SCOPE + NO_SCOPE == TOTAL_ACTIVE`` and
    ``DATABASE_OWNED_ACTIVE + DATABASE_UNOWNED_ACTIVE == TOTAL_ACTIVE``, both asserted by tests.
    """
    def scalar(where=""):
        return conn.execute(text(
            f"SELECT count(*) FROM documents d WHERE {_ACTIVE_CLAUSE}{where}")).scalar() or 0

    person = " AND d.person_id IS NOT NULL"
    household = " AND d.household_id IS NOT NULL"
    organization = " AND d.organization_id IS NOT NULL"
    no_person = " AND d.person_id IS NULL"
    no_household = " AND d.household_id IS NULL"
    no_organization = " AND d.organization_id IS NULL"
    return {
        "TOTAL_ACTIVE": scalar(),
        "DATABASE_OWNED_ACTIVE": scalar(
            " AND (d.person_id IS NOT NULL OR d.household_id IS NOT NULL "
            "OR d.organization_id IS NOT NULL)"),
        "DATABASE_UNOWNED_ACTIVE": scalar(no_person + no_household + no_organization),
        "PERSON_ONLY": scalar(person + no_household + no_organization),
        "HOUSEHOLD_ONLY": scalar(household + no_person + no_organization),
        "ORGANIZATION_ONLY": scalar(organization + no_person + no_household),
        "MULTI_SCOPE": scalar(
            " AND ((d.person_id IS NOT NULL)::int + (d.household_id IS NOT NULL)::int"
            " + (d.organization_id IS NOT NULL)::int) > 1"),
        "NO_SCOPE": scalar(no_person + no_household + no_organization),
    }


def corpus_totals(conn) -> dict[str, Any]:
    """Whole-corpus counts, independent of any filter the preview was run with."""
    def scalar(sql):
        return conn.execute(text(sql)).scalar() or 0
    return {
        "total_documents": scalar("SELECT count(*) FROM documents"),
        "total_active_documents": scalar(f"SELECT count(*) FROM documents d WHERE {_ACTIVE_CLAUSE}"),
        "excluded_nonclient_active": scalar(
            f"SELECT count(*) FROM documents d WHERE {_ACTIVE_CLAUSE} "
            f"AND d.review_status = '{EXCLUDED_NONCLIENT_REVIEW_STATUS}'"),
        "database_ownership": database_ownership_census(conn),
    }


# --- taxonomy census -----------------------------------------------------------------------------

def _taxonomy_census(rows, reader) -> dict[str, Any]:
    l1, l2, shapes, depth, year_pos, generic = Counter(), Counter(), Counter(), Counter(), \
        Counter(), Counter()
    parsed = 0
    for row in rows:
        segments = path_segments(row.get("source_uri"), row.get("source_path"))
        if not segments:
            continue
        parsed += 1
        depth[len(segments)] += 1
        l1[segments[0]] += 1
        if len(segments) > 1:
            l2[segments[1]] += 1
        shape = []
        for i, segment in enumerate(segments):
            if segment_year(segment) is not None:
                year_pos[i] += 1
                shape.append("<YEAR>")
            elif i == 0:
                shape.append("<CLIENT>" if reader == "taxdome" else segment)
            elif is_generic_segment(segment):
                generic[segment.strip().lower()] += 1
                shape.append("<GENERIC>")
            else:
                shape.append(segment)
        shapes["/".join(shape[:5])] += 1
    return {
        "references": len(rows),
        "parsed": parsed,
        "distinct_level_1": len(l1),
        "distinct_level_2": len(l2),
        "level_1_top": dict(l1.most_common(25)),
        "level_2_top": dict(l2.most_common(25)),
        "folder_depth": dict(sorted(depth.items())),
        "year_segment_positions": dict(sorted(year_pos.items())),
        "generic_segments": dict(generic.most_common(25)),
        "top_shapes": dict(shapes.most_common(30)),
        "distinct_shapes": len(shapes),
    }


def taxdome_taxonomy(conn) -> dict[str, Any]:
    """READ-ONLY census of every TaxDome source path — what hierarchy the corpus actually has."""
    rows = [dict(r) for r in conn.execute(text(
        "SELECT source_uri, source_path, available FROM document_sources "
        "WHERE source_system = 'TaxDome Drive' ORDER BY id")).mappings()]
    census = _taxonomy_census(rows, "taxdome")
    census["recognised_categories"] = dict(sorted(TAXDOME_CATEGORY_MAP.items()))
    census["available"] = sum(1 for r in rows if r["available"])
    return census


def sharepoint_taxonomy(conn) -> dict[str, Any]:
    """READ-ONLY census of every SharePoint source path, split by client vs operational root."""
    rows = [dict(r) for r in conn.execute(text(
        "SELECT source_uri, source_path, available FROM document_sources "
        "WHERE source_system = 'SharePoint' ORDER BY id")).mappings()]
    census = _taxonomy_census(rows, "sharepoint")
    client_refs = operational_refs = unknown_refs = 0
    for row in rows:
        segments = path_segments(row.get("source_uri"), row.get("source_path"))
        if not segments:
            continue
        root = segments[0].strip().lower()
        if root in SHAREPOINT_CLIENT_ROOTS:
            client_refs += 1
        elif root in SHAREPOINT_OPERATIONAL_ROOTS:
            operational_refs += 1
        else:
            unknown_refs += 1
    census.update({
        "available": sum(1 for r in rows if r["available"]),
        "client_root_references": client_refs,
        "operational_root_references": operational_refs,
        "unknown_root_references": unknown_refs,
        "client_roots": {v["label"]: v["kind"] for v in SHAREPOINT_CLIENT_ROOTS.values()},
        "operational_roots": sorted(SHAREPOINT_OPERATIONAL_ROOTS),
        "recognised_categories": dict(sorted(SHAREPOINT_CATEGORY_MAP.items())),
    })
    return census
