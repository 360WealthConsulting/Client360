"""PHASE R — reconcile the legacy filing tree into canonical identity. Read-only planner.

THE SITUATION THIS EXISTS FOR
------------------------------
Batch 1 and batch 2 were applied to production before the canonical architecture landed. There are
now 3,138 ``client-*`` folders and 16,854 documents carrying a ``folder_id``. Those are facts, not
proposals, and the canonical model has to absorb them rather than stand a second tree beside them.

THE KEY INSIGHT: THE LEGACY TREE IS ALREADY THE RIGHT SHAPE, WEARING THE WRONG NAME
------------------------------------------------------------------------------------
A legacy code encodes exactly what canonical identity needs::

    client-person-5830                                     -> client  (person, 5830)
    client-person-5830--category-tax-preparation           -> service (person, 5830, tax_preparation)
    client-person-5830--category-tax-preparation--year-2023 -> year   (person, 5830, tax_preparation, 2023)

So a legacy folder does not need replacing — it needs its identity columns filled in. Migration
``cf01`` adds those columns as NULLable, which means adoption is an ``UPDATE`` of five columns on an
existing row: ``id`` is untouched, and every one of the 16,854 ``documents.folder_id`` references
keeps pointing at the same row.

WHY ADOPTION BEATS REPLACEMENT
-------------------------------
The alternative — create a ``cf-*`` folder, move the document, delete the legacy folder — mutates
three things per document instead of five columns per folder, and every moved document is a chance
to lose a reference. Adoption touches ~3,138 folder rows and ZERO document rows. That is the whole
argument: prefer the design with less production mutation and a simpler rollback.

The folder ``code`` deliberately stays legacy. Canonical identity is the tuple, not the string, so
the code is now just a label with historical value. Renaming 3,138 codes would be pure churn and
would break nothing but could break something.

WHAT ADOPTION CANNOT FIX
-------------------------
A legacy *category* folder is a canonical *service* folder — a legitimate intermediate node. But a
document filed directly IN one sits at depth 2, and depth 2 is not a canonical destination. Those
documents must acquire a STRONG tax year and move down one level. They are not grandfathered: with
no strong year they stay exactly where they are, classified for review, until separately authorized.

NOTHING HERE WRITES. This module plans; the manifest it produces is applied under its own separate
authorization, never together with Phase A or Phase B.
"""
from __future__ import annotations

from collections import Counter

from app.services.canonical_filing import AUTO_FILE_SAFE, destination_identity
from app.services.filing_service_vocabulary import FILING_SERVICES, service_code_for_label
from app.services.legacy_filing_codes import parse_legacy_code, slugify

# --- document classifications --------------------------------------------------------------------
CANONICAL_EQUIVALENT = "CANONICAL_EQUIVALENT"
CANONICAL_NEEDS_YEAR = "CANONICAL_NEEDS_YEAR"
CANONICAL_SERVICE_MAPPING_REQUIRED = "CANONICAL_SERVICE_MAPPING_REQUIRED"
CANONICAL_CONFLICT = "CANONICAL_CONFLICT"
CANONICAL_REVIEW = "CANONICAL_REVIEW"
CANONICAL_UNRESOLVED = "CANONICAL_UNRESOLVED"

# --- proposed actions ----------------------------------------------------------------------------
ADOPT_FOLDER_IN_PLACE = "ADOPT_FOLDER_IN_PLACE"
ATTACH_CANONICAL_IDENTITY = "ATTACH_CANONICAL_IDENTITY"
MOVE_DOCUMENT = "MOVE_DOCUMENT"
LEAVE_REVIEW = "LEAVE_REVIEW"
CREATE_REPLACEMENT_REQUIRED = "CREATE_REPLACEMENT_REQUIRED"
NOOP_ALREADY_CANONICAL = "NOOP_ALREADY_CANONICAL"

#: legacy folder kind -> canonical folder kind. "category" is canonical "service"; the level is the
#: same, only the word differs.
KIND_MAP = {"client": "client", "category": "service", "year": "year"}

#: slug produced by the legacy ``slugify`` -> canonical service code. Built from the vocabulary so
#: it cannot drift from it; a slug with no entry is a mapping gap, never a guess.
SERVICE_SLUG_TO_CODE = {slugify(s.label): s.code for s in FILING_SERVICES}


def legacy_service_code(category_slug):
    """Canonical service code for a legacy category slug, or ``None`` when unmapped.

    The slug is lossy — ``sales-litter-tax`` cannot be turned back into ``Sales & Litter Tax`` by
    string surgery — so it is looked up, never reconstructed.
    """
    if category_slug is None:
        return None
    code = SERVICE_SLUG_TO_CODE.get(category_slug)
    if code:
        return code
    # Legacy slugs came from the SharePoint labels, so try the vocabulary's own alias table too.
    return service_code_for_label(category_slug.replace("-", " "))


def folder_identity_from_code(code) -> dict | None:
    """Canonical identity a legacy folder code implies, or ``None`` when it is not a legacy code."""
    parsed = parse_legacy_code(code)
    if parsed is None:
        return None
    kind = KIND_MAP[parsed["kind"]]
    service = legacy_service_code(parsed["category_slug"]) if parsed["category_slug"] else None
    return {
        "folder_kind": kind,
        "owner_scope_type": parsed["scope_type"],
        "owner_scope_id": parsed["scope_id"],
        "service_code": service,
        "tax_year": parsed["tax_year"],
        "unmapped_service_slug": parsed["category_slug"] if (
            parsed["category_slug"] and service is None) else None,
    }


def plan_folders(legacy_folders) -> dict:
    """Per-folder adoption plan.

    ``legacy_folders`` is an iterable of dicts with ``id``, ``code`` and ``owner_scope_type``
    (NULL until adopted). Returns the plan plus any identity collision — two legacy folders that
    would claim the same canonical identity, which the partial unique index would refuse.
    """
    plan, collisions = [], {}
    seen: dict[tuple, dict] = {}
    for folder in legacy_folders:
        code = folder["code"]
        identity = folder_identity_from_code(code)
        if identity is None:
            plan.append({"folder_id": folder["id"], "folder_code": code, "legacy_semantics": None,
                         "canonical_identity": None, "proposed_action": LEAVE_REVIEW,
                         "reason": "code is not a recognised legacy pattern"})
            continue
        if identity["unmapped_service_slug"]:
            plan.append({"folder_id": folder["id"], "folder_code": code,
                         "legacy_semantics": identity, "canonical_identity": None,
                         "proposed_action": LEAVE_REVIEW,
                         "reason": f"service slug {identity['unmapped_service_slug']!r} "
                                   "is not in the canonical vocabulary"})
            continue
        if folder.get("owner_scope_type") is not None:
            plan.append({"folder_id": folder["id"], "folder_code": code,
                         "legacy_semantics": identity, "canonical_identity": identity,
                         "proposed_action": NOOP_ALREADY_CANONICAL,
                         "reason": "identity already attached"})
            continue
        key = (identity["owner_scope_type"], identity["owner_scope_id"], identity["folder_kind"],
               identity["service_code"] or "", -1 if identity["tax_year"] is None
               else identity["tax_year"])
        if key in seen:
            collisions.setdefault(key, [seen[key]["folder_code"]]).append(code)
            plan.append({"folder_id": folder["id"], "folder_code": code,
                         "legacy_semantics": identity, "canonical_identity": identity,
                         "proposed_action": LEAVE_REVIEW,
                         "reason": f"canonical identity already claimed by "
                                   f"{seen[key]['folder_code']!r}"})
            continue
        entry = {"folder_id": folder["id"], "folder_code": code, "legacy_semantics": identity,
                 "canonical_identity": identity, "proposed_action": ATTACH_CANONICAL_IDENTITY,
                 "reason": "adoptable in place — id and every document reference preserved"}
        seen[key] = entry
        plan.append(entry)
    return {"folders": plan, "collisions": {str(k): v for k, v in collisions.items()},
            "action_counts": dict(sorted(Counter(f["proposed_action"] for f in plan).items()))}


def classify_document(assignment, canonical_row) -> dict:
    """Where one already-filed document should end up.

    ``assignment`` carries ``document_id``, ``folder_id``, ``folder_code``; ``canonical_row`` is the
    row :func:`app.services.canonical_filing.classify` produced for the same document, or ``None``
    when the document is not in the canonical preview at all.
    """
    code = assignment.get("folder_code")
    legacy = folder_identity_from_code(code)
    record = {
        "document_id": assignment["document_id"],
        "existing_folder_id": assignment.get("folder_id"),
        "existing_folder_code": code,
        "legacy_semantics": legacy,
        "canonical_identity": None,
        "classification": None,
        "proposed_action": None,
        "reason": None,
    }

    if legacy is None:
        record.update(classification=CANONICAL_UNRESOLVED, proposed_action=LEAVE_REVIEW,
                      reason="existing folder code is not a recognised legacy pattern")
        return record
    if legacy["unmapped_service_slug"]:
        record.update(classification=CANONICAL_SERVICE_MAPPING_REQUIRED,
                      proposed_action=LEAVE_REVIEW,
                      reason=f"legacy service slug {legacy['unmapped_service_slug']!r} has no "
                             "canonical vocabulary entry")
        return record

    if canonical_row is None or canonical_row["status"] != AUTO_FILE_SAFE:
        # The document sits in a legacy folder but does not currently qualify canonically. A
        # depth-2 placement is the common case: it needs a strong year it does not have.
        if legacy["folder_kind"] == "service":
            record.update(classification=CANONICAL_NEEDS_YEAR, proposed_action=LEAVE_REVIEW,
                          reason="filed at depth 2 and no strong tax year is available — not "
                                 "grandfathered, held until a year is established")
        else:
            record.update(classification=CANONICAL_REVIEW, proposed_action=LEAVE_REVIEW,
                          reason=(canonical_row or {}).get("reason")
                          or "not eligible under the canonical contract")
        return record

    target = destination_identity(
        canonical_row["owner_scope_type"], canonical_row["owner_scope_id"],
        canonical_row["service_code"], canonical_row["tax_year"])
    record["canonical_identity"] = target

    same_owner = (legacy["owner_scope_type"] == target["owner_scope_type"]
                  and legacy["owner_scope_id"] == target["owner_scope_id"])
    if not same_owner:
        record.update(classification=CANONICAL_CONFLICT, proposed_action=LEAVE_REVIEW,
                      reason=f"filed under {legacy['owner_scope_type']}:{legacy['owner_scope_id']} "
                             f"but resolves to {target['owner_scope_type']}:"
                             f"{target['owner_scope_id']}")
        return record
    if legacy["service_code"] != target["service_code"]:
        record.update(classification=CANONICAL_CONFLICT, proposed_action=LEAVE_REVIEW,
                      reason=f"filed under service {legacy['service_code']!r} but resolves to "
                             f"{target['service_code']!r}")
        return record

    if legacy["folder_kind"] == "year" and legacy["tax_year"] == target["tax_year"]:
        # Same owner, same service, same year: the existing folder IS the canonical destination
        # once its identity columns are filled in. The document does not move at all.
        record.update(classification=CANONICAL_EQUIVALENT,
                      proposed_action=NOOP_ALREADY_CANONICAL,
                      reason="existing folder is the canonical destination once adopted")
        return record
    if legacy["folder_kind"] == "year" and legacy["tax_year"] != target["tax_year"]:
        record.update(classification=CANONICAL_CONFLICT, proposed_action=LEAVE_REVIEW,
                      reason=f"filed under year {legacy['tax_year']} but resolves to "
                             f"{target['tax_year']}")
        return record

    # Depth-2: right client, right service, no year. It has a strong year now, so it moves down one
    # level into the year folder — which may itself have to be created by Phase A first.
    record.update(classification=CANONICAL_NEEDS_YEAR, proposed_action=MOVE_DOCUMENT,
                  reason=f"depth-2 placement; strong year {target['tax_year']} moves it into "
                         f"{target['folder_code']}")
    return record


def summarize(folder_plan, document_records) -> dict:
    return {
        "folders": {
            "total": len(folder_plan["folders"]),
            "actions": folder_plan["action_counts"],
            "identity_collisions": len(folder_plan["collisions"]),
        },
        "documents": {
            "total": len(document_records),
            "classifications": dict(sorted(Counter(
                r["classification"] for r in document_records).items())),
            "actions": dict(sorted(Counter(
                r["proposed_action"] for r in document_records).items())),
        },
    }
