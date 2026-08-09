"""Coverage for the document download source-path selection (repository vs legacy).

The Robinson document #800 scenario: a relocated "Client360 Repository" document whose storage_path is
stored RELATIVE to the content root must be served from its absolute storage_uri (previously it fell
through to the relative storage_path and 404'd). Legacy documents without an absolute storage_uri still
resolve via storage_path, and a genuinely missing file still fails safely. No production data is touched.
"""
import uuid
from pathlib import Path

import pytest
from fastapi.responses import FileResponse

from app.db import documents, engine
from app.routes.documents import download_document

_TAG = uuid.uuid4().hex[:8]
_C: list = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _C:
            c.execute(documents.delete().where(documents.c.id.in_(_C)))
    _C.clear()


class _State:
    request_id = "dl-test"


class _Req:
    def __init__(self):
        self.state = _State()
        self.headers = {}          # no 'accept' -> render_error returns JSON (no template render)


def _doc(**over):
    vals = dict(person_id=None, household_id=None, organization_id=None, original_name="doc.pdf",
                stored_name=f"dl-{_TAG}-{uuid.uuid4().hex}", storage_path="x", storage_uri=None,
                size_bytes=10, sha256="0" * 64, status="active", archived=False,
                content_type="application/pdf", storage_provider="Client360 Local")
    vals.update(over)
    with engine.begin() as c:
        did = c.execute(documents.insert().values(**vals).returning(documents.c.id)).scalar_one()
    _C.append(did)
    return did


def _write(path: Path, data=b"%PDF-1.4 test") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_repository_document_serves_absolute_storage_uri(tmp_path):
    # Robinson #800 shape: relocated household doc; storage_path is RELATIVE to the content root, but the
    # absolute storage_uri points at the real file. It must be served from storage_uri.
    f = _write(tmp_path / "Content" / "Households" / "Robinson Household" / "Tax" / "2025" / "r [800].pdf",
               b"ROBINSON-800")
    did = _doc(household_id=None, storage_provider="Client360 Repository", storage_uri=str(f),
               storage_path="Households\\Robinson Household\\Tax\\2025\\r [800].pdf")   # relative -> would 404
    resp = download_document(did, _Req())
    assert isinstance(resp, FileResponse) and resp.status_code == 200
    assert Path(resp.path) == f


def test_local_document_serves_absolute_storage_uri(tmp_path):
    f = _write(tmp_path / "Data" / "TaxDome" / "x.pdf")
    did = _doc(storage_provider="Client360 Local", storage_uri=str(f), storage_path="rel/x.pdf")
    resp = download_document(did, _Req())
    assert isinstance(resp, FileResponse) and Path(resp.path) == f


def test_legacy_relative_uri_falls_back_to_storage_path(tmp_path):
    # a legacy directly-uploaded document has no absolute storage_uri -> resolve via storage_path
    f = _write(tmp_path / "legacy" / "a.pdf")
    did = _doc(storage_provider="Local Upload", storage_uri="uploads/a.pdf",   # relative -> not used
               storage_path=str(f))
    resp = download_document(did, _Req())
    assert isinstance(resp, FileResponse) and Path(resp.path) == f


def test_missing_file_fails_safely(tmp_path):
    did = _doc(storage_provider="Client360 Repository",
               storage_uri=str(tmp_path / "gone" / "missing [999].pdf"), storage_path="Households\\x.pdf")
    resp = download_document(did, _Req())
    assert resp.status_code == 404
    assert not isinstance(resp, FileResponse)


def test_archived_document_is_unavailable(tmp_path):
    f = _write(tmp_path / "Content" / "arch.pdf")
    did = _doc(storage_provider="Client360 Repository", storage_uri=str(f), storage_path="rel",
               archived=True)
    resp = download_document(did, _Req())
    assert resp.status_code == 404
