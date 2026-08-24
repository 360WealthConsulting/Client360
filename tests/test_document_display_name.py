"""Canonical document display_name — read fallback + reviewed SAFE-only apply.

display_name is a PRESENTATION field. Every test here also asserts the things that must never move:
original_name, stored_name, storage_path, storage_uri and sha256 stay byte-identical, and the
physical file is still located by storage_path — never by either name.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, insert, select

from app.db import audit_events, documents, engine, households, people, relationship_entities
from app.security.models import Principal
from app.services.business_workspace import get_business_workspace
from app.services.document_naming import document_display_name
from app.services.document_naming_apply import (
    APPLIED,
    CONFLICT_EXISTING_NAME,
    NOT_IN_PREVIEW,
    REFUSED_BUCKET,
    REFUSED_COLLISION,
    REFUSED_EMPTY,
    UNCHANGED_ALREADY_SET,
    DocumentNamingApplyError,
    apply_display_names,
)
from app.services.document_normalization_preview import build_preview

EDITOR = Principal(1, "staff@t", "Staff", frozenset({"documents.edit", "client.read"}))
READER = Principal(2, "ro@t", "ReadOnly", frozenset({"documents.view"}))

_TAGS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for tag in _TAGS:
        like = f"%{tag}%"
        with engine.begin() as c:
            ppl = list(c.scalars(select(people.c.id).where(people.c.last_name.like(like))))
            hhs = list(c.scalars(select(households.c.id).where(households.c.name.like(like))))
            ents = list(c.scalars(select(relationship_entities.c.id)
                                  .where(relationship_entities.c.name.like(like))))
            for col, vals in ((documents.c.person_id, ppl), (documents.c.household_id, hhs),
                              (documents.c.organization_id, ents)):
                if vals:
                    c.execute(documents.delete().where(col.in_(vals)))
            if ents:
                c.execute(relationship_entities.delete().where(relationship_entities.c.id.in_(ents)))
            if ppl:
                c.execute(people.delete().where(people.c.id.in_(ppl)))
            if hhs:
                c.execute(households.delete().where(households.c.id.in_(hhs)))
    _TAGS.clear()


def _tag():
    t = "DDN" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _person(c, tag, first, last):
    return c.execute(insert(people).values(first_name=first, last_name=f"{last}{tag}",
                                           full_name=None, active=True)
                     .returning(people.c.id)).scalar_one()


def _household(c, tag, name):
    return c.execute(insert(households).values(name=f"{name} {tag}")
                     .returning(households.c.id)).scalar_one()


def _business(c, tag, name):
    return c.execute(insert(relationship_entities).values(entity_type="business",
                                                          name=f"{name} {tag}", active=True)
                     .returning(relationship_entities.c.id)).scalar_one()


def _doc(c, name, *, person_id=None, household_id=None, organization_id=None, display_name=None):
    u = uuid.uuid4().hex
    return c.execute(insert(documents).values(
        original_name=name, stored_name=f"stored-{u}", storage_path=f"/vault/{u}.bin",
        storage_uri=f"file:///vault/{u}.bin", size_bytes=1, sha256=u.ljust(64, "0")[:64],
        status="active", archived=False, display_name=display_name,
        person_id=person_id, household_id=household_id, organization_id=organization_id,
    ).returning(documents.c.id)).scalar_one()


def _physical(doc_id):
    """The fields that must NEVER change: identity + how the file is located."""
    with engine.connect() as c:
        return c.execute(select(
            documents.c.original_name, documents.c.stored_name, documents.c.storage_path,
            documents.c.storage_uri, documents.c.sha256, documents.c.size_bytes,
            documents.c.person_id, documents.c.household_id, documents.c.organization_id,
        ).where(documents.c.id == doc_id)).one()


def _display(doc_id):
    with engine.connect() as c:
        return c.scalar(select(documents.c.display_name).where(documents.c.id == doc_id))


def _row(doc_id):
    return next(r for r in build_preview()["rows"] if r["document_id"] == doc_id)


# --------------------------------------------------------------------- read fallback
def test_display_helper_prefers_display_name_then_original():
    assert document_display_name({"display_name": "2025 - W-2 - Ann", "original_name": "w2.pdf"}) \
        == "2025 - W-2 - Ann"
    assert document_display_name({"display_name": None, "original_name": "w2.pdf"}) == "w2.pdf"
    assert document_display_name({"display_name": "   ", "original_name": "w2.pdf"}) == "w2.pdf"
    assert document_display_name({"display_name": None, "original_name": None}) == ""
    assert document_display_name(None) == ""


def test_business_workspace_shows_display_name_and_still_exposes_the_original():
    tag = _tag()
    with engine.begin() as c:
        biz = _business(c, tag, "Pullen Homes Inc")
        did = _doc(c, "scan001.pdf", organization_id=biz, display_name="2024 - Form 1120S - Pullen")
        plain = _doc(c, "untouched.pdf", organization_id=biz)
    docs = {d["id"]: d for d in get_business_workspace(biz)["documents"]}
    assert docs[did]["name"] == "2024 - Form 1120S - Pullen"
    assert docs[did]["original_name"] == "scan001.pdf"          # provenance never hidden
    assert docs[plain]["name"] == "untouched.pdf"               # fallback for the unnamed row


# --------------------------------------------------------------------- SAFE apply
def test_safe_document_gets_a_display_name_and_nothing_else_moves():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Adam", "Davis")
        did = _doc(c, "2025 W2.pdf", person_id=pid)
    assert _row(did)["bucket"] == "SAFE"
    before = _physical(did)

    dry = apply_display_names(principal=EDITOR, document_ids=[did], dry_run=True)
    assert dry["counts"][APPLIED] == 1 and dry["applied"] == 0 and dry["would_apply"] == 1
    assert _display(did) is None                                 # dry run wrote nothing

    result = apply_display_names(principal=EDITOR, document_ids=[did], dry_run=False, request_id="t")
    assert result["applied"] == 1
    assert _display(did) == f"2025 - W-2 - Adam Davis{tag}"
    assert _physical(did) == before                              # byte-identical provenance
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(audit_events).where(
            audit_events.c.action == "document.display_name.set",
            audit_events.c.entity_id == str(did))) == 1


def test_person_household_and_business_documents_all_apply():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Adam", "Davis")
        hid = _household(c, tag, "Davis Household")
        bid = _business(c, tag, "Davis Holdings")
        ids = [_doc(c, "2025 W2.pdf", person_id=pid),
               _doc(c, "2024 1040.pdf", household_id=hid),
               _doc(c, "2024 1120S.pdf", organization_id=bid)]
    before = {i: _physical(i) for i in ids}
    result = apply_display_names(principal=EDITOR, document_ids=ids, dry_run=False)
    assert result["counts"][APPLIED] == 3
    for i in ids:
        assert _display(i) and _physical(i) == before[i]


def test_ordinal_duplicate_name_persists_exactly():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Adam", "Davis")
        did = _doc(c, "2025 W2 (2).pdf", person_id=pid)
    apply_display_names(principal=EDITOR, document_ids=[did], dry_run=False)
    assert _display(did) == f"2025 - W-2 - Adam Davis{tag} - (2)"


# --------------------------------------------------------------------- refusals
def test_review_document_is_refused():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Adam", "Davis")
        did = _doc(c, "1040 2024 amended.pdf", person_id=pid)      # version marker -> REVIEW
    assert _row(did)["bucket"] == "REVIEW"
    result = apply_display_names(principal=EDITOR, document_ids=[did], dry_run=False)
    assert result["counts"][REFUSED_BUCKET] == 1 and _display(did) is None


def test_skip_document_is_refused():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Adam", "Davis")
        did = _doc(c, "doc.pdf", person_id=pid)
    assert _row(did)["bucket"] == "SKIP"
    result = apply_display_names(principal=EDITOR, document_ids=[did], dry_run=False)
    assert result["counts"][REFUSED_BUCKET] == 1 and _display(did) is None


def test_unchanged_document_is_never_written():
    """The read-time fallback to original_name already produces the right result."""
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Adam", "Davis")
        did = _doc(c, f"2025 W-2 Adam Davis{tag} extra detail.pdf", person_id=pid)
    assert _row(did)["bucket"] == "UNCHANGED"
    result = apply_display_names(principal=EDITOR, document_ids=[did], dry_run=False)
    assert result["counts"][REFUSED_BUCKET] == 1
    assert _display(did) is None
    assert document_display_name({"display_name": None,
                                  "original_name": f"2025 W-2 Adam Davis{tag} extra detail.pdf"}) \
        == f"2025 W-2 Adam Davis{tag} extra detail.pdf"


def test_collision_is_refused_even_when_safe_shaped():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Adam", "Davis")
        a = _doc(c, "2025 W2.pdf", person_id=pid)
        b = _doc(c, "W2 2025.pdf", person_id=pid)                 # identical candidate, no ordinal
    assert _row(a)["collision"] and _row(b)["collision"]
    result = apply_display_names(principal=EDITOR, document_ids=[a, b], dry_run=False)
    # A collision already forces REVIEW, so the bucket gate rejects first; either way nothing is written.
    assert result["counts"][APPLIED] == 0
    assert result["counts"][REFUSED_BUCKET] + result["counts"][REFUSED_COLLISION] == 2
    assert _display(a) is None and _display(b) is None


def test_collision_gate_is_independent_of_the_bucket_gate():
    """Defence in depth: even a row that arrives labelled SAFE is refused when it collides, so a
    future bucket change can never leak a colliding name into the database."""
    from app.services.document_naming_apply import _eligible
    assert _eligible({"bucket": "SAFE", "collision": True,
                      "proposed_display_name": "X"}) == (False, REFUSED_COLLISION)
    assert _eligible({"bucket": "SAFE", "collision": False,
                      "proposed_display_name": "  "}) == (False, REFUSED_EMPTY)
    assert _eligible({"bucket": "SAFE", "collision": False,
                      "proposed_display_name": "X"}) == (True, None)
    for bucket in ("REVIEW", "UNCHANGED", "SKIP"):
        assert _eligible({"bucket": bucket, "collision": False,
                          "proposed_display_name": "X"}) == (False, REFUSED_BUCKET)


def test_safe_all_mode_never_touches_review_skip_or_unchanged():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Adam", "Davis")
        safe = _doc(c, "2025 W2.pdf", person_id=pid)
        review = _doc(c, "1040 2024 amended.pdf", person_id=pid)
        skip = _doc(c, "doc.pdf", person_id=pid)
        unchanged = _doc(c, f"2024 Form 1040 Adam Davis{tag} extra.pdf", person_id=pid)
    apply_display_names(principal=EDITOR, safe_all=True, dry_run=False)
    assert _display(safe)
    assert _display(review) is None and _display(skip) is None and _display(unchanged) is None


# --------------------------------------------------------------------- idempotency + conflict
def test_applying_twice_is_idempotent():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Adam", "Davis")
        did = _doc(c, "2025 W2.pdf", person_id=pid)
    first = apply_display_names(principal=EDITOR, document_ids=[did], dry_run=False)
    name_after_first = _display(did)
    second = apply_display_names(principal=EDITOR, document_ids=[did], dry_run=False)
    assert first["counts"][APPLIED] == 1
    assert second["counts"][APPLIED] == 0 and second["counts"][UNCHANGED_ALREADY_SET] == 1
    assert _display(did) == name_after_first
    with engine.connect() as c:                                   # no second audit event
        assert c.scalar(select(func.count()).select_from(audit_events).where(
            audit_events.c.action == "document.display_name.set",
            audit_events.c.entity_id == str(did))) == 1


def test_a_different_existing_display_name_is_never_overwritten():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Adam", "Davis")
        did = _doc(c, "2025 W2.pdf", person_id=pid, display_name="Hand written name by staff")
    result = apply_display_names(principal=EDITOR, document_ids=[did], dry_run=False)
    assert result["counts"][CONFLICT_EXISTING_NAME] == 1 and result["counts"][APPLIED] == 0
    assert _display(did) == "Hand written name by staff"


# --------------------------------------------------------------------- authorization + request shape
def test_documents_edit_capability_is_required():
    with pytest.raises(PermissionError) as exc:
        apply_display_names(principal=READER, document_ids=[1], dry_run=False)
    assert "documents.edit" in str(exc.value)


def test_exactly_one_selection_mode_is_required():
    for kwargs in ({}, {"document_ids": [1], "safe_all": True}):
        with pytest.raises(DocumentNamingApplyError):
            apply_display_names(principal=EDITOR, dry_run=True, **kwargs)


def test_unknown_document_id_is_reported_not_written():
    result = apply_display_names(principal=EDITOR, document_ids=[2_000_000_000], dry_run=False)
    assert result["counts"][NOT_IN_PREVIEW] == 1 and result["counts"][APPLIED] == 0


# --------------------------------------------------------------------- physical file untouched
def test_download_still_resolves_the_same_physical_file():
    """The stored file is located by storage_path/storage_uri; naming never participates."""
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Adam", "Davis")
        did = _doc(c, "2025 W2.pdf", person_id=pid)
    with engine.connect() as c:
        before = c.execute(select(documents.c.storage_path, documents.c.storage_uri,
                                  documents.c.stored_name, documents.c.sha256)
                           .where(documents.c.id == did)).one()
    apply_display_names(principal=EDITOR, document_ids=[did], dry_run=False)
    with engine.connect() as c:
        after = c.execute(select(documents.c.storage_path, documents.c.storage_uri,
                                 documents.c.stored_name, documents.c.sha256)
                          .where(documents.c.id == did)).one()
    assert after == before
    from app.services.documents import get_document
    doc = get_document(did)
    assert doc["storage_path"] == before[0] and doc["original_name"] == "2025 W2.pdf"


def test_document_detail_still_exposes_the_original_filename():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Adam", "Davis")
        did = _doc(c, "2025 W2.pdf", person_id=pid)
    apply_display_names(principal=EDITOR, document_ids=[did], dry_run=False)
    from app.services.documents import get_document
    doc = get_document(did)
    assert doc["original_name"] == "2025 W2.pdf"                 # detail/provenance unchanged
    assert doc["display_name"] == f"2025 - W-2 - Adam Davis{tag}"


def test_apply_writes_only_the_display_name_column():
    tag = _tag()
    with engine.begin() as c:
        pid = _person(c, tag, "Adam", "Davis")
        did = _doc(c, "2025 W2.pdf", person_id=pid)
    with engine.connect() as c:
        before = dict(c.execute(select(documents).where(documents.c.id == did)).mappings().one())
    apply_display_names(principal=EDITOR, document_ids=[did], dry_run=False)
    with engine.connect() as c:
        after = dict(c.execute(select(documents).where(documents.c.id == did)).mappings().one())
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"display_name"}, changed
