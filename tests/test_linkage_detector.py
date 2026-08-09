"""Coverage for the unresolved-subject linkage detector (PR-3).

Proves: one folder -> one linkage exception (independent of document count); reruns are idempotent; a
reusable approved resolution suppresses creation while reject/defer/ambiguous does not; the PR-2 evidence
bundle persists in the opened event; already-linked documents are excluded from discovery; PREVIEW makes
zero writes; and production create writes ONLY Exception Engine records (no canonical/document/file writes).

Created exceptions are intentionally not torn down (exception_events are append-only by trigger, matching
the existing exception-engine tests); dedupe keys are tag-unique so runs never collide, and CI resets the
schema each run.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import func, select

from app.db import documents, engine, metadata, people, source_contacts
from app.importers.taxdome_drive import _name_key
from app.security.models import Principal
from app.services.migration.evidence_assembler import build_context
from app.services.migration.linkage_detector import (
    LINKAGE_CODE,
    dedupe_key,
    detect,
    discover_folder_subjects,
)
from app.services.resolution_knowledge import record_decision

_TAG = uuid.uuid4().hex[:8]
_SYS = "TaxDome Drive"
_frd = metadata.tables["folder_resolution_decisions"]
_exceptions = metadata.tables["exceptions"]
_exception_events = metadata.tables["exception_events"]
_C = {"documents": [], "people": [], "source_contacts": []}
_PRINCIPAL = Principal(999001, "sys@e.com", "System", frozenset({"exception.read", "exception.write"}))


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        c.execute(_frd.delete().where(_frd.c.subject_system == _SYS.casefold()))
        for tbl, key in ((documents, "documents"), (source_contacts, "source_contacts"),
                         (people, "people")):
            if _C[key]:
                c.execute(tbl.delete().where(tbl.c.id.in_(_C[key])))
    for k in _C:
        _C[k].clear()


def _person(full_name):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True)
                        .returning(people.c.id)).scalar_one()
    _C["people"].append(pid)
    return pid


def _doc(folder, original_name="doc.pdf", *, person_id=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=None, organization_id=None, original_name=original_name,
            stored_name=f"ld-{_TAG}-{uuid.uuid4().hex}", storage_path="x",
            storage_uri="C:\\legacy\\" + original_name, size_bytes=10,
            sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            tags={"source_system": _SYS, "taxdome_folder": folder}).returning(documents.c.id)).scalar_one()
    _C["documents"].append(did)
    return did


def _subject(folder):
    return {"source_system": _SYS, "subject_type": "folder",
            "subject_key": _name_key(folder), "display_name": folder}


def _count(tbl):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(tbl)).scalar_one()


def _opened_metadata(eid):
    with engine.connect() as c:
        return c.execute(select(_exception_events.c["metadata"]).where(
            _exception_events.c.exception_id == eid,
            _exception_events.c.event_type == "opened")).scalar_one()


# --- creation --------------------------------------------------------------

def test_one_folder_multiple_docs_creates_one_exception():
    folder = f"Evan Folder {_TAG}"
    _doc(folder, "1040.pdf")
    _doc(folder, "w2.pdf")
    _doc(folder, "statement.pdf")                       # 3 docs, one folder
    s = detect(preview=False, principal=_PRINCIPAL, subjects=[_subject(folder)],
               context=build_context())
    assert s["created"] == 1 and len(s["exception_ids"]) == 1
    with engine.connect() as c:
        row = c.execute(select(_exceptions.c.domain, _exceptions.c.category, _exceptions.c.dedupe_key)
                        .where(_exceptions.c.id == s["exception_ids"][0])).mappings().one()
    assert row["domain"] == "linkage"
    assert row["dedupe_key"] == dedupe_key(_SYS, "folder", _name_key(folder))


def test_rerun_is_idempotent():
    folder = f"Idem Folder {_TAG}"
    _doc(folder)
    ctx = build_context()
    first = detect(preview=False, principal=_PRINCIPAL, subjects=[_subject(folder)], context=ctx)
    second = detect(preview=False, principal=_PRINCIPAL, subjects=[_subject(folder)], context=ctx)
    assert first["created"] == 1
    assert second["created"] == 0 and second["idempotent_existing"] == 1
    assert first["exception_ids"] == second["exception_ids"]     # same exception, not a duplicate


def test_evidence_bundle_persists_in_opened_event():
    folder = f"Evidence Folder {_TAG}"
    _doc(folder, "return.pdf")
    s = detect(preview=False, principal=_PRINCIPAL, subjects=[_subject(folder)], context=build_context())
    meta = _opened_metadata(s["exception_ids"][0])
    assert meta["detector"] == LINKAGE_CODE
    assert meta["subject"]["display_name"] == folder
    ev = meta["evidence"]
    assert ev["display_name"] == folder and ev["document_count"] == 1
    for key in ("identifiers", "source_contact_candidates", "person_candidates", "household_candidates",
                "business_candidates", "provenance", "relationships", "deterministic_outcome",
                "held_reason", "suggested_action", "match_reason", "confidence", "evidence_flags"):
        assert key in ev


# --- reusable-resolution suppression --------------------------------------

def test_reusable_positive_resolution_suppresses_creation():
    folder = f"Known Folder {_TAG}"
    pid = _person(folder)
    _doc(folder)
    record_decision(subject_system=_SYS, subject_type="folder", subject_key=_name_key(folder),
                    display_name=folder, decision="link_person", resulting_entity_type="person",
                    resulting_entity_id=pid, reviewed_by="Tester")
    s = detect(preview=False, principal=_PRINCIPAL, subjects=[_subject(folder)], context=build_context())
    assert s["skipped_reusable"] == 1 and s["created"] == 0 and s["exception_ids"] == []


@pytest.mark.parametrize("decision", ["reject", "defer", "ambiguous"])
def test_non_reusable_knowledge_does_not_suppress(decision):
    folder = f"Nonreuse {decision} {_TAG}"
    _doc(folder)
    record_decision(subject_system=_SYS, subject_type="folder", subject_key=_name_key(folder),
                    display_name=folder, decision=decision, reviewed_by="Tester")
    s = detect(preview=True, subjects=[_subject(folder)], context=build_context())
    assert s["skipped_reusable"] == 0 and s["would_create"] == 1


# --- discovery scope -------------------------------------------------------

def test_already_linked_documents_excluded_from_discovery():
    unresolved = f"Unresolved Folder {_TAG}"
    linked = f"Linked Folder {_TAG}"
    _doc(unresolved)
    pid = _person(f"Owner {_TAG}")
    _doc(linked, person_id=pid)                          # already linked -> excluded
    with engine.connect() as c:
        found = {d["display_name"] for d in discover_folder_subjects(c)}
    assert unresolved in found and linked not in found


# --- write scope -----------------------------------------------------------

def test_preview_performs_zero_writes():
    folder = f"Preview Folder {_TAG}"
    _doc(folder)
    before_exc, before_evt = _count(_exceptions), _count(_exception_events)
    s = detect(preview=True, subjects=[_subject(folder)], context=build_context())
    assert s["would_create"] == 1
    assert _count(_exceptions) == before_exc and _count(_exception_events) == before_evt


def test_create_writes_only_exception_records():
    folder = f"Scope Folder {_TAG}"
    _doc(folder)
    psl = metadata.tables["person_source_links"]
    before = {t.name: _count(t) for t in (people, documents, source_contacts, _frd, psl)}
    before_exc = _count(_exceptions)

    detect(preview=False, principal=_PRINCIPAL, subjects=[_subject(folder)], context=build_context())

    after = {t.name: _count(t) for t in (people, documents, source_contacts, _frd, psl)}
    assert after == before                               # no canonical/document/ledger writes
    assert _count(_exceptions) == before_exc + 1         # only an exception was created


def test_production_create_requires_principal():
    with pytest.raises(ValueError):
        detect(preview=False, subjects=[_subject(f"NoPrincipal {_TAG}")], context=build_context())
