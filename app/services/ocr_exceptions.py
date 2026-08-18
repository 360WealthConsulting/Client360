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


class OcrIsolationError(ValueError):
    """A caller of ``run_ocr`` did not make an explicit isolation choice, so the call is refused rather
    than silently running production OCR in-process (an unkillable path with no wall-clock timeout).

    Fail-closed contract: pass ``factory_ref=`` for the isolated production path, or ``isolate=False`` to
    deliberately opt into the in-process diagnostics/test path (for injected fake extractors that cannot
    cross the subprocess spawn boundary). Subclasses ``ValueError`` so existing broad handlers still catch
    it, but it is a programming error — raised before any document is processed, never recorded as a
    per-document failure."""
