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
    for name in ("wealthbox", "taxdome", "sharepoint", "scanner", "docs"):
        (tmp_path / name).mkdir()
    return MigrationConfig(
        migration_root=tmp_path / "migration",
        wealthbox_export=tmp_path / "wealthbox",
        taxdome_root=tmp_path / "taxdome",
        sharepoint_root=tmp_path / "sharepoint",
        scanner_root=tmp_path / "scanner",
        document_root=tmp_path / "docs",
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


# --- apply is guarded off in Phase 1 ------------------------------------------

def test_wealthbox_apply_is_disabled(cfg):
    with pytest.raises(NotImplementedError, match="PREVIEW-ONLY"):
        WealthboxContactsMigration(cfg).run(Mode.APPLY)
