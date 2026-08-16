"""Canonical document + source references (ADR-072).

One canonical ``documents`` row, many ``document_sources`` references. Ingestion providers (TaxDome,
Drake, SharePoint, Schwab, AssetMark, Microsoft 365, Upload, Scanner, Email) call
``resolve_or_create_canonical`` with a content SHA-256: an identical document already in Client360 is
reused (a new source reference is attached, provenance preserved) rather than duplicated; a new document
creates the canonical row plus its first source reference. Read side: ``sources_for_documents`` powers
the Documents tab "source references" view.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Table, and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import documents, engine, metadata


def _ds() -> Table:
    ds = metadata.tables.get("document_sources")
    if ds is None:
        ds = Table("document_sources", metadata, autoload_with=engine)
    return ds


def add_source_reference(conn, document_id: int, *, source_system: str, source_uri: str = "",
                         source_path: str | None = None, source_external_id: str | None = None,
                         source_hash: str | None = None, metadata_json: dict | None = None) -> None:
    """Attach (or refresh) a source reference for a canonical document. Idempotent on
    (document_id, source_system, source_uri) — a re-sync updates last_synced_at + availability."""
    ds = _ds()
    now = datetime.now(UTC)
    stmt = pg_insert(ds).values(
        document_id=document_id, source_system=source_system, source_uri=source_uri or "",
        source_path=source_path, source_external_id=source_external_id, source_hash=source_hash,
        available=True, last_synced_at=now, metadata=metadata_json or {})
    conn.execute(stmt.on_conflict_do_update(
        constraint="uq_document_source_ref",
        set_={"source_path": source_path, "source_external_id": source_external_id,
              "source_hash": source_hash, "available": True, "last_synced_at": now,
              "metadata": metadata_json or {}}))


def resolve_or_create_canonical(*, sha256: str, original_name: str, stored_name: str,
                                storage_provider: str, storage_uri: str, storage_path: str,
                                size_bytes: int, content_type: str | None = None,
                                category: str | None = None, tags: dict | None = None,
                                person_id: int | None = None, household_id: int | None = None,
                                source_system: str, source_uri: str = "", source_path: str | None = None,
                                source_external_id: str | None = None, conn=None) -> dict:
    """Resolve a document by content hash (ADR-072). Hash hit → reuse the canonical document and attach
    a source reference (fill NULL ownership only; never overwrite a link). Hash miss → create the
    canonical document + its first source reference. Returns
    ``{document_id, reused, source_system}``."""

    def _do(c):
        existing = c.execute(
            select(documents.c.id, documents.c.person_id, documents.c.household_id)
            .where(documents.c.sha256 == sha256, documents.c.status != "deleted")
            .order_by(documents.c.id).limit(1)).mappings().first()
        if existing is not None:
            doc_id = existing["id"]
            fill = {}
            if person_id is not None and existing["person_id"] is None and household_id is None:
                fill["person_id"] = person_id
            if household_id is not None and existing["household_id"] is None:
                fill["household_id"] = household_id
            if fill:
                c.execute(documents.update().where(documents.c.id == doc_id).values(**fill))
            add_source_reference(c, doc_id, source_system=source_system, source_uri=source_uri,
                                 source_path=source_path, source_external_id=source_external_id,
                                 source_hash=sha256)
            return {"document_id": doc_id, "reused": True, "source_system": source_system}
        doc_id = c.execute(documents.insert().values(
            original_name=original_name, stored_name=stored_name, storage_path=storage_path,
            storage_provider=storage_provider, storage_uri=storage_uri, size_bytes=size_bytes,
            sha256=sha256, content_type=content_type, category=category, tags=tags or {},
            person_id=person_id, household_id=household_id, status="active", archived=False,
            uploaded_by=f"{source_system} Sync").returning(documents.c.id)).scalar_one()
        add_source_reference(c, doc_id, source_system=source_system, source_uri=source_uri,
                             source_path=source_path, source_external_id=source_external_id,
                             source_hash=sha256)
        return {"document_id": doc_id, "reused": False, "source_system": source_system}

    if conn is not None:
        result = _do(conn)
        _analyze_new_document(conn, result)
        return result
    with engine.begin() as connection:
        result = _do(connection)
        _analyze_new_document(connection, result)     # same txn: the new doc is visible; savepoint-isolated
    return result


def _analyze_new_document(conn, result):
    """Future-ingestion hook: analyze every NEW canonical document so it is auto-proposed after ingestion.
    Fully guarded — savepoint-isolated inside analyze_and_persist and wrapped here — so an analysis failure
    (or an unavailable pipeline) NEVER blocks ingestion and NEVER assigns ownership. Skips reused docs."""
    try:
        from app.services.document_pipeline import AUTO_ANALYZE_NEW_DOCUMENTS, analyze_and_persist
        if not AUTO_ANALYZE_NEW_DOCUMENTS or result.get("reused"):
            return
        analyze_and_persist(result["document_id"], conn=conn)
    except Exception:                                 # noqa: BLE001 — ingestion must always succeed
        pass


def sources_for_documents(document_ids) -> dict[int, list[dict]]:
    """All source references for the given canonical documents, keyed by document_id."""
    ids = [i for i in document_ids if i]
    if not ids:
        return {}
    ds = _ds()
    out: dict[int, list[dict]] = {}
    with engine.connect() as conn:
        for r in conn.execute(
            select(ds.c.document_id, ds.c.source_system, ds.c.source_uri, ds.c.source_path,
                   ds.c.source_external_id, ds.c.available, ds.c.last_synced_at)
            .where(ds.c.document_id.in_(ids)).order_by(ds.c.source_system)).mappings():
            out.setdefault(r["document_id"], []).append(dict(r))
    return out


def sources_for_document(document_id: int) -> list[dict]:
    return sources_for_documents([document_id]).get(document_id, [])


def mark_source_unavailable(conn, document_id: int, source_system: str, source_uri: str = "") -> None:
    """Flag a source reference unavailable (the source's copy disappeared). The canonical document and
    other sources are untouched — never deletes the canonical row or the local copy."""
    ds = _ds()
    conn.execute(ds.update().where(and_(
        ds.c.document_id == document_id, ds.c.source_system == source_system,
        ds.c.source_uri == (source_uri or ""))).values(
        available=False, last_synced_at=datetime.now(UTC)))
