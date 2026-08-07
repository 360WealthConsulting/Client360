"""Enterprise Ingestion Engine for Client360 — permanent infrastructure, not a one-time utility.

ONE engine. MANY source adapters. MANY artifact types. The authoritative model is Client360's canonical
data model (people / households / organizations / relationships / documents / notes / tasks / …) plus the
one on-prem repository at ``D:\\Client360Data`` for file-bearing artifacts.

    ┌ adapters (discover + normalize ONLY) ┐        ┌ engine (source-agnostic, permanent) ┐
    │ TaxDome · SharePoint · OneDrive ·    │        │ Discover → Normalize → Classify →   │
    │ Wealthbox · Drake · Scanner · Email  │  ───►  │ Canonical Match → Validate → Preview │
    │ · CSV · firm acquisitions · …        │        │ → Apply → Reconcile → Archive Source │
    └──────────────────────────────────────┘        └──────────────────────────────────────┘

Two extension seams, both permanent:
  * a **SourceAdapter** (one per source/firm) yields typed :class:`IngestionRecord` s — discovery only;
  * an **ArtifactHandler** (one per artifact type) knows how to validate / preview / (later) apply that
    artifact into the canonical model. Documents are simply the first handler.

Onboarding another firm ten years from now = writing ONE adapter. A brand-new business object = ONE
handler. Everything between — matching, validation, preview, apply, reconciliation, provenance,
rollback, archival — is written once, here, and shared by every source and every artifact type.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from app.importers.taxdome_drive import _folder_person_keys, _is_ignored_file, _name_key
from app.services.migration.base import MigrationJob, Mode, Outcome
from app.services.migration.config import MigrationConfig
from app.services.migration.events import (
    CollectingEventPublisher,
    EventPublisher,
    Stage,
    StageEvent,
)

_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_FILE_ATTRIBUTE_OFFLINE = 0x1000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_PLACEHOLDER_MASK = _FILE_ATTRIBUTE_OFFLINE | _FILE_ATTRIBUTE_RECALL_ON_OPEN | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS


# --- the normalized record every adapter emits (any artifact type) -----------

@dataclass
class EnterpriseArtifact:
    """One normalized enterprise object from any source — the engine assumes NOTHING about files. A
    ``document``'s payload holds a file path; a ``note``'s holds body text; a ``contact``/``task``/
    ``email``/``event``/AI-metadata artifact holds structured fields. ``artifact_type`` selects the
    handler that maps it into Client360's canonical model."""

    source_system: str
    artifact_type: str                   # "document" | "note" | "task" | "email" | "contact" | "event" | …
    group_key: str                       # entity hint used for canonical matching (e.g. client folder / name)
    payload: dict = field(default_factory=dict)
    entity_type: str | None = None       # optional pre-resolved canonical (record-based sources)
    canonical_id: int | None = None
    display_name: str | None = None
    source_metadata: dict = field(default_factory=dict)   # provenance (source ids, paths, timestamps, …)


class SourceAdapter(ABC):
    """A source/firm adapter. Implements ONLY discovery + where its data lives — nothing downstream."""

    source_system: str = "unknown"

    @abstractmethod
    def discover(self, config: MigrationConfig) -> Iterator[IngestionRecord]:
        ...

    def source_root(self, config: MigrationConfig) -> Path:  # noqa: ARG002 — adapters override
        return Path(".")

    def available(self, config: MigrationConfig) -> bool:
        return self.source_root(config).exists()


# --- shared helpers (identical for every source AND artifact type) ------------

def _is_placeholder(st) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _PLACEHOLDER_MASK)


def _year_and_subpath(dir_parts: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    if dir_parts and _YEAR_RE.match(dir_parts[0]):
        return dir_parts[0], dir_parts[1:]
    return "Undated", dir_parts


def _sanitize(component: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", component).strip().rstrip(".") or "_"


def _person_display(p: dict) -> str:
    last, first = (p.get("last_name") or "").strip(), (p.get("first_name") or "").strip()
    return f"{last}, {first}".strip(", ") or (p.get("full_name") or f"Person {p['id']}")


def _load_directory(conn):
    from sqlalchemy import select

    from app.db import households, people
    idx: dict[str, list[dict]] = {}
    for r in conn.execute(select(people.c.id, people.c.first_name, people.c.last_name,
                                 people.c.full_name, people.c.household_id)).mappings():
        key = _name_key(r["full_name"])
        if key:
            idx.setdefault(key, []).append(dict(r))
    hh = {r["id"]: r["name"] for r in conn.execute(select(households.c.id, households.c.name)).mappings()}
    return idx, hh


def classify_group(group_key: str, idx: dict[str, list[dict]], hh: dict[int, str]) -> dict:
    """matched / ambiguous / unmatched for a group, with matched entity type + canonical id + display.
    Source-agnostic: any adapter that groups by a person/household name reuses this."""
    base = {"status": "unmatched", "reason": "no matching canonical person", "entity_type": "",
            "canonical_id": None, "display_name": "", "candidates": []}
    keys = _folder_person_keys(group_key)
    if not keys:
        return {**base, "reason": "no parseable name"}
    per_key = {k: idx.get(k, []) for k in keys}
    if any(len(v) > 1 for v in per_key.values()):
        cand = sorted({c["full_name"] for v in per_key.values() for c in v})
        return {**base, "status": "ambiguous", "reason": "a name matches multiple people", "candidates": cand[:8]}
    matched = [v[0] for v in per_key.values() if len(v) == 1]
    if not matched:
        return base
    unique = {m["id"] for m in matched}
    households = {m["household_id"] for m in matched if m["household_id"] is not None}
    if len(keys) == 1 and len(unique) == 1:
        p = matched[0]
        return {"status": "matched", "reason": "unique person", "entity_type": "person",
                "canonical_id": p["id"], "display_name": _person_display(p), "candidates": [p["full_name"]]}
    if len(households) == 1:
        hid = next(iter(households))
        return {"status": "matched", "reason": "joint -> shared household", "entity_type": "household",
                "canonical_id": hid, "display_name": hh.get(hid) or f"Household {hid}",
                "candidates": [m["full_name"] for m in matched]}
    if len(unique) == 1:
        p = matched[0]
        return {"status": "matched", "reason": "single distinct person", "entity_type": "person",
                "canonical_id": p["id"], "display_name": _person_display(p), "candidates": [p["full_name"]]}
    return {**base, "status": "ambiguous", "reason": "matched people without one common household",
            "candidates": [m["full_name"] for m in matched]}


def _dest_preflight(dest_root: Path, source_bytes: int) -> dict:
    drive = Path(dest_root.anchor or dest_root)
    out = {"dest_root_exists": dest_root.exists(), "dest_drive": str(drive), "dest_drive_exists": drive.exists()}
    try:
        usage = shutil.disk_usage(str(drive if drive.exists() else dest_root))
        out["dest_free_gb"] = round(usage.free / (1024 ** 3), 2)
        out["source_gb"] = round(source_bytes / (1024 ** 3), 2)
        out["fits_with_10pct_margin"] = usage.free > source_bytes * 1.1
    except OSError:
        out["dest_free_gb"] = None
        out["fits_with_10pct_margin"] = None
    return out


# --- artifact handlers (one per artifact type; documents are the first) -------

@dataclass
class HandlerResult:
    est_rows: int
    fields: dict                          # merged into the per (group, artifact) preview row
    exceptions: list[dict] = field(default_factory=list)
    blocked: bool = False                 # affects readiness (e.g. cloud-only placeholders)
    review: bool = False                  # affects readiness (e.g. unreadable / collisions)


class ArtifactHandler(ABC):
    """Maps ONE artifact type into the canonical model. ``preview`` is read-only; ``apply`` (added later)
    is the only place that writes. Registering a handler is how a new business object is supported."""

    artifact_type: str = "unknown"

    @abstractmethod
    def preview(self, records: list[IngestionRecord], match: dict, config: MigrationConfig, ctx: dict) -> HandlerResult:
        ...


class DocumentHandler(ArtifactHandler):
    """Documents → files under ``<dest>\\Clients\\<id> - <display>\\<category>\\<year>\\<subpath>`` +
    (on apply) canonical ``documents`` + ``document_sources`` rows. Validates placeholders / zero-byte /
    unreadable / duplicate candidates / destination collisions."""

    artifact_type = "document"

    def preview(self, records, match, config, ctx) -> HandlerResult:
        dest_root = ctx["dest_root"]
        matched = match["status"] == "matched"
        dest_folder = f"{match['canonical_id']} - {_sanitize(match['display_name'])}" if matched else ""
        files = zero = unread = ignored = ph = coll = docrows = 0
        total_bytes = 0
        category = records[0].payload.get("category", "Documents") if records else "Documents"
        for rec in records:
            files += 1
            abs_path = rec.payload["abs_path"]
            fname = Path(rec.payload["rel_within_group"]).name
            if _is_ignored_file(fname):
                ignored += 1
                continue
            try:
                st = os.stat(abs_path)
            except OSError:
                unread += 1
                continue
            size = st.st_size
            total_bytes += size
            docrows += 1
            if size == 0:
                zero += 1
            if _is_placeholder(st):
                ph += 1
            ctx["name_size_seen"][(fname.lower(), size)] = ctx["name_size_seen"].get((fname.lower(), size), 0) + 1
            if matched:
                dir_parts = Path(rec.payload["rel_within_group"]).parts[:-1]
                year, sub = _year_and_subpath(dir_parts)
                dest = str(dest_root / "Clients" / dest_folder / category / year / Path(*sub) / fname)
                if ctx["dest_seen"].get(dest):
                    coll += 1
                ctx["dest_seen"][dest] = ctx["dest_seen"].get(dest, 0) + 1
        exceptions = []
        if ph:
            exceptions.append({"artifact_type": "document", "reason": f"{ph} cloud-only placeholder(s)"})
        if coll:
            exceptions.append({"artifact_type": "document", "reason": f"{coll} destination collision(s)"})
        fields = {
            "category": category,
            "proposed_destination_root": str(dest_root / "Clients" / dest_folder) if matched else "(review queue)",
            "files": files, "bytes": total_bytes, "placeholder_count": ph, "zero_byte_count": zero,
            "unreadable_count": unread, "ignored_count": ignored, "collision_count": coll,
        }
        return HandlerResult(est_rows=docrows, fields=fields, exceptions=exceptions,
                             blocked=bool(ph), review=bool(unread or coll))


#: The permanent artifact-handler registry. Add a handler to support a new business object.
HANDLERS: dict[str, ArtifactHandler] = {
    DocumentHandler.artifact_type: DocumentHandler(),
}


# --- the engine (source-agnostic AND artifact-agnostic) ----------------------

class IngestionEngine:
    """Orchestrates the independent ingestion stages for any adapter and any artifact type. Each stage
    runs as its own step and publishes a StageEvent on completion (Event Publishing service), so
    downstream systems (OCR / AI / indexing / search / compliance / audit) subscribe without touching the
    engine. Preview runs Discovery → Normalization → Canonical Matching → Transformation → Validation →
    Preview; Apply / Reconciliation / Retirement are separate stages built after preview approval. Preview
    is strictly read-only — its default publisher keeps events in memory (no database writes)."""

    def __init__(self, adapter: SourceAdapter, config: MigrationConfig, publisher: EventPublisher | None = None):
        self.adapter = adapter
        self.config = config
        self.publisher = publisher or CollectingEventPublisher()
        self.stages_run: list[str] = []

    def _emit(self, stage: Stage, counts: dict) -> None:
        self.stages_run.append(str(stage))
        self.publisher.publish(StageEvent(stage=stage, source_system=self.adapter.source_system, counts=counts))

    # -- stages not yet implemented (declared so the platform shape is explicit) --
    def apply(self, *_a, **_k):
        raise NotImplementedError("APPLY stage is not implemented yet (preview must be approved first).")

    def reconcile(self, *_a, **_k):
        raise NotImplementedError("RECONCILIATION stage is not implemented yet.")

    def retire(self, *_a, **_k):
        raise NotImplementedError("RETIREMENT stage is not implemented yet.")

    def preview(self) -> tuple[dict, list[dict], list[dict], list[str]]:
        cfg = self.config
        dest_root = cfg.migration_dest_root
        src_root = self.adapter.source_root(cfg)
        if not self.adapter.available(cfg):
            return ({"groups": 0, "source_root": str(src_root), "destination_root": str(dest_root),
                     "stage_events": []},
                    [], [{"reason": f"source not found: {src_root}"}], [f"Source not found: {src_root}"])

        # STAGE: DISCOVERY + NORMALIZATION (the only source-specific step)
        groups: dict[str, dict[str, list[EnterpriseArtifact]]] = {}
        artifact_types: set[str] = set()
        total_records = 0
        for rec in self.adapter.discover(cfg):
            total_records += 1
            artifact_types.add(rec.artifact_type)
            groups.setdefault(rec.group_key, {}).setdefault(rec.artifact_type, []).append(rec)
        self._emit(Stage.DISCOVERY, {"groups": len(groups), "records": total_records,
                                     "artifact_types": sorted(artifact_types)})
        self._emit(Stage.NORMALIZATION, {"records": total_records})

        # STAGE: CANONICAL MATCHING (resolve each group's entity up front)
        idx: dict[str, list[dict]] = {}
        hh: dict[int, str] = {}
        db_note = None
        try:
            from app.db import engine
            with engine.connect() as conn:
                idx, hh = _load_directory(conn)
        except Exception as exc:  # noqa: BLE001 — preview must not fail on DB access
            db_note = f"canonical directory unavailable ({exc}); groups reported unmatched"
        matches: dict[str, dict] = {}
        by_status = {"matched": 0, "ambiguous": 0, "unmatched": 0}
        for group_key, by_type in groups.items():
            first = next(iter(next(iter(by_type.values()))))
            if first.entity_type and first.canonical_id:              # adapter pre-resolved the canonical entity
                match = {"status": "matched", "reason": "adapter-resolved", "entity_type": first.entity_type,
                         "canonical_id": first.canonical_id,
                         "display_name": first.display_name or str(first.canonical_id), "candidates": []}
            elif idx:
                match = classify_group(group_key, idx, hh)
            else:
                match = {"status": "unmatched", "reason": db_note or "no canonical directory",
                         "entity_type": "", "canonical_id": None, "display_name": "", "candidates": []}
            matches[group_key] = match
            by_status[match["status"]] += 1
        self._emit(Stage.CANONICAL_MATCHING, dict(by_status))

        # STAGE: TRANSFORMATION + VALIDATION + PREVIEW (per artifact type, via its handler)
        ctx = {"name_size_seen": {}, "dest_seen": {}, "dest_root": dest_root}
        rows: list[dict] = []
        exceptions: list[dict] = []
        by_readiness = {"ready": 0, "review-required": 0, "blocked": 0}
        est_rows = {"matched": 0, "ambiguous": 0, "unmatched": 0}
        per_type_rows: dict[str, int] = {}
        agg = {"files": 0, "bytes": 0, "zero_byte": 0, "unreadable": 0, "ignored": 0, "placeholders": 0}

        for group_key, by_type in sorted(groups.items(), key=lambda kv: kv[0].lower()):
            match = matches[group_key]
            for artifact_type, records in sorted(by_type.items()):
                per_type_rows[artifact_type] = per_type_rows.get(artifact_type, 0) + 1
                handler = HANDLERS.get(artifact_type)
                if handler is None:
                    exceptions.append({"source_group": group_key, "artifact_type": artifact_type,
                                       "reason": "no handler registered for this artifact type"})
                    continue
                res = handler.preview(records, match, cfg, ctx)
                est_rows[match["status"]] += res.est_rows
                readiness = ("review-required" if match["status"] != "matched" else
                             "blocked" if res.blocked else "review-required" if res.review else "ready")
                by_readiness[readiness] += 1
                agg["files"] += res.fields.get("files", 0)
                agg["bytes"] += res.fields.get("bytes", 0)
                agg["zero_byte"] += res.fields.get("zero_byte_count", 0)
                agg["unreadable"] += res.fields.get("unreadable_count", 0)
                agg["ignored"] += res.fields.get("ignored_count", 0)
                agg["placeholders"] += res.fields.get("placeholder_count", 0)
                for e in res.exceptions:
                    exceptions.append({"source_group": group_key, **e})
                rows.append({
                    "source_group": group_key, "artifact_type": artifact_type,
                    "classification": match["status"], "match_reason": match["reason"],
                    "entity_type": match["entity_type"], "canonical_id": match["canonical_id"] or "",
                    "display_name": match["display_name"], "estimated_document_rows": res.est_rows,
                    "readiness": readiness, "candidate_names": "; ".join(match["candidates"]),
                    **res.fields,
                })

        dup_groups = sum(1 for n in ctx["name_size_seen"].values() if n > 1)
        dup_files = sum(n - 1 for n in ctx["name_size_seen"].values() if n > 1)
        collisions = sum(v - 1 for v in ctx["dest_seen"].values() if v > 1)
        self._emit(Stage.TRANSFORMATION, {"destinations_planned": est_rows["matched"]})
        self._emit(Stage.VALIDATION, {"blocked": by_readiness["blocked"],
                                      "review_required": by_readiness["review-required"],
                                      "issues": len(exceptions)})
        self._emit(Stage.PREVIEW, {"rows": len(rows)})
        counts = {
            "source_system": self.adapter.source_system, "artifact_types": sorted(artifact_types),
            "stage_events": list(self.stages_run),
            "source_root": str(src_root), "destination_root": str(dest_root),
            "groups": len(groups), "top_level_folders": len(groups),
            "matched_folders": by_status["matched"], "ambiguous_folders": by_status["ambiguous"],
            "unmatched_folders": by_status["unmatched"],
            "ready_folders": by_readiness["ready"], "review_required_folders": by_readiness["review-required"],
            "blocked_folders": by_readiness["blocked"],
            "rows_by_artifact_type": per_type_rows,
            "total_files": agg["files"], "total_bytes": agg["bytes"],
            "estimated_gb": round(agg["bytes"] / (1024 ** 3), 2),
            "ignored_files": agg["ignored"], "zero_byte_files": agg["zero_byte"],
            "unreadable_files": agg["unreadable"], "cloud_only_placeholders": agg["placeholders"],
            "duplicate_candidate_groups": dup_groups, "duplicate_candidate_files": dup_files,
            "destination_collisions": collisions,
            "estimated_document_rows_total": sum(est_rows.values()),
            "estimated_document_rows_matched": est_rows["matched"],
            "estimated_document_rows_ambiguous_review": est_rows["ambiguous"],
            "estimated_document_rows_unmatched_review": est_rows["unmatched"],
            **_dest_preflight(dest_root, agg["bytes"]),
        }
        notes = [
            "PREVIEW ONLY — no database rows, no files copied/moved, no existing data modified.",
            f"Enterprise ingestion engine · adapter: {self.adapter.source_system} (discovery only) · "
            f"artifact types: {', '.join(sorted(artifact_types)) or 'none'}.",
            "Ambiguous/unmatched groups are review-only: no unlinked canonical rows proposed, nothing "
            "copied — held in the human-review queue until linked (MDM; no bulk auto-merge).",
            "cloud_only_placeholders>0 marks groups BLOCKED — those files must be hydrated before apply.",
        ]
        if db_note:
            notes.append(db_note)
        return counts, rows, exceptions, notes


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
        f"  D: drive exists: {counts.get('dest_drive_exists')}   dest folder exists: {counts.get('dest_root_exists')}",
        f"  free: {counts.get('dest_free_gb')} GB   source: {counts.get('source_gb', counts.get('estimated_gb'))} GB"
        f"   fits(+10%): {counts.get('fits_with_10pct_margin')}",
        "",
        f"entity groups : {counts.get('groups')}   "
        f"matched: {counts.get('matched_folders')}   ambiguous: {counts.get('ambiguous_folders')}   "
        f"unmatched: {counts.get('unmatched_folders')}",
        f"readiness  ready: {counts.get('ready_folders')}   review-required: "
        f"{counts.get('review_required_folders')}   blocked: {counts.get('blocked_folders')}",
        f"rows by artifact type: {counts.get('rows_by_artifact_type')}",
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
    PREVIEW-only in this phase (a generic APPLY engine consumes the same records after approval)."""

    supported_modes = frozenset({Mode.PREVIEW})

    def __init__(self, adapter: SourceAdapter, config: MigrationConfig | None = None):
        super().__init__(config)
        self.adapter = adapter
        self.source_system = adapter.source_system

    def _preview(self, **_opts) -> Outcome:
        counts, rows, exceptions, notes = IngestionEngine(self.adapter, self.config).preview()
        write_named_artifacts(getattr(self, "_last_run_dir", None), rows, counts, notes, self.source_system)
        return Outcome(counts=counts, exceptions=exceptions, reconciliation=rows, notes=notes)


# Backward-compatible aliases (documents were the first implementation; records were once file-only).
DocumentMigrationJob = IngestionJob
IngestionRecord = EnterpriseArtifact
SourceItem = EnterpriseArtifact
