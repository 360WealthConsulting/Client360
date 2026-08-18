"""OCR exception types — deliberately free of any app.db / heavy imports.

Isolated here so the OCR *extraction backend* (and the per-document subprocess worker) can import the
timeout/unavailable types WITHOUT importing ``app.services.document_ocr`` (which imports ``app.db`` and
reflects the whole schema on connect). A spawned per-document child therefore starts cheaply and needs no
database. ``document_ocr`` re-exports these names for backward compatibility.
"""
from __future__ import annotations


class OcrBackendUnavailable(RuntimeError):
    """No OCR engine is configured on this host — a supported document cannot be processed yet."""


class OcrTimeout(RuntimeError):
    """OCR exceeded its per-page or per-document wall-clock budget (or made no progress within the
    watchdog window). Distinct from a generic extraction failure so a timed-out document is recorded
    separately and the batch moves on to the next document."""


# Stable, machine-detectable error code for an encrypted / password-protected PDF. It is written verbatim
# into ``document_ocr.last_error`` and operator surfaces match on the ``password_required:`` prefix to show
# "Password required / Encrypted PDF" and to tell it apart from an ordinary unsupported file type. Keep the
# prefix stable — UI/reporting parse it. (No schema change: the persisted status stays ``unsupported``; a
# first-class ``encrypted`` status is a possible later schema-evolution phase, not needed for correctness.)
ENCRYPTED_PDF_ERROR_CODE = "password_required"
ENCRYPTED_PDF_LAST_ERROR = "password_required: encrypted PDF; OCR skipped (no password)"


class OcrEncryptedPdf(RuntimeError):
    """A PDF is encrypted / password-protected and cannot be read without a password.

    A DISTINCT, TERMINAL outcome — never a transient failure. It is recorded as ``unsupported`` with the
    structured :data:`ENCRYPTED_PDF_LAST_ERROR` reason, counted separately (``encrypted``), NOT retried,
    and the batch moves straight to the next document. We NEVER guess, crack, or bypass the password —
    detection is read-only and stops before any expensive rendering/OCR."""


class OcrIsolationError(ValueError):
    """A caller of ``run_ocr`` did not make an explicit isolation choice, so the call is refused rather
    than silently running production OCR in-process (an unkillable path with no wall-clock timeout).

    Fail-closed contract: pass ``factory_ref=`` for the isolated production path, or ``isolate=False`` to
    deliberately opt into the in-process diagnostics/test path (for injected fake extractors that cannot
    cross the subprocess spawn boundary). Subclasses ``ValueError`` so existing broad handlers still catch
    it, but it is a programming error — raised before any document is processed, never recorded as a
    per-document failure."""
