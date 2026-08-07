"""TaxDome document migration — the FIRST implementation of the generic migration engine.

This is no longer a TaxDome-specific pipeline: it is the generic :class:`DocumentMigrationJob` driven by
the :class:`TaxDomeAdapter`. The adapter only discovers files; the shared engine does classify → match →
destination → validate identically for every source, into the one Client360 repository. Kept as a named
class purely for the CLI/tests; adding a new source is a new adapter, not a new pipeline.
"""
from __future__ import annotations

from app.services.migration.adapters.taxdome import TaxDomeAdapter
from app.services.migration.config import MigrationConfig
from app.services.migration.engine import IngestionJob


class TaxDomeDocumentMigration(IngestionJob):
    def __init__(self, config: MigrationConfig | None = None):
        super().__init__(TaxDomeAdapter(), config)
