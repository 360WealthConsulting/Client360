"""Transformation Service — the extension point for artifact ENRICHMENT.

Transformation is a distinct stage from Validation. Validation decides whether an artifact is fit to
ingest; Transformation DERIVES new material from it. A transformer takes a versioned artifact and produces
zero or more :class:`DerivedArtifact` records (which are themselves ingested as new versioned artifacts,
never overwriting the source).

This is where the following plug in LATER — none are implemented now; only the boundary + contract exist:

  * OCR                      * tax-return parsing
  * text extraction          * thumbnails / previews
  * AI classification        * embeddings
  * metadata extraction      * document categorization

A transformer NEVER touches the filesystem or the database directly — it reads/writes only through the
Storage Service, and its outputs land in the ``Derivatives`` repository area. Transformers run during the
APPLY phase (post-validation) and are invoked via the registry below; the ingestion engine does not depend
on any specific transformer existing.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.services.migration.artifact import VersionedEnterpriseArtifact


@dataclass
class DerivedArtifact:
    """An artifact produced BY a transformer FROM a source artifact (e.g. OCR text, a thumbnail,
    an embedding). Ingested as its own versioned artifact; its provenance links back to the source."""

    artifact_type: str                                   # e.g. "ocr_text" | "thumbnail" | "embedding"
    payload: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)       # includes the source artifact_id / version_id


class Transformer(ABC):
    """One enrichment. ``transform`` is contract-only in this phase; implementations arrive with APPLY."""

    #: the source artifact types this transformer applies to (e.g. {"document"})
    applies_to: frozenset[str] = frozenset()

    @abstractmethod
    def transform(self, artifact: VersionedEnterpriseArtifact, ctx: dict) -> list[DerivedArtifact]:
        ...


#: The permanent transformer registry. Empty by design — register a Transformer to enable an enrichment.
#: Adding OCR/AI/etc. is one registry entry; the engine and every other stage are unchanged.
TRANSFORMERS: dict[str, Transformer] = {}
