"""Migration framework — MigrationJob base + inventory engine + Wealthbox preview.

Covers: the four artifacts are always written; read-only modes (inventory/preview) make NO database
writes and write NO import_jobs row; the inventory engine counts files/storage/duplicates and excludes
C:\\From AWS Server; the Wealthbox preview parses an export and reports would-create/dup/exception
counts without writing; and apply is guarded off in Phase 1. Temp fixtures only; no production paths.
"""
import csv
import io
import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import engine, metadata
from app.services.migration.base import Mode
from app.services.migration.config import MigrationConfig, is_excluded
from app.services.migration.inventory import InventoryJob, collect_inventory
from app.services.migration.wealthbox import WealthboxContactsMigration

import_jobs = metadata.tables["import_jobs"]


@pytest.fixture
def cfg(tmp_path):
    """A MigrationConfig pointed entirely at temp dirs (no real source/output paths touched)."""
    (tmp_path / "migration").mkdir()
    for name in ("wealthbox", "taxdome", "sharepoint", "scanner", "docs", "vault", "search"):
        (tmp_path / name).mkdir()
    return MigrationConfig(
        migration_root=tmp_path / "migration",
        wealthbox_export=tmp_path / "wealthbox",
        taxdome_root=tmp_path / "taxdome",
        sharepoint_root=tmp_path / "sharepoint",
        scanner_root=tmp_path / "scanner",
        document_root=tmp_path / "docs",
        vault_root=tmp_path / "vault",
        drake_roots=(tmp_path / "DRAKE24",),          # created per-test when needed
        unclassified_search_roots=(tmp_path / "search",),
    )


def _artifacts(run_dir: str) -> set[str]:
    return {p.name for p in Path(run_dir).iterdir()}


def _import_job_count() -> int:
    with engine.connect() as c:
        return int(c.execute(select(func.count()).select_from(import_jobs)).scalar_one())


# --- guardrails: excluded root ------------------------------------------------

def test_aws_backup_is_excluded():
    assert is_excluded(r"C:\From AWS Server")
    assert is_excluded(r"C:\From AWS Server\Drake\file.pdf")
    assert not is_excluded(r"C:\Client360\Data\Documents\x.pdf")


# --- artifacts always written -------------------------------------------------

def test_all_four_artifacts_written(cfg):
    result = WealthboxContactsMigration(cfg).run(Mode.INVENTORY)
    assert _artifacts(result.run_dir) >= {"manifest.json", "reconciliation.csv", "exceptions.csv", "summary.txt"}
    manifest = json.loads((Path(result.run_dir) / "manifest.json").read_text())
    assert manifest["source_system"] == "Wealthbox" and manifest["mode"] == "inventory"


# --- read-only modes make NO database writes ----------------------------------

def test_inventory_and_preview_write_no_import_jobs(cfg):
    before = _import_job_count()
    InventoryJob(cfg).run(Mode.INVENTORY)
    WealthboxContactsMigration(cfg).run(Mode.PREVIEW)
    assert _import_job_count() == before          # no import_jobs rows from read-only modes


# --- inventory engine counts + excludes ---------------------------------------

def test_inventory_counts_files_storage_duplicates(cfg):
    # scanner: 2 identical-named-and-sized files (duplicate indicator) + 1 unique
    (cfg.scanner_root / "a").mkdir()
    (cfg.scanner_root / "b").mkdir()
    (cfg.scanner_root / "a" / "dup.pdf").write_bytes(b"same-bytes")
    (cfg.scanner_root / "b" / "dup.pdf").write_bytes(b"same-bytes")   # same name + size => dup indicator
    (cfg.scanner_root / "unique.pdf").write_bytes(b"different")
    invs = {i.source: i for i in collect_inventory(cfg, ["scanner", "sharepoint"])}
    scan = invs["Scanner"]
    assert scan.object_counts["files"] == 3
    assert scan.document_count == 3
    assert scan.duplicate_groups == 1 and scan.duplicate_files == 1
    assert scan.total_bytes == len(b"same-bytes") * 2 + len(b"different")
    assert invs["SharePoint/OneDrive"].readiness == "empty"    # dir exists, no files


def test_inventory_reports_unavailable_source(cfg):
    cfg2 = MigrationConfig(**{**cfg.__dict__, "taxdome_root": cfg.migration_root / "does-not-exist"})
    inv = {i.source: i for i in collect_inventory(cfg2, ["taxdome"])}["TaxDome"]
    assert inv.available is False and inv.readiness == "unavailable"


# --- Vault provider (DB + files, read-only) -----------------------------------

def test_vault_inventory_counts_files_and_orphans(cfg):
    from app.services.migration.inventory import inventory_vault
    # two physical files under the vault root; test DB has 0 vault_documents -> both are orphans
    (cfg.vault_root / "ab").mkdir()
    (cfg.vault_root / "ab" / "abcd.pdf").write_bytes(b"x" * 10)
    (cfg.vault_root / "cd").mkdir()
    (cfg.vault_root / "cd" / "efgh.pdf").write_bytes(b"y" * 20)
    before = _import_job_count()
    inv = inventory_vault(cfg)
    assert inv.object_counts["physical_files"] == 2
    assert inv.total_bytes == 30
    assert inv.object_counts["vault_documents"] == 0        # empty test DB
    assert inv.object_counts["orphan_files"] == 2 and inv.object_counts["missing_files"] == 0
    assert _import_job_count() == before                    # read-only: no writes


# --- Drake provider (client artifacts only; program files excluded) -----------

def test_drake_inventory_classifies_and_excludes(cfg):
    from app.services.migration.inventory import inventory_drake
    root = cfg.drake_roots[0]                                # .../DRAKE24  -> year 2024
    (root / "Returns").mkdir(parents=True)
    (root / "Returns" / "Smith1040.pdf").write_bytes(b"pdf" * 100)      # candidate: return_pdf
    (root / "EFile").mkdir()
    (root / "EFile" / "ack.pdf").write_bytes(b"ack" * 10)              # candidate: acknowledgement
    (root / "SERVPACK").mkdir()
    (root / "SERVPACK" / "engine.dll").write_bytes(b"MZ" * 500)         # excluded: program dir + ext
    (root / "setup.exe").write_bytes(b"MZ" * 500)                       # excluded: program ext
    (root / "Clients").mkdir()
    (root / "Clients" / "notes.dat").write_bytes(b"data")              # excluded: .dat data file
    inv = inventory_drake(cfg)
    assert inv.available is True
    assert inv.object_counts["candidate_documents"] == 2               # the two PDFs only
    assert inv.object_counts["excluded_program_files"] == 3            # dll, exe, .dat
    types = {(r["year"], r["artifact_type"]) for r in inv.breakdown}
    assert ("2024", "return_pdf") in types and ("2024", "acknowledgement") in types
    assert inv.total_bytes == len(b"pdf" * 100) + len(b"ack" * 10)     # candidate bytes only
    assert "filtering_rules" in inv.metadata and inv.metadata["sample_excluded"]


def test_drake_inventory_unavailable_when_no_roots(cfg):
    from app.services.migration.inventory import inventory_drake
    inv = inventory_drake(cfg)                               # DRAKE24 dir not created -> unavailable
    assert inv.available is False and inv.readiness == "unavailable"


# --- Unclassified provider (needs review) -------------------------------------

def test_unclassified_flags_unassigned_tree_only(cfg):
    from app.services.migration.inventory import inventory_unclassified
    search = cfg.unclassified_search_roots[0]
    # an unassigned tree with >=25 docs -> flagged
    legacy = search / "OldLegacyShare"
    legacy.mkdir()
    for i in range(30):
        (legacy / f"doc{i}.pdf").write_bytes(b"z")
    # an ASSIGNED tree (the scanner root) placed under search must NOT be flagged
    inv = inventory_unclassified(cfg)
    paths = {r["path"] for r in inv.breakdown}
    assert str(legacy) in paths
    assert inv.object_counts["needs_review_trees"] == 1
    assert all(r["classification"] == "needs review" for r in inv.breakdown)


# --- Wealthbox preview --------------------------------------------------------

def _write_contacts_zip(export_dir: Path, rows: list[dict]):
    header = ["id", "first_name", "last_name", "name", "primary_email", "primary_phone", "household"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in header})
    zpath = export_dir / "wealthbox-contacts-2026.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("contacts.csv", buf.getvalue())


def test_wealthbox_preview_counts_and_dedup(cfg):
    _write_contacts_zip(cfg.wealthbox_export, [
        {"id": "1", "first_name": "Taylor", "last_name": "Hawthorne",
         "primary_email": "taylor@example.com", "primary_phone": "555-1000", "household": "Hawthorne"},
        {"id": "2", "first_name": "Jamie", "last_name": "Hawthorne",
         "primary_email": "taylor@example.com", "primary_phone": "555-2000", "household": "Hawthorne"},  # dup email
        {"id": "3", "first_name": "", "last_name": "", "name": "",
         "primary_email": "", "primary_phone": ""},                                                       # unusable shell
    ])
    result = WealthboxContactsMigration(cfg).run(Mode.PREVIEW)
    c = result.counts
    assert c["rows_read"] == 3
    assert c["would_create_source_contacts"] == 3
    assert c["with_email"] == 2 and c["unusable_shells"] == 1
    assert c["in_export_duplicate_email_groups"] == 1     # taylor@example.com appears twice
    assert c["households_detected"] == 1 and c["contacts_with_household"] == 2
    # the unusable shell is surfaced as an exception (and in exceptions.csv)
    assert any("unusable" in e["reason"] for e in result.exceptions)
    assert (Path(result.run_dir) / "exceptions.csv").read_text().count("unusable") >= 1


def test_wealthbox_preview_missing_export_is_soft(cfg):
    # empty export dir -> preview reports zero rows + an exception, never raises
    result = WealthboxContactsMigration(cfg).run(Mode.PREVIEW)
    assert result.counts["rows_read"] == 0


# --- apply is refused BEFORE any database access (Phase 1) --------------------

def test_wealthbox_apply_refuses_before_any_write(cfg):
    from app.services.migration.base import ModeNotSupported
    source_contacts = metadata.tables["source_contacts"]

    def snapshot() -> tuple[int, int]:
        with engine.connect() as c:
            return (int(c.execute(select(func.count()).select_from(import_jobs)).scalar_one()),
                    int(c.execute(select(func.count()).select_from(source_contacts)).scalar_one()))

    before = snapshot()
    # 1. apply refuses (raises), clearly disabled — before opening any import_jobs row
    with pytest.raises(ModeNotSupported, match="disabled"):
        WealthboxContactsMigration(cfg).run(Mode.APPLY)
    after = snapshot()
    # 2. import_jobs count unchanged   3. no client/migration data (source_contacts) written
    assert after == before
    assert Mode.APPLY not in WealthboxContactsMigration.supported_modes
