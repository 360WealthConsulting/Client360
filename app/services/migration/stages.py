"""The ten independent ingestion stages, each a service with one responsibility.

The engine composes these; no stage knows about the others' internals, and none embeds identity, storage,
or the event subscribers. Read stages operate on a shared :class:`PipelineContext`. The mutating stages
(Apply / Reconciliation / Retirement) are separate services, declared but NOT implemented in this phase —
the engine can never apply, reconcile, or retire until they are built and explicitly invoked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.services.migration.config import MigrationConfig
from app.services.migration.events import Stage
from app.services.migration.handlers import HANDLERS
from app.services.migration.identity import CanonicalMatch, IdentityService
from app.services.migration.ports import SourceAdapter
from app.services.migration.storage import StorageService


@dataclass
class PipelineContext:
    config: MigrationConfig
    adapter: SourceAdapter
    identity: IdentityService
    storage: StorageService
    dest_root: object                                    # Path to D:\Client360Data
    groups: dict = field(default_factory=dict)           # group_key -> {artifact_type -> [records]}
    artifact_types: set = field(default_factory=set)
    total_records: int = 0
    matches: dict = field(default_factory=dict)          # group_key -> CanonicalMatch
    handler_results: dict = field(default_factory=dict)  # (group_key, artifact_type) -> HandlerResult
    readiness: dict = field(default_factory=dict)        # (group_key, artifact_type) -> str
    rows: list = field(default_factory=list)
    exceptions: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    # working tallies + dedup state
    by_status: dict = field(default_factory=lambda: {"matched": 0, "ambiguous": 0, "unmatched": 0})
    by_readiness: dict = field(default_factory=lambda: {"ready": 0, "review-required": 0, "blocked": 0})
    est_rows: dict = field(default_factory=lambda: {"matched": 0, "ambiguous": 0, "unmatched": 0})
    per_type_rows: dict = field(default_factory=dict)
    agg: dict = field(default_factory=lambda: {"files": 0, "bytes": 0, "zero_byte": 0,
                                               "unreadable": 0, "ignored": 0, "placeholders": 0})
    name_size_seen: dict = field(default_factory=dict)
    dest_seen: dict = field(default_factory=dict)

    def handler_ctx(self) -> dict:
        return {"storage": self.storage, "dest_root": self.dest_root,
                "name_size_seen": self.name_size_seen, "dest_seen": self.dest_seen}


class DiscoveryService:
    stage = Stage.DISCOVERY

    def run(self, ctx: PipelineContext) -> dict:
        for rec in ctx.adapter.discover(ctx.config):
            ctx.total_records += 1
            ctx.artifact_types.add(rec.artifact_type)
            ctx.groups.setdefault(rec.group_key, {}).setdefault(rec.artifact_type, []).append(rec)
        return {"groups": len(ctx.groups), "records": ctx.total_records,
                "artifact_types": sorted(ctx.artifact_types)}


class NormalizationService:
    stage = Stage.NORMALIZATION

    def run(self, ctx: PipelineContext) -> dict:
        # Adapters emit already-normalized records; this stage is where cross-source field canonicalization
        # would live. Pass-through today.
        return {"records": ctx.total_records}


class CanonicalMatchingService:
    """Asks the Identity Service to resolve each group's canonical entity (engine never matches inline)."""

    stage = Stage.CANONICAL_MATCHING

    def run(self, ctx: PipelineContext) -> dict:
        ctx.identity.load()
        for group_key, by_type in ctx.groups.items():
            first = next(iter(next(iter(by_type.values()))))
            match = ctx.identity.resolve(group_key, first)
            ctx.matches[group_key] = match
            ctx.by_status[match.status] += 1
        return dict(ctx.by_status)


class TransformationService:
    """Plans each artifact's destination + gathers its stats via the Storage Service (per-type handler)."""

    stage = Stage.TRANSFORMATION

    def run(self, ctx: PipelineContext) -> dict:
        hctx = ctx.handler_ctx()
        for group_key, by_type in sorted(ctx.groups.items(), key=lambda kv: kv[0].lower()):
            match = ctx.matches[group_key]
            for artifact_type, records in sorted(by_type.items()):
                ctx.per_type_rows[artifact_type] = ctx.per_type_rows.get(artifact_type, 0) + 1
                handler = HANDLERS.get(artifact_type)
                if handler is None:
                    ctx.exceptions.append({"source_group": group_key, "artifact_type": artifact_type,
                                           "reason": "no handler registered for this artifact type"})
                    continue
                ctx.handler_results[(group_key, artifact_type)] = handler.preview(records, match, ctx.config, hctx)
        return {"planned": len(ctx.handler_results)}


class ValidationService:
    """Interprets each handler's validation outcome into readiness + exceptions + aggregate tallies."""

    stage = Stage.VALIDATION

    def run(self, ctx: PipelineContext) -> dict:
        for (group_key, artifact_type), res in ctx.handler_results.items():
            match: CanonicalMatch = ctx.matches[group_key]
            readiness = ("review-required" if match.status != "matched" else
                         "blocked" if res.blocked else "review-required" if res.review else "ready")
            ctx.readiness[(group_key, artifact_type)] = readiness
            ctx.by_readiness[readiness] += 1
            ctx.est_rows[match.status] += res.est_rows
            ctx.agg["files"] += res.fields.get("files", 0)
            ctx.agg["bytes"] += res.fields.get("bytes", 0)
            ctx.agg["zero_byte"] += res.fields.get("zero_byte_count", 0)
            ctx.agg["unreadable"] += res.fields.get("unreadable_count", 0)
            ctx.agg["ignored"] += res.fields.get("ignored_count", 0)
            ctx.agg["placeholders"] += res.fields.get("placeholder_count", 0)
            for e in res.exceptions:
                ctx.exceptions.append({"source_group": group_key, **e})
        return {"blocked": ctx.by_readiness["blocked"], "review_required": ctx.by_readiness["review-required"],
                "issues": len(ctx.exceptions)}


class PreviewService:
    """Assembles the per-(group, artifact) rows + the aggregate counts (never writes)."""

    stage = Stage.PREVIEW

    def run(self, ctx: PipelineContext) -> dict:
        for (group_key, artifact_type), res in ctx.handler_results.items():
            match: CanonicalMatch = ctx.matches[group_key]
            ent = match.entity
            ctx.rows.append({
                "source_group": group_key, "artifact_type": artifact_type,
                "classification": match.status, "match_reason": match.reason,
                "entity_type": ent.entity_type if ent else "", "canonical_id": ent.canonical_id if ent else "",
                "display_name": ent.display_name if ent else "",
                "estimated_document_rows": res.est_rows,
                "readiness": ctx.readiness[(group_key, artifact_type)],
                "candidate_names": "; ".join(match.candidates),
                **res.fields,
            })
        dup_groups = sum(1 for n in ctx.name_size_seen.values() if n > 1)
        dup_files = sum(n - 1 for n in ctx.name_size_seen.values() if n > 1)
        collisions = sum(v - 1 for v in ctx.dest_seen.values() if v > 1)
        usage_path = ctx.dest_root if ctx.dest_root.exists() else Path(ctx.dest_root.anchor or ".")
        free, _total = ctx.storage.free_and_total(str(usage_path))
        source_bytes = ctx.agg["bytes"]
        ctx.counts = {
            "source_system": ctx.adapter.source_system, "artifact_types": sorted(ctx.artifact_types),
            "source_root": str(ctx.adapter.source_root(ctx.config)), "destination_root": str(ctx.dest_root),
            "groups": len(ctx.groups),
            "matched_folders": ctx.by_status["matched"], "ambiguous_folders": ctx.by_status["ambiguous"],
            "unmatched_folders": ctx.by_status["unmatched"],
            "ready_folders": ctx.by_readiness["ready"], "review_required_folders": ctx.by_readiness["review-required"],
            "blocked_folders": ctx.by_readiness["blocked"], "rows_by_artifact_type": ctx.per_type_rows,
            "total_files": ctx.agg["files"], "total_bytes": ctx.agg["bytes"],
            "estimated_gb": round(ctx.agg["bytes"] / (1024 ** 3), 2),
            "ignored_files": ctx.agg["ignored"], "zero_byte_files": ctx.agg["zero_byte"],
            "unreadable_files": ctx.agg["unreadable"], "cloud_only_placeholders": ctx.agg["placeholders"],
            "duplicate_candidate_groups": dup_groups, "duplicate_candidate_files": dup_files,
            "destination_collisions": collisions,
            "estimated_document_rows_total": sum(ctx.est_rows.values()),
            "estimated_document_rows_matched": ctx.est_rows["matched"],
            "estimated_document_rows_ambiguous_review": ctx.est_rows["ambiguous"],
            "estimated_document_rows_unmatched_review": ctx.est_rows["unmatched"],
            "dest_root_exists": ctx.dest_root.exists(),
            "dest_free_gb": round(free / (1024 ** 3), 2) if free is not None else None,
            "source_gb": round(source_bytes / (1024 ** 3), 2),
            "fits_with_10pct_margin": (free > source_bytes * 1.1) if free is not None else None,
        }
        return {"rows": len(ctx.rows)}


# --- mutating stages: separate services, declared but NOT implemented ---------

class ApplyService:
    """Writes NEW versioned artifacts into the canonical model + repository (via Storage). Never
    overwrites. NOT IMPLEMENTED — built only after the preview is approved."""

    stage = Stage.APPLY

    def run(self, *_a, **_k):
        raise NotImplementedError("APPLY stage is not implemented yet (preview must be approved first).")


class ReconciliationService:
    stage = Stage.RECONCILIATION

    def run(self, *_a, **_k):
        raise NotImplementedError("RECONCILIATION stage is not implemented yet.")


class RetirementService:
    """Retires a legacy source AFTER successful reconciliation + explicit approval. The ingestion engine
    never disables or deletes a legacy system. NOT IMPLEMENTED."""

    stage = Stage.RETIREMENT

    def run(self, *_a, **_k):
        raise NotImplementedError("RETIREMENT stage is not implemented yet (post-reconciliation + approval).")
