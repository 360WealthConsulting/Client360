"""Deterministic display-name quality. A Phase B gate, never a Phase A one.

WHY THIS IS A SEPARATE CONCERN
-------------------------------
Placement and naming are independent. A document called ``IMG_4695.jpg`` owned by a client with one
service and a strong tax year has a *correct* canonical destination — the folder tree should be
built for it either way. What should not happen is filing it under a name nobody can read, because
once it is filed the opaque name is what staff see in the folder.

So Phase A materializes folders for every AUTO_FILE_SAFE document regardless of name quality, and
Phase B admits only documents whose name is either engine-generated or already useful as it stands.

WHAT "ENGINE NAMED" MEANS, AND WHY THE FALLBACK IS USUALLY FINE
----------------------------------------------------------------
``document_naming.safe_document_label`` is a SAFETY filter, not a quality normaliser: display name,
then original name, then a scrubbed version, then a constructed ``year - type - owner`` label, then
``Document <id>``. It returns the original filename whenever that filename carries no sensitive
identifier — so "fell back to the raw filename" means *the filename was already safe*, not *naming
failed*. Measured across the corpus the overwhelming majority of those raw names are perfectly
readable (``MCC 1099 2023.pdf``, ``2023 Signature Documents (CASPER AARON).pdf``).

Only genuinely opaque names need holding, and the existing engine cannot improve them: a safe name
never reaches the constructed-label branch. Improving them means changing naming precedence, which
is a naming-engine decision and is deliberately out of scope here. This module only CLASSIFIES.

THE RULE ORDER MATTERS
-----------------------
Structural opacity is checked first and wins outright — a GUID with a year in it is still a GUID.
Only then do year / form-token / two-real-words admit a name. Checking "has no three-letter word"
too early would condemn ``2021 8879 S.pdf``, which is a perfectly good name.
"""
from __future__ import annotations

import re

ENGINE_NAMED = "engine_named"
RAW_ALREADY_USEFUL = "raw_filename_already_useful"
RAW_LOW_QUALITY = "raw_filename_low_quality"
MISSING = "missing"

#: Qualities Phase B admits.
PHASE_B_ALLOWED = frozenset({ENGINE_NAMED, RAW_ALREADY_USEFUL})

# --- structural opacity: these win outright ------------------------------------------------------
_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", re.I)
_LONG_HEX = re.compile(r"\b[0-9a-f]{16,}\b", re.I)
_CAMERA = re.compile(r"^(img|dsc|dcim|photo|image|pxl|mvimg|screenshot)[\W_]*\d", re.I)
_SCANNER = re.compile(r"^(scan|xerox\s*scan|camscanner|adobe\s*scan)[\W_]*[\d_]", re.I)
_TIMESTAMP = re.compile(r"^\d{8,}([\W_]|$)")
_GENERIC = re.compile(r"^(document|doc|file|untitled|new\s*doc(ument)?|scan|image|photo|"
                      r"attachment|unnamed|copy|final|temp)\s*(\(\d+\))?$", re.I)

# --- signals that a name is useful ---------------------------------------------------------------
_YEAR = re.compile(r"(19|20)\d{2}")
_FORM = re.compile(r"(1040|1065|1120s?|1041|8879|w-?2|w-?3|w-?9|1099|1098|k-?1|5498|1095|941|940|"
                   r"st-?9|st-?8|va-?[56]|bpol|2553|organizer|return|statement|invoice|receipt|"
                   r"payroll|ledger|reconcil|signature|efile|e-file|extension|amend)", re.I)
_WORD = re.compile(r"[A-Za-z]{3,}")
_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,5}$")


def strip_extension(name) -> str:
    return _EXTENSION.sub("", str(name or "")).strip()


def is_opaque(name) -> bool:
    """Structurally unreadable: a GUID, a hash, a camera roll, a scanner dump, a bare timestamp."""
    stem = strip_extension(name)
    lowered = stem.lower()
    return bool(_GUID.search(stem) or _LONG_HEX.search(stem) or _CAMERA.match(lowered)
                or _SCANNER.match(lowered) or _TIMESTAMP.match(lowered) or _GENERIC.match(lowered))


def is_useful_filename(name) -> bool:
    """A name a human can act on: carries a year, a form/document-kind token, or two real words."""
    stem = strip_extension(name)
    if not stem:
        return False
    if is_opaque(stem):
        return False
    return bool(_YEAR.search(stem) or _FORM.search(stem) or len(_WORD.findall(stem)) >= 2)


def classify(proposed_display_name, original_name) -> str:
    """Return one of :data:`ENGINE_NAMED`, :data:`RAW_ALREADY_USEFUL`, :data:`RAW_LOW_QUALITY`,
    :data:`MISSING`.

    Pure and total: depends only on the two strings, never on a document id or a curated list, so
    the same filename always classifies the same way.
    """
    proposed = (proposed_display_name or "").strip()
    raw = (original_name or "").strip()
    if not proposed:
        return MISSING
    if proposed.casefold() != raw.casefold():
        return ENGINE_NAMED
    return RAW_ALREADY_USEFUL if is_useful_filename(raw) else RAW_LOW_QUALITY


def allowed_in_phase_b(quality) -> bool:
    return quality in PHASE_B_ALLOWED
