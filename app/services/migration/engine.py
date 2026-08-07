"""Enterprise Ingestion Platform — the orchestrator.

The engine ONLY composes services and runs the stage pipeline, emitting an event after each completed
stage. It embeds no matching (asks the Identity Service), no filesystem access (handlers use the Storage
Service), and no knowledge of downstream subscribers (they subscribe to the Event Publishing service).
Apply / Reconciliation / Retirement are separate services and are not run here — preview is read-only.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from app.services.migration.artifact import (
    CanonicalEntity,
    EnterpriseArtifact,
    IngestionRecord,
    SourceItem,
    VersionedEnterpriseArtifact,
)
from app.services.migration.base import MigrationJob, Mode, Outcome
from app.services.migration.config import MigrationConfig
from app.services.migration.events import (
    CollectingEventPublisher,
    EventPublisher,
    OutboxEventPublisher,
    Stage,
    StageEvent,
)
from app.services.migration.handlers import (
    HANDLERS,
    ArtifactHandler,
    DocumentHandler,
    HandlerResult,
)
from app.services.migration.identity import (
    SUPPORTED_ENTITY_TYPES,
    CanonicalMatch,
    IdentityService,
)
from app.services.migration.ports import SourceAdapter
from app.services.migration.stages import (
    ApplyService,
    CanonicalMatchingService,
    DiscoveryService,
    NormalizationService,
    PipelineContext,
    PreviewService,
    ReconciliationService,
    RetirementService,
    TransformationService,
    ValidationService,
)
from app.services.migration.storage import (
    LocalFilesystemStorage,
    RepositoryArea,
    StorageService,
    repository_uri,
)
from app.services.migration.transform import TRANSFORMERS, DerivedArtifact, Transformer

__all__ = [
    "VersionedEnterpriseArtifact", "EnterpriseArtifact", "IngestionRecord", "SourceItem", "CanonicalEntity",
    "SourceAdapter", "IdentityService", "CanonicalMatch", "SUPPORTED_ENTITY_TYPES",
    "StorageService", "LocalFilesystemStorage", "RepositoryArea", "repository_uri",
    "Transformer", "DerivedArtifact", "TRANSFORMERS",
    "ArtifactHandler", "DocumentHandler", "HandlerResult", "HANDLERS", "Stage", "StageEvent",
    "EventPublisher", "CollectingEventPublisher", "OutboxEventPublisher",
    "IngestionEngine", "IngestionJob", "DocumentMigrationJob",
]

#: The read-only preview pipeline: each stage is an independent service run in order.
READ_PIPELINE = (DiscoveryService, NormalizationService, CanonicalMatchingService,
                 TransformationService, ValidationService, PreviewService)


class IngestionEngine:
    """Composes the services and runs the stage pipeline. Preview is strictly read-only."""

    def __init__(self, adapter: SourceAdapter, config: MigrationConfig, *,
                 identity: IdentityService | None = None, storage: StorageService | None = None,
                 publisher: EventPublisher | None = None):
        self.adapter = adapter
        self.config = config
        self.identity = identity or IdentityService(config)
        self.storage = storage or LocalFilesystemStorage()
        self.publisher = publisher or CollectingEventPublisher()
        # Mutating stages are separate services and are never run by preview.
        self.apply_service = ApplyService()
        self.reconciliation_service = ReconciliationService()
        self.retirement_service = RetirementService()
        self.stages_run: list[str] = []

    def _emit(self, stage: Stage, counts: dict) -> None:
        self.stages_run.append(str(stage))
        self.publisher.publish(StageEvent(stage=stage, source_system=self.adapter.source_system, counts=counts))

    def preview(self) -> tuple[dict, list[dict], list[dict], list[str]]:
        cfg = self.config
        dest_root = cfg.migration_dest_root
        src_root = self.adapter.source_root(cfg)
        if not self.adapter.available(cfg):
            return ({"groups": 0, "source_root": str(src_root), "destination_root": str(dest_root),
                     "stage_events": []},
                    [], [{"reason": f"source not found: {src_root}"}], [f"Source not found: {src_root}"])

        ctx = PipelineContext(config=cfg, adapter=self.adapter, identity=self.identity,
                              storage=self.storage, dest_root=dest_root)
        for stage_cls in READ_PIPELINE:
            service = stage_cls()
            self._emit(service.stage, service.run(ctx))
        ctx.counts["stage_events"] = list(self.stages_run)

        notes = [
            "PREVIEW ONLY — no database rows, no files copied/moved, no existing data modified.",
            f"Enterprise ingestion platform · adapter: {self.adapter.source_system} · "
            f"artifact types: {', '.join(sorted(ctx.artifact_types)) or 'none'}.",
            "Ambiguous/unmatched groups are review-only: no unlinked canonical rows proposed, nothing "
            "copied — held in the human-review queue until linked (MDM; no bulk auto-merge).",
            "cloud_only_placeholders>0 marks groups BLOCKED — those files must be hydrated before apply.",
        ]
        if self.identity.note:
            notes.append(self.identity.note)
        return ctx.counts, ctx.rows, ctx.exceptions, notes

    # Mutating stages delegate to their separate services (declared, not implemented in this phase).
    def apply(self, *a, **k):
        return self.apply_service.run(*a, **k)

    def reconcile(self, *a, **k):
        return self.reconciliation_service.run(*a, **k)

    def retire(self, *a, **k):
        return self.retirement_service.run(*a, **k)


# --- artifact writer (identical for every source and artifact type) ----------

def write_named_artifacts(run_dir, rows: list[dict], counts: dict, notes: list[str], source_system: str) -> None:
    if run_dir is None:
        return
    run_dir = Path(run_dir)
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with (run_dir / "migration_preview.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields or ["source_group"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    (run_dir / "migration_manifest.json").write_text(
        json.dumps({"source_system": source_system, "mode": "preview", "counts": counts, "notes": notes},
                   indent=2, default=str), encoding="utf-8")
    lines = [
        f"Enterprise Ingestion — PREVIEW (read-only) — {source_system}",
        f"artifact types : {', '.join(counts.get('artifact_types', [])) or 'none'}",
        f"source      : {counts.get('source_root')}",
        f"destination : {counts.get('destination_root')}",
        f"  dest exists: {counts.get('dest_root_exists')}   free: {counts.get('dest_free_gb')} GB   "
        f"source: {counts.get('source_gb', counts.get('estimated_gb'))} GB   fits(+10%): {counts.get('fits_with_10pct_margin')}",
        "",
        f"entity groups : {counts.get('groups')}   matched: {counts.get('matched_folders')}   "
        f"ambiguous: {counts.get('ambiguous_folders')}   unmatched: {counts.get('unmatched_folders')}",
        f"readiness  ready: {counts.get('ready_folders')}   review-required: "
        f"{counts.get('review_required_folders')}   blocked: {counts.get('blocked_folders')}",
        f"rows by artifact type: {counts.get('rows_by_artifact_type')}",
        f"stages run: {counts.get('stage_events')}",
        "",
        f"files: {counts.get('total_files')}   size: {counts.get('estimated_gb')} GB   "
        f"ignored: {counts.get('ignored_files')}   zero-byte: {counts.get('zero_byte_files')}   "
        f"unreadable: {counts.get('unreadable_files')}   placeholders: {counts.get('cloud_only_placeholders')}",
        f"duplicate candidates: {counts.get('duplicate_candidate_groups')} groups / "
        f"{counts.get('duplicate_candidate_files')} files   destination collisions: {counts.get('destination_collisions')}",
        f"estimated canonical rows — total {counts.get('estimated_document_rows_total')} | "
        f"matched {counts.get('estimated_document_rows_matched')} | "
        f"review {counts.get('estimated_document_rows_ambiguous_review', 0) + counts.get('estimated_document_rows_unmatched_review', 0)}",
        "",
        *notes,
    ]
    (run_dir / "migration_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- the generic job (any adapter, any artifact type) ------------------------

class IngestionJob(MigrationJob):
    """A MigrationJob driven by a SourceAdapter. Same pipeline for every source and artifact type;
    PREVIEW-only in this phase."""

    supported_modes = frozenset({Mode.PREVIEW})

    def __init__(self, adapter: SourceAdapter, config: MigrationConfig | None = None):
        super().__init__(config)
        self.adapter = adapter
        self.source_system = adapter.source_system

    def _preview(self, **_opts) -> Outcome:
        counts, rows, exceptions, notes = IngestionEngine(self.adapter, self.config).preview()
        write_named_artifacts(getattr(self, "_last_run_dir", None), rows, counts, notes, self.source_system)
        return Outcome(counts=counts, exceptions=exceptions, reconciliation=rows, notes=notes)


DocumentMigrationJob = IngestionJob
