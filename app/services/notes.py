"""Person notes — permanent client note + typed append-only notes (Sprint 1, Task 3).

Two database-backed note models:

- **Permanent client note** (``person_permanent_notes``): ONE editable long-lived note per
  person for enduring CRM facts/preferences/planning context. Edits are audited by the caller
  (append-only audit trail); no separate version-history table. Legacy ``notes/{id}.txt`` blobs
  migrate here (idempotent, never overwritten).
- **Person notes** (``person_notes``): append-only, author-attributed, timestamped entries with
  a ``note_type`` — activity notes today; Task 5 call-logging and later communication features
  reuse this same table (types: ``note``/``call``/``meeting``/``email``/``task``/``system``).
  Append-only inserts mean simultaneous additions can never overwrite each other.

Import-inert: reads neither the filesystem nor the database at import time.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Table, desc, select

NOTES_ROOT = Path("notes")

#: Marks a permanent note migrated from a legacy filesystem blob (idempotency).
FILESYSTEM_SOURCE = "filesystem_migration"

#: The note_type vocabulary for ``person_notes`` (shared with future timeline/communication work).
NOTE_TYPES: frozenset[str] = frozenset({"note", "call", "meeting", "email", "task", "system"})

#: Note types a staff member may create through the UI: general activity notes plus the
#: one-click communication types (call/email/meeting). Excludes the internal ``task``/``system``
#: types, which are written by services rather than typed by a person.
ACTIVITY_NOTE_TYPES: frozenset[str] = frozenset({"note", "call", "email", "meeting"})

#: Human labels + timeline verbs for the communication types (Task 5).
COMMUNICATION_TYPES: frozenset[str] = frozenset({"call", "email", "meeting"})

#: Optional direction for a logged communication (null for general notes).
COMMUNICATION_DIRECTIONS: frozenset[str] = frozenset({"inbound", "outbound"})


def _table(name: str) -> Table:
    from app.db import engine, metadata
    table = metadata.tables.get(name)
    if table is None:
        table = Table(name, metadata, autoload_with=engine)
    return table


def _users_table() -> Table:
    from app.db import metadata
    return metadata.tables["users"]


# --- permanent client note (one editable per person; audited, no version table) ---

def get_permanent_note(person_id: int, *, conn=None) -> dict:
    """The person's current permanent note (migrating a legacy file on first access). Returns
    ``{body, updated_by_user_id, updated_at, created_at}``; body is '' if none yet."""
    ensure_permanent_migrated(person_id, conn=conn)
    pn = _table("person_permanent_notes")

    def _do(c):
        row = c.execute(select(pn).where(pn.c.person_id == person_id)).mappings().first()
        if row is None:
            return {"body": "", "updated_by_user_id": None, "updated_at": None, "created_at": None}
        return dict(row)

    return _run(conn, _do)


def save_permanent_note(person_id: int, body: str, *, editor_user_id: int | None,
                        source: str = "staff", conn=None) -> None:
    """Create or replace the person's permanent note. Auditing is the caller's responsibility."""
    body = body if body is not None else ""
    pn = _table("person_permanent_notes")

    def _do(c):
        now = datetime.now(UTC)
        if c.execute(select(pn.c.id).where(pn.c.person_id == person_id)).first():
            c.execute(pn.update().where(pn.c.person_id == person_id).values(
                body=body, updated_by_user_id=editor_user_id, source=source, updated_at=now))
        else:
            c.execute(pn.insert().values(
                person_id=person_id, body=body, updated_by_user_id=editor_user_id, source=source, updated_at=now))

    return _run(conn, _do)


# --- person notes (append-only, typed) ---------------------------------------

def add_person_note(person_id: int, body: str, *, author_user_id: int | None = None,
                    note_type: str = "note", direction: str | None = None, conn=None) -> int:
    """Append one typed person note. Returns the new note id. Reused by Task 5 (call logging)
    and later communication features via ``note_type``. ``direction`` (inbound/outbound) is
    optional and only meaningful for communications."""
    body = (body or "").strip()
    if not body:
        raise ValueError("Note body is required.")
    if note_type not in NOTE_TYPES:
        raise ValueError(f"Invalid note_type: {note_type!r}")
    direction = (direction or "").strip() or None
    if direction is not None and direction not in COMMUNICATION_DIRECTIONS:
        raise ValueError(f"Invalid direction: {direction!r}")
    notes = _table("person_notes")

    def _do(c):
        return c.execute(notes.insert().values(
            person_id=person_id, body=body, author_user_id=author_user_id,
            note_type=note_type, direction=direction).returning(notes.c.id)).scalar_one()

    return _run(conn, _do)


def list_person_notes(person_id: int, *, note_types=None, conn=None) -> list[dict]:
    """A person's notes, newest first, with the author's display name resolved. Optionally
    filter to specific ``note_types``."""
    notes, users = _table("person_notes"), _users_table()

    def _do(c):
        query = (
            select(notes.c.id, notes.c.body, notes.c.author_user_id, notes.c.note_type,
                   notes.c.direction, notes.c.created_at,
                   users.c.display_name.label("author_name"))
            .select_from(notes.outerjoin(users, users.c.id == notes.c.author_user_id))
            .where(notes.c.person_id == person_id)
        )
        if note_types:
            query = query.where(notes.c.note_type.in_(list(note_types)))
        rows = c.execute(query.order_by(desc(notes.c.created_at), desc(notes.c.id))).mappings().all()
        return [dict(r) for r in rows]

    return _run(conn, _do)


def search_person_notes(query: str, *, limit: int = 50, conn=None) -> list[dict]:
    """Search person-note bodies (case-insensitive). Notes are queryable, unlike the old files."""
    term = f"%{(query or '').strip()}%"
    notes = _table("person_notes")

    def _do(c):
        return [dict(r) for r in c.execute(
            select(notes.c.id, notes.c.person_id, notes.c.note_type, notes.c.body,
                   notes.c.author_user_id, notes.c.created_at)
            .where(notes.c.body.ilike(term)).order_by(desc(notes.c.created_at)).limit(limit)
        ).mappings().all()]

    return _run(conn, _do)


# --- automatic migration of legacy filesystem notes -> permanent note --------

def ensure_permanent_migrated(person_id: int, *, conn=None) -> bool:
    """Idempotently migrate a person's legacy ``notes/{id}.txt`` blob into their PERMANENT note
    (not into activity history). True if a migration was performed; no-op if a permanent note
    already exists or no non-empty legacy file exists."""
    note_file = NOTES_ROOT / f"{person_id}.txt"
    if not note_file.exists():
        return False
    body = note_file.read_text(encoding="utf-8")
    if not body.strip():
        return False
    pn = _table("person_permanent_notes")

    def _do(c):
        if c.execute(select(pn.c.id).where(pn.c.person_id == person_id)).first():
            return False  # never overwrite an existing permanent note
        try:
            created_at = datetime.fromtimestamp(note_file.stat().st_mtime, tz=UTC)
        except OSError:
            created_at = datetime.now(UTC)
        c.execute(pn.insert().values(
            person_id=person_id, body=body, updated_by_user_id=None,
            source=FILESYSTEM_SOURCE, created_at=created_at, updated_at=created_at))
        return True

    return _run(conn, _do)


def migrate_filesystem_notes(*, conn=None) -> dict:
    """Bulk cutover: migrate every ``notes/{id}.txt`` blob into permanent notes (idempotent)."""
    summary = {"files": 0, "migrated": 0, "skipped": 0}
    if not NOTES_ROOT.exists():
        return summary
    for note_file in sorted(NOTES_ROOT.glob("*.txt")):
        stem = note_file.stem
        if not stem.isdigit():
            continue
        summary["files"] += 1
        if ensure_permanent_migrated(int(stem), conn=conn):
            summary["migrated"] += 1
        else:
            summary["skipped"] += 1
    return summary


def _run(conn, fn):
    if conn is not None:
        return fn(conn)
    from app.db import engine
    with engine.begin() as c:
        return fn(c)
