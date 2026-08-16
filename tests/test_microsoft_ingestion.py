"""Continuous ingestion — Microsoft/SharePoint sync runner: change detection, idempotency, run records."""
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db import document_facts, documents, engine, metadata, people
from app.importers import sharepoint
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import microsoft_ingestion as mi

document_sources = metadata.tables["document_sources"]

_TAG = uuid.uuid4().hex[:8]
_A = _TAG.translate(str.maketrans("0123456789", "abcdefghij"))
_SEEN_DOCS: set = set()


@pytest.fixture(autouse=True)
def _clear_cache():
    sharepoint._database.cache_clear()          # importer uses the test DB (DATABASE_URL env)
    yield
    with engine.begin() as c:
        ids = list(_SEEN_DOCS)
        if ids:
            c.execute(document_facts.delete().where(document_facts.c.document_id.in_(ids)))
            c.execute(document_sources.delete().where(document_sources.c.document_id.in_(ids)))
            c.execute(documents.delete().where(documents.c.id.in_(ids)))
    _SEEN_DOCS.clear()


def _item(tmp_path, *, name, uri, body="hello world statement", modified="2024-01-01T00:00:00",
          deleted=False):
    f = tmp_path / f"{uuid.uuid4().hex}_{name}"
    if not deleted:
        f.write_text(body)
    item = {"name": name, "web_url": uri, "item_id": uri.rsplit("/", 1)[-1],
            "site": "Site1", "library": "Docs", "modified_at": modified}
    if deleted:
        item["deleted"] = True
    else:
        item["local_path"] = str(f)
        item["size"] = f.stat().st_size
    return item


def _run(items, tmp_path, **kw):
    r = mi.run_sharepoint_sync(items=items, destination_root=str(tmp_path / "dest"), ocr=False, **kw)
    _track()
    return r


def _track():
    with engine.connect() as c:
        for did in c.execute(select(document_sources.c.document_id)
                             .where(document_sources.c.source_system == "SharePoint")).scalars():
            _SEEN_DOCS.add(did)


def _canonical_count(uri):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(document_sources)
                         .where(document_sources.c.source_system == "SharePoint",
                                document_sources.c.source_uri == uri)).scalar()


def _doc_for(uri):
    with engine.connect() as c:
        return c.execute(select(document_sources.c.document_id, document_sources.c.available)
                         .where(document_sources.c.source_system == "SharePoint",
                                document_sources.c.source_uri == uri)
                         .order_by(document_sources.c.id.desc())).mappings().first()


# --- change detection ------------------------------------------------------------------------------

def test_first_seen_ingested_once(tmp_path):
    uri = f"https://sp/{_A}/a1"
    r = _run([_item(tmp_path, name="a.txt", uri=uri)], tmp_path)
    assert r["canonical_created"] == 1 and _canonical_count(uri) == 1


def test_unchanged_skipped_on_next_run(tmp_path):
    uri = f"https://sp/{_A}/a2"
    item = _item(tmp_path, name="a.txt", uri=uri)
    _run([item], tmp_path)
    r2 = _run([item], tmp_path)                       # same size + modified -> skipped
    assert r2["skipped"] == 1 and r2["canonical_created"] == 0
    assert r2.get("affected_document_ids", []) == []  # nothing re-analyzed (no OCR)
    assert _canonical_count(uri) == 1                 # no duplicate


def test_changed_item_reprocessed(tmp_path):
    uri = f"https://sp/{_A}/a3"
    _run([_item(tmp_path, name="a.txt", uri=uri, body="v1", modified="2024-01-01T00:00:00")], tmp_path)
    r2 = _run([_item(tmp_path, name="a.txt", uri=uri, body="v2 different content",
                     modified="2024-02-02T00:00:00")], tmp_path)
    assert r2["skipped"] == 0 and r2["canonical_created"] == 1   # new content -> new canonical version


def test_duplicate_source_event_no_duplicate_canonical(tmp_path):
    uri = f"https://sp/{_A}/a4"
    item = _item(tmp_path, name="a.txt", uri=uri)
    _run([item, item], tmp_path)                      # same uri twice in one run
    assert _canonical_count(uri) == 1


def test_source_deletion_keeps_canonical(tmp_path):
    uri = f"https://sp/{_A}/a5"
    _run([_item(tmp_path, name="a.txt", uri=uri)], tmp_path)
    did = _doc_for(uri)["document_id"]
    _run([_item(tmp_path, name="a.txt", uri=uri, deleted=True)], tmp_path)
    with engine.connect() as c:
        assert c.execute(select(documents.c.id).where(documents.c.id == did)).scalar() == did  # kept
    assert _doc_for(uri)["available"] is False        # source reference marked unavailable


def test_scan_idempotency(tmp_path):
    uri = f"https://sp/{_A}/a6"
    item = _item(tmp_path, name="a.txt", uri=uri)
    _run([item], tmp_path)
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(documents)).scalar()
    _run([item], tmp_path); _run([item], tmp_path)
    with engine.connect() as c:
        after = c.execute(select(func.count()).select_from(documents)).scalar()
    assert after == before                            # repeated runs create no new canonical documents


# --- pipeline invocation + OCR-on-need -------------------------------------------------------------

def test_new_document_invokes_pipeline(tmp_path):
    uri = f"https://sp/{_A}/a7"
    email = f"sp-{_TAG}@mail.com"
    from app.db import person_source_links, source_contacts
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=f"Spperson {_A}", active=True)
                        .returning(people.c.id)).scalar_one()
        sid = c.execute(source_contacts.insert().values(source_system="TaxDome", source_file="t",
              source_record_id=uuid.uuid4().hex, source_hash=uuid.uuid4().hex, email=email, raw_data={})
              .returning(source_contacts.c.id)).scalar_one()
        c.execute(person_source_links.insert().values(person_id=pid, source_contact_id=sid,
                  match_method="email", confirmed=True))
    _run([_item(tmp_path, name="s.txt", uri=uri, body=f"remit to {email}")], tmp_path)
    did = _doc_for(uri)["document_id"]
    with engine.connect() as c:
        fact = c.execute(select(document_facts.c.fact_value).where(
            document_facts.c.document_id == did, document_facts.c.fact_type == "owner_proposal",
            document_facts.c.is_current.is_(True))).scalar()
    assert fact is not None                           # pipeline ran on the new doc (proposal persisted)
    with engine.begin() as c:                          # cleanup the extra person
        c.execute(person_source_links.delete().where(person_source_links.c.person_id == pid))
        c.execute(source_contacts.delete().where(source_contacts.c.id == sid))
        c.execute(people.delete().where(people.c.id == pid))
    assert _owner_all_null(did)                        # ingestion assigns NO ownership


def _owner_all_null(did):
    with engine.connect() as c:
        r = c.execute(select(documents.c.person_id, documents.c.household_id, documents.c.organization_id)
                      .where(documents.c.id == did)).first()
    return r == (None, None, None)


# --- failure isolation + run record ----------------------------------------------------------------

def test_failure_isolated_and_run_recorded(tmp_path):
    uri = f"https://sp/{_A}/a8"
    good = _item(tmp_path, name="good.txt", uri=uri)
    bad = {"name": "bad.txt", "web_url": f"https://sp/{_A}/bad", "item_id": "bad"}  # no local_path -> raises
    r = _run([bad, good], tmp_path)
    assert r["status"] == "completed_with_errors" and len(r["errors"]) == 1
    assert _canonical_count(uri) == 1                 # the good item still ingested
    status = {s["source"]: s for s in mi.ingestion_status()}
    assert status["SharePoint"]["status"] == "completed_with_errors"
    assert status["SharePoint"]["scanned"] >= 2


def test_no_client_created(tmp_path):
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(people)).scalar()
    _run([_item(tmp_path, name="a.txt", uri=f"https://sp/{_A}/a9",
                body=f"Dear Unknownperson {_A}, contact new-{_TAG}@x.com")], tmp_path)
    with engine.connect() as c:
        after = c.execute(select(func.count()).select_from(people)).scalar()
    assert after == before                            # ingestion never creates a client


# --- authorization ---------------------------------------------------------------------------------

def test_ingestion_status_page_requires_identity_manage():
    dep = require_capability("identity.manage")
    admin = Principal(1, "a@t", "Admin", frozenset({"identity.manage"}))
    assert dep(principal=admin) is admin
    with pytest.raises(HTTPException) as exc:
        dep(principal=Principal(2, "x@t", "Staff", frozenset({"client.read"})))
    assert exc.value.status_code == 403


def test_route_registered():
    from app.main import app
    assert "/admin/ingestion" in {getattr(r, "path", None) for r in app.routes}
