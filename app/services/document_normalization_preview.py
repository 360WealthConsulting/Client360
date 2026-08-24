"""Document name normalization PREVIEW — READ-ONLY (SELECT only; writes nothing, renames nothing).

Answers one question for the trusted document population: *what would these documents be called if
they were named consistently?* It computes a candidate ``YEAR - TYPE - OWNER`` display name for every
trusted document from data Client360 already holds — ``original_name``, ``category``, and the
person/household/organization owner — and buckets the result for human review.

Explicitly NOT done here: no database column is added, no document row is modified, no physical file is
renamed, no OCR is run, no source file or SharePoint/OneDrive content is read. The only I/O is a
handful of SELECT statements over an ``engine.connect()`` connection that is never given a transaction.

Buckets
    SAFE       owner + a confident, useful type; deterministic candidate; no collision for that owner.
    REVIEW     partial or ambiguous type, a collision, or the original filename holds detail the
               candidate would lose.
    UNCHANGED  the existing filename is already at least as clear as the candidate.
    SKIP       generic filename with insufficient metadata, unsupported/unknown type, or inconsistent
               ownership (more than one owner column populated).

The production census showed only 2% of filenames are generic, so most originals are informative. The
bucketing is deliberately conservative: when in doubt a row lands in REVIEW or UNCHANGED, never SAFE.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import select

from app.db import (
    documents,
    engine,
    households,
    people,
    relationship_entities,
)
from app.services.document_naming import (
    canonical_display_name,
    detect_foreign_person_token,
    detect_form_families,
    detect_version_markers,
    duplicate_suffix,
    extract_year,
    has_ambiguous_year,
    is_generic_filename,
    mentions_instructions,
    residual_qualifier,
    resolve_document_type,
    strip_extension,
    type_label,
)
from app.services.person_names import person_display_name

BUCKETS = ("SAFE", "REVIEW", "UNCHANGED", "SKIP")

#: A type must be at least this confident to allow SAFE. Below it the row goes to REVIEW.
SAFE_CONFIDENCE = 0.85
#: Types too coarse to justify overwriting an informative filename on their own.
_COARSE_TYPES = frozenset({"tax_documents", "financial_statement"})

_TRUSTED = (
    (documents.c.status != "deleted")
    & (documents.c.archived.is_(False))
)


def _owner_names(conn, doc_rows):
    """id -> display name for every owner referenced by the batch (three set-based reads)."""
    pids = {r["person_id"] for r in doc_rows if r["person_id"]}
    hids = {r["household_id"] for r in doc_rows if r["household_id"]}
    oids = {r["organization_id"] for r in doc_rows if r["organization_id"]}
    persons, homes, orgs = {}, {}, {}
    if pids:
        for r in conn.execute(select(people.c.id, people.c.full_name, people.c.first_name,
                                     people.c.last_name).where(people.c.id.in_(pids))).mappings():
            persons[r["id"]] = person_display_name(r["full_name"], r["first_name"], r["last_name"])
    if hids:
        homes = dict(conn.execute(select(households.c.id, households.c.name)
                                  .where(households.c.id.in_(hids))).all())
    if oids:
        orgs = dict(conn.execute(select(relationship_entities.c.id, relationship_entities.c.name)
                                 .where(relationship_entities.c.id.in_(oids))).all())
    return persons, homes, orgs


def _known_first_names(conn):
    """Lowercase first names that Client360 already knows belong to people. Used only to recognise a
    person name in a filename — it is what stops custodians such as "Fidelity" being read as people.
    One set-based read; no name dictionary, no external data."""
    return {n.strip().lower() for (n,) in conn.execute(
        select(people.c.first_name).where(people.c.first_name.isnot(None)).distinct())
        if n and n.strip()}


def _owner_of(row, persons, homes, orgs):
    """(owner_type, owner_id, owner_name, inconsistent). Ownership is inconsistent when more than one
    owner column is populated — such a row is never normalized, only reported."""
    populated = [k for k in ("organization_id", "household_id", "person_id") if row[k]]
    inconsistent = len(populated) > 1
    if row["organization_id"]:
        return "business", row["organization_id"], orgs.get(row["organization_id"]), inconsistent
    if row["household_id"]:
        return "household", row["household_id"], homes.get(row["household_id"]), inconsistent
    if row["person_id"]:
        return "person", row["person_id"], persons.get(row["person_id"]), inconsistent
    return None, None, None, inconsistent


def _source_system(row):
    tags = row["tags"]
    if isinstance(tags, dict) and tags.get("source_system"):
        return str(tags["source_system"])
    return row["storage_provider"] or "(unknown)"


def _original_already_clear(original_stem, candidate, *, year, type_code, entity):
    """True when the existing filename already says everything the candidate would.

    The candidate adds value only when it supplies something the original lacks. When the original
    already states the year, the document type AND the owner, renaming it would reorder words
    without adding information — pure churn across a population where 56% of filenames already
    carry a year. Such rows are left UNCHANGED.
    """
    if not candidate:
        return True
    stem_l = original_stem.lower()
    label = (type_label(type_code) or "").lower()
    has_year = bool(year) and str(year) in stem_l
    has_type = bool(label) and (label in stem_l or (type_code or "").lower() in stem_l)
    has_owner = any(w.lower() in stem_l for w in (entity or "").split() if len(w) > 2)
    return has_year and has_type and has_owner


def build_preview(*, limit=None, examples=50) -> dict:
    """Compute the read-only preview over the trusted population. Writes nothing."""
    rows_out = []
    counts = Counter()
    by_owner_type, by_doc_type, by_source = Counter(), Counter(), Counter()
    seen_names = defaultdict(set)          # (owner_type, owner_id) -> candidate names already used
    collisions = 0

    stmt = (select(documents.c.id, documents.c.original_name, documents.c.category,
                   documents.c.person_id, documents.c.household_id, documents.c.organization_id,
                   documents.c.tags, documents.c.storage_provider)
            .where(_TRUSTED,
                   documents.c.person_id.isnot(None)
                   | documents.c.household_id.isnot(None)
                   | documents.c.organization_id.isnot(None))
            .order_by(documents.c.id))
    if limit:
        stmt = stmt.limit(limit)

    with engine.connect() as conn:
        doc_rows = conn.execute(stmt).mappings().all()
        persons, homes, orgs = _owner_names(conn, doc_rows)
        first_names = _known_first_names(conn)

        for row in doc_rows:
            owner_type, owner_id, owner_name, inconsistent = _owner_of(row, persons, homes, orgs)
            original = row["original_name"] or ""
            stem = strip_extension(original)
            year = extract_year(original)
            match = resolve_document_type(row["category"], original)
            type_code, confidence, type_source = match.code, match.confidence, match.source
            qualifier = residual_qualifier(original, year=year, type_code=type_code,
                                           entity=owner_name, matched_text=match.matched_text)
            # Signals that must stop a candidate from being called safe, independent of type quality.
            version_markers = detect_version_markers(original)
            multi_form = len(detect_form_families(original)) > 1
            ambiguous_year = has_ambiguous_year(original)
            instructions = mentions_instructions(original)
            # Conservative possible-wrong-owner signal: person-owned documents only, leading token
            # only, and only when that token is a first name Client360 already knows. Never
            # reassigns ownership — it only asks a human to look.
            foreign_person = (detect_foreign_person_token(
                original, owner_name=owner_name, known_first_names=first_names)
                if owner_type == "person" else None)
            candidate = canonical_display_name(year=year, type_code=type_code, entity=owner_name,
                                               qualifier=qualifier)

            collision = False
            if candidate:
                key = (owner_type, owner_id)
                collision = candidate in seen_names[key]
                if collision:
                    # The filesystem duplicate marker is normally filler, but when its absence is
                    # what makes two documents identical it is the only distinguishing detail the
                    # filename has -- keep it rather than fall back to a document id.
                    dup = duplicate_suffix(original)
                    if dup:
                        with_dup = canonical_display_name(year=year, type_code=type_code,
                                                          entity=owner_name,
                                                          qualifier=" ".join(
                                                              x for x in (qualifier, dup) if x))
                        if with_dup and with_dup not in seen_names[key]:
                            candidate, qualifier = with_dup, " ".join(
                                x for x in (qualifier, dup) if x)
                seen_names[key].add(candidate)
            if collision:
                collisions += 1

            reasons = []
            if inconsistent:
                bucket = "SKIP"
                reasons.append("more than one owner column populated")
            elif not owner_name:
                bucket = "SKIP"
                reasons.append("owner could not be resolved to a name")
            elif type_code == "unknown" and is_generic_filename(original):
                bucket = "SKIP"
                reasons.append("generic filename and no resolvable document type")
            elif not candidate:
                bucket = "SKIP"
                reasons.append("no document type and no meaningful filename detail")
            elif _original_already_clear(stem, candidate, year=year, type_code=type_code,
                                         entity=owner_name):
                bucket = "UNCHANGED"
                reasons.append("existing filename already states the year, type and owner")
            elif collision:
                bucket = "REVIEW"
                reasons.append("candidate name collides with another document for the same owner")
            elif multi_form:
                bucket = "REVIEW"
                reasons.append("filename names more than one materially different form")
            elif version_markers:
                bucket = "REVIEW"
                reasons.append(
                    f"amendment/version semantics not representable: {', '.join(version_markers[:3])}")
            elif ambiguous_year:
                bucket = "REVIEW"
                reasons.append("more than one distinct year in the filename")
            elif instructions:
                bucket = "REVIEW"
                reasons.append("filename says 'instructions' — an instructions packet is not the form")
            elif foreign_person:
                bucket = "REVIEW"
                reasons.append(
                    f"filename leads with a person name that is not the owner ('{foreign_person}') "
                    f"— possible wrong owner")
            elif type_code == "unknown":
                bucket = "REVIEW"
                reasons.append("document type unresolved; candidate relies on filename detail only")
            elif confidence < SAFE_CONFIDENCE:
                bucket = "REVIEW"
                reasons.append(f"type confidence {confidence:.2f} below {SAFE_CONFIDENCE}")
            elif type_code in _COARSE_TYPES and qualifier:
                bucket = "REVIEW"
                reasons.append("type is coarse and the filename carries extra detail")
            elif qualifier and not year:
                bucket = "REVIEW"
                reasons.append("no year in filename and the original carries detail worth keeping")
            else:
                bucket = "SAFE"
                reasons.append(f"type from {type_source} (confidence {confidence:.2f}); owner resolved")

            counts[bucket] += 1
            by_owner_type[owner_type or "(unresolved)"] += 1
            by_doc_type[type_code] += 1
            by_source[_source_system(row)] += 1
            rows_out.append({
                "document_id": row["id"], "current_filename": original,
                "proposed_display_name": candidate, "owner": owner_name, "owner_type": owner_type,
                "owner_id": owner_id, "document_type": type_code, "type_label": type_label(type_code),
                "type_source": type_source, "confidence": confidence, "year": year,
                "qualifier": qualifier, "source_system": _source_system(row),
                "collision": collision, "version_markers": version_markers,
                "multi_form": multi_form, "ambiguous_year": ambiguous_year,
                "instructions": instructions, "foreign_person": foreign_person,
                "bucket": bucket, "reason": "; ".join(reasons),
            })

    def sample(bucket, n):
        return [r for r in rows_out if r["bucket"] == bucket][:n]

    return {
        "total_reviewed": len(rows_out),
        "counts": {b: counts[b] for b in BUCKETS},
        "by_owner_type": dict(by_owner_type.most_common()),
        "by_document_type": dict(by_doc_type.most_common()),
        "by_source_system": dict(by_source.most_common()),
        "collisions": collisions,
        "examples": {"SAFE": sample("SAFE", examples), "REVIEW": sample("REVIEW", 25),
                     "UNCHANGED": sample("UNCHANGED", 25), "SKIP": sample("SKIP", 25)},
        "rows": rows_out,
    }
