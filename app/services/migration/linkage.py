"""Canonical Linkage Remediation — READ-ONLY preview.

Many migrated documents (e.g. TaxDome folders that were never matched at import time) have NO canonical
link: ``person_id`` / ``household_id`` / ``organization_id`` are all NULL, so the relocation naming policy
routes them to ``Firm``. They are not firm documents — they are client/business documents whose canonical
entity link was never populated. This service PREVIEWS how each unlinked document's source folder resolves
to an EXISTING canonical entity, so the links can be repaired before relocation.

Matching is strict and reuses the proven TaxDome matchers — exact/normalized only, ambiguity is never
guessed, and NOTHING is ever created:

    1. existing canonical person (exact/normalized folder-name match)
    2. existing canonical household (joint folder whose members share one household)
    3. existing canonical organization (relationship_entities standalone entity, exact/normalized name)
    4. otherwise Review / Unresolved (including every ambiguous match)

READ-ONLY: it proposes ``entity_type`` + ``entity_id`` per document and writes report artifacts only. It
never writes a row, never creates an entity, never moves bytes, and never touches ``storage_uri`` or
``document_sources``.
"""
from __future__ import annotations

from app.importers.taxdome_drive import _is_ignored_file, _name_key
from app.services.migration.artifact import VersionedEnterpriseArtifact
from app.services.migration.base import MigrationJob, Mode, Outcome
from app.services.migration.identity import IdentityService

_TAXDOME = "TaxDome Drive"


def _source_system(tags) -> str:
    return tags.get("source_system", "") if isinstance(tags, dict) else ""


def _folder(tags) -> str:
    return (tags.get("taxdome_folder") or "").strip() if isinstance(tags, dict) else ""


class LinkageRemediationJob(MigrationJob):
    """Read-only preview that resolves unlinked documents' source folders to existing canonical entities."""

    source_system = "Linkage Remediation"
    supported_modes = frozenset({Mode.PREVIEW})

    def __init__(self, config=None, *, identity: IdentityService | None = None):
        super().__init__(config)
        self.identity = identity or IdentityService(self.config)

    # -- read-only data access ------------------------------------------------
    def _load_unlinked(self):
        from sqlalchemy import and_, select

        from app.db import engine, metadata
        documents = metadata.tables["documents"]
        cols = [documents.c.id, documents.c.original_name, documents.c.tags, documents.c.storage_uri]
        where = and_(documents.c.person_id.is_(None), documents.c.household_id.is_(None),
                     documents.c.organization_id.is_(None), documents.c.status != "deleted")
        with engine.connect() as conn:
            return [dict(m) for m in conn.execute(select(*cols).where(where)).mappings()]

    def _load_org_index(self) -> dict[str, list[dict]]:
        """Index standalone organizations from relationship_entities by normalized name. Standalone =
        not a mirror of a person/household (person_id and household_id both NULL)."""
        from sqlalchemy import and_, select

        from app.db import engine, metadata
        re_tbl = metadata.tables.get("relationship_entities")
        index: dict[str, list[dict]] = {}
        if re_tbl is None:
            return index
        where = and_(re_tbl.c.person_id.is_(None), re_tbl.c.household_id.is_(None),
                     re_tbl.c.active.is_(True), re_tbl.c.name.isnot(None))
        with engine.connect() as conn:
            for r in conn.execute(select(re_tbl.c.id, re_tbl.c.name, re_tbl.c.entity_type).where(where)).mappings():
                key = _name_key(r["name"])
                if key:
                    index.setdefault(key, []).append(dict(r))
        return index

    # -- resolution (strict; never creates, never guesses) --------------------
    def _resolve_folder(self, folder: str, org_index: dict) -> dict:
        if not folder:
            return {"resolution": "unmatched", "entity_type": "", "entity_id": "",
                    "reason": "document has no source folder", "candidates": []}
        record = VersionedEnterpriseArtifact(source_system=self.source_system,
                                             artifact_type="document", group_key=folder)
        m = self.identity.resolve(folder, record)          # 1) person / 2) household (exact/normalized)
        if m.status == "matched" and m.entity is not None:
            kind = "people" if m.entity.entity_type == "person" else "households"
            return {"resolution": kind, "entity_type": m.entity.entity_type,
                    "entity_id": m.entity.canonical_id, "reason": f"identity: {m.reason}",
                    "candidates": list(m.candidates)}
        if m.status == "ambiguous":
            return {"resolution": "ambiguous", "entity_type": "", "entity_id": "",
                    "reason": f"ambiguous person/household: {m.reason}", "candidates": list(m.candidates)}
        # 3) organization (only when no person/household matched)
        cands = org_index.get(_name_key(folder), [])
        if len(cands) == 1:
            return {"resolution": "businesses", "entity_type": "organization",
                    "entity_id": cands[0]["id"], "reason": "organization exact/normalized name match",
                    "candidates": [cands[0]["name"]]}
        if len(cands) > 1:
            return {"resolution": "ambiguous", "entity_type": "", "entity_id": "",
                    "reason": "ambiguous organization match", "candidates": [c["name"] for c in cands[:8]]}
        return {"resolution": "unmatched", "entity_type": "", "entity_id": "",
                "reason": m.reason or "no canonical person/household/organization match", "candidates": []}

    # -- preview --------------------------------------------------------------
    def _preview(self, **_opts) -> Outcome:
        self.identity.load()
        org_index = self._load_org_index()

        docs = [d for d in self._load_unlinked() if _source_system(d.get("tags")) == _TAXDOME]
        kept, junk = [], 0
        for d in docs:
            if _is_ignored_file(d.get("original_name") or ""):
                junk += 1
            else:
                kept.append(d)

        # resolve each unique folder ONCE, then attribute to its documents
        folders: dict[str, list[dict]] = {}
        for d in kept:
            folders.setdefault(_folder(d.get("tags")), []).append(d)
        resolved = {f: self._resolve_folder(f, org_index) for f in folders}

        from collections import Counter
        buckets = ("people", "households", "businesses", "ambiguous", "unmatched")
        doc_counts = dict.fromkeys(buckets, 0)
        folder_counts = dict.fromkeys(buckets, 0)
        by_source_system: Counter = Counter()
        from_named_folder = 0
        rows: list[dict] = []
        exceptions: list[dict] = []

        for folder, group in sorted(folders.items(), key=lambda kv: kv[0].lower()):
            res = resolved[folder]
            b = res["resolution"]
            folder_counts[b] += 1
            doc_counts[b] += len(group)
            for d in group:
                src_system = _source_system(d.get("tags"))
                by_source_system[src_system or "(none)"] += 1
                if folder:
                    from_named_folder += 1
                rows.append({
                    "document_id": d["id"], "source_folder": folder or "(none)",
                    "source_system": src_system, "original_name": d.get("original_name") or "",
                    "resolution": b, "proposed_entity_type": res["entity_type"],
                    "proposed_entity_id": res["entity_id"], "match_reason": res["reason"],
                    "candidates": "; ".join(res["candidates"]),
                })
            if b in ("ambiguous", "unmatched"):
                exceptions.append({"source_folder": folder or "(none)", "resolution": b,
                                   "reason": res["reason"], "document_count": len(group),
                                   "candidates": "; ".join(res["candidates"])})

        counts = {
            "unlinked_documents": len(kept), "junk_excluded": junk,
            "unique_source_folders": len(folders),
            "documents_resolvable_people": doc_counts["people"],
            "documents_resolvable_households": doc_counts["households"],
            "documents_resolvable_businesses": doc_counts["businesses"],
            "documents_ambiguous": doc_counts["ambiguous"],
            "documents_unmatched": doc_counts["unmatched"],
            "folders_people": folder_counts["people"], "folders_households": folder_counts["households"],
            "folders_businesses": folder_counts["businesses"], "folders_ambiguous": folder_counts["ambiguous"],
            "folders_unmatched": folder_counts["unmatched"],
            "documents_from_named_folder": from_named_folder,
            "documents_by_source_system": dict(by_source_system),
        }
        if self.identity.note:
            counts["identity_note"] = self.identity.note
        notes = [
            "PREVIEW ONLY — proposes canonical links; writes NO rows, creates NO entities, moves NO bytes, "
            "does not touch storage_uri or document_sources.",
            "Strict matching: exact/normalized only (reuses the TaxDome matchers). Ambiguous matches and "
            "no-match folders both go to Review/Unresolved — never guessed, never bulk auto-linked.",
            "Priority: existing person -> existing household -> existing organization -> Review/Unresolved.",
            f"Junk artifacts excluded (Thumbs.db / desktop.ini / ~$* / *.tmp): {junk}.",
        ]
        return Outcome(counts=counts, exceptions=exceptions, reconciliation=rows, notes=notes)
