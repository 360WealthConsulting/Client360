"""TaxDome Document Migration — PREVIEW (read-only) coverage.

Proves the preview matches top-level client folders to canonical people/households (matched / ambiguous
/ unmatched), counts files/folders/bytes/duplicates/estimated document rows, writes the three named
artifacts, and makes ZERO database writes and ZERO file changes. Also proves APPLY is refused before any
database access. Temp source tree + temp people only; people cleaned up.
"""
import uuid

import pytest
from sqlalchemy import func, select

from app.db import engine, households, metadata, people
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


def _person(full_name, household_id=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            full_name=full_name, active=True, household_id=household_id).returning(people.c.id)).scalar_one()
    _CREATED["people"].append(pid)
    return pid


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _CREATED["households"].append(hid)
    return hid


@pytest.fixture
def source(tmp_path):
    """A temp TaxDome tree with a matched, ambiguous, unmatched, and joint-household folder."""
    root = tmp_path / "TaxDome"
    def mk(folder, files):
        d = root / folder
        d.mkdir(parents=True)
        for name, data in files:
            (d / name).write_bytes(data)
    # matched (unique person)
    mk(f"Alpha {_TAG}", [("2023 return.pdf", b"a" * 100), ("desktop.ini", b"x")])   # desktop.ini ignored
    # ambiguous (two people share the name)
    mk(f"Bravo {_TAG}", [("doc.pdf", b"b" * 50)])
    # unmatched (no person)
    mk(f"Nomatch {_TAG}", [("scan.pdf", b"c" * 30)])
    # joint -> shared household
    mk(f"Carol {_TAG} and Dave {_TAG}", [("joint.pdf", b"d" * 40)])
    return root


@pytest.fixture
def cfg(tmp_path, source):
    (tmp_path / "out").mkdir()
    base = MigrationConfig.from_env()
    import dataclasses
    return dataclasses.replace(base, migration_root=tmp_path / "out", taxdome_migration_root=source)


def _seed_people():
    _person(f"Alpha {_TAG}")                                  # unique -> matched person
    _person(f"Bravo {_TAG}"); _person(f"Bravo {_TAG}")        # duplicate -> ambiguous
    hid = _household(f"Carol-Dave {_TAG}")
    _person(f"Carol {_TAG}", household_id=hid)
    _person(f"Dave {_TAG}", household_id=hid)                 # joint -> matched household


def _counts():
    with engine.connect() as c:
        return (int(c.execute(select(func.count()).select_from(import_jobs)).scalar_one()),
                int(c.execute(select(func.count()).select_from(documents)).scalar_one()))


def test_preview_classifies_matched_ambiguous_unmatched(cfg):
    _seed_people()
    before = _counts()
    result = TaxDomeDocumentMigration(cfg).run(Mode.PREVIEW)
    c = result.counts
    assert c["top_level_folders"] == 4
    assert c["matched_folders"] == 2       # Alpha (person) + Carol&Dave (household)
    assert c["ambiguous_folders"] == 1     # Bravo
    assert c["unmatched_folders"] == 1     # Nomatch
    # per-folder statuses
    by = {r["top_level_folder"]: r for r in result.reconciliation}
    assert by[f"Alpha {_TAG}"]["match_status"] == "matched" and by[f"Alpha {_TAG}"]["person_id"]
    assert by[f"Carol {_TAG} and Dave {_TAG}"]["match_status"] == "matched"
    assert by[f"Carol {_TAG} and Dave {_TAG}"]["household_id"]
    assert by[f"Bravo {_TAG}"]["match_status"] == "ambiguous"
    assert by[f"Nomatch {_TAG}"]["match_status"] == "unmatched"
    # counts: ignored desktop.ini excluded from estimated document rows
    assert c["ignored_files"] >= 1
    assert c["estimated_document_rows_total"] == 4         # 4 real docs (desktop.ini excluded)
    assert c["total_files"] == 5                           # 4 docs + desktop.ini
    assert c["total_bytes"] == 100 + 50 + 30 + 40
    # ZERO database writes
    assert _counts() == before


def test_preview_writes_the_three_named_artifacts(cfg):
    _seed_people()
    from pathlib import Path
    result = TaxDomeDocumentMigration(cfg).run(Mode.PREVIEW)
    d = Path(result.run_dir)
    for name in ("migration_preview.csv", "migration_summary.txt", "migration_manifest.json"):
        assert (d / name).exists(), name
    preview = (d / "migration_preview.csv").read_text()
    assert "match_status" in preview and f"Alpha {_TAG}" in preview


def test_preview_does_not_modify_source_files(cfg, source):
    _seed_people()
    before = sorted(p.name for p in source.rglob("*"))
    TaxDomeDocumentMigration(cfg).run(Mode.PREVIEW)
    after = sorted(p.name for p in source.rglob("*"))
    assert before == after                                # no copy/move/delete/rename


def test_apply_is_refused_before_any_write(cfg):
    before = _counts()
    with pytest.raises(ModeNotSupported, match="disabled"):
        TaxDomeDocumentMigration(cfg).run(Mode.APPLY)
    assert _counts() == before
    assert Mode.APPLY not in TaxDomeDocumentMigration.supported_modes
