"""Filing evidence for a canonical document — PURE, READ-ONLY, never writes.

This is a COMPOSER, not a second filing engine. Every judgement it returns comes from an existing
Client360 component:

* category   → :func:`document_classification.classify_document` (the Knowledge-pipeline classifier)
* tax year   → :func:`document_tax_year.infer_tax_year` (filename + SharePoint year folders)
* owner      → the folder-name rules in :mod:`app.importers.taxdome_drive`, via household aliases

What this module adds is the part no existing component covers: normalising the several folder
spellings one household is filed under, attributing a document to a NAMED PERSON inside a household
that already owns it, and stating a policy for business relationships. It proposes; it never links.

The safety rules it enforces, each of which exists because the production audit found the failure:

* a phone last-4, a ZIP, an address or a bare surname can never identify an owner — the firm's own
  switchboard number on a letterhead put 2,157 documents on one person;
* a folder year and an explicit filename year that disagree are a REVIEW, never a silent choice;
* a document with no year evidence gets no year — Wealth Consulting account documents are largely
  yearless and must stay that way;
* person attribution never overrides household ownership; it annotates it.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from app.services.document_classification import classify_document
from app.services.document_tax_year import infer_tax_year, source_path_for

# --- household alias normalisation ---------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")
#: Dropped before comparing, so "White, Michael & Debra" and "Michael and Debra White" agree.
_ALIAS_NOISE = {"and", "the", "household", "family"}
#: A trailing "(1)" (or "(2)"…) is SharePoint's duplicate-folder shell, not a different client.
_SHELL_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")


def normalise_household_alias(folder: str | None) -> str:
    """Order-insensitive, case-insensitive key for a client folder name.

    ``WHITE, MICHAEL AND DEBRA``, ``White, Michael & Debra``, ``White, Michael & Debra(1)`` and
    ``Michael and Debra White`` all reduce to the same key, so the several spellings one household is
    filed under stop looking like several clients.
    """
    if not folder:
        return ""
    cleaned = _SHELL_SUFFIX_RE.sub("", str(folder))
    tokens = [t for t in _TOKEN_RE.findall(cleaned.lower()) if t not in _ALIAS_NOISE]
    return " ".join(sorted(tokens))


def is_duplicate_shell_folder(folder: str | None) -> bool:
    """True for SharePoint's ``…(1)`` duplicate-folder shell.

    These are empty copies left behind by the library. They must not raise a missing-document alert:
    the folder is a duplicate of one already ingested, not a tree Client360 failed to read.
    """
    return bool(folder) and _SHELL_SUFFIX_RE.search(str(folder)) is not None


def household_aliases_match(a: str | None, b: str | None) -> bool:
    """Do two folder spellings name the same household?"""
    key_a, key_b = normalise_household_alias(a), normalise_household_alias(b)
    return bool(key_a) and key_a == key_b


# --- person attribution inside an established household -------------------------------------------

#: Initial tokens the firm uses for individual household members inside shared filenames.
#: DLW is DELIBERATELY ABSENT: it is not unambiguously resolvable from the evidence available, and a
#: wrong attribution is worse than none. It must stay a human decision.
UNRESOLVED_PERSON_INITIALS = frozenset({"DLW"})

_INITIAL_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{3})(?![A-Za-z0-9])")


class PersonAttribution(NamedTuple):
    """A proposed person WITHIN an already-owning household. Annotation, never ownership."""

    initials: str | None
    person_name: str | None
    confidence: str            # "strong" | "unresolved" | "none"
    evidence: list[str]
    requires_review: bool


def attribute_person(filename: str | None, *, member_initials: dict[str, str],
                     household_owned: bool) -> PersonAttribution:
    """Attribute a document to a named household member from initials in its filename.

    ``member_initials`` maps initials to the member's name (e.g. ``{"MBW": "Michael Blaine White"}``)
    and is supplied by the caller from the household's OWN roster — this module never guesses who
    initials belong to.

    Attribution only applies inside a household that already owns the document: it refines *whose*
    document it is within a household, and never creates, moves or overrides ownership. Initials in
    :data:`UNRESOLVED_PERSON_INITIALS` always return ``unresolved`` for review.
    """
    if not household_owned:
        return PersonAttribution(None, None, "none",
                                 ["person attribution applies only inside an owning household"], False)
    found = {m.group(1).upper() for m in _INITIAL_RE.finditer(filename or "")}
    known = found & set(member_initials)
    unresolved = found & UNRESOLVED_PERSON_INITIALS
    if unresolved and not known:
        token = sorted(unresolved)[0]
        return PersonAttribution(token, None, "unresolved",
                                 [f"filename carries '{token}', which is not unambiguously resolvable"],
                                 True)
    if len(known) == 1:
        token = known.pop()
        return PersonAttribution(token, member_initials[token], "strong",
                                 [f"filename carries '{token}' ({member_initials[token]})"], False)
    if len(known) > 1:
        tokens = ", ".join(sorted(known))
        return PersonAttribution(None, None, "unresolved",
                                 [f"filename names several household members: {tokens}"], True)
    return PersonAttribution(None, None, "none", [], False)


# --- business relationships ------------------------------------------------------------------------

class BusinessVerdict(NamedTuple):
    verdict: str              # "candidate_for_review" | "rejected_surname_only" | "no_evidence"
    evidence: list[str]
    may_auto_link: bool       # ALWAYS False — a business relationship is never created automatically


def business_relationship_verdict(business_name: str | None, *, client_surname: str | None,
                                  corroborating_evidence=()) -> BusinessVerdict:
    """Assess whether a business could be related to a client. NEVER auto-links.

    A shared surname is not evidence — "White Willow Homestead LLC" and a client named White have a
    word in common and nothing more. A relationship becomes a REVIEW CANDIDATE only when some
    independent signal ties them (a matching client email or domain, a shared EIN, an explicit
    reference inside the client's own documents), and even then a person decides.
    """
    if not business_name:
        return BusinessVerdict("no_evidence", [], False)
    name_tokens = {t for t in _TOKEN_RE.findall(business_name.lower())}
    surname = (client_surname or "").strip().lower()
    shares_surname = bool(surname) and surname in name_tokens
    evidence = [str(e) for e in corroborating_evidence if e]

    if evidence:
        detail = list(evidence)
        if shares_surname:
            detail.append(f"also shares the client surname '{surname}' (not evidence on its own)")
        return BusinessVerdict("candidate_for_review", detail, False)
    if shares_surname:
        return BusinessVerdict(
            "rejected_surname_only",
            [f"only link is the shared surname '{surname}' — never sufficient to relate a business"],
            False)
    return BusinessVerdict("no_evidence", [], False)


# --- the composite ---------------------------------------------------------------------------------

def filing_evidence(row, *, member_initials=None) -> dict:
    """All read-only filing evidence for one document: category, tax year, and person attribution.

    Returns proposals with their evidence, confidence and any conflict that requires review. Writes
    nothing, and never proposes an owner — ownership stays with the folder-resolution rules and their
    guardrails (see ``document_high_validation``).
    """
    filename = row.get("original_name") or ""
    path = source_path_for(row)
    household_owned = row.get("household_id") is not None

    year = infer_tax_year(row)
    doc_type, confidence = classify_document(filename, None)
    person = attribute_person(filename, member_initials=member_initials or {},
                              household_owned=household_owned)

    review_reasons = []
    if year.confidence == "conflict":
        review_reasons.append("tax year: " + "; ".join(year.evidence))
    if person.requires_review:
        review_reasons.extend(person.evidence)

    return {
        "document_id": row.get("id"),
        "original_name": filename,
        "source_path": path,
        "current_category": row.get("category"),
        "proposed_category": doc_type if doc_type != "unknown" else None,
        "category_confidence": confidence if doc_type != "unknown" else 0.0,
        "category_evidence": ([f"filename matches {doc_type}"] if doc_type != "unknown" else []),
        "current_tax_year": ((row.get("tags") or {}).get("tax_year")
                             if isinstance(row.get("tags"), dict) else None),
        "proposed_tax_year": year.year if year.is_proposed else None,
        "tax_year_confidence": year.confidence,
        "tax_year_evidence": year.evidence,
        "tax_year_conflict": year.confidence == "conflict",
        "current_owner": _owner_label(row),
        "proposed_person": person.person_name,
        "person_initials": person.initials,
        "person_confidence": person.confidence,
        "person_evidence": person.evidence,
        "requires_review": bool(review_reasons),
        "review_reasons": review_reasons,
        "would_write": False,          # this module never writes
    }


def _owner_label(row) -> str | None:
    if row.get("person_id"):
        return f"person:{row['person_id']}"
    if row.get("household_id"):
        return f"household:{row['household_id']}"
    if row.get("organization_id"):
        return f"organization:{row['organization_id']}"
    return None
