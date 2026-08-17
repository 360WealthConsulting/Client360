"""Unified staff review inbox — cheap read-only backlog summary + lane navigation."""
import inspect
import uuid

from sqlalchemy import select

from app.db import document_ocr, documents, engine, people
from app.services.document_review_inbox import REVIEW_LANES, inbox_summary


def _doc(*, owned=False):
    sfx = uuid.uuid4().hex
    with engine.begin() as c:
        pid = None
        if owned:
            pid = c.execute(people.insert().values(full_name=f"Owner {sfx[:6]}", active=True)
                            .returning(people.c.id)).scalar_one()
        did = c.execute(documents.insert().values(
            original_name="d.pdf", stored_name=f"s-{sfx}", storage_provider="local",
            storage_uri=f"/tmp/{sfx}.pdf", storage_path=f"{sfx}.pdf", size_bytes=1,
            sha256=(sfx + sfx)[:64], status="active", archived=False, person_id=pid)
            .returning(documents.c.id)).scalar_one()
    return did


def test_inbox_summary_reflects_unowned_and_ocr():
    before = inbox_summary()
    d_unowned = _doc(owned=False)
    _doc(owned=True)                                         # owned -> must NOT count as unassigned
    with engine.begin() as c:
        c.execute(document_ocr.insert().values(document_id=d_unowned, status="completed"))
    after = inbox_summary()
    assert after["unassigned_documents"] == before["unassigned_documents"] + 1   # only the unowned doc
    assert after["ocr"]["completed"] == before["ocr"]["completed"] + 1
    assert set(after["ocr"]) == {"completed", "failed", "timed_out", "unsupported", "pending"}
    assert after["total_documents"] >= before["total_documents"] + 2


def test_inbox_summary_lists_every_review_lane():
    lanes = inbox_summary()["lanes"]
    assert lanes is REVIEW_LANES and len(lanes) == 5
    urls = {lane["url"] for lane in lanes}
    assert urls == {"/admin/documents/unassigned", "/admin/documents/review-queue",
                    "/admin/documents/high-confirm", "/admin/documents/entity-proposals",
                    "/admin/documents/context-review"}
    assert all(lane.get("label") and lane.get("desc") for lane in lanes)


def test_review_inbox_route_is_capability_gated():
    from app.routes.admin_review_inbox import review_inbox
    src = inspect.getsource(review_inbox)
    assert 'require_capability("client.read")' in src        # + the ^/admin identity.manage middleware gate


def test_review_inbox_template_renders_lanes_and_counts():
    html = open("app/templates/admin/review_inbox.html", encoding="utf-8").read()
    assert "{% for lane in lanes %}" in html and 'href="{{ lane.url }}"' in html   # links every lane by data
    assert "{{ unassigned_documents }}" in html and "{{ ocr.completed }}" in html   # backlog + OCR counts


def test_review_inbox_mounted():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/admin/review" in paths


def test_unowned_query_excludes_owned_and_deleted():
    # An owned doc and a deleted doc must not inflate the backlog.
    before = inbox_summary()["unassigned_documents"]
    with engine.begin() as c:
        sfx = uuid.uuid4().hex
        c.execute(documents.insert().values(
            original_name="del.pdf", stored_name=f"s-{sfx}", storage_provider="local",
            storage_uri=f"/tmp/{sfx}", storage_path=sfx, size_bytes=1, sha256=(sfx + sfx)[:64],
            status="deleted", archived=False))
    with engine.connect() as c:
        _ = c.execute(select(documents.c.id).limit(1)).first()
    assert inbox_summary()["unassigned_documents"] == before   # deleted doc not counted
