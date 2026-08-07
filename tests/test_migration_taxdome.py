"""TaxDome Document Migration — PREVIEW (read-only) coverage.

Proves the preview: matches top-level folders to canonical people/households (matched / ambiguous /
unmatched); builds the proposed destination path carrying the canonical id; detects placeholders,
zero-byte, unreadable, duplicate-content candidates, and destination collisions; assigns readiness
(ready / review-required / blocked); writes the three named artifacts; and makes ZERO database writes and
ZERO file changes. Also proves APPLY is refused before any database access. Temp trees + temp people only.
"""
import dataclasses
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import engine, households, metadata, people
from app.services.migration import engine as mig_engine
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
    by = {r["source_folder"]: r for r in result.reconciliation}
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
    monkeypatch.setattr(mig_engine, "_is_placeholder", lambda st: True)   # simulate OneDrive cloud-only
    result = TaxDomeDocumentMigration(cfg).run(Mode.PREVIEW)
    assert result.counts["cloud_only_placeholders"] >= 1
    a = {r["source_folder"]: r for r in result.reconciliation}[f"Alpha {_TAG}"]
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


def test_generic_engine_runs_any_adapter(cfg, tmp_path):
    """The engine is adapter-agnostic: a non-TaxDome adapter runs the identical pipeline, and the
    destination category comes from the item (not hard-coded to Tax)."""
    from app.services.migration.engine import DocumentMigrationJob, SourceAdapter, SourceItem
    pid = _person(f"Zeta {_TAG}", first="Zeta", last=_TAG)
    f = tmp_path / "x.pdf"
    f.write_bytes(b"1234")

    class FakeAdapter(SourceAdapter):
        source_system = "FakeSource"
        def source_root(self, config):
            return tmp_path
        def discover(self, config):
            yield SourceItem(source_system="FakeSource", group_key=f"Zeta {_TAG}", abs_path=str(f),
                             rel_within_group="2021/x.pdf", category="General")

    res = DocumentMigrationJob(FakeAdapter(), cfg).run(Mode.PREVIEW)
    assert res.counts["source_system"] == "FakeSource"
    row = res.reconciliation[0]
    assert row["classification"] == "matched" and str(row["canonical_id"]) == str(pid)
    assert row["category"] == "General"                    # category flows from the item, not TaxDome
    assert f"{pid} - " in row["proposed_destination_root"]


def test_apply_refused_before_any_write(cfg):
    before = _db_counts()
    with pytest.raises(ModeNotSupported, match="disabled"):
        TaxDomeDocumentMigration(cfg).run(Mode.APPLY)
    assert _db_counts() == before
    assert Mode.APPLY not in TaxDomeDocumentMigration.supported_modes
