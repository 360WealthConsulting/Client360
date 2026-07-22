"""Document versioning (Phase D.16) — immutable version history over document_versions.

Extends the existing (client-portal) ``document_versions`` table into the full model: major/minor
numbering, current flag, author, approval, notes. History is append-only in practice (new versions
supersede; a historical version can be restored to current). Restoring makes a prior version
current again — the history itself is never rewritten.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db import document_versions, documents, engine


class VersionError(Exception):
    """Invalid version operation."""


def _now():
    return datetime.now(UTC)


def create_version(principal, document_id: int, *, actor_user_id, bump="minor", stored_name=None,
                   storage_path=None, storage_uri=None, sha256=None, size_bytes=None,
                   content_type=None, notes=None) -> dict:
    """Create a new current version. ``bump`` is 'major' or 'minor'. The prior current version is
    superseded (is_current cleared); documents.current_version is advanced."""
    with engine.begin() as c:
        if c.scalar(select(documents.c.id).where(documents.c.id == document_id)) is None:
            raise VersionError("document does not exist")
        cur = c.execute(select(document_versions).where(
            document_versions.c.document_id == document_id,
            document_versions.c.is_current.is_(True))).mappings().first()
        maxn = c.scalar(select(func.max(document_versions.c.version_number))
                        .where(document_versions.c.document_id == document_id)) or 0
        major = (cur["major"] if cur else 1)
        minor = (cur["minor"] if cur else 0)
        if bump == "major":
            major, minor = major + 1, 0
        else:
            minor = minor + 1
        if cur:
            c.execute(document_versions.update().where(document_versions.c.id == cur["id"])
                      .values(is_current=False))
        row = c.execute(document_versions.insert().values(
            document_id=document_id, version_number=maxn + 1, major=major, minor=minor,
            stored_name=stored_name, storage_path=storage_path, storage_uri=storage_uri,
            sha256=sha256, size_bytes=size_bytes, content_type=content_type,
            author_user_id=actor_user_id, notes=notes, is_current=True, created_at=_now())
            .returning(document_versions)).mappings().one()
        c.execute(documents.update().where(documents.c.id == document_id)
                  .values(current_version=maxn + 1, updated_at=_now()))
        from app.services.document_platform.service import _event, _publish_timeline
        _event(c, document_id, event_type="version_created", actor=actor_user_id,
               note=f"v{major}.{minor}")
        doc = c.execute(select(documents).where(documents.c.id == document_id)).mappings().one()
        _publish_timeline(dict(doc), event_type="version_created",
                          title=f"Document version created — {doc['original_name']}")
    return dict(row)


def list_versions(document_id: int) -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(document_versions)
                                           .where(document_versions.c.document_id == document_id)
                                           .order_by(document_versions.c.version_number.desc())).mappings()]


def current_version(document_id: int) -> dict | None:
    with engine.connect() as c:
        row = c.execute(select(document_versions).where(
            document_versions.c.document_id == document_id,
            document_versions.c.is_current.is_(True))).mappings().first()
    return dict(row) if row else None


def approve_version(principal, version_id: int, *, actor_user_id) -> dict:
    with engine.begin() as c:
        v = c.execute(select(document_versions).where(
            document_versions.c.id == version_id)).mappings().first()
        if v is None:
            raise VersionError("version does not exist")
        c.execute(document_versions.update().where(document_versions.c.id == version_id)
                  .values(approved_by=actor_user_id, approved_at=_now()))
        return dict(c.execute(select(document_versions).where(
            document_versions.c.id == version_id)).mappings().one())


def restore_version(principal, document_id: int, version_id: int, *, actor_user_id) -> dict:
    """Make a historical version current again. The history is not rewritten — the target
    version's is_current is set and the previously-current one is cleared."""
    with engine.begin() as c:
        v = c.execute(select(document_versions).where(
            document_versions.c.id == version_id,
            document_versions.c.document_id == document_id)).mappings().first()
        if v is None:
            raise VersionError("version does not belong to this document")
        c.execute(document_versions.update()
                  .where(document_versions.c.document_id == document_id).values(is_current=False))
        c.execute(document_versions.update().where(document_versions.c.id == version_id)
                  .values(is_current=True))
        c.execute(documents.update().where(documents.c.id == document_id)
                  .values(current_version=v["version_number"], updated_at=_now()))
        from app.services.document_platform.service import _event, _publish_timeline
        _event(c, document_id, event_type="restored", actor=actor_user_id,
               note=f"restored v{v['version_number']}")
        doc = c.execute(select(documents).where(documents.c.id == document_id)).mappings().one()
        _publish_timeline(dict(doc), event_type="restored",
                          title=f"Document version restored — {doc['original_name']}")
        return dict(c.execute(select(document_versions).where(
            document_versions.c.id == version_id)).mappings().one())
