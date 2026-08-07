"""Ports (interfaces) for the ingestion platform. Kept dependency-free so services + adapters + the engine
can all import them without cycles."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

from app.services.migration.artifact import VersionedEnterpriseArtifact
from app.services.migration.config import MigrationConfig


class SourceAdapter(ABC):
    """A source/firm adapter. Implements ONLY discovery + normalization — it yields typed
    :class:`VersionedEnterpriseArtifact` records and does nothing downstream."""

    source_system: str = "unknown"

    @abstractmethod
    def discover(self, config: MigrationConfig) -> Iterator[VersionedEnterpriseArtifact]:
        ...

    def source_root(self, config: MigrationConfig) -> Path:  # noqa: ARG002 — adapters override
        return Path(".")

    def available(self, config: MigrationConfig) -> bool:
        return self.source_root(config).exists()
