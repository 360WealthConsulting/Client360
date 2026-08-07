"""TaxDome source adapter — DISCOVERY ONLY.

Walks the confirmed local TaxDome document repository and yields one :class:`SourceItem` per file. Each
TaxDome top-level folder is one client (the match hint); category is ``Tax``. It performs no matching,
builds no destinations, and touches no database — the generic engine does all of that identically for
every source.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from app.services.migration.config import MigrationConfig
from app.services.migration.engine import SourceAdapter, SourceItem


class TaxDomeAdapter(SourceAdapter):
    source_system = "TaxDome Documents"

    def source_root(self, config: MigrationConfig) -> Path:
        return config.taxdome_migration_root

    def discover(self, config: MigrationConfig) -> Iterator[SourceItem]:
        root = self.source_root(config)
        if not root.exists():
            return
        for entry in sorted((e for e in os.scandir(root) if e.is_dir()), key=lambda e: e.name.lower()):
            group = entry.name                                  # one TaxDome folder = one client
            for dirpath, _dirnames, filenames in os.walk(entry.path):
                for name in filenames:
                    abs_path = os.path.join(dirpath, name)
                    rel_within = os.path.relpath(abs_path, entry.path).replace(os.sep, "/")
                    yield SourceItem(
                        source_system=self.source_system, group_key=group, abs_path=abs_path,
                        rel_within_group=rel_within, category="Tax",
                        source_metadata={"source_root": str(root),
                                         "source_relative_path": os.path.relpath(abs_path, root)})
