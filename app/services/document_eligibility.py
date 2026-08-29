"""Which files may participate in DOCUMENT INTELLIGENCE (OCR, extraction, classification, ownership).

An entire Drake application tree was copied into SharePoint and ingested by importers that apply no
type filter at all, so program binaries, fonts, help files and per-client data blobs (``.dll``,
``.000``, ``.exe``, ``.ttf``, ``.di6``, …) became owner-proposal candidates. This module is the single
positive definition of what counts as a document.

It is deliberately a POSITIVE family map, not a blacklist: a new Drake/vendor runtime extension is
excluded automatically because it is simply not a recognized document family, with no list to
maintain. ``_DOC_FAMILY`` originated in the SharePoint importer, which still uses it to LABEL
documents (``sharepoint_doc_type``); it lives here now so labelling and eligibility can never drift
apart into two maps.

SCOPE — this decides ANALYSIS participation only:
  * ingestion is unchanged: ineligible files still become ``documents`` rows,
  * ``document_sources`` provenance is still recorded,
  * no source file is moved or deleted,
  * every historical row stays queryable and auditable.
Nothing here deletes, hides, or rewrites anything.

AUTHORITY BOUNDARY — eligibility is a PRESENTATION/COST decision, never an evidential one:
  * It must NEVER control MDM, canonical person/household/business identity, duplicate resolution,
    Drake authority, provenance, or evidence retention.
  * Drake is a PRIMARY AUTHORITY for identity and duplicate resolution. That authority flows through
    ``source_contacts`` → ``drake_identity`` → ``drake_identity_match_candidates`` →
    ``person_source_links``/``person_merge`` and does not touch the ``documents`` corpus at all, so
    nothing here can suppress it. Keep it that way: a Drake-native file may be authoritative for
    identity, preserved as provenance, and still be neither OCR- nor classification-eligible.
  * The ONLY consumer is ``document_high_validation._unassigned_ids`` (document ownership/review).
    Before calling this from anywhere else, confirm the caller is not an identity/dedup path.
  * Known, accepted limitation: an ineligible artifact cannot be assigned an owner from the manual
    review queue. Restoring that would be a separate, explicit change — not a weakening of this map.
"""
from __future__ import annotations

#: Extension → coarse document family. The authoritative definition of "this is a document".
DOC_FAMILY: dict[str, frozenset[str]] = {
    "pdf": frozenset({"pdf"}),
    "word": frozenset({"doc", "docx", "docm", "dot", "dotx", "rtf"}),
    "excel": frozenset({"xls", "xlsx", "xlsm", "xlsb", "csv"}),
    "powerpoint": frozenset({"ppt", "pptx", "ppsx", "pps"}),
    # "heif" is not in the SharePoint labelling map but IS in document_ocr.SUPPORTED_EXT; it must be
    # eligible or an OCR-supported image would be excluded from analysis. Asserted by a test.
    "image": frozenset({"png", "jpg", "jpeg", "gif", "tif", "tiff", "bmp", "heic", "heif", "webp"}),
    "text": frozenset({"txt", "md", "log"}),
    "email": frozenset({"msg", "eml"}),
    # Already extracted by the analysis pipeline (calendar items, and the XML Drake/e-file exports
    # the Drake importer classifies as ``xml_export``), so they are documents for these purposes.
    "structured": frozenset({"xml", "ics"}),
}

#: Every eligible extension, flattened.
ELIGIBLE_EXTENSIONS: frozenset[str] = frozenset().union(*DOC_FAMILY.values())

_OTHER = "other"


def extension_of(name: str | None) -> str:
    """Lowercased extension without the dot; ``""`` when the name carries none."""
    n = name or ""
    return n.rsplit(".", 1)[-1].lower() if "." in n else ""


def document_family(name: str | None) -> str:
    """Coarse family for a filename, or ``"other"`` when it is not a recognized document type."""
    ext = extension_of(name)
    for family, exts in DOC_FAMILY.items():
        if ext in exts:
            return family
    return _OTHER


def is_intelligence_eligible(name: str | None, content_type: str | None = None) -> bool:
    """May this file enter OCR / extraction / classification / owner-proposal workflows?

    Decided by the positive family map above. ``content_type`` is accepted as a secondary signal for
    files whose NAME carries no usable extension — a SharePoint item served as ``application/pdf``
    is a document even when it arrived as ``scan`` with no suffix. A content type is never allowed to
    VETO an eligible extension, and the catch-all ``application/octet-stream`` grants nothing.
    """
    if document_family(name) != _OTHER:
        return True
    if extension_of(name):
        return False            # it has an extension and that extension is not a document type
    ct = (content_type or "").split(";")[0].strip().lower()
    if not ct or ct == "application/octet-stream":
        return False
    return ct in _CONTENT_TYPES


#: Content types that identify a document when the filename cannot.
_CONTENT_TYPES: frozenset[str] = frozenset({
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/rtf", "text/rtf",
    "text/plain", "text/csv", "text/markdown", "text/xml", "application/xml",
    "message/rfc822", "application/vnd.ms-outlook", "text/calendar",
    "image/png", "image/jpeg", "image/gif", "image/tiff", "image/bmp", "image/heic",
})
