"""Artifact Handlers — one per artifact type; documents are the first.

A handler maps ONE artifact type into Client360's canonical model. It transforms + validates through the
injected Storage Service (never the filesystem directly) and, on apply (not built), writes new versioned
artifacts. Adding a new business object (email, task, CRM entity, tax return, AI metadata, …) is one new
handler — the engine and every other stage are unchanged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.importers.taxdome_drive import _is_ignored_file
from app.services.migration.artifact import VersionedEnterpriseArtifact
from app.services.migration.config import MigrationConfig
from app.services.migration.identity import CanonicalMatch
from app.services.migration.storage import StorageService

_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def _year_and_subpath(dir_parts: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    if dir_parts and _YEAR_RE.match(dir_parts[0]):
        return dir_parts[0], dir_parts[1:]
    return "Undated", dir_parts


def _sanitize(component: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", component).strip().rstrip(".") or "_"


@dataclass
class HandlerResult:
    est_rows: int
    fields: dict
    exceptions: list[dict] = field(default_factory=list)
    blocked: bool = False
    review: bool = False


class ArtifactHandler:
    """Base handler. ``preview`` is read-only (validate + plan destination via Storage). ``apply`` (not
    built) is the only writer and must create NEW versions — never overwrite."""

    artifact_type: str = "unknown"

    def preview(self, records: list[VersionedEnterpriseArtifact], match: CanonicalMatch,
                config: MigrationConfig, ctx: dict) -> HandlerResult:
        raise NotImplementedError


class DocumentHandler(ArtifactHandler):
    artifact_type = "document"

    def preview(self, records, match, config, ctx) -> HandlerResult:
        storage: StorageService = ctx["storage"]
        dest_root: Path = ctx["dest_root"]
        matched = match.status == "matched" and match.entity is not None
        dest_folder = (f"{match.entity.canonical_id} - {_sanitize(match.entity.display_name)}"
                       if matched else "")
        files = zero = unread = ignored = ph = coll = docrows = 0
        total_bytes = 0
        category = records[0].payload.get("category", "Documents") if records else "Documents"
        for rec in records:
            files += 1
            fname = Path(rec.payload["rel_within_group"]).name
            if _is_ignored_file(fname):
                ignored += 1
                continue
            info = storage.stat(rec.payload["abs_path"])           # STORAGE service — never os.* directly
            if not info.exists:
                unread += 1
                continue
            total_bytes += info.size
            docrows += 1
            if info.size == 0:
                zero += 1
            if info.is_placeholder:
                ph += 1
            ctx["name_size_seen"][(fname.lower(), info.size)] = ctx["name_size_seen"].get((fname.lower(), info.size), 0) + 1
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


#: The permanent artifact-handler registry. Register a handler to support a new business object.
HANDLERS: dict[str, ArtifactHandler] = {
    DocumentHandler.artifact_type: DocumentHandler(),
}
