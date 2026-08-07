"""TaxDome source adapter — DISCOVERY + NORMALIZATION only.

Walks the confirmed local TaxDome document repository and yields one :class:`VersionedEnterpriseArtifact`
per file (artifact_type ``document``). Each TaxDome top-level folder is one client (the match hint);
category is ``Tax``. It performs no matching, plans no destinations, touches no storage or database — the
platform's services do all of that identically for every source.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from app.services.migration.artifact import VersionedEnterpriseArtifact
from app.services.migration.config import MigrationConfig
from app.services.migration.ports import SourceAdapter


class TaxDomeAdapter(SourceAdapter):
    source_system = "TaxDome"

    def source_root(self, config: MigrationConfig) -> Path:
        return config.taxdome_migration_root

    def discover(self, config: MigrationConfig) -> Iterator[VersionedEnterpriseArtifact]:
        root = self.source_root(config)
        if not root.exists():
            return
        for entry in sorted((e for e in os.scandir(root) if e.is_dir()), key=lambda e: e.name.lower()):
            group = entry.name                                  # one TaxDome folder = one client
            for dirpath, _dirnames, filenames in os.walk(entry.path):
                for name in filenames:
                    abs_path = os.path.join(dirpath, name)
                    rel_within = os.path.relpath(abs_path, entry.path).replace(os.sep, "/")
                    yield VersionedEnterpriseArtifact(
                        source_system=self.source_system, artifact_type="document", group_key=group,
                        payload={"abs_path": abs_path, "rel_within_group": rel_within, "category": "Tax"},
                        provenance={"source_root": str(root),
                                    "source_relative_path": os.path.relpath(abs_path, root)})
