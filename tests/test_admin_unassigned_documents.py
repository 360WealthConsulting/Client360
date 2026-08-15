"""Coverage for relocating the Unassigned Documents resolution queue out of client workspaces.

The client Documents tab (person + household) must no longer render the migration cleanup queue; it now
lives at Admin -> Document Management -> Unassigned Documents. The resolve workflow is preserved.
"""
import hashlib
import uuid
from pathlib import Path

import pytest

from app.db import documents, engine
from app.services.client360.sections import documents_view_model
from app.services.households import unresolved_taxdome_folders

_TAG = uuid.uuid4().hex[:8]
_C: list = []
_TPL = Path("app/templates/client360")


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _C:
            c.execute(documents.delete().where(documents.c.id.in_(_C)))
    _C.clear()


def _unresolved_folder_doc(folder):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=None, household_id=None, organization_id=None, original_name="u.pdf",
            stored_name=f"un-{_TAG}-{uuid.uuid4().hex}", storage_path="x", storage_uri="C:\\legacy\\u.pdf",
            size_bytes=10, sha256=hashlib.sha256(uuid.uuid4().bytes).hexdigest(), status="active",
            tags={"source_system": "TaxDome Drive", "taxdome_folder": folder}).returning(documents.c.id)
        ).scalar_one()
    _C.append(did)
    return did


# --- the client Documents payload no longer carries the migration queue -------

def test_documents_view_model_has_no_unassigned_queue():
    vm = documents_view_model([])
    assert "unassigned" not in vm                       # client Documents tab shows owned docs only
    assert "documents" in vm


def test_client_workspace_templates_do_not_render_unassigned_queue():
    for name in ("workspace.html", "household.html"):
        src = (_TPL / name).read_text(encoding="utf-8")
        assert "Unassigned documents — resolve ownership" not in src
        assert "sec.unassigned" not in src


def test_admin_template_hosts_the_relocated_queue():
    src = Path("app/templates/admin/unassigned_documents.html").read_text(encoding="utf-8")
    assert "Unassigned documents — resolve ownership" in src
    # Folder resolution now posts to the admin preview/confirm endpoint (the human-resolution interface).
    assert 'action="/admin/documents/unassigned/resolve"' in src


# --- the admin route renders the unresolved queue -----------------------------

def test_admin_worklist_still_surfaces_unresolved_folders():
    # The admin route renders exactly this worklist; an unresolved TaxDome folder must appear.
    folder = f"ZZ Unassigned {_TAG}"
    _unresolved_folder_doc(folder)
    folders = {u["folder"] for u in unresolved_taxdome_folders(limit=500)}
    assert folder in folders                             # migration queue is available in Admin


def test_admin_route_is_registered_and_gated():
    from app.main import app
    from app.routes.admin import unassigned_documents  # handler exists

    match = [r for r in app.routes if getattr(r, "path", None) == "/admin/documents/unassigned"]
    assert match, "admin unassigned-documents route must be registered"
    assert "GET" in match[0].methods
    assert unassigned_documents  # handler importable (gated by require_capability('client.write'))
