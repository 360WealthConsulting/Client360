"""Safe user-facing names for VAULT documents — the vault row shape over the ONE filename-safety
implementation.

Why this exists
    ``vault_documents`` is a separate storage model from ``documents``. It keeps the uploaded name in
    ``original_filename`` and a free-text ``display_name``, and three download routes handed one of
    those straight to ``FileResponse(filename=...)``. That value becomes the response's
    ``Content-Disposition`` and therefore the name the browser SAVES the file under, so a client who
    uploaded ``2024 W2 SSN 123-45-6789.pdf`` had that name delivered back verbatim by the staff vault,
    the client portal and the portal JSON API.

    There is no second detector here, deliberately. What counts as a sensitive identifier is decided
    only by :mod:`app.services.document_name_safety`; the header/path hardening, the final re-check
    and the ``Document <id>`` fallback belong to :func:`app.services.document_naming
    .document_delivery_filename`. This module adds the vault row shape and the vault's own candidate
    order, so a protection added centrally applies here automatically.

What does NOT change
    The vault has always DELIVERED ``original_filename``, so a filename that is already safe is still
    delivered byte-for-byte as before — this closes an exposure, it does not rename anyone's
    downloads. Only an UNSAFE name is replaced.

Provenance is untouched
    ``original_filename``, ``display_name``, ``storage_key``, ``checksum_sha256`` and the stored bytes
    are only ever READ. Nothing here writes, renames, moves or re-hashes anything, and the file served
    is located by ``storage_key`` exactly as before — only the LABEL on the response changes.
"""
from __future__ import annotations

from app.services.document_name_safety import is_safe, scrub
from app.services.document_naming import (
    document_delivery_filename,
    safe_document_label,
    strip_extension,
)


def _get(doc):
    """Uniform accessor for a mapping row or an ORM object."""
    return doc.get if hasattr(doc, "get") else (lambda k, d=None: getattr(doc, k, d))


def _safe_stem(name) -> str:
    """``name`` reduced to a stem carrying no sensitive identifier, or ``""`` if none survives.

    The decision and the removal are entirely :mod:`app.services.document_name_safety`'s; this only
    chooses between "already safe" and "safe once scrubbed". A residue with no letters left in it
    (a bare year, a fragment of the identifier's neighbours) is not a name, so the caller falls
    through to its next candidate instead of delivering it.
    """
    stem = strip_extension((name or "").strip())
    if not stem:
        return ""
    if is_safe(stem):
        return stem
    cleaned = scrub(stem).strip()
    if cleaned and is_safe(cleaned) and any(c.isalpha() for c in cleaned):
        return cleaned
    return ""


def safe_vault_label(doc) -> str:
    """The label a vault document may be SHOWN under, guaranteed free of sensitive identifiers.

    Used for the client-facing document list, which has always shown ``display_name`` — so that stays
    the first candidate and a safe one is returned unchanged.
    """
    if doc is None:
        return ""
    get = _get(doc)
    return safe_document_label({
        "id": get("id"),
        "display_name": get("display_name"),
        "original_name": get("original_filename"),
    })


def safe_vault_delivery_filename(doc) -> str:
    """The filename a vault document is DELIVERED under (``Content-Disposition``).

    Candidate order, first safe result wins — ``original_filename`` FIRST, because that is the name
    the vault has always delivered and an unchanged download is the correct outcome for the safe
    majority:

    1. ``original_filename`` when it is already safe,
    2. ``original_filename`` with the sensitive spans scrubbed out, when a real name survives,
    3. ``display_name`` under the same two rules,
    4. ``Document <id>``.

    The chosen base is then handed to :func:`document_delivery_filename`, which strips anything that
    could inject a response header or describe a path, re-checks the result so "safe" is a property of
    what actually leaves the process, appends the ORIGINAL file's extension, and supplies the
    ``Document <id>`` fallback. An unsafe ``original_filename`` is never delivered.
    """
    if doc is None:
        return ""
    get = _get(doc)
    original = get("original_filename")
    base = _safe_stem(original) or _safe_stem(get("display_name"))
    # ``original_name`` carries the extension (and is the last-resort candidate the central helper
    # re-checks); ``display_name`` carries the base chosen above, or "" to force the id fallback.
    return document_delivery_filename({
        "id": get("id"),
        "display_name": base,
        "original_name": original,
    })
