"""What a document type may be PROPOSED as — client-visible, staff-only, or review-required.

This module proposes. It never publishes, and nothing in it is consulted at read time: a client sees
a document because a human made a publication decision, not because a policy table said the type was
usually fine. The policy exists to make a 21,496-document backlog reviewable in bands instead of one
file at a time.

THREE RULES SHAPE IT.

1. ONLY AN EXPLICIT CLASSIFICATION CAN PROPOSE CLIENT-VISIBLE. A filename is a hint, not a
   classification. ``2024 Tax Return (EXAMPLE TAXPAYER).pdf`` is *probably* a filed return, and "probably"
   is not a basis for showing a document to a client. So a filename signal may push a document
   TOWARD staff-only — withholding a client's own document is a recoverable mistake — and may never
   push one toward visible. Unclassified means review-required, however suggestive the name.

2. SOURCE SYSTEM IS NOT AUTHORIZATION. "It came from Drake" says where a file was ingested from, not
   whether its client may read it. The Drake corpus contains filed returns and scenario mock-ups in
   the same folder. No band in this module is reachable by source system alone, and ``source_system``
   is deliberately not a parameter of :func:`propose`.

3. OWNERSHIP IS NOT VISIBILITY. Whether a document is correctly owned is a question about filing.
   Whether its client may read it is a question about disclosure. They are answered separately, and
   this module answers only the second.

WHERE THE CONSERVATIVE CALLS WENT. Agency notices, identity documents, bank and brokerage statements
are all plausibly "the client's own". None of them is proposed client-visible here. A notice is
addressed to the client but may carry examination detail; an identity document is the highest-
sensitivity material in the corpus and publishing it opens a second channel to it; a statement may
carry joint or held-away holdings belonging to someone else. Each is a disclosure decision a person
should make, so each lands in review-required rather than being waved through.
"""
from __future__ import annotations

CLIENT_VISIBLE = "proposed_client_visible"
STAFF_ONLY = "proposed_staff_only"
REVIEW_REQUIRED = "review_required"

BANDS = (CLIENT_VISIBLE, STAFF_ONLY, REVIEW_REQUIRED)

#: Filed returns — the client's own return as filed, in every form family the practice files.
_FILED_RETURNS = frozenset({
    "1040", "1040-X", "1040X", "1040-SR", "1040-NR",
    "1065", "1120", "1120-S", "1120S", "1041", "990", "990-EZ", "990-PF",
    "706", "709", "state_return", "760", "760X",
})

#: Organizers — the questionnaire the client fills in. Theirs by construction.
_ORGANIZERS = frozenset({"organizer", "tax_organizer", "engagement_organizer"})

#: Source documents — issued TO the client by an employer, payer, broker or agency, and supplied by
#: them. Publishing returns the client their own paperwork.
_SOURCE_DOCUMENTS = frozenset({
    "W-2", "W2", "W-2G", "1099", "1099-B", "1099-DIV", "1099-INT", "1099-MISC", "1099-NEC",
    "1099-R", "1099-S", "1099-G", "1099-K", "1099-Q", "SSA-1099", "RRB-1099",
    "1098", "1098-T", "1098-E", "1098-C", "1095-A", "1095-B", "1095-C",
    "K-1", "K1", "5498", "5498-SA",
})

#: Accepted e-file acknowledgements — proof the return was accepted. A REJECTION acknowledgement is
#: deliberately not here; see _STAFF_ONLY_TYPES.
_EFILE_ACCEPTED = frozenset({
    "efile_acknowledgement", "efile_accepted", "ack_accepted", "9325", "form_9325",
})

CLIENT_VISIBLE_TYPES = _FILED_RETURNS | _ORGANIZERS | _SOURCE_DOCUMENTS | _EFILE_ACCEPTED

#: Preparer work product and firm administration. Never the client's to read.
STAFF_ONLY_TYPES = frozenset({
    "workpaper", "workpapers", "diagnostic", "diagnostics", "preparer_note", "preparer_notes",
    "review_note", "tax_projection_workpaper", "scenario", "mockup", "mock_up", "draft_return",
    "administrative", "admin", "internal_memo", "firm_admin", "billing_internal",
    "rejected", "efile_rejection", "efile_rejected", "ack_rejected",
    "duplicate", "superseded",
})

#: Filename markers that may push a document to staff-only when its type is unknown. One-directional
#: by design — see rule 1 in the module docstring. Matched case-insensitively as substrings.
STAFF_ONLY_NAME_MARKERS = (
    "mock up", "mockup", "mock-up", "workpaper", "work paper", "diagnostic",
    "preparer note", "preparer_note", "internal only", "internal-only",
    "do not send", "do not release", "draft", "scenario", "rejected", "rejection",
    "paper filing", "for paper filing", "superseded", "duplicate copy",
)

#: Types whose sensitivity earns a human decision even though the client plausibly owns the file.
#: Present for documentation: they resolve to REVIEW_REQUIRED by falling through, and naming them
#: makes that a decision rather than an oversight.
DELIBERATELY_REVIEW_REQUIRED_TYPES = frozenset({
    "drivers_license", "passport", "ssn_card", "identification",
    "bank_statement", "brokerage_statement", "financial_statement",
    "irs_notice", "state_notice", "insurance_policy",
})

#: Classifier verdicts that carry no information.
_EMPTY_TYPES = frozenset({"", "unknown", "unclassified", "other", "none"})


def _normalize(value) -> str:
    return (value or "").strip()


def is_classified(document_type) -> bool:
    """Whether the classifier reached an actual verdict. ``unknown`` is not a verdict."""
    return _normalize(document_type).lower() not in _EMPTY_TYPES


def _match(document_type, vocabulary) -> bool:
    """Case-insensitive membership, so ``w-2`` and ``W-2`` are the same type."""
    dt = _normalize(document_type).lower()
    return any(dt == known.lower() for known in vocabulary)


def name_suggests_staff_only(original_name) -> bool:
    lowered = (original_name or "").lower()
    return any(marker in lowered for marker in STAFF_ONLY_NAME_MARKERS)


def propose(*, document_type=None, original_name=None) -> str:
    """The band this document may be PROPOSED in. Never a publication, never an authorization.

    Note the parameter list: no source system, no owner, no household. Neither where a file came from
    nor whose it is can move it between bands.
    """
    # Staff-only wins over everything, including an explicit client-visible type: a filed return
    # named "2023 1040 DRAFT" is a draft first. Withholding is the recoverable direction.
    if _match(document_type, STAFF_ONLY_TYPES) or name_suggests_staff_only(original_name):
        return STAFF_ONLY
    if not is_classified(document_type):
        return REVIEW_REQUIRED
    if _match(document_type, CLIENT_VISIBLE_TYPES):
        return CLIENT_VISIBLE
    return REVIEW_REQUIRED
