"""VersionedEnterpriseArtifact — the normalized, version-history-bearing enterprise object.

The platform assumes NOTHING about files: a ``document``'s payload holds a storage reference, a
``note``/``task``/``contact``/``email``/``event``/AI-metadata artifact holds structured fields. Every
artifact carries version history and provenance so an ingestion NEVER overwrites an existing artifact —
applying a change creates a NEW version linked to its predecessor; the prior version is archived.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CanonicalEntity:
    """The Client360 canonical entity an artifact belongs to (resolved by the Identity Service).

    ``entity_type`` is one of the Identity Service's supported kinds:
    ``person`` | ``household`` | ``organization`` | ``trust`` | ``estate``.
    """

    entity_type: str
    canonical_id: int
    display_name: str = ""


@dataclass
class VersionedEnterpriseArtifact:
    """One normalized enterprise object with version history. Version/imported/hash fields are populated
    by the Apply service (never during read-only preview); nothing overwrites an existing version."""

    source_system: str
    artifact_type: str                                   # document|note|email|task|activity|crm|tax_return|…
    group_key: str                                       # entity hint used by the Identity Service
    payload: dict = field(default_factory=dict)          # artifact-type-specific (storage ref, body, fields…)
    provenance: dict = field(default_factory=dict)       # source ids / paths / timestamps

    # --- source identity (set by the adapter during discovery) ---
    source_record_id: str | None = None                  # the source system's own id for this record/file
    source_locator: str | None = None                    # original source path / URL / object key

    # --- artifact + version history (artifact_id + version fields set on apply; append-only) ---
    artifact_id: str | None = None                       # stable canonical artifact id (constant across versions)
    version_id: str | None = None
    previous_version_id: str | None = None
    effective_date: str | None = None                    # when the artifact content is effective (source date)
    imported_date: str | None = None                     # when ingested into Client360
    archived_date: str | None = None                     # set when superseded by a newer version
    integrity_hash: str | None = None                    # SHA-256, computed on apply

    # --- canonical binding + adapter pre-resolution ---
    canonical_entity: CanonicalEntity | None = None      # set by the Identity Service (type + id + name)
    entity_type: str | None = None                       # optional: adapter already knows the entity
    canonical_id: int | None = None
    display_name: str | None = None


# Compatibility aliases (documents were the first artifact; records were once file-only).
EnterpriseArtifact = VersionedEnterpriseArtifact
IngestionRecord = VersionedEnterpriseArtifact
SourceItem = VersionedEnterpriseArtifact
