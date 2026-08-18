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
