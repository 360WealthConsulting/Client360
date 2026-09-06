"""Derived service line for TaxDome documents. The owner-profile rule, and nothing else.

WHY DERIVATION IS NEEDED AT ALL
--------------------------------
TaxDome's folder tree encodes **who produced a document**, not which service it belongs to. Its
level 2 is ``Client uploaded documents`` / ``Firm docs shared with client``; below that it is
client-authored free text. There is no service vocabulary anywhere in it at any depth. Meanwhile
every table that could state a client's services — ``engagements``, ``organization_service_lines``,
``service_agreements``, ``tax_engagements``, ``payroll_accounts`` — is empty. So a TaxDome document
has an owner and a year and no service, and without one it can never reach a canonical destination.

THE ONE RULE THAT SURVIVED VALIDATION
--------------------------------------
Measured by leave-one-out over 13,224 held-out labelled documents (each document removed from its
own owner's profile so it cannot vote for itself):

* **owner profile — single established service line: precision 0.9990**, rising to 0.9996 once the
  profile needs five or more backing documents.
* filename/token rules, validated owner-blind across disjoint owner folds: **0.9718**.
* hand-written form-number rules: **0.9611**, and Bookkeeping alone scored **0.1077**.

The token result is the interesting one. Naively they looked excellent, but their "pure" tokens were
client names — ``sandeep``, ``nayyar``, ``onesco`` — pure only because those clients happen to have
one service. Owner-blind folds removed the leak and the ceiling collapsed.

The false positives explain why, and they settle the architecture: a file literally named
``august sales tax 2022.pdf`` is filed by the firm under **Tax Preparation**, and ``2019 W2s and
W3.pdf`` under **Payroll**. Service line is a property of the ENGAGEMENT the firm has with a client,
not of the document's content. The same W-2 belongs to Payroll for a payroll client and to Tax
Preparation for a tax client. Content rules answer "what is this document?" when the question is
"which engagement does it belong to?", so they are not merely less precise, they are the wrong
question — which is why none are implemented here.

CONTENT IS A VETO, NEVER A SOURCE
----------------------------------
:func:`contradiction_veto` may only ever REMOVE a derivation. It cannot assign one. A document whose
content plainly contradicts its owner's single service goes to REVIEW rather than being filed on
either signal.

DERIVED IS NOT ASSERTED
------------------------
Every derivation carries :data:`DERIVATION_RULE`, the owner scope, the backing count, a deterministic
digest of the backing document ids, and the veto outcome. A derived service must never be
indistinguishable from one a human assigned, and nothing here writes to the database.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict

from app.services.filing_manifest import canonical_bytes
from app.services.filing_service_vocabulary import service_code_for_label

#: The rule's identity, recorded on every derivation it produces.
DERIVATION_RULE = "owner_profile_single_service_v1"

#: Minimum backing documents. Five is where measured precision reaches 0.9996 and stays there.
MIN_BACKING_DOCUMENTS = 5

#: The source system whose documents need derivation.
TAXDOME_SOURCE = "TaxDome Drive"

STATUS_DERIVED = "derived"
STATUS_REVIEW = "review"
STATUS_UNRESOLVED = "unresolved"

#: Reasons, one per document, first-fail ordered by :func:`derive_service`.
R_NOT_TAXDOME = "not_a_taxdome_document"
R_NO_OWNER = "no_resolved_owner"
R_OWNER_CONFLICT = "ownership_conflict"
R_NO_PROFILE = "owner_has_no_established_service"
R_MULTI_SERVICE = "owner_has_multiple_services"
R_THIN_PROFILE = "owner_profile_below_minimum_backing"
R_UNSORTED = "taxdome_unsorted"
R_VETO = "content_contradicts_owner_service"

#: Content cues used ONLY to veto. Deliberately narrow: a cue that fires wrongly costs a document
#: its automatic destination, which is the safe direction to fail.
VETO_CUES: dict[str, tuple[str, ...]] = {
    "payroll": (r"\b941\b", r"\b940\b", r"\bva-?5\b", r"\bva-?6\b", r"\bvec\b", r"\bpayroll\b",
                r"\bw-?3\b", r"\bpay\s*stub\b", r"\btimesheet\b"),
    "sales_litter_tax": (r"\bst-?9\b", r"\bst-?8\b", r"\bsales\s*tax\b", r"\blitter\b", r"\bbpol\b",
                         r"\bbusiness\s*personal\s*property\b"),
    "bookkeeping": (r"\bbank\s*statement\b", r"\breconcil", r"\bgeneral\s*ledger\b",
                    r"\bquickbooks\b", r"\bqbo\b", r"\bprofit\s*(and|&)\s*loss\b",
                    r"\bbalance\s*sheet\b"),
    "wealth_accounts": (r"\bbrokerage\b", r"\badvisory\b", r"\bacat\b", r"\brmd\b"),
    "tax_preparation": (r"\b8879\b", r"\b1040\b", r"\btax\s*return\b", r"\borganizer\b"),
}

_COMPILED_CUES = {code: tuple(re.compile(p, re.I) for p in patterns)
                  for code, patterns in VETO_CUES.items()}

_UNSORTED_SEGMENTS = frozenset({"unsorted", "misc", "miscellaneous", "to be sorted", "to sort"})
_SEPARATORS = re.compile(r"[\\/]+")


def _owner_of(proposal) -> tuple[str, int] | None:
    scope_type = proposal.get("proposed_scope_type")
    scope_id = proposal.get("proposed_scope_id")
    if not scope_type or scope_id is None:
        return None
    return (str(scope_type), int(scope_id))


def is_taxdome(proposal) -> bool:
    return (proposal.get("source") or "") == TAXDOME_SOURCE


def path_segments(proposal) -> list[str]:
    raw = _SEPARATORS.split(str(proposal.get("source_path") or ""))
    segments = [s for s in raw if s]
    if segments and segments[0].endswith(":"):
        segments = segments[1:]
    return segments


def is_unsorted(proposal) -> bool:
    """True when any folder segment is an Unsorted-class bucket. Filename is not a segment."""
    if not is_taxdome(proposal):
        return False
    return any(s.strip().lower() in _UNSORTED_SEGMENTS for s in path_segments(proposal)[:-1])


def build_owner_profiles(proposals, *, min_backing_documents=MIN_BACKING_DOCUMENTS) -> dict:
    """Owner -> established service profile, built from trustworthy NON-TaxDome evidence only.

    A backing document must (a) come from somewhere other than TaxDome, (b) resolve to exactly one
    owner, and (c) carry a real service line — a provenance bucket is not a service and
    :func:`service_code_for_label` returns ``None`` for one.

    Excluding TaxDome from the evidence is what stops the rule feeding on itself: a derived service
    can never become the basis for another derivation.
    """
    backing: dict[tuple[str, int], Counter] = defaultdict(Counter)
    ids: dict[tuple[str, int], dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for proposal in proposals:
        if is_taxdome(proposal):
            continue
        if (proposal.get("filing_scope_state") or "") != "resolved":
            continue
        owner = _owner_of(proposal)
        if owner is None:
            continue
        code = service_code_for_label(proposal.get("proposed_top_level_category"))
        if code is None:
            continue
        backing[owner][code] += 1
        ids[owner][code].append(int(proposal["document_id"]))

    profiles: dict[tuple[str, int], dict] = {}
    for owner, counts in backing.items():
        services = sorted(counts)
        total = sum(counts.values())
        single = services[0] if len(services) == 1 else None
        document_ids = sorted(i for group in ids[owner].values() for i in group)
        profiles[owner] = {
            "owner_scope_type": owner[0],
            "owner_scope_id": owner[1],
            "services": services,
            "service_counts": dict(sorted(counts.items())),
            "single_service_code": single,
            "backing_document_count": total,
            "backing_document_ids": document_ids,
            "backing_digest": backing_digest(document_ids),
            "meets_minimum": total >= min_backing_documents,
        }
    return profiles


def backing_digest(document_ids) -> str:
    """Deterministic digest of the backing set, so a manifest can pin it without listing thousands."""
    return hashlib.sha256(canonical_bytes(sorted(int(i) for i in document_ids))).hexdigest()


def contradiction_veto(proposal, service_code) -> dict:
    """Does the document's own content contradict ``service_code``? Veto only; never assigns.

    Fires when content cues name one or more services and ``service_code`` is not among them.
    Silence — no cue at all — is not a contradiction; most filenames say nothing about service.
    """
    haystack = f"{proposal.get('original_name') or ''} {proposal.get('source_path') or ''}"
    hits = sorted(code for code, patterns in _COMPILED_CUES.items()
                  if any(p.search(haystack) for p in patterns))
    fired = bool(hits) and service_code not in hits
    return {"veto": fired, "cues": hits, "contradicted_service": service_code if fired else None}


def derive_service(proposal, profiles, *, min_backing_documents=MIN_BACKING_DOCUMENTS) -> dict:
    """Derive a service line for one TaxDome document, with full provenance.

    Returns a record whose ``status`` is :data:`STATUS_DERIVED`, :data:`STATUS_REVIEW` or
    :data:`STATUS_UNRESOLVED`. Gates are first-fail ordered so each document has exactly one reason
    and the census reconciles.
    """
    record = {
        "derivation_rule": DERIVATION_RULE,
        "owner_scope_type": None,
        "owner_scope_id": None,
        "derived_service_line": None,
        "derived_service_label": None,
        "backing_document_count": 0,
        "backing_digest": None,
        "status": STATUS_UNRESOLVED,
        "reason": None,
        "contradiction_veto": False,
        "veto_cues": [],
        "min_backing_documents": min_backing_documents,
    }
    if not is_taxdome(proposal):
        record["reason"] = R_NOT_TAXDOME
        return record
    if (proposal.get("filing_scope_state") or "") == "conflict":
        record.update(status=STATUS_REVIEW, reason=R_OWNER_CONFLICT)
        return record
    owner = _owner_of(proposal)
    if owner is None or (proposal.get("filing_scope_state") or "") != "resolved":
        record["reason"] = R_NO_OWNER
        return record
    record["owner_scope_type"], record["owner_scope_id"] = owner

    profile = profiles.get(owner)
    if profile is None:
        record["reason"] = R_NO_PROFILE
        return record
    record["backing_document_count"] = profile["backing_document_count"]
    record["backing_digest"] = profile["backing_digest"]
    if profile["single_service_code"] is None:
        record.update(status=STATUS_REVIEW, reason=R_MULTI_SERVICE)
        return record
    if profile["backing_document_count"] < min_backing_documents:
        record.update(status=STATUS_REVIEW, reason=R_THIN_PROFILE)
        return record

    code = profile["single_service_code"]
    if is_unsorted(proposal):
        record.update(status=STATUS_REVIEW, reason=R_UNSORTED)
        return record
    veto = contradiction_veto(proposal, code)
    record["veto_cues"] = veto["cues"]
    if veto["veto"]:
        record.update(status=STATUS_REVIEW, reason=R_VETO, contradiction_veto=True)
        return record

    from app.services.filing_service_vocabulary import service_label_for_code
    record.update(status=STATUS_DERIVED, derived_service_line=code,
                  derived_service_label=service_label_for_code(code))
    return record
