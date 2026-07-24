"""Enterprise Document Intelligence & Records Lifecycle layer (Phase D.50).

A governed, READ-ONLY composition that provides firm-wide document + records visibility — inventory,
retention, archive, lifecycle, missing documentation, and completeness — WITHOUT introducing a second DMS,
OCR engine, indexing/search engine, archive, document database, metadata store, or records repository. It
composes named document dashboards from declarative document + retention + panel registries over the
platform's AUTHORITATIVE document systems: the Document Platform (Phase D.16 — the single document +
metadata + lifecycle + retention-policy owner), Governance retention (Phase D.23 — records retention / legal
holds / disposition), and Compliance Intelligence (Phase D.47 — documentation gaps, normalized from the
authoritative exception engine). It defines no new metrics, owns no persistence, runs no OCR, builds no
index, and never mutates, archives, deletes, re-classifies, or alters retention; every panel is explainable,
deep-links to its authoritative document surface, and carries counts + status only — never document content.
"""
from .service import (
    client_documents,
    compose_dashboard,
    document_summary,
    get_panel,
    household_documents,
    list_dashboards,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "document_summary",
    "client_documents",
    "household_documents",
]
