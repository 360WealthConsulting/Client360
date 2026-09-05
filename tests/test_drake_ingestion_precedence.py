"""Drake ingestion precedence — the importer must never assign an owner from a folder name.

Drake document ingestion used to resolve its top-level folder through the TaxDome ``resolve_folder``
matcher and write the result into ``documents.person_id`` / ``household_id``. Every ownership write in
the platform requires all three owner columns to be NULL, so that first write was also the last one:
it permanently locked out Drake's own SSN/EIN identifier-hash resolution
(``app.services.drake_document_owner``) for the document.

These tests pin the corrected contract:

  * ingestion registers Drake documents UNOWNED, even where the folder match is at its strongest;
  * every piece of Drake provenance survives, including the native Drake client id recorded on
    existing source references by the out-of-band migration;
  * a document that already has an owner is never re-owned by a re-sync;
  * TaxDome and SharePoint are untouched by this change.

Nothing here asserts what the Drake identity path CONCLUDES — that is
``tests/test_drake_document_owner.py``. These tests only prove ingestion leaves the door open for it.

Temp dirs + test rows only; no real Drake data, and no production ownership is read or written.
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import uuid

import pytest
from sqlalchemy import delete, select

from app.db import documents, engine, households, metadata, people
from app.importers import drake
from app.services.document_sources import add_source_reference

_TAG = "DRAKEPREC"


@pytest.fixture(autouse=True)
def _clean():
    ds = metadata.tables["document_sources"]
    facts = metadata.tables.get("document_facts")
    classifications = metadata.tables.get("document_classifications")

    def _wipe():
        with engine.begin() as c:
            doc_ids = set(c.scalars(select(documents.c.id).where(
                documents.c.original_name.like(f"%{_TAG}%"))))
            doc_ids |= set(c.scalars(select(documents.c.id).where(
                documents.c.stored_name.like(f"%{_TAG}%"))))
            if doc_ids:
                for table in (facts, classifications):
                    if table is not None:
                        c.execute(delete(table).where(table.c.document_id.in_(doc_ids)))
                c.execute(delete(ds).where(ds.c.document_id.in_(doc_ids)))
                c.execute(delete(documents).where(documents.c.id.in_(doc_ids)))
            pids = list(c.scalars(select(people.c.id).where(people.c.full_name.like(f"%{_TAG}%"))))
            if pids:
                c.execute(delete(people).where(people.c.id.in_(pids)))
            c.execute(delete(households).where(households.c.name.like(f"%{_TAG}%")))

    _wipe()
    drake._database.cache_clear()
    yield
    _wipe()


def _dirs(tmp_path):
    src, dst = tmp_path / "drake", tmp_path / "dst"
    src.mkdir()
    return src, dst


def _sync(src, dst, **kw):
    return drake.sync(src, dst, progress=lambda *_a, **_k: None, **kw)


def _person(full_name, *, household_id=None):
    first, _, last = full_name.partition(" ")
    with engine.begin() as c:
        return c.execute(people.insert().values(
            first_name=first, last_name=last, full_name=full_name,
            household_id=household_id, active=True).returning(people.c.id)).scalar_one()


def _doc_row(original_name):
    with engine.connect() as c:
        return c.execute(
            select(documents.c.id, documents.c.person_id, documents.c.household_id,
                   documents.c.organization_id, documents.c.tags)
            .where(documents.c.original_name == original_name)).mappings().one()


def _referenced_names(path):
    """Every name the module actually REFERENCES — imports, calls, attributes.

    Parsed rather than grepped so the module docstring, which necessarily names ``resolve_folder`` to
    explain why it is gone, can neither satisfy nor break the assertion. Only executable code counts.
    """
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.asname or a.name for a in node.names)
    return names


# --- the importer must not reach for the TaxDome resolver at all --------------

def test_drake_importer_does_not_reference_the_taxdome_folder_resolver():
    """A source assertion, not a behavioural one, so the coupling cannot come back unnoticed.

    ``resolve_folder`` reaching Drake ingestion is the defect itself; a behavioural test would only
    catch it once someone had ALSO wired the result into an ownership column, which is one refactor
    too late.
    """
    assert "resolve_folder" not in _referenced_names("app/importers/drake.py")


# --- ownership is never written from a folder name ---------------------------

def test_person_named_folder_does_not_assign_person_ownership(tmp_path):
    src, dst = _dirs(tmp_path)
    _person(f"Rodney {_TAG}")
    name = f"2021 Tax Return Documents (Rodney {_TAG}).pdf"
    (src / f"Rodney {_TAG}").mkdir()
    (src / f"Rodney {_TAG}" / name).write_text("single taxpayer bytes")

    summary = _sync(src, dst)

    row = _doc_row(name)
    assert row["person_id"] is None
    assert row["household_id"] is None
    assert row["organization_id"] is None
    assert summary["left_unassigned"] == 1
    assert "linked_person" not in summary and "linked_household" not in summary


def test_joint_folder_does_not_assign_household_ownership(tmp_path):
    """The strongest case the old behaviour had: two names, both unique, one shared household."""
    src, dst = _dirs(tmp_path)
    with engine.begin() as c:
        hid = c.execute(households.insert().values(
            name=f"{_TAG} Household").returning(households.c.id)).scalar_one()
    _person(f"Michael {_TAG}", household_id=hid)
    _person(f"Debra {_TAG}", household_id=hid)
    name = f"2024 1040 joint {_TAG}.pdf"
    (src / f"Michael and Debra {_TAG}").mkdir()
    (src / f"Michael and Debra {_TAG}" / name).write_text("joint bytes")

    _sync(src, dst)

    row = _doc_row(name)
    assert row["household_id"] is None
    assert row["person_id"] is None


def test_business_return_in_person_named_folder_stays_unowned(tmp_path):
    """The business-to-person contamination the Drake owner spec forbids.

    A 1120S filed for a client's company routinely sits in a folder named after the owner-operator.
    Under the old behaviour the folder name assigned it to that natural person. It must now stay
    unowned: no organization resolver exists, so HOLD is the correct terminal state.
    """
    src, dst = _dirs(tmp_path)
    _person(f"Mark {_TAG}")
    name = f"2023 Tax Return Documents ({_TAG} HOLDINGS LLC).pdf"
    (src / f"Mark {_TAG}").mkdir()
    (src / f"Mark {_TAG}" / name).write_text("1120S bytes")

    _sync(src, dst)

    row = _doc_row(name)
    assert row["person_id"] is None
    assert row["household_id"] is None
    assert row["organization_id"] is None


# --- provenance survives ------------------------------------------------------

def test_drake_provenance_tags_are_preserved(tmp_path):
    src, dst = _dirs(tmp_path)
    name = f"2022 Federal 1040 {_TAG}.pdf"
    folder = f"Client Folder {_TAG}"
    (src / folder).mkdir()
    (src / folder / name).write_text("provenance bytes")

    _sync(src, dst)

    tags = _doc_row(name)["tags"]
    assert tags["source_system"] == "Drake"
    assert tags["taxdome_folder"] == folder            # folder/client info retained as provenance
    assert tags["drake_doc_type"] == "federal_return"
    assert tags["tax_year"] == "2022"
    assert tags["source_relative_path"].endswith(name)
    assert tags["source_path"].endswith(name)
    assert tags["sync_version"] == drake.SYNC_VERSION
    assert tags["last_synced_at"]


def test_existing_drake_client_id_and_source_external_id_survive_a_resync(tmp_path):
    """The out-of-band migration recorded the native Drake client id on every Drake source reference
    (``source_external_id``) and in ``tags.drake_client_id``. ``add_source_reference`` upserts on
    (document_id, source_system, source_uri) and assigns ``source_external_id`` unconditionally, so a
    re-sync must carry the recorded value through rather than blank it.
    """
    src, dst = _dirs(tmp_path)
    folder = f"Barbara {_TAG}"
    name = f"2021 Tax Return Documents ({_TAG} BARBARA S).pdf"
    (src / folder).mkdir()
    target = src / folder / name
    content = f"client id bytes {uuid.uuid4().hex}"
    target.write_text(content)
    sha = hashlib.sha256(content.encode()).hexdigest()
    client_id = "158FDCA1"

    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=name, stored_name=f"drake:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri="/x/prior",
            size_bytes=len(content), sha256=sha, status="active", archived=False,
            tags={"source_system": "Drake", "drake_client_id": client_id,
                  "migration_batch": f"{_TAG}-batch"}).returning(documents.c.id)).scalar_one()
        add_source_reference(c, did, source_system="Drake", source_uri=str(target),
                             source_external_id=client_id, source_hash=sha)

    _sync(src, dst)

    ds = metadata.tables["document_sources"]
    with engine.connect() as c:
        ref = c.execute(select(ds.c.source_external_id).where(
            ds.c.document_id == did, ds.c.source_system == "Drake",
            ds.c.source_uri == str(target))).mappings().one()
        tags = c.execute(select(documents.c.tags).where(documents.c.id == did)).scalar()
    assert ref["source_external_id"] == client_id       # native Drake client id preserved
    assert tags["drake_client_id"] == client_id         # and the tag the migration wrote
    assert tags["migration_batch"] == f"{_TAG}-batch"


# --- an owned document is never re-owned --------------------------------------

def test_existing_canonical_ownership_is_not_overwritten(tmp_path):
    """A Drake file whose content already exists as an owned canonical document.

    The folder names a DIFFERENT person than the stored owner. The stored owner must win, and the
    folder name must fill no column — including on the SHA-256 reuse path, where
    ``resolve_or_create_canonical`` fills NULL ownership only.
    """
    src, dst = _dirs(tmp_path)
    owner = _person(f"Owner {_TAG}")
    _person(f"Interloper {_TAG}")
    name = f"2020 shared return {_TAG}.pdf"
    content = f"shared bytes {uuid.uuid4().hex}"
    sha = hashlib.sha256(content.encode()).hexdigest()

    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=name, stored_name=f"taxdome:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri="/x/shared",
            size_bytes=len(content), sha256=sha, status="active", archived=False,
            person_id=owner, tags={"source_system": "TaxDome Drive"}).returning(
                documents.c.id)).scalar_one()

    (src / f"Interloper {_TAG}").mkdir()
    (src / f"Interloper {_TAG}" / name).write_text(content)

    summary = _sync(src, dst)

    with engine.connect() as c:
        row = c.execute(select(documents.c.person_id, documents.c.household_id)
                        .where(documents.c.id == did)).mappings().one()
    assert summary["reused_canonical"] == 1
    assert row["person_id"] == owner                    # unchanged
    assert row["household_id"] is None                  # folder name filled nothing


# --- the other two importers are untouched ------------------------------------

def test_taxdome_folder_resolution_is_unchanged():
    """TaxDome still owns, exports and uses its resolver. This change is Drake-only."""
    from app.importers import taxdome_drive
    assert callable(taxdome_drive.resolve_folder)
    src = pathlib.Path("app/importers/taxdome_drive.py").read_text(encoding="utf-8")
    assert "def resolve_folder(" in src
    assert "_link_folders(db, folders_seen)" in src     # TaxDome still auto-links its own folders


def test_sharepoint_still_uses_the_taxdome_resolver():
    """SharePoint's deterministic client-folder anchoring is deliberately left alone."""
    src = pathlib.Path("app/importers/sharepoint.py").read_text(encoding="utf-8")
    assert 'resolve_folder(conn, item["client_folder"])' in src
