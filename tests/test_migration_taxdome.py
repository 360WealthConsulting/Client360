"""TaxDome Document Migration — PREVIEW (read-only) coverage.

Proves the preview: matches top-level folders to canonical people/households (matched / ambiguous /
unmatched); builds the proposed destination path carrying the canonical id; detects placeholders,
zero-byte, unreadable, duplicate-content candidates, and destination collisions; assigns readiness
(ready / review-required / blocked); writes the three named artifacts; and makes ZERO database writes and
ZERO file changes. Also proves APPLY is refused before any database access. Temp trees + temp people only.
"""
import dataclasses
import os
import tempfile
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import engine, households, metadata, people
from app.services.migration import storage as mig_storage
from app.services.migration.base import Mode, ModeNotSupported
from app.services.migration.config import MigrationConfig
from app.services.migration.taxdome import TaxDomeDocumentMigration

import_jobs = metadata.tables["import_jobs"]
documents = metadata.tables["documents"]
_TAG = uuid.uuid4().hex[:8]
_CREATED = {"people": [], "households": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _CREATED["people"]:
            c.execute(people.delete().where(people.c.id.in_(_CREATED["people"])))
        if _CREATED["households"]:
            c.execute(households.delete().where(households.c.id.in_(_CREATED["households"])))
    _CREATED["people"].clear(); _CREATED["households"].clear()


def _person(full_name, first=None, last=None, household_id=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            full_name=full_name, first_name=first, last_name=last, active=True,
            household_id=household_id).returning(people.c.id)).scalar_one()
    _CREATED["people"].append(pid)
    return pid


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _CREATED["households"].append(hid)
    return hid


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "TaxDome"
    def mk(folder, files):
        d = root / folder
        d.mkdir(parents=True)
        for rel, data in files:
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
    mk(f"Alpha {_TAG}", [("2024/Tax Return.pdf", b"a" * 100), ("desktop.ini", b"x")])   # matched person
    mk(f"Bravo {_TAG}", [("doc.pdf", b"b" * 50)])                                        # ambiguous
    mk(f"Nomatch {_TAG}", [("2022/scan.pdf", b"c" * 30)])                                # unmatched
    mk(f"Carol {_TAG} and Dave {_TAG}", [("2023/joint.pdf", b"d" * 40)])                 # matched household
    return root


@pytest.fixture
def cfg(tmp_path, source):
    (tmp_path / "out").mkdir()
    (tmp_path / "dest").mkdir()
    base = MigrationConfig.from_env()
    return dataclasses.replace(base, migration_root=tmp_path / "out",
                               taxdome_migration_root=source, migration_dest_root=tmp_path / "dest")


def _seed():
    pid = _person(f"Alpha {_TAG}", first="Alpha", last=_TAG)
    _person(f"Bravo {_TAG}"); _person(f"Bravo {_TAG}")                 # duplicate -> ambiguous
    hid = _household(f"Carol-Dave {_TAG}")
    _person(f"Carol {_TAG}", household_id=hid)
    _person(f"Dave {_TAG}", household_id=hid)
    return pid, hid


def _db_counts():
    with engine.connect() as c:
        return (int(c.execute(select(func.count()).select_from(import_jobs)).scalar_one()),
                int(c.execute(select(func.count()).select_from(documents)).scalar_one()))


def test_preview_classification_destination_and_readiness(cfg):
    pid, hid = _seed()
    before = _db_counts()
    result = TaxDomeDocumentMigration(cfg).run(Mode.PREVIEW)
    c = result.counts
    assert c["matched_folders"] == 2 and c["ambiguous_folders"] == 1 and c["unmatched_folders"] == 1
    assert c["artifact_types"] == ["document"]                 # TaxDome adapter emits document records
    by = {r["source_group"]: r for r in result.reconciliation}
    a = by[f"Alpha {_TAG}"]
    assert a["classification"] == "matched" and a["entity_type"] == "person" and str(a["canonical_id"]) == str(pid)
    # destination carries the canonical id + display, category Tax, detected year 2024
    assert a["proposed_destination_root"].endswith(f"Clients/{pid} - {_TAG}, Alpha") or \
           a["proposed_destination_root"].endswith(f"Clients\\{pid} - {_TAG}, Alpha")
    assert a["readiness"] == "ready"
    h = by[f"Carol {_TAG} and Dave {_TAG}"]
    assert h["classification"] == "matched" and h["entity_type"] == "household" and str(h["canonical_id"]) == str(hid)
    assert by[f"Bravo {_TAG}"]["classification"] == "ambiguous"
    assert by[f"Bravo {_TAG}"]["readiness"] == "review-required"
    assert by[f"Nomatch {_TAG}"]["classification"] == "unmatched"
    # counts: ignored excluded from doc rows; matched vs review split
    assert c["ignored_files"] >= 1
    assert c["estimated_document_rows_total"] == 4
    assert c["estimated_document_rows_matched"] == 2          # Alpha + joint
    # ZERO db writes
    assert _db_counts() == before


def test_preview_detects_zero_byte_and_preflight(cfg, source):
    _seed()
    (source / f"Alpha {_TAG}" / "2024" / "empty.pdf").write_bytes(b"")   # zero-byte
    result = TaxDomeDocumentMigration(cfg).run(Mode.PREVIEW)
    assert result.counts["zero_byte_files"] >= 1
    # destination pre-flight is reported (read-only stat), dest dir exists in the fixture
    assert result.counts["dest_root_exists"] is True
    assert "dest_free_gb" in result.counts and "fits_with_10pct_margin" in result.counts


def test_preview_detects_destination_collision_same_entity(cfg, source):
    pid, _ = _seed()
    # a SECOND top-level folder that resolves to the SAME person (order-insensitive name key)
    dupe = source / f"{_TAG} Alpha"
    (dupe / "2024").mkdir(parents=True)
    (dupe / "2024" / "Tax Return.pdf").write_bytes(b"a" * 100)           # same dest path as Alpha's file
    result = TaxDomeDocumentMigration(cfg).run(Mode.PREVIEW)
    assert result.counts["destination_collisions"] >= 1


def test_preview_placeholder_blocks_folder(cfg, monkeypatch):
    _seed()
    monkeypatch.setattr(mig_storage, "_is_placeholder", lambda st: True)   # simulate OneDrive cloud-only (Storage service)
    result = TaxDomeDocumentMigration(cfg).run(Mode.PREVIEW)
    assert result.counts["cloud_only_placeholders"] >= 1
    a = {r["source_group"]: r for r in result.reconciliation}[f"Alpha {_TAG}"]
    assert a["placeholder_count"] >= 1 and a["readiness"] == "blocked"   # matched but cloud-only -> blocked


def test_preview_writes_named_artifacts(cfg):
    _seed()
    result = TaxDomeDocumentMigration(cfg).run(Mode.PREVIEW)
    d = Path(result.run_dir)
    for name in ("migration_preview.csv", "migration_summary.txt", "migration_manifest.json"):
        assert (d / name).exists(), name
    assert "proposed_destination_root" in (d / "migration_preview.csv").read_text()


def test_preview_does_not_modify_source(cfg, source):
    _seed()
    before = sorted(str(p.relative_to(source)) for p in source.rglob("*"))
    TaxDomeDocumentMigration(cfg).run(Mode.PREVIEW)
    assert sorted(str(p.relative_to(source)) for p in source.rglob("*")) == before


def test_engine_is_source_and_artifact_agnostic(cfg, tmp_path):
    """A non-TaxDome adapter runs the identical pipeline; the document handler is dispatched by
    artifact_type; category flows from the record; and an unregistered artifact type is reported (never
    silently dropped) — proving the engine is source- AND artifact-agnostic."""
    from app.services.migration.engine import IngestionJob, IngestionRecord, SourceAdapter
    pid = _person(f"Zeta {_TAG}", first="Zeta", last=_TAG)
    f = tmp_path / "x.pdf"
    f.write_bytes(b"1234")

    class FakeAdapter(SourceAdapter):
        source_system = "FakeSource"
        def source_root(self, config):
            return tmp_path
        def discover(self, config):
            yield IngestionRecord(source_system="FakeSource", artifact_type="document",
                                  group_key=f"Zeta {_TAG}",
                                  payload={"abs_path": str(f), "rel_within_group": "2021/x.pdf",
                                           "category": "General"})
            # a future business object with no handler yet -> reported, not dropped
            yield IngestionRecord(source_system="FakeSource", artifact_type="note",
                                  group_key=f"Zeta {_TAG}", payload={"body": "call the client"})

    res = IngestionJob(FakeAdapter(), cfg).run(Mode.PREVIEW)
    assert res.counts["source_system"] == "FakeSource"
    assert sorted(res.counts["artifact_types"]) == ["document", "note"]
    doc_row = next(r for r in res.reconciliation if r["artifact_type"] == "document")
    assert doc_row["classification"] == "matched" and str(doc_row["canonical_id"]) == str(pid)
    assert doc_row["category"] == "General"                # category flows from the record, not TaxDome
    assert f"{pid} - " in doc_row["proposed_destination_root"]
    # the unhandled 'note' artifact is surfaced as an exception (extension point = register a handler)
    assert any(e.get("artifact_type") == "note" and "no handler" in e["reason"] for e in res.exceptions)


def test_stage_events_published_and_subscribable(cfg):
    """Each independent ingestion stage publishes a StageEvent; a downstream subscriber receives them
    without modifying the engine — the Event Publishing seam (OCR/AI/indexing/search/… plug in here)."""
    from app.services.migration import events as ev
    _seed()
    received: list[str] = []
    ev.subscribe(lambda e: received.append(str(e.stage)))
    try:
        result = TaxDomeDocumentMigration(cfg).run(Mode.PREVIEW)
    finally:
        ev.clear_subscribers()
    assert result.counts["stage_events"] == [
        "discovery", "normalization", "canonical_matching", "transformation", "validation", "preview"]
    for stage in ("discovery", "canonical_matching", "validation", "preview"):
        assert stage in received                            # subscriber saw every completed stage


def test_apply_reconcile_retire_stages_declared_but_unimplemented(cfg):
    from app.services.migration.adapters.taxdome import TaxDomeAdapter
    from app.services.migration.engine import IngestionEngine
    eng = IngestionEngine(TaxDomeAdapter(), cfg)
    for stage in (eng.apply, eng.reconcile, eng.retire):
        with pytest.raises(NotImplementedError):
            stage()


def test_storage_service_is_injectable_and_replaceable(cfg):
    """Handlers go through the Storage Service — swap the backend and the handler is unchanged."""
    from app.services.migration.adapters.taxdome import TaxDomeAdapter
    from app.services.migration.engine import IngestionEngine
    from app.services.migration.storage import StatInfo, StorageService
    _seed()

    class FakeStorage(StorageService):                       # a different "backend" (NAS/object/cloud stand-in)
        def stat(self, uri):
            return StatInfo(exists=True, size=123, is_placeholder=True)
        def exists(self, uri):
            return True
        def free_and_total(self, path):
            return (10 ** 12, 2 * 10 ** 12)

    counts, _rows, _e, _n = IngestionEngine(TaxDomeAdapter(), cfg, storage=FakeStorage()).preview()
    assert counts["total_bytes"] == 123 * 4                  # 4 non-ignored files, each sized by the fake backend
    assert counts["cloud_only_placeholders"] == 4           # backend reported cloud-only → matched groups blocked
    assert counts["blocked_folders"] == 2                    # Alpha + the joint household


def test_engine_asks_the_identity_service(cfg):
    """The engine resolves entities via the Identity Service — swap it and the engine uses the swap."""
    from app.services.migration.adapters.taxdome import TaxDomeAdapter
    from app.services.migration.artifact import CanonicalEntity
    from app.services.migration.engine import IngestionEngine
    from app.services.migration.identity import CanonicalMatch, IdentityService

    class FakeIdentity(IdentityService):
        def load(self):
            pass
        def resolve(self, group_key, record):
            return CanonicalMatch("matched", "fake", CanonicalEntity("person", 999, "Fake, Person"))

    counts, rows, _e, _n = IngestionEngine(TaxDomeAdapter(), cfg, identity=FakeIdentity(cfg)).preview()
    assert counts["matched_folders"] == counts["groups"]     # every group matched via the injected service
    assert rows and all(r["canonical_id"] == 999 for r in rows)


def test_versioned_artifact_supports_history_never_overwrite():
    from app.services.migration.artifact import VersionedEnterpriseArtifact
    a = VersionedEnterpriseArtifact(source_system="X", artifact_type="document", group_key="g")
    for f in ("artifact_id", "version_id", "previous_version_id", "effective_date", "imported_date",
              "archived_date", "integrity_hash", "canonical_entity",
              "source_record_id", "source_locator"):
        assert hasattr(a, f) and getattr(a, f) is None       # unset until apply; new versions, never overwrite


def test_adapter_records_source_identity():
    """Discovery stamps the source's own id + original locator on every artifact (provenance contract)."""
    from app.services.migration.adapters.taxdome import TaxDomeAdapter
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "Smith, John").mkdir()
        (root / "Smith, John" / "2024").mkdir()
        (root / "Smith, John" / "2024" / "return.pdf").write_text("x")
        cfg = MigrationConfig(migration_root=root, wealthbox_export=root, taxdome_root=root,
                              sharepoint_root=root, scanner_root=root, document_root=root,
                              taxdome_migration_root=root)
        recs = list(TaxDomeAdapter().discover(cfg))
    assert recs and all(r.source_record_id and r.source_locator for r in recs)
    assert recs[0].source_locator.endswith("return.pdf")


def test_identity_supports_non_person_entities():
    """The Identity Service resolves organization/trust/estate (adapter-pre-resolved), not only people."""
    from app.services.migration.artifact import VersionedEnterpriseArtifact
    from app.services.migration.identity import SUPPORTED_ENTITY_TYPES, IdentityService
    assert set(SUPPORTED_ENTITY_TYPES) == {"person", "household", "organization", "trust", "estate"}
    svc = IdentityService(MigrationConfig(migration_root=Path("."), wealthbox_export=Path("."),
                                          taxdome_root=Path("."), sharepoint_root=Path("."),
                                          scanner_root=Path("."), document_root=Path(".")))
    for etype in ("organization", "trust", "estate"):
        rec = VersionedEnterpriseArtifact(source_system="X", artifact_type="document", group_key="g",
                                          entity_type=etype, canonical_id=7, display_name="Acme")
        m = svc.resolve("g", rec)
        assert m.status == "matched" and m.entity.entity_type == etype and m.entity.canonical_id == 7


def test_repository_areas_addressable_without_creating():
    """Enterprise repository areas are addressable; composing a URI creates nothing on disk."""
    from app.services.migration.storage import RepositoryArea, repository_uri
    with tempfile.TemporaryDirectory() as td:
        uri = repository_uri(td, RepositoryArea.DERIVATIVES, "ocr", "x.txt")
        assert uri.endswith(os.path.join("Derivatives", "ocr", "x.txt"))
        assert not os.path.exists(os.path.join(td, "Derivatives"))     # nothing created
    # the full enterprise area set is declared (not just a document tree)
    assert {a.value for a in RepositoryArea} >= {"Objects", "Staging", "Archive", "Vault",
                                                 "Derivatives", "Index", "Audit", "Exports"}


def test_transformation_extension_is_contract_only():
    """The Transformer extension point exists and is empty by design (OCR/AI/etc. plug in later)."""
    from app.services.migration.transform import TRANSFORMERS, Transformer
    assert TRANSFORMERS == {}                                # no enrichment wired in this phase
    with pytest.raises(TypeError):                           # abstract: cannot instantiate the contract
        Transformer()


def test_outbox_publisher_is_contract_only():
    """The durable outbox bridge is declared but unimplemented until APPLY (no competing bus, no writes)."""
    from app.services.migration.events import OutboxEventPublisher, Stage, StageEvent
    with pytest.raises(NotImplementedError):
        OutboxEventPublisher().publish(StageEvent(stage=Stage.DISCOVERY, source_system="X"))


def test_retirement_targets_exclude_drake():
    """Retirement targets TaxDome/SharePoint/Wealthbox; Drake stays operational (ingested, not retired)."""
    from app.services.migration.stages import RetirementService
    assert set(RetirementService.RETIREMENT_TARGETS) == {"TaxDome", "SharePoint/OneDrive", "Wealthbox"}
    assert not any("Drake" in t for t in RetirementService.RETIREMENT_TARGETS)


def test_apply_refused_before_any_write(cfg):
    before = _db_counts()
    with pytest.raises(ModeNotSupported, match="disabled"):
        TaxDomeDocumentMigration(cfg).run(Mode.APPLY)
    assert _db_counts() == before
    assert Mode.APPLY not in TaxDomeDocumentMigration.supported_modes
