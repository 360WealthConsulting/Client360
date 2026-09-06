"""The canonical filing destination contract: ``CLIENT > SERVICE_LINE > TAX_YEAR``.

WHAT A DESTINATION IS
----------------------
Exactly three levels, always. A document is either filed at
``owner / service line / four-digit year`` or it is not automatically filed at all. There is no
two-level destination and no "No Year" folder — the previous batch filed 9,361 of its 16,304
documents at ``CLIENT/SERVICE`` with no year, and that is the practice this module replaces.

IDENTITY IS NOT PATH TEXT
--------------------------
A folder's identity is::

    (owner_scope_type, owner_scope_id, service_code, tax_year)

Owner identity is the scope id, so renaming a client moves no folders. The service level keys on the
vocabulary CODE, not the label, so re-wording "Sales & Litter Tax" moves no folders either. What a
human sees is derived separately by :mod:`app.services.filing_labels` and never participates in
identity — which is what lets two owners share a visible label without sharing a folder.

FIRST-FAIL GATES
-----------------
:func:`classify` runs its gates in a fixed order and returns the FIRST failure, so every document
lands in exactly one bucket and the census reconciles against the corpus total. The order encodes
precedence: ownership before service, service before year, and safety holds last.

WHY MODERATE YEARS ARE REVIEW
------------------------------
``document_filing_preview`` grades a tax year ``strong`` when a recorded tag agrees or two
independent sources agree, and ``moderate`` when one source says so alone. Measured across the
corpus, every unevidenced year, every implausible year (1999 from ``1999 Barrier Island.jpeg``) and
every filename-contradicting year fell in the moderate half; zero fell in the strong half. Moderate
is good enough to show a human and never good enough to put in an automatic path.
"""
from __future__ import annotations

import re

from app.services.filing_labels import UnsafeLabelError, folder_safe_label
from app.services.filing_service_vocabulary import (
    is_provenance_label,
    service_code_for_label,
    service_label_for_code,
)
from app.services.taxdome_service_derivation import (
    MIN_BACKING_DOCUMENTS,
    STATUS_DERIVED,
    build_owner_profiles,
    derive_service,
    is_taxdome,
    is_unsorted,
)

AUTO_FILE_SAFE = "AUTO_FILE_SAFE"
REVIEW = "REVIEW"
UNRESOLVED = "UNRESOLVED"
EXCLUDED = "EXCLUDED"

#: Exactly three. Asserted after construction, not assumed.
CANONICAL_DEPTH = 3

SCOPE_TYPES = ("person", "household", "organization")

#: First-fail reasons.
R_EXCLUDED = "excluded_nonclient"
R_OWNER_CONFLICT = "ownership_conflict"
R_NO_OWNER = "missing_owner"
R_OWNER_LABEL_UNSAFE = "owner_label_unusable"
R_NO_SERVICE = "missing_service_line"
R_PROVENANCE_ONLY = "service_line_is_provenance_only"
R_TAXDOME_UNSORTED = "taxdome_unsorted"
R_TAXDOME_MULTI_SERVICE = "taxdome_owner_multiple_services"
R_TAXDOME_THIN_PROFILE = "taxdome_owner_profile_below_minimum"
R_TAXDOME_NO_PROFILE = "taxdome_owner_no_established_service"
R_TAXDOME_VETO = "taxdome_content_contradiction_veto"
R_YEAR_CONFLICT = "tax_year_conflict"
R_YEAR_MODERATE = "tax_year_not_strong"
R_NO_YEAR = "missing_tax_year"
R_NO_DISPLAY_NAME = "missing_display_name"
R_SAFETY_HOLD = "filing_safety_hold"

STRONG = "strong"

_YEAR_RE = re.compile(r"^\d{4}$")

#: Plausible tax years. Outside this a "year" is a number that merely looks like one.
MIN_TAX_YEAR, MAX_TAX_YEAR = 1990, 2100

#: Folder code prefix. Distinct from the retired batch-1 ``client-…`` codes, so a legacy folder can
#: never be mistaken for — or reused as — a canonical one.
CODE_PREFIX = "cf"

FOLDER_KINDS = ("client", "service", "year")


# --- natural identity ----------------------------------------------------------------------------

def client_key(scope_type, scope_id) -> tuple:
    return ("client", str(scope_type), int(scope_id))


def service_key(scope_type, scope_id, service_code) -> tuple:
    return ("service", str(scope_type), int(scope_id), str(service_code))


def year_key(scope_type, scope_id, service_code, tax_year) -> tuple:
    return ("year", str(scope_type), int(scope_id), str(service_code), int(tax_year))


def client_code(scope_type, scope_id) -> str:
    return f"{CODE_PREFIX}-client-{scope_type}-{int(scope_id)}"


def service_code_for_folder(scope_type, scope_id, service_code) -> str:
    return f"{client_code(scope_type, scope_id)}--svc-{service_code}"


def year_code(scope_type, scope_id, service_code, tax_year) -> str:
    return (f"{service_code_for_folder(scope_type, scope_id, service_code)}"
            f"--yr-{int(tax_year):04d}")


def destination_identity(scope_type, scope_id, service_code, tax_year) -> dict:
    """The canonical destination identity — the thing a folder IS, independent of any label."""
    return {
        "owner_scope_type": str(scope_type),
        "owner_scope_id": int(scope_id),
        "service_code": str(service_code),
        "tax_year": int(tax_year),
        "folder_code": year_code(scope_type, scope_id, service_code, tax_year),
    }


# --- the contract --------------------------------------------------------------------------------

def _coerce_year(value):
    text = str(value or "").strip()
    if not _YEAR_RE.match(text):
        return None
    year = int(text)
    return year if MIN_TAX_YEAR <= year <= MAX_TAX_YEAR else None


def _service_for(proposal, profiles, *, min_backing_documents):
    """Resolve the service line, returning ``(code, derivation_or_None, failure_reason_or_None)``.

    Non-TaxDome documents take the firm's own SharePoint filing decision. TaxDome documents have no
    service in their tree at all and go through the owner-profile rule, which carries provenance.
    """
    if not is_taxdome(proposal):
        label = proposal.get("proposed_top_level_category")
        if label is None or str(label).strip() == "":
            return None, None, R_NO_SERVICE
        if is_provenance_label(label):
            return None, None, R_PROVENANCE_ONLY
        code = service_code_for_label(label)
        if code is None:
            return None, None, R_NO_SERVICE
        return code, None, None

    derivation = derive_service(proposal, profiles,
                                min_backing_documents=min_backing_documents)
    if derivation["status"] == STATUS_DERIVED:
        return derivation["derived_service_line"], derivation, None
    reason = {
        "owner_has_no_established_service": R_TAXDOME_NO_PROFILE,
        "owner_has_multiple_services": R_TAXDOME_MULTI_SERVICE,
        "owner_profile_below_minimum_backing": R_TAXDOME_THIN_PROFILE,
        "taxdome_unsorted": R_TAXDOME_UNSORTED,
        "content_contradicts_owner_service": R_TAXDOME_VETO,
        "ownership_conflict": R_OWNER_CONFLICT,
        "no_resolved_owner": R_NO_OWNER,
    }.get(derivation["reason"], R_NO_SERVICE)
    return None, derivation, reason


#: Which bucket each failure reason lands in.
_REASON_BUCKET = {
    R_EXCLUDED: EXCLUDED,
    R_OWNER_CONFLICT: REVIEW,
    R_NO_OWNER: UNRESOLVED,
    R_OWNER_LABEL_UNSAFE: REVIEW,
    R_NO_SERVICE: UNRESOLVED,
    R_PROVENANCE_ONLY: REVIEW,
    R_TAXDOME_UNSORTED: REVIEW,
    R_TAXDOME_MULTI_SERVICE: REVIEW,
    R_TAXDOME_THIN_PROFILE: REVIEW,
    R_TAXDOME_NO_PROFILE: UNRESOLVED,
    R_TAXDOME_VETO: REVIEW,
    R_YEAR_CONFLICT: REVIEW,
    R_YEAR_MODERATE: REVIEW,
    R_NO_YEAR: UNRESOLVED,
    R_NO_DISPLAY_NAME: REVIEW,
    R_SAFETY_HOLD: REVIEW,
}


def classify(proposal, profiles, *, min_backing_documents=MIN_BACKING_DOCUMENTS) -> dict:
    """Classify one preview proposal against the canonical contract.

    Returns a row carrying the destination when AUTO_FILE_SAFE, and exactly one first-fail reason
    otherwise. Never raises on ordinary data: an unusable owner label is a REVIEW outcome, not an
    exception, because one bad name must not stop a corpus-wide preview.
    """
    row = {
        "document_id": proposal.get("document_id"),
        "original_name": proposal.get("original_name") or "",
        "source_system": proposal.get("source") or "",
        "source_path": proposal.get("source_path") or "",
        "owner_scope_type": None, "owner_scope_id": None,
        "owner_source_label": None, "owner_folder_label": None, "owner_label_sanitized": False,
        "service_code": None, "service_label": None, "service_source": None,
        "tax_year": None, "tax_year_confidence": proposal.get("tax_year_confidence"),
        "tax_year_source": proposal.get("tax_year_source"),
        "folder_segments": [], "folder_path": "", "folder_code": None,
        "destination_identity": None,
        "proposed_document_type": proposal.get("proposed_document_type") or "",
        "source_provenance": proposal.get("proposed_top_level_category")
        if is_provenance_label(proposal.get("proposed_top_level_category")) else "",
        "taxdome_unsorted": is_unsorted(proposal),
        "derivation": None,
        "status": None, "reason": None,
    }
    row.update(display_name_fields(proposal))

    def fail(reason):
        row["status"] = _REASON_BUCKET[reason]
        row["reason"] = reason
        return row

    # 1. excluded non-client material never reaches the client filing tree.
    if R_EXCLUDED in (proposal.get("reasons") or []):
        return fail(R_EXCLUDED)

    # 2. ownership: conflict is a review, absence is unresolved.
    scope_state = proposal.get("filing_scope_state") or ""
    if scope_state == "conflict":
        return fail(R_OWNER_CONFLICT)
    scope_type = proposal.get("proposed_scope_type")
    scope_id = proposal.get("proposed_scope_id")
    if scope_state != "resolved" or scope_type not in SCOPE_TYPES or scope_id is None:
        return fail(R_NO_OWNER)
    row["owner_scope_type"], row["owner_scope_id"] = str(scope_type), int(scope_id)

    # 3. a visible label must exist and must survive sanitation.
    source_label = proposal.get("proposed_scope_name")
    row["owner_source_label"] = source_label
    try:
        row["owner_folder_label"] = folder_safe_label(source_label)
    except UnsafeLabelError:
        return fail(R_OWNER_LABEL_UNSAFE)
    row["owner_label_sanitized"] = row["owner_folder_label"] != str(source_label or "")

    # 4. service line — the firm's decision, or the validated owner-profile derivation.
    service, derivation, failure = _service_for(
        proposal, profiles, min_backing_documents=min_backing_documents)
    row["derivation"] = derivation
    if failure:
        return fail(failure)
    row["service_code"] = service
    row["service_label"] = service_label_for_code(service)
    row["service_source"] = "taxdome_owner_profile_derivation" if derivation else "source_taxonomy"

    # 5. TaxDome Unsorted is a hold for every source, checked here so it applies even when the
    #    service came from the taxonomy rather than the derivation.
    if row["taxdome_unsorted"]:
        return fail(R_TAXDOME_UNSORTED)

    # 6. tax year: conflict and moderate are both REVIEW; only strong may be filed.
    confidence = proposal.get("tax_year_confidence")
    if confidence == "conflict":
        return fail(R_YEAR_CONFLICT)
    year = _coerce_year(proposal.get("proposed_tax_year"))
    if year is None:
        return fail(R_NO_YEAR)
    if confidence != STRONG:
        return fail(R_YEAR_MODERATE)
    row["tax_year"] = year

    # 7. a document must have a name to file under.
    if not (row["proposed_display_name"] or "").strip():
        return fail(R_NO_DISPLAY_NAME)

    # 8. any safety hold the preview itself raised.
    if (proposal.get("filing_status") or "") == "UNRESOLVED":
        return fail(R_SAFETY_HOLD)

    segments = [row["owner_folder_label"], row["service_label"], str(year)]
    if len(segments) != CANONICAL_DEPTH or not all(s and str(s).strip() for s in segments):
        return fail(R_SAFETY_HOLD)
    row["folder_segments"] = segments
    row["folder_path"] = "/".join(segments)
    identity = destination_identity(scope_type, scope_id, service, year)
    row["destination_identity"] = identity
    row["folder_code"] = identity["folder_code"]
    row["status"] = AUTO_FILE_SAFE
    return row


def display_name_fields(proposal) -> dict:
    """Naming reported, never re-implemented.

    The preview already runs ``document_naming.safe_document_label``; this only grades the result.
    A raw-filename fallback does not invalidate placement — the folder is right either way — but it
    must be countable so naming can be worked on separately.
    """
    proposed = (proposal.get("proposed_display_name") or "").strip()
    raw = (proposal.get("original_name") or "").strip()
    fallback = bool(proposed) and proposed.casefold() == raw.casefold()
    return {
        "proposed_display_name": proposed,
        "display_name_source": proposal.get("display_name_source") or "",
        "display_name_quality": ("missing" if not proposed
                                 else "raw_filename_fallback" if fallback
                                 else "engine_named"),
        "raw_filename_fallback": fallback,
    }


def build_rows(proposals, *, min_backing_documents=MIN_BACKING_DOCUMENTS) -> tuple[list[dict], dict]:
    """Classify a whole preview. Returns ``(rows, owner_profiles)``.

    Profiles are built once from the same proposal set, so the derivation evidence and the
    classification are guaranteed to be the same snapshot.
    """
    profiles = build_owner_profiles(proposals, min_backing_documents=min_backing_documents)
    rows = [classify(p, profiles, min_backing_documents=min_backing_documents) for p in proposals]
    return rows, profiles


def folder_nodes(rows) -> list[dict]:
    """The distinct folder tree the AUTO_FILE_SAFE rows require, parents before children.

    Ordering is by kind then code, which is also a valid insert order: a client node always precedes
    its service nodes and a service node always precedes its years.
    """
    from app.services.filing_manifest import claim

    clients: dict[str, dict] = {}
    services: dict[str, dict] = {}
    years: dict[str, dict] = {}
    for row in rows:
        if row["status"] != AUTO_FILE_SAFE:
            continue
        stype, sid = row["owner_scope_type"], row["owner_scope_id"]
        service, year = row["service_code"], row["tax_year"]
        c_code = client_code(stype, sid)
        s_code = service_code_for_folder(stype, sid, service)
        y_code = year_code(stype, sid, service, year)
        claim(clients, c_code, {
            "code": c_code, "name": row["owner_folder_label"], "kind": "client",
            "parent_code": None, "owner_scope_type": stype, "owner_scope_id": sid,
            "service_code": None, "tax_year": None,
        }, compare_fields=("name", "parent_code", "owner_scope_type", "owner_scope_id"))
        claim(services, s_code, {
            "code": s_code, "name": row["service_label"], "kind": "service",
            "parent_code": c_code, "owner_scope_type": stype, "owner_scope_id": sid,
            "service_code": service, "tax_year": None,
        }, compare_fields=("name", "parent_code", "owner_scope_type", "owner_scope_id",
                           "service_code"))
        claim(years, y_code, {
            "code": y_code, "name": str(year), "kind": "year",
            "parent_code": s_code, "owner_scope_type": stype, "owner_scope_id": sid,
            "service_code": service, "tax_year": year,
        }, compare_fields=("name", "parent_code", "owner_scope_type", "owner_scope_id",
                           "service_code", "tax_year"))
    return ([clients[k] for k in sorted(clients)]
            + [services[k] for k in sorted(services)]
            + [years[k] for k in sorted(years)])


def summarize(rows) -> dict:
    """Census that reconciles: bucket counts sum to the row count, reasons sum to the failures."""
    from collections import Counter

    buckets = Counter(r["status"] for r in rows)
    auto = [r for r in rows if r["status"] == AUTO_FILE_SAFE]
    return {
        "total_rows": len(rows),
        "buckets": dict(sorted(buckets.items())),
        "first_fail_reasons": dict(sorted(Counter(
            r["reason"] for r in rows if r["reason"]).items())),
        "auto_file_safe": len(auto),
        "by_source": dict(sorted(Counter(r["source_system"] for r in auto).items())),
        "by_service": dict(sorted(Counter(r["service_label"] for r in auto).items())),
        "by_owner_type": dict(sorted(Counter(r["owner_scope_type"] for r in auto).items())),
        "by_tax_year": dict(sorted(Counter(str(r["tax_year"]) for r in auto).items())),
        "by_service_source": dict(sorted(Counter(r["service_source"] for r in auto).items())),
        "destinations": len({r["folder_code"] for r in auto}),
        "owners": len({(r["owner_scope_type"], r["owner_scope_id"]) for r in auto}),
        "raw_filename_fallback": sum(1 for r in auto if r["raw_filename_fallback"]),
        "display_name_quality": dict(sorted(Counter(
            r["display_name_quality"] for r in auto).items())),
        "owner_labels_sanitized": sum(1 for r in auto if r["owner_label_sanitized"]),
        "depth_census": dict(sorted(Counter(len(r["folder_segments"]) for r in auto).items())),
    }
