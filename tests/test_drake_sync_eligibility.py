"""Drake sync: what is a document, which roots are one run, and when reconciliation must not run.

A Drake installation tree is not a document export. Across the four production roots 25,711 files
are reachable and only 828 are client documents; the rest are Drake's own index files, ``Archive\\``
snapshots and internal artifacts. Ingesting those would create ~24,900 permanently unresolvable
documents, because only a file under ``<CLIENT_ID>\\Documents\\`` carries a derivable identity.

The four roots are ONE logical source set. Missing-source reconciliation compares against the union
of every root, and a run that could not read a root must not conclude anything is absent.

Temp/test rows only, all tagged and torn down.
"""
import shutil
import uuid

import pytest
from sqlalchemy import delete, select, text

from app.db import engine, metadata
from app.importers import drake
from app.importers.drake import (
    DrakeClientIdConflict,
    _resolve_source_external_id,
    is_eligible_document,
)

documents = metadata.tables["documents"]
document_sources = metadata.tables["document_sources"]

CID = "A1B2C3D4"


# ==================================================================================================
# Eligibility — pure
# ==================================================================================================

def test_immediate_child_pdf_is_eligible():
    assert is_eligible_document(rf"3\{CID}\Documents\2024 Return.pdf") is True


def test_uppercase_pdf_is_eligible():
    """The DDM store holds both cases; both are ordinary client documents."""
    assert is_eligible_document(rf"3\{CID}\Documents\Rejection.PDF") is True
    assert is_eligible_document(rf"3\{CID}\Documents\F.Pdf") is True


def test_pdf_in_a_subfolder_beneath_documents_is_ignored():
    assert is_eligible_document(rf"3\{CID}\Documents\Sub\x.pdf") is False
    assert is_eligible_document(rf"3\{CID}\Documents\A\B\x.pdf") is False


def test_archive_content_is_ignored():
    assert is_eligible_document(rf"3\{CID}\Archive\20220304112315\x.pdf") is False
    assert is_eligible_document(rf"3\{CID}\Documents\Archive\20220304112315\x.pdf") is False


def test_drake_binaries_in_the_client_root_are_ignored():
    for name in (f"{CID}.DI1", f"{CID}.EI1", f"{CID}.LI1", f"{CID}.PI1", "Archive.Txt"):
        assert is_eligible_document(rf"3\{CID}\{name}") is False, name


@pytest.mark.parametrize("name", [
    "book.dat", "x.ddmpsp", "export.csv", "return.xml", "sheet.xls", "bundle.zip", "notes.txt",
])
def test_non_pdf_immediate_children_are_ignored(name):
    assert is_eligible_document(rf"3\{CID}\Documents\{name}") is False


def test_client_folder_must_be_an_8_hex_ddm_id():
    assert is_eligible_document(r"3\SMITHJOHN\Documents\x.pdf") is False
    assert is_eligible_document(r"3\A1B2C3D\Documents\x.pdf") is False       # 7 chars
    assert is_eligible_document(r"3\A1B2C3D4E\Documents\x.pdf") is False     # 9 chars
    assert is_eligible_document(r"3\G1B2C3D4\Documents\x.pdf") is False      # non-hex


def test_documents_folder_name_is_case_insensitive():
    assert is_eligible_document(rf"3\{CID}\documents\x.pdf") is True


def test_forward_slashes_and_deep_roots_are_accepted():
    assert is_eligible_document(f"3/{CID}/Documents/x.pdf") is True
    assert is_eligible_document(rf"DT\3\{CID}\Documents\x.pdf") is True


@pytest.mark.parametrize("path", ["", "x.pdf", rf"{CID}\x.pdf"])
def test_too_shallow_is_ineligible(path):
    assert is_eligible_document(path) is False


def test_no_filename_heuristics_are_applied():
    """The historical 795 were hand-curated; their residue must not become policy."""
    for name in ("2021 1120S (X) - Draft.pdf", "Notes Messages.pdf",
                 "TaxFormSelection__03212022_104540.pdf", "Copy of return.pdf",
                 "2021 1040X (X)..pdf", "2021 1040X (X)2.pdf", "reject.PDF"):
        assert is_eligible_document(rf"3\{CID}\Documents\{name}") is True, name


# ==================================================================================================
# Multi-root discovery + fail-closed reconciliation, end to end
# ==================================================================================================

def _tree(root, client_id, name="2024 Return.pdf", body=b"%PDF-1.4 x\n"):
    d = root / "DT" / "3" / client_id / "Documents"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(body)
    return d / name


@pytest.fixture
def world(tmp_path):
    # ``marker`` is the unique tmp directory: it appears in every source_uri this test creates, so
    # teardown and lookups scope to THIS test and cannot see another's rows. ``tag`` supplies the
    # 8-hex DDM client ids, which must be valid hex to be eligible at all.
    tag = uuid.uuid4().hex[:6].upper()
    w = {"tag": tag, "marker": str(tmp_path),
         "roots": [tmp_path / "DRAKE21", tmp_path / "DRAKE22"], "dest": tmp_path / "dest"}
    yield w
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(select(document_sources.c.document_id).where(
            document_sources.c.source_system == "Drake",
            # ``contains(autoescape=True)`` — a Windows path is full of backslashes and PostgreSQL
            # treats backslash as LIKE's escape character, so a raw LIKE never matches.
            document_sources.c.source_uri.contains(w["marker"], autoescape=True)))]
        if ids:
            c.execute(delete(document_sources).where(document_sources.c.document_id.in_(ids)))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))


def _sync(w, roots=None, **kw):
    return drake.sync(source_roots=[str(r / "DT") for r in (roots or w["roots"])],
                      destination_root=w["dest"], progress=None, **kw)


def _refs(w):
    with engine.connect() as c:
        return c.execute(select(document_sources.c.source_uri, document_sources.c.available,
                                document_sources.c.source_external_id,
                                documents.c.person_id, documents.c.household_id,
                                documents.c.organization_id)
                         .select_from(document_sources.join(
                             documents, documents.c.id == document_sources.c.document_id))
                         .where(document_sources.c.source_system == "Drake",
                                document_sources.c.source_uri.contains(w["marker"],
                                                                       autoescape=True))
                         ).mappings().all()


def test_all_configured_roots_are_one_logical_run(world):
    a = f"AA{world['tag']}"
    b = f"BB{world['tag']}"
    _tree(world["roots"][0], a)
    _tree(world["roots"][1], b)
    s = _sync(world)
    assert s["roots_configured"] == 2
    assert len(s["roots_discovered"]) == 2
    assert s["files_examined"] == 2
    assert len(_refs(world)) == 2


def test_internal_files_are_ignored_not_ingested(world):
    cid = f"CC{world['tag']}"
    _tree(world["roots"][0], cid)
    root = world["roots"][0] / "DT" / "3" / cid
    (root / f"{cid}.DI1").write_bytes(b"idx")
    (root / "Archive" / "20220304").mkdir(parents=True)
    (root / "Archive" / "20220304" / "old.pdf").write_bytes(b"%PDF old\n")
    (root / "Documents" / "sheet.xls").write_bytes(b"xls")
    (root / "Documents" / "Sub").mkdir()
    (root / "Documents" / "Sub" / "deep.pdf").write_bytes(b"%PDF deep\n")

    s = _sync(world)
    assert s["files_examined"] == 1, "only the immediate-child PDF is a document"
    assert s["ignored"] >= 4
    assert len(_refs(world)) == 1


def test_a_file_in_another_configured_root_is_not_marked_missing(world):
    a, b = f"DA{world['tag']}", f"DB{world['tag']}"
    _tree(world["roots"][0], a)
    _tree(world["roots"][1], b)
    _sync(world)
    assert all(r["available"] for r in _refs(world))

    # Re-run with BOTH roots: nothing is absent, so nothing may be marked unavailable.
    s = _sync(world)
    assert s["missing_reconciliation"] == "performed"
    # The global "missing" counter also sees other suites' Drake rows in the shared test DB;
    # what matters is that NONE of THIS test's refs were touched.
    assert all(r["available"] for r in _refs(world))


def test_one_inaccessible_root_prevents_missing_reconciliation(world):
    a, b = f"EA{world['tag']}", f"EB{world['tag']}"
    _tree(world["roots"][0], a)
    _tree(world["roots"][1], b)
    _sync(world)
    assert all(r["available"] for r in _refs(world))

    # Root 2 disappears (a mount hiccup). Its documents are NOT absent — they are unverifiable.
    shutil.rmtree(world["roots"][1])
    s = _sync(world)

    assert s["source_root_failures"] == 1
    assert s["missing_reconciliation"] == "skipped_root_failure"
    assert s["missing"] == 0, "a failed run must not even COUNT anything missing"
    assert all(r["available"] for r in _refs(world)), \
        "a failed root must never cause available=False"


def test_genuine_absence_with_all_roots_healthy_does_mark_unavailable(world):
    """The counterpart: reconciliation still works when discovery is complete."""
    a = f"FA{world['tag']}"
    doc = _tree(world["roots"][0], a)
    world["roots"][1].mkdir(parents=True, exist_ok=True)
    (world["roots"][1] / "DT").mkdir(parents=True, exist_ok=True)
    _sync(world)
    assert all(r["available"] for r in _refs(world))

    doc.unlink()                       # the file really is gone
    s = _sync(world)
    assert s["source_root_failures"] == 0
    assert s["missing_reconciliation"] == "performed"
    assert s["missing"] >= 1
    assert not any(r["available"] for r in _refs(world))


# ==================================================================================================
# Dry-run purity
# ==================================================================================================

def _counts():
    with engine.connect() as c:
        return (c.execute(text("SELECT count(*) FROM documents")).scalar(),
                c.execute(text("SELECT count(*) FROM document_sources")).scalar(),
                c.execute(text("SELECT count(*) FROM audit_events")).scalar())


def test_dry_run_makes_zero_database_mutations(world):
    cid = f"CD{world['tag']}"
    _tree(world["roots"][0], cid)
    world["roots"][1].mkdir(parents=True, exist_ok=True)
    (world["roots"][1] / "DT").mkdir(parents=True, exist_ok=True)

    before = _counts()
    s = _sync(world, dry_run=True)
    assert _counts() == before
    assert s["dry_run"] is True
    assert _refs(world) == []


def test_dry_run_makes_zero_filesystem_writes(world):
    cid = f"CE{world['tag']}"
    _tree(world["roots"][0], cid)
    world["roots"][1].mkdir(parents=True, exist_ok=True)
    (world["roots"][1] / "DT").mkdir(parents=True, exist_ok=True)

    _sync(world, dry_run=True)
    assert not world["dest"].exists(), "dry run must not create the destination tree"


def test_dry_run_still_reports_identity_coverage(world):
    cid = f"CF{world['tag']}"
    _tree(world["roots"][0], cid)
    world["roots"][1].mkdir(parents=True, exist_ok=True)
    (world["roots"][1] / "DT").mkdir(parents=True, exist_ok=True)
    s = _sync(world, dry_run=True)
    assert s["client_id_captured"] == 1
    assert s["client_id_missing"] == 0
    assert s["left_unassigned"] == 1


# ==================================================================================================
# Existing safety guarantees are preserved
# ==================================================================================================

def test_new_documents_remain_unowned(world):
    cid = f"DC{world['tag']}"
    _tree(world["roots"][0], cid)
    world["roots"][1].mkdir(parents=True, exist_ok=True)
    (world["roots"][1] / "DT").mkdir(parents=True, exist_ok=True)
    s = _sync(world)
    row = _refs(world)[0]
    assert row["person_id"] is None
    assert row["household_id"] is None
    assert row["organization_id"] is None
    assert s["left_unassigned"] == 1


def test_no_folder_name_resolver_is_invoked(monkeypatch, world):
    """Structural proof: if any folder→owner matcher were called, this fails."""
    import app.importers.taxdome_drive as td

    called = []
    if hasattr(td, "resolve_folder"):
        monkeypatch.setattr(td, "resolve_folder",
                            lambda *a, **k: called.append(1) or (None, None))
    cid = f"DD{world['tag']}"
    _tree(world["roots"][0], cid)
    world["roots"][1].mkdir(parents=True, exist_ok=True)
    (world["roots"][1] / "DT").mkdir(parents=True, exist_ok=True)
    _sync(world)
    assert called == [], "ingestion must not consult a folder-name owner resolver"
    assert not hasattr(drake, "resolve_folder")


def test_native_client_id_is_captured_and_preserved(world):
    cid = f"DE{world['tag']}"
    doc = _tree(world["roots"][0], cid)
    world["roots"][1].mkdir(parents=True, exist_ok=True)
    (world["roots"][1] / "DT").mkdir(parents=True, exist_ok=True)
    _sync(world)
    assert _refs(world)[0]["source_external_id"] == cid

    doc.write_bytes(b"%PDF-1.4 changed\n")     # force past the size fast path
    _sync(world)
    rows = _refs(world)
    assert len(rows) == 1 and rows[0]["source_external_id"] == cid


def test_existing_source_external_id_survives_an_unparseable_path():
    assert _resolve_source_external_id(r"weird\layout\x.pdf",
                                       {"source_external_id": CID}) == CID


def test_conflicting_native_id_fails_closed():
    with pytest.raises(DrakeClientIdConflict, match="conflict"):
        _resolve_source_external_id(rf"3\{CID}\Documents\x.pdf",
                                    {"source_external_id": "FFFFFFFF"})


def test_default_source_root_is_not_silently_used_for_multi_root(world):
    """A multi-root call must use exactly the roots given, never the D:\\DrakeExport fallback."""
    cid = f"DF{world['tag']}"
    _tree(world["roots"][0], cid)
    world["roots"][1].mkdir(parents=True, exist_ok=True)
    (world["roots"][1] / "DT").mkdir(parents=True, exist_ok=True)
    s = _sync(world, dry_run=True)
    assert s["source_roots"] == [str(world["roots"][0] / "DT"), str(world["roots"][1] / "DT")]
    assert str(drake.DEFAULT_SOURCE_ROOT) not in s["source_roots"]
