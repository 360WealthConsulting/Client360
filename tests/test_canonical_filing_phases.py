"""Database-backed proof of the two-phase separation, idempotency, drift guards and the migration.

Every test runs against ``client360_test`` (enforced by ``tests/conftest.py``) and cleans up after
itself. The properties proved here are the ones that cannot be proved by a pure test: that Phase A
leaves the documents table byte-identical, that Phase B leaves the folder table byte-identical, and
that a second run of either is a no-op rather than a duplicate.
"""
from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select, text

from app.db import documents, engine, metadata, people
from app.services.canonical_filing import build_rows
from app.services.canonical_filing_phases import (
    build_phase_a_manifest,
    build_phase_b_manifest,
    target_subtree_digest_of,
    target_subtree_nodes,
)
from app.services.filing_manifest import PHASE_A, PHASE_B, confirm_phrase, rollback_phrase
from scripts import apply_canonical_filing as phase_b
from scripts import apply_canonical_folders as phase_a
from scripts import rollback_canonical_filing as rollback_b
from scripts import rollback_canonical_folders as rollback_a

_TAG = "CanonFilingTest"
folders_table = metadata.tables["document_folders"]

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- fixtures ------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean():
    yield
    with engine.begin() as connection:
        ids = [r[0] for r in connection.execute(
            select(documents.c.id).where(documents.c.stored_name.like(f"file:{_TAG}%")))]
        if ids:
            connection.execute(documents.update().where(documents.c.id.in_(ids))
                               .values(folder_id=None))
            connection.execute(delete(documents).where(documents.c.id.in_(ids)))
        # Children before parents: parent_folder_id is ON DELETE SET NULL.
        for kind in ("year", "service", "client"):
            connection.execute(delete(folders_table).where(
                folders_table.c.folder_kind == kind,
                folders_table.c.code.like("cf-client-person-%")))
        connection.execute(delete(people).where(people.c.last_name == _TAG))


def _person(first="Ada") -> int:
    with engine.begin() as connection:
        return connection.execute(people.insert().values(
            first_name=first, last_name=_TAG, full_name=f"{first} {_TAG}", active=True)
            .returning(people.c.id)).scalar_one()


def _document(person_id, *, archived=False, status="active") -> int:
    filename = f"{_TAG}-{uuid.uuid4().hex[:6]}.pdf"
    with engine.begin() as connection:
        return connection.execute(documents.insert().values(
            original_name=filename, stored_name=f"file:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri=f"/x/{filename}",
            size_bytes=10, sha256=uuid.uuid4().hex * 2, status=status, archived=archived,
            review_status="not_required", current_version=1, person_id=person_id,
            folder_id=None, tags={}).returning(documents.c.id)).scalar_one()


def _proposal(document_id, person_id, name, *, year=2023, category="Tax Preparation"):
    return {
        "document_id": document_id, "original_name": f"{year} Return.pdf",
        "source": "SharePoint", "source_path": f"Clients/{category}/X/{year} Return.pdf",
        "proposed_scope_type": "person", "proposed_scope_id": person_id,
        "proposed_scope_name": name, "filing_scope_state": "resolved",
        "proposed_top_level_category": category,
        "proposed_tax_year": year, "tax_year_confidence": "strong",
        "tax_year_source": "filename", "proposed_display_name": f"1040 - {name}",
        "display_name_source": "document_naming.safe_document_label",
        "proposed_document_type": "1040", "filing_status": "AUTO_FILE_SAFE", "reasons": [],
    }


def _write(path: Path, payload) -> tuple[str, str]:
    from app.services.filing_manifest import sha256_of
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
                    encoding="utf-8", newline="\n")
    return str(path), sha256_of(path)


@pytest.fixture
def batch(tmp_path):
    """One person, three live documents across two tax years — four canonical folders."""
    person_id = _person()
    name = f"Ada {_TAG}"
    ids = [_document(person_id) for _ in range(3)]
    proposals = [_proposal(ids[0], person_id, name, year=2023),
                 _proposal(ids[1], person_id, name, year=2023),
                 _proposal(ids[2], person_id, name, year=2024)]
    rows, _ = build_rows(proposals)
    manifest_a = build_phase_a_manifest(rows)
    manifest_b = build_phase_b_manifest(rows, manifest_a)
    path_a, sha_a = _write(tmp_path / "phase_a.json", manifest_a)
    path_b, sha_b = _write(tmp_path / "phase_b.json", manifest_b)
    return {"person": person_id, "name": name, "ids": sorted(ids), "rows": rows,
            "manifest_a": manifest_a, "manifest_b": manifest_b,
            "path_a": path_a, "sha_a": sha_a, "path_b": path_b, "sha_b": sha_b,
            "out": tmp_path / "out", "tmp": tmp_path}


def _apply_a(batch, **kw):
    kw.setdefault("apply_changes", True)
    kw.setdefault("confirm", confirm_phrase(PHASE_A, batch["manifest_a"]["folder_count"]))
    kw.setdefault("actor_user_id", 1)
    kw.setdefault("manifest_sha256", batch["sha_a"])
    kw.setdefault("report_dir", batch["out"] / "a")
    return phase_a.run(batch["path_a"], **kw)


def _apply_b(batch, **kw):
    kw.setdefault("apply_changes", True)
    kw.setdefault("confirm", confirm_phrase(PHASE_B, batch["manifest_b"]["document_count"]))
    kw.setdefault("actor_user_id", 1)
    kw.setdefault("manifest_sha256", batch["sha_b"])
    kw.setdefault("report_dir", batch["out"] / "b")
    return phase_b.run(batch["path_b"], **kw)


def _documents_fingerprint():
    with engine.connect() as connection:
        return connection.execute(text(
            "select md5(string_agg(d::text, E'\n' order by d.id)) from documents d")).scalar()


def _folders_fingerprint():
    with engine.connect() as connection:
        return connection.execute(text(
            "select md5(string_agg(f::text, E'\n' order by f.id)) from document_folders f")).scalar()


def _folder_count(batch):
    codes = [n["code"] for n in batch["manifest_a"]["folders"]]
    with engine.connect() as connection:
        return connection.execute(text(
            "select count(*) from document_folders where code = any(:codes)"),
            {"codes": codes}).scalar()


# --- migration -----------------------------------------------------------------------------------

def _cf01():
    spec = importlib.util.spec_from_file_location(
        "cf01_test", REPO_ROOT / "migrations" / "versions" / "cf01_canonical_filing_identity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _columns(connection, table):
    return {r[0] for r in connection.execute(text(
        "select column_name from information_schema.columns "
        " where table_schema = 'public' and table_name = :t"), {"t": table})}


def test_migration_cf01_round_trips():
    """downgrade() then upgrade(), inside a transaction that is always rolled back."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    module = _cf01()
    assert module.revision == "cf01" and module.down_revision == "psl02"

    connection = engine.connect()
    transaction = connection.begin()
    try:
        assert {"tax_year", "tax_year_confidence", "tax_year_source"} <= _columns(connection,
                                                                                 "documents")
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.downgrade()
        assert not {"tax_year_confidence"} & _columns(connection, "documents")
        assert not {"owner_scope_type", "folder_kind"} & _columns(connection, "document_folders")
        with Operations.context(context):
            module.upgrade()
        assert {"tax_year", "tax_year_confidence", "tax_year_source"} <= _columns(connection,
                                                                                 "documents")
        assert {"owner_scope_type", "owner_scope_id", "folder_kind", "service_code", "tax_year",
                "owner_source_label"} <= _columns(connection, "document_folders")
    finally:
        transaction.rollback()
        connection.close()


def test_tax_year_columns_carry_their_constraints():
    with engine.connect() as connection:
        transaction = connection.begin()
        person_id = None
        try:
            person_id = connection.execute(people.insert().values(
                first_name="Con", last_name=_TAG, full_name=f"Con {_TAG}", active=True)
                .returning(people.c.id)).scalar_one()
            base = dict(original_name="c.pdf", stored_name=f"file:{_TAG}{uuid.uuid4().hex}",
                        storage_path="/x", storage_provider="Client360 Local", storage_uri="/x/c",
                        size_bytes=1, sha256=uuid.uuid4().hex * 2, status="active",
                        review_status="not_required", current_version=1, person_id=person_id,
                        tags={})
            with pytest.raises(Exception, match="ck_documents_tax_year_range"):
                connection.execute(text(
                    "insert into documents (original_name, stored_name, storage_path, size_bytes, "
                    " sha256, person_id, tax_year, tax_year_confidence) values "
                    " ('c.pdf', :sn, '/x', 1, :sha, :p, 1899, 'strong')"),
                    {"sn": f"file:{_TAG}{uuid.uuid4().hex}", "sha": uuid.uuid4().hex * 2,
                     "p": person_id})
            assert base  # the fixture row shape is what the constraint is checked against
        finally:
            transaction.rollback()


def test_a_year_without_a_confidence_is_refused():
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            person_id = connection.execute(people.insert().values(
                first_name="Con2", last_name=_TAG, full_name=f"Con2 {_TAG}", active=True)
                .returning(people.c.id)).scalar_one()
            with pytest.raises(Exception, match="ck_documents_tax_year_confidence_pairing"):
                connection.execute(text(
                    "insert into documents (original_name, stored_name, storage_path, size_bytes, "
                    " sha256, person_id, tax_year) values "
                    " ('c.pdf', :sn, '/x', 1, :sha, :p, 2023)"),
                    {"sn": f"file:{_TAG}{uuid.uuid4().hex}", "sha": uuid.uuid4().hex * 2,
                     "p": person_id})
        finally:
            transaction.rollback()


def test_the_tag_based_tax_year_still_works_untouched():
    """Transitional compatibility: the migration adds columns and rewrites no existing data."""
    person_id = _person("Tagged")
    document_id = _document(person_id)
    with engine.begin() as connection:
        connection.execute(text("update documents set tags = :t where id = :i"),
                           {"t": json.dumps({"tax_year": "2023"}), "i": document_id})
    with engine.connect() as connection:
        row = connection.execute(text(
            "select tags->>'tax_year' as tag, tax_year from documents where id = :i"),
            {"i": document_id}).mappings().one()
    assert row["tag"] == "2023"
    assert row["tax_year"] is None


# --- phase A -------------------------------------------------------------------------------------

def test_phase_a_dry_run_writes_nothing(batch):
    before = _folders_fingerprint()
    report = _apply_a(batch, apply_changes=False)
    assert report["committed"] is False and report["dry_run"] is True
    assert report["would_create"] == 4
    assert _folder_count(batch) == 0
    assert _folders_fingerprint() == before


def test_phase_a_creates_the_canonical_tree(batch):
    report = _apply_a(batch)
    assert report["committed"] is True
    assert report["created"] == 4 and report["reused"] == 0
    assert report["audit_rows"] == 4
    with engine.connect() as connection:
        rows = connection.execute(text(
            "select code, folder_kind, owner_scope_type, owner_scope_id, service_code, tax_year "
            "  from document_folders where owner_scope_id = :p order by code"),
            {"p": batch["person"]}).mappings().all()
    kinds = sorted(r["folder_kind"] for r in rows)
    assert kinds == ["client", "service", "year", "year"]
    assert all(r["owner_scope_type"] == "person" for r in rows)


def test_phase_a_cannot_mutate_a_document(batch):
    before = _documents_fingerprint()
    _apply_a(batch)
    assert _documents_fingerprint() == before
    with engine.connect() as connection:
        filed = connection.execute(text(
            "select count(*) from documents where id = any(:ids) and folder_id is not null"),
            {"ids": batch["ids"]}).scalar()
    assert filed == 0


def test_phase_a_is_idempotent(batch):
    first = _apply_a(batch)
    assert first["created"] == 4
    after_first = _folders_fingerprint()
    second = _apply_a(batch, report_dir=batch["out"] / "a2")
    assert second["created"] == 0 and second["reused"] == 4
    assert second["committed"] is True
    assert _folder_count(batch) == 4
    assert _folders_fingerprint() == after_first


def test_a_concurrent_duplicate_folder_cannot_be_created(batch):
    """The partial unique index is the guard even if the advisory lock were bypassed."""
    _apply_a(batch)
    node = next(n for n in batch["manifest_a"]["folders"] if n["kind"] == "year")
    with engine.begin() as connection, pytest.raises(Exception) as excinfo:
        connection.execute(text(
            "insert into document_folders (code, name, owner_scope_type, owner_scope_id, "
            " folder_kind, service_code, tax_year) values (:c, :n, :t, :i, :k, :s, :y)"),
            {"c": node["code"] + "-dup", "n": node["name"], "t": node["owner_scope_type"],
             "i": node["owner_scope_id"], "k": node["kind"], "s": node["service_code"],
             "y": node["tax_year"]})
    assert "uq_document_folders_owner_scope_identity" in str(excinfo.value)


def test_phase_a_refuses_a_phase_b_manifest(batch):
    with pytest.raises(SystemExit, match="one authorization may never execute both phases"):
        phase_a.run(batch["path_b"], apply_changes=False)


def test_phase_a_refuses_a_tampered_manifest(batch, tmp_path):
    tampered = json.loads(Path(batch["path_a"]).read_text(encoding="utf-8"))
    tampered["folders"][0]["name"] = "Somebody Else"
    path, sha = _write(tmp_path / "tampered_a.json", tampered)
    with pytest.raises(SystemExit, match="the manifest was edited"):
        phase_a.run(path, manifest_sha256=sha)


def test_phase_a_refuses_a_wrong_sha(batch):
    with pytest.raises(SystemExit, match="SHA256"):
        phase_a.run(batch["path_a"], manifest_sha256="0" * 64)


def test_phase_a_refuses_a_wrong_confirmation_or_actor(batch):
    with pytest.raises(SystemExit, match="confirmation phrase"):
        _apply_a(batch, confirm="APPLY-DOCUMENT-FILING-BATCH1-16304")
    with pytest.raises(SystemExit, match="not the approved actor"):
        _apply_a(batch, actor_user_id=7)
    assert _folder_count(batch) == 0


def test_phase_a_refuses_the_retired_batch_phrase(batch):
    with pytest.raises(SystemExit, match="confirmation phrase"):
        _apply_a(batch, confirm="APPLY-DOCUMENT-FILING-BATCH1-16304")


def test_phase_a_leaves_master_entity_names_untouched(batch):
    with engine.connect() as connection:
        before = connection.execute(text("select full_name from people where id = :i"),
                                    {"i": batch["person"]}).scalar()
    _apply_a(batch)
    with engine.connect() as connection:
        after = connection.execute(text("select full_name from people where id = :i"),
                                   {"i": batch["person"]}).scalar()
    assert after == before == f"Ada {_TAG}"


# --- phase B -------------------------------------------------------------------------------------

def test_phase_b_refuses_a_phase_a_manifest(batch):
    with pytest.raises(SystemExit, match="one authorization may never execute both phases"):
        phase_b.run(batch["path_a"], apply_changes=False)


def test_phase_b_aborts_when_the_folders_do_not_exist(batch):
    with pytest.raises(SystemExit, match="Phase B never creates a folder"):
        phase_b.run(batch["path_b"], apply_changes=False)
    assert _folder_count(batch) == 0


def test_phase_b_dry_run_writes_nothing(batch):
    _apply_a(batch)
    before_documents, before_folders = _documents_fingerprint(), _folders_fingerprint()
    report = _apply_b(batch, apply_changes=False)
    assert report["committed"] is False and report["assigned"] == 0
    assert _documents_fingerprint() == before_documents
    assert _folders_fingerprint() == before_folders


def test_phase_b_files_documents_without_touching_the_folder_tree(batch):
    _apply_a(batch)
    before_folders = _folders_fingerprint()
    report = _apply_b(batch)
    assert report["committed"] is True
    assert report["assigned"] == 3 and report["audit_rows"] == 3
    assert _folders_fingerprint() == before_folders
    with engine.connect() as connection:
        placed = connection.execute(text(
            "select d.id, f.code from documents d join document_folders f on f.id = d.folder_id "
            " where d.id = any(:ids)"), {"ids": batch["ids"]}).all()
    assert len(placed) == 3
    by_id = dict(placed)
    for assignment in batch["manifest_b"]["assignments"]:
        assert by_id[assignment["document_id"]] == assignment["folder_code"]


def test_phase_b_records_derivation_provenance_in_the_audit_trail(tmp_path):
    """A derived service must be visible as derived in the audit row, not just in the manifest."""
    person_id = _person("Derived")
    name = f"Derived {_TAG}"
    backing_ids = [_document(person_id) for _ in range(6)]
    taxdome_id = _document(person_id)
    proposals = [_proposal(i, person_id, name) for i in backing_ids]
    proposals.append({
        **_proposal(taxdome_id, person_id, name),
        "source": "TaxDome Drive",
        "source_path": rf"{name}\Firm docs shared with client\2023\2023 Signature Documents.pdf",
        "proposed_top_level_category": "Firm Deliverables",
        "original_name": "2023 Signature Documents.pdf",
    })
    rows, _ = build_rows(proposals)
    manifest_a = build_phase_a_manifest(rows)
    manifest_b = build_phase_b_manifest(rows, manifest_a)
    path_a, sha_a = _write(tmp_path / "a.json", manifest_a)
    path_b, sha_b = _write(tmp_path / "b.json", manifest_b)

    phase_a.run(path_a, apply_changes=True, manifest_sha256=sha_a, actor_user_id=1,
                confirm=confirm_phrase(PHASE_A, manifest_a["folder_count"]),
                report_dir=tmp_path / "ra")
    report = phase_b.run(path_b, apply_changes=True, manifest_sha256=sha_b, actor_user_id=1,
                         confirm=confirm_phrase(PHASE_B, manifest_b["document_count"]),
                         report_dir=tmp_path / "rb")
    assert report["committed"] is True
    assert manifest_b["derived_assignments"] == 1
    with engine.connect() as connection:
        metadata_rows = [json.loads(r[0]) if isinstance(r[0], str) else r[0]
                         for r in connection.execute(text(
                             "select metadata from audit_events where request_id = :r"),
                             {"r": report["request_id"]})]
    derived = [m for m in metadata_rows if m.get("derivation_rule")]
    assert len(derived) == 1
    assert derived[0]["derivation_rule"] == "owner_profile_single_service_v1"
    assert derived[0]["backing_document_count"] == 6
    assert derived[0]["service_source"] == "taxdome_owner_profile_derivation"


def test_phase_b_aborts_on_eligibility_drift(batch):
    _apply_a(batch)
    with engine.begin() as connection:
        connection.execute(text("update documents set archived = true where id = :i"),
                           {"i": batch["ids"][0]})
    with pytest.raises(SystemExit, match="no longer match the approved manifest"):
        _apply_b(batch)
    with engine.connect() as connection:
        filed = connection.execute(text(
            "select count(*) from documents where id = any(:ids) and folder_id is not null"),
            {"ids": batch["ids"]}).scalar()
    assert filed == 0


def test_phase_b_aborts_on_ownership_drift(batch, capsys):
    _apply_a(batch)
    other = _person("Bob")
    with engine.begin() as connection:
        connection.execute(text("update documents set person_id = :p where id = :i"),
                           {"p": other, "i": batch["ids"][0]})
    with pytest.raises(SystemExit, match="no longer match the approved manifest"):
        _apply_b(batch)
    assert "ownership drifted" in capsys.readouterr().out


def test_phase_b_aborts_when_the_folder_tree_changed_after_approval(batch):
    _apply_a(batch)
    with engine.begin() as connection:
        connection.execute(text(
            "update document_folders set name = 'Renamed' where code = :c"),
            {"c": batch["manifest_a"]["folders"][0]["code"]})
    with pytest.raises(SystemExit, match="live folder tree digest"):
        _apply_b(batch)


def test_phase_b_refuses_a_tampered_manifest(batch, tmp_path):
    _apply_a(batch)
    tampered = json.loads(Path(batch["path_b"]).read_text(encoding="utf-8"))
    tampered["assignments"][0]["tax_year"] = 1999
    path, sha = _write(tmp_path / "tampered_b.json", tampered)
    with pytest.raises(SystemExit, match="the manifest was edited"):
        phase_b.run(path, manifest_sha256=sha)


def test_phase_b_refuses_a_widened_scope(batch, tmp_path):
    """A document added to the manifest after freezing changes the digest and is refused."""
    _apply_a(batch)
    widened = json.loads(Path(batch["path_b"]).read_text(encoding="utf-8"))
    extra = dict(widened["assignments"][0])
    extra["document_id"] = 99999999
    widened["assignments"].append(extra)
    widened["document_count"] += 1
    path, sha = _write(tmp_path / "widened_b.json", widened)
    with pytest.raises(SystemExit, match="the manifest was edited"):
        phase_b.run(path, manifest_sha256=sha)


def test_phase_b_refuses_a_wrong_confirmation_or_actor(batch):
    _apply_a(batch)
    with pytest.raises(SystemExit, match="confirmation phrase"):
        _apply_b(batch, confirm=confirm_phrase(PHASE_A, 3))
    with pytest.raises(SystemExit, match="not the approved actor"):
        _apply_b(batch, actor_user_id=7)


def test_an_already_filed_document_aborts(batch, capsys):
    _apply_a(batch)
    _apply_b(batch)
    capsys.readouterr()
    with pytest.raises(SystemExit, match="no longer match the approved manifest"):
        _apply_b(batch, report_dir=batch["out"] / "b2")
    assert "already filed in folder" in capsys.readouterr().out


# --- phase B eligibility -------------------------------------------------------------------------
#
# The planner used to emit every AUTO_FILE_SAFE row that passed the naming gate, whatever its live
# filing state, while apply_canonical_filing refuses any row that already has a folder_id. In
# production that mismatch aborted a Phase B whose 7,422 already-filed documents were listed
# alongside the 3,801 that were genuinely eligible. These prove the plan now agrees with the apply,
# and that agreeing did not cost the apply its own guard.

def _pure_rows(specs):
    """Rows for synthetic documents — no database. ``specs`` is (document_id, year, opaque)."""
    name = f"Ada {_TAG}"
    proposals = []
    for document_id, year, opaque in specs:
        proposal = _proposal(document_id, 4242, name, year=year)
        if opaque:
            # Same string both sides and not useful -> raw_filename_low_quality -> naming hold.
            proposal["original_name"] = "IMG_4695.jpg"
            proposal["proposed_display_name"] = "IMG_4695.jpg"
        proposals.append(proposal)
    rows, _ = build_rows(proposals)
    return rows


def test_phase_b_excludes_documents_that_are_already_filed():
    rows = _pure_rows([(101, 2023, False), (102, 2023, False), (103, 2024, False)])
    manifest_a = build_phase_a_manifest(rows)
    manifest_b = build_phase_b_manifest(rows, manifest_a, filed_document_ids=[101, 103])

    assert [a["document_id"] for a in manifest_b["assignments"]] == [102]
    assert manifest_b["document_count"] == 1
    assert manifest_b["already_filed_count"] == 2
    assert manifest_b["already_filed_document_ids"] == [101, 103]


def test_phase_b_keeps_every_unfiled_document():
    rows = _pure_rows([(101, 2023, False), (102, 2023, False), (103, 2024, False)])
    manifest_b = build_phase_b_manifest(rows, build_phase_a_manifest(rows))

    assert [a["document_id"] for a in manifest_b["assignments"]] == [101, 102, 103]
    assert manifest_b["already_filed_count"] == 0
    assert manifest_b["already_filed_document_ids"] == []


def test_a_naming_hold_is_still_held_back():
    rows = _pure_rows([(101, 2023, False), (102, 2023, True)])
    manifest_b = build_phase_b_manifest(rows, build_phase_a_manifest(rows))

    assert [a["document_id"] for a in manifest_b["assignments"]] == [101]
    assert manifest_b["naming_hold_count"] == 1
    assert manifest_b["naming_hold_document_ids"] == [102]


def test_already_filed_wins_over_naming_hold_so_the_buckets_never_overlap():
    """A filed document is not waiting for a better name — it is done. One bucket, never two."""
    rows = _pure_rows([(101, 2023, True)])
    manifest_b = build_phase_b_manifest(rows, build_phase_a_manifest(rows),
                                        filed_document_ids=[101])

    assert manifest_b["already_filed_document_ids"] == [101]
    assert manifest_b["naming_hold_document_ids"] == []
    assert manifest_b["document_count"] == 0


def test_phase_a_population_is_unaffected_by_what_is_already_filed():
    """Phase A materializes the tree for the WHOLE AUTO_FILE_SAFE population, filed or not."""
    rows = _pure_rows([(101, 2023, False), (102, 2023, True), (103, 2024, False)])
    before = build_phase_a_manifest(rows)
    build_phase_b_manifest(rows, before, filed_document_ids=[101, 102, 103])
    after = build_phase_a_manifest(rows)

    assert after["folder_count"] == before["folder_count"] == 4
    assert after["census"] == before["census"]
    assert after["folder_manifest_digest"] == before["folder_manifest_digest"]


def test_phase_b_counts_reconcile_between_every_bucket():
    # 101 unfiled+named -> planned; 102 unfiled+opaque -> naming hold; 103 and 104 filed.
    rows = _pure_rows([(101, 2023, False), (102, 2023, True),
                       (103, 2024, False), (104, 2024, True)])
    manifest_b = build_phase_b_manifest(rows, build_phase_a_manifest(rows),
                                        filed_document_ids=[103, 104])

    reconciliation = manifest_b["reconciliation"]
    assert reconciliation == {"auto_file_safe": 4, "already_filed": 2, "naming_hold": 1,
                              "planned": 1, "reconciles": True}
    assert reconciliation["auto_file_safe"] == (
        reconciliation["already_filed"] + reconciliation["naming_hold"]
        + reconciliation["planned"])
    assert manifest_b["auto_file_safe_count"] == 4
    assert manifest_b["document_count"] == reconciliation["planned"]


def test_excluding_a_filed_document_preserves_the_phase_a_binding():
    """The folder digest binds B to A. Narrowing the document set must not disturb it."""
    rows = _pure_rows([(101, 2023, False), (102, 2024, False)])
    manifest_a = build_phase_a_manifest(rows)
    full = build_phase_b_manifest(rows, manifest_a)
    narrowed = build_phase_b_manifest(rows, manifest_a, filed_document_ids=[101])

    assert (narrowed["folder_manifest_digest"] == full["folder_manifest_digest"]
            == manifest_a["folder_manifest_digest"])
    assert narrowed["assignment_digest"] != full["assignment_digest"]
    assert narrowed["confirm_phrase"] == confirm_phrase(PHASE_B, 1)


def test_replanning_after_a_partial_apply_files_only_what_is_left(batch, tmp_path):
    """The production failure, end to end: replan against live state and the rest goes home."""
    _apply_a(batch)
    stranded = batch["manifest_b"]["assignments"][0]
    with engine.begin() as connection:
        folder_id = connection.execute(text(
            "select id from document_folders where code = :c"),
            {"c": stranded["folder_code"]}).scalar()
        connection.execute(text("update documents set folder_id = :f where id = :i"),
                           {"f": folder_id, "i": stranded["document_id"]})

    # The manifest planned before that filing still names it, and the apply correctly refuses.
    with pytest.raises(SystemExit, match="no longer match the approved manifest"):
        _apply_b(batch)

    replanned = build_phase_b_manifest(batch["rows"], batch["manifest_a"],
                                       filed_document_ids=[stranded["document_id"]])
    assert replanned["document_count"] == 2
    assert stranded["document_id"] not in [a["document_id"] for a in replanned["assignments"]]

    path, sha = _write(tmp_path / "phase_b_replanned.json", replanned)
    report = phase_b.run(path, apply_changes=True, confirm=confirm_phrase(PHASE_B, 2),
                         actor_user_id=1, manifest_sha256=sha,
                         report_dir=tmp_path / "out_replanned")
    assert report["committed"] is True and report["assigned"] == 2
    with engine.connect() as connection:
        filed = connection.execute(text(
            "select count(*) from documents where id = any(:ids) and folder_id is not null"),
            {"ids": batch["ids"]}).scalar()
    assert filed == 3


def test_apply_still_refuses_a_document_filed_after_planning(batch, tmp_path):
    """Eligibility in the plan must not cost the apply its drift guard."""
    _apply_a(batch)
    planned = build_phase_b_manifest(batch["rows"], batch["manifest_a"], filed_document_ids=[])
    assert planned["document_count"] == 3
    path, sha = _write(tmp_path / "phase_b_fresh.json", planned)

    drifted = planned["assignments"][0]
    with engine.begin() as connection:
        folder_id = connection.execute(text(
            "select id from document_folders where code = :c"),
            {"c": drifted["folder_code"]}).scalar()
        connection.execute(text("update documents set folder_id = :f where id = :i"),
                           {"f": folder_id, "i": drifted["document_id"]})

    with pytest.raises(SystemExit, match="no longer match the approved manifest"):
        phase_b.run(path, apply_changes=True, confirm=confirm_phrase(PHASE_B, 3),
                    actor_user_id=1, manifest_sha256=sha, report_dir=tmp_path / "out_drift")


# --- phase B target-subtree binding ----------------------------------------------------------------
#
# _live_subtree deliberately reads only the batch's destinations and their ancestors, but the digest
# it produced was compared against folder_manifest_digest — the digest of the WHOLE phase A manifest.
# Those agree only when a batch targets every folder phase A created, which the pre-eligibility phase
# B did. Once the plan was correctly narrowed to 3,787 documents over 852 of 1,941 destinations, a
# proper subset could not hash to the whole and every run aborted. The subtree now has its own
# digest, with the phase A digest hashed into it so the binding is checked rather than just carried.

def _file(document_id, folder_code) -> int:
    with engine.begin() as connection:
        folder_id = connection.execute(text(
            "select id from document_folders where code = :c"), {"c": folder_code}).scalar()
        connection.execute(text("update documents set folder_id = :f where id = :i"),
                           {"f": folder_id, "i": document_id})
    return folder_id


def _rename(folder_code, name="Renamed"):
    with engine.begin() as connection:
        connection.execute(text("update document_folders set name = :n where code = :c"),
                           {"n": name, "c": folder_code})


def _narrow_to_one_year(batch):
    """Replan phase B after the 2023 documents are filed — one destination out of two.

    This is the production shape: the target subtree becomes client + service + the 2024 year,
    a PROPER subset of the four folders phase A created.
    """
    by_year = {}
    for assignment in batch["manifest_b"]["assignments"]:
        by_year.setdefault(assignment["tax_year"], []).append(assignment)
    filed = [a["document_id"] for a in by_year[2023]]
    for assignment in by_year[2023]:
        _file(assignment["document_id"], assignment["folder_code"])
    narrowed = build_phase_b_manifest(batch["rows"], batch["manifest_a"],
                                      filed_document_ids=filed)
    return narrowed, by_year[2023][0]["folder_code"], by_year[2024][0]["folder_code"]


def _run_b(manifest, tmp_path, name, **kw):
    path, sha = _write(tmp_path / f"{name}.json", manifest)
    kw.setdefault("apply_changes", True)
    kw.setdefault("confirm", confirm_phrase(PHASE_B, manifest["document_count"]))
    kw.setdefault("actor_user_id", 1)
    kw.setdefault("manifest_sha256", sha)
    kw.setdefault("report_dir", tmp_path / f"out_{name}")
    return phase_b.run(path, **kw)


def test_a_proper_subset_has_its_own_digest_not_the_phase_a_one():
    rows = _pure_rows([(101, 2023, False), (102, 2024, False)])
    manifest_a = build_phase_a_manifest(rows)
    narrowed = build_phase_b_manifest(rows, manifest_a, filed_document_ids=[101])

    assert narrowed["document_count"] == 1
    assert narrowed["target_subtree_folder_count"] == 3          # client + service + one year
    assert manifest_a["folder_count"] == 4                        # phase A still built both years
    assert narrowed["target_subtree_digest"] != narrowed["folder_manifest_digest"]
    # And the full phase A binding is still carried, unchanged.
    assert narrowed["folder_manifest_digest"] == manifest_a["folder_manifest_digest"]


def test_the_target_subtree_is_exactly_the_destinations_and_their_ancestors():
    rows = _pure_rows([(101, 2023, False), (102, 2024, False)])
    manifest_a = build_phase_a_manifest(rows)
    narrowed = build_phase_b_manifest(rows, manifest_a, filed_document_ids=[101])

    targets = sorted({a["folder_code"] for a in narrowed["assignments"]})
    subtree = target_subtree_nodes(manifest_a, targets)
    assert [n["kind"] for n in subtree] == ["client", "service", "year"]
    assert set(targets) <= {n["code"] for n in subtree}
    assert narrowed["target_subtree_census"] == {"client": 1, "service": 1, "year": 1}


def test_the_subtree_digest_is_bound_to_the_phase_a_digest():
    """Editing the recorded phase A digest changes the subtree digest — the binding is checked."""
    rows = _pure_rows([(101, 2023, False), (102, 2024, False)])
    manifest_a = build_phase_a_manifest(rows)
    manifest_b = build_phase_b_manifest(rows, manifest_a)
    subtree = target_subtree_nodes(
        manifest_a, sorted({a["folder_code"] for a in manifest_b["assignments"]}))

    honest = target_subtree_digest_of(subtree, manifest_a["folder_manifest_digest"])
    forged = target_subtree_digest_of(subtree, "0" * 64)
    assert honest == manifest_b["target_subtree_digest"]
    assert forged != honest


def test_eligibility_reconciliation_survives_the_new_digest():
    rows = _pure_rows([(101, 2023, False), (102, 2023, True), (103, 2024, False)])
    manifest_b = build_phase_b_manifest(rows, build_phase_a_manifest(rows),
                                        filed_document_ids=[103])
    reconciliation = manifest_b["reconciliation"]
    assert reconciliation == {"auto_file_safe": 3, "already_filed": 1, "naming_hold": 1,
                              "planned": 1, "reconciles": True}


def test_a_narrowed_phase_b_verifies_against_live_state(batch, tmp_path):
    """The production failure: a proper subset of phase A's destinations must now pass."""
    _apply_a(batch)
    narrowed, _filed_year_code, target_code = _narrow_to_one_year(batch)
    assert narrowed["document_count"] == 1
    assert narrowed["target_subtree_folder_count"] == 3
    assert narrowed["target_subtree_digest"] != narrowed["folder_manifest_digest"]

    report = _run_b(narrowed, tmp_path, "narrowed")
    assert report["committed"] is True and report["assigned"] == 1
    assert report["target_subtree_digest"] == narrowed["target_subtree_digest"]
    with engine.connect() as connection:
        code = connection.execute(text(
            "select f.code from documents d join document_folders f on f.id = d.folder_id "
            " where d.id = :i"), {"i": narrowed["assignments"][0]["document_id"]}).scalar()
    assert code == target_code


def test_a_phase_a_folder_outside_the_subtree_does_not_abort_the_run(batch, tmp_path):
    """The whole reason the read is scoped: unrelated folders must not invalidate this batch."""
    _apply_a(batch)
    narrowed, outside_code, _target = _narrow_to_one_year(batch)
    _rename(outside_code, "Some Other Batch Renamed This")

    report = _run_b(narrowed, tmp_path, "outside")
    assert report["committed"] is True and report["assigned"] == 1


def test_mutating_a_target_folder_aborts(batch, tmp_path):
    _apply_a(batch)
    narrowed, _outside, target_code = _narrow_to_one_year(batch)
    _rename(target_code)
    with pytest.raises(SystemExit, match="target subtree digest"):
        _run_b(narrowed, tmp_path, "target_mutated")


def test_mutating_a_required_ancestor_aborts(batch, tmp_path):
    _apply_a(batch)
    narrowed, _outside, _target = _narrow_to_one_year(batch)
    client_code = next(n["code"] for n in batch["manifest_a"]["folders"] if n["kind"] == "client")
    _rename(client_code)
    with pytest.raises(SystemExit, match="target subtree digest"):
        _run_b(narrowed, tmp_path, "ancestor_mutated")


def test_a_missing_target_aborts(batch, tmp_path):
    _apply_a(batch)
    narrowed, _outside, target_code = _narrow_to_one_year(batch)
    with engine.begin() as connection:
        connection.execute(text("delete from document_folders where code = :c"),
                           {"c": target_code})
    with pytest.raises(SystemExit, match="approved folders do not exist"):
        _run_b(narrowed, tmp_path, "target_missing")


def test_a_changed_parent_chain_aborts(batch, tmp_path):
    """Re-parent the year folder straight onto the client, skipping its service."""
    _apply_a(batch)
    narrowed, _outside, target_code = _narrow_to_one_year(batch)
    client_code = next(n["code"] for n in batch["manifest_a"]["folders"] if n["kind"] == "client")
    with engine.begin() as connection:
        client_id = connection.execute(text(
            "select id from document_folders where code = :c"), {"c": client_code}).scalar()
        connection.execute(text(
            "update document_folders set parent_folder_id = :p where code = :c"),
            {"p": client_id, "c": target_code})
    with pytest.raises(SystemExit, match="target subtree digest"):
        _run_b(narrowed, tmp_path, "reparented")


def test_a_manifest_without_the_subtree_digest_is_refused(batch, tmp_path):
    """A plan frozen before this binding existed must be re-planned, not silently accepted."""
    _apply_a(batch)
    stale = json.loads(Path(batch["path_b"]).read_text(encoding="utf-8"))
    stale.pop("target_subtree_digest")
    path, sha = _write(tmp_path / "stale_b.json", stale)
    with pytest.raises(SystemExit, match="carries no target_subtree_digest"):
        phase_b.run(path, apply_changes=True, confirm=confirm_phrase(PHASE_B, 3),
                    actor_user_id=1, manifest_sha256=sha, report_dir=tmp_path / "out_stale")


def test_document_drift_is_still_refused_under_the_new_binding(batch, tmp_path):
    """The folder gate changed; the document gate did not."""
    _apply_a(batch)
    narrowed, _outside, _target = _narrow_to_one_year(batch)
    with engine.begin() as connection:
        connection.execute(text("update documents set archived = true where id = :i"),
                           {"i": narrowed["assignments"][0]["document_id"]})
    with pytest.raises(SystemExit, match="no longer match the approved manifest"):
        _run_b(narrowed, tmp_path, "doc_drift")


# --- rollback ------------------------------------------------------------------------------------

def test_phase_b_rollback_restores_folder_id_and_leaves_folders_alone(batch):
    _apply_a(batch)
    report = _apply_b(batch)
    snapshot = report["snapshot"]
    before_folders = _folders_fingerprint()
    rolled = rollback_b.run(snapshot, apply_changes=True, actor_user_id=1,
                            snapshot_sha256=report["snapshot_sha256"],
                            confirm=rollback_phrase(PHASE_B, 3))
    assert rolled["committed"] is True and rolled["restored"] == 3
    assert _folders_fingerprint() == before_folders
    with engine.connect() as connection:
        filed = connection.execute(text(
            "select count(*) from documents where id = any(:ids) and folder_id is not null"),
            {"ids": batch["ids"]}).scalar()
    assert filed == 0


def test_phase_b_rollback_detects_drift(batch):
    _apply_a(batch)
    report = _apply_b(batch)
    with engine.begin() as connection:
        connection.execute(text("update documents set folder_id = null where id = :i"),
                           {"i": batch["ids"][0]})
    with pytest.raises(SystemExit, match="drifted since the apply"):
        rollback_b.run(report["snapshot"], apply_changes=True, actor_user_id=1,
                       snapshot_sha256=report["snapshot_sha256"],
                       confirm=rollback_phrase(PHASE_B, 3))


def test_phase_a_rollback_refuses_while_documents_are_filed(batch):
    report_a = _apply_a(batch)
    _apply_b(batch)
    with pytest.raises(SystemExit, match="roll back phase B before phase A"):
        rollback_a.run(report_a["snapshot"], apply_changes=True, actor_user_id=1,
                       snapshot_sha256=report_a["snapshot_sha256"],
                       confirm=rollback_phrase(PHASE_A, 4))


def test_phase_a_rollback_removes_only_what_it_created(batch):
    report_a = _apply_a(batch)
    before_documents = _documents_fingerprint()
    rolled = rollback_a.run(report_a["snapshot"], apply_changes=True, actor_user_id=1,
                            snapshot_sha256=report_a["snapshot_sha256"],
                            confirm=rollback_phrase(PHASE_A, 4))
    assert rolled["committed"] is True and rolled["deleted"] == 4
    assert _folder_count(batch) == 0
    assert _documents_fingerprint() == before_documents


def test_phase_a_rollback_leaves_reused_folders_alone(batch):
    _apply_a(batch)
    second = _apply_a(batch, report_dir=batch["out"] / "a2")
    assert second["reused"] == 4
    rolled = rollback_a.run(second["snapshot"], apply_changes=True, actor_user_id=1,
                            snapshot_sha256=second["snapshot_sha256"],
                            confirm=rollback_phrase(PHASE_A, 4))
    assert rolled["deleted"] == 0
    assert _folder_count(batch) == 4
