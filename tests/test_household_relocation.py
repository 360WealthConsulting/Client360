"""Coverage for Stage C household document relocation (preview + guarded delegation).

Scopes to Stage-B household-owned documents (person_id NULL) and reuses the production relocation engine
for the guarded copy -> verify -> repoint. Robinson document 800 is the worked positive example. Proves
scoping, classification (needs/already/missing/collision), fail-closed guards, and that APPLY copies +
verifies + repoints storage_uri while retaining the source and preserving document_sources.
"""
import dataclasses
import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import documents, engine, households, metadata, people, relationship_entities
from app.services.migration.canonical_repair import RepairGuardError
from app.services.migration.config import MigrationConfig
from app.services.migration.household_relocation import apply, preview

_document_sources = metadata.tables["document_sources"]
_TAG = uuid.uuid4().hex[:8]
_C = {"documents": [], "people": [], "households": [], "relationship_entities": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _C["documents"]:
            c.execute(_document_sources.delete().where(_document_sources.c.document_id.in_(_C["documents"])))
            c.execute(documents.delete().where(documents.c.id.in_(_C["documents"])))
        for tbl, key in ((relationship_entities, "relationship_entities"), (people, "people"),
                         (households, "households")):
            if _C[key]:
                c.execute(tbl.delete().where(tbl.c.id.in_(_C[key])))
    for k in _C:
        _C[k].clear()


def _cfg(tmp_path):
    (tmp_path / "dest").mkdir(exist_ok=True)
    return dataclasses.replace(MigrationConfig.from_env(),
                               migration_root=tmp_path / "out", migration_dest_root=tmp_path / "dest")


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data), hashlib.sha256(data).hexdigest()


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _C["households"].append(hid)
    return hid


def _person(full):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full, first_name="P", last_name=_TAG,
                                               active=True).returning(people.c.id)).scalar_one()
    _C["people"].append(pid)
    return pid


def _entity(name):
    with engine.begin() as c:
        eid = c.execute(relationship_entities.insert().values(entity_type="business", name=name,
                                                              active=True).returning(relationship_entities.c.id)
                        ).scalar_one()
    _C["relationship_entities"].append(eid)
    return eid


def _doc(name, storage_uri, size, sha, *, person_id=None, household_id=None, organization_id=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            original_name=name, stored_name=f"hr-{_TAG}-{uuid.uuid4().hex}", storage_path=storage_uri,
            storage_uri=storage_uri, size_bytes=size, sha256=sha, status="active", category="tax",
            tags={"source_system": "Drake", "tax_year": "2025"}).returning(documents.c.id)).scalar_one()
    _C["documents"].append(did)
    return did


def _doc_row(did):
    with engine.connect() as c:
        return c.execute(select(documents.c.person_id, documents.c.household_id, documents.c.storage_uri,
                                documents.c.storage_provider).where(documents.c.id == did)).mappings().one()


# --- preview scoping + classification ----------------------------------------

def test_preview_scopes_to_household_owned_only(tmp_path):
    cfg = _cfg(tmp_path)
    hid = _household(f"Robinson Household {_TAG}")
    pid = _person(f"Solo Person {_TAG}")
    eid = _entity(f"Acme {_TAG}")
    sh = tmp_path / "legacy" / "hh.pdf"; zh, hh_sha = _write(sh, b"H" * 50)
    sp = tmp_path / "legacy" / "pp.pdf"; zp, pp_sha = _write(sp, b"P" * 40)
    sb = tmp_path / "legacy" / "bb.pdf"; zb, bb_sha = _write(sb, b"B" * 30)
    d_hh = _doc("hh return.pdf", str(sh), zh, hh_sha, household_id=hid)     # in scope
    d_pp = _doc("pp return.pdf", str(sp), zp, pp_sha, person_id=pid)        # excluded (person-owned)
    d_bb = _doc("bb return.pdf", str(sb), zb, bb_sha, organization_id=eid)  # excluded (org-owned)

    res = preview(config=cfg)
    ids = {r["document_id"] for r in res["rows"]}
    assert d_hh in ids and d_pp not in ids and d_bb not in ids
    assert d_hh in res["manifest"] and d_pp not in res["manifest"] and d_bb not in res["manifest"]


def test_robinson_800_under_content_wrong_path_is_needs_relocation(tmp_path):
    # Reproduces the production bug: the file already lives UNDER Content, but at the OLD Clients\<person>
    # path — that is the WRONG destination for a household-owned doc and MUST be needs_relocation.
    cfg = _cfg(tmp_path)
    hid = _household(f"Robinson Household {_TAG}")
    src = cfg.migration_dest_root / "Clients" / "Robinson, Alicia" / "Tax" / "2025" / "old.pdf"
    size, sha = _write(src, b"R" * 128)
    did = _doc("2025 Tax Return Documents (ROBINSON, SAMUEL M & ALICIA L).pdf", str(src), size, sha,
               household_id=hid)
    res = preview(config=cfg)
    row = next(r for r in res["rows"] if r["document_id"] == did)
    assert row["state"] == "needs_relocation" and row["area"] == "Households"
    assert "Clients" in row["current_storage_uri"]                 # current: old Clients path (under Content)
    assert "Households" in row["proposed_destination"]             # proposed: Households destination
    assert row["size_bytes"] == size and did in res["manifest"]


def test_preview_already_at_exact_destination_and_missing(tmp_path):
    cfg = _cfg(tmp_path)
    hid = _household(f"HH {_TAG}")
    # insert with a temp source, learn the projected destination for its id, then place the file EXACTLY
    # there — only an exact-destination match may count as already_in_repository.
    src0 = tmp_path / "legacy" / "x.pdf"; z0, h0 = _write(src0, b"I" * 20)
    d_in = _doc("x.pdf", str(src0), z0, h0, household_id=hid)
    dest = next(r for r in preview(config=cfg)["rows"] if r["document_id"] == d_in)["proposed_destination"]
    _write(Path(dest), b"I" * 20)
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == d_in).values(storage_uri=dest, storage_path=dest))
    d_missing = _doc("ghost.pdf", str(tmp_path / "nope" / "ghost.pdf"), 10, "0" * 64, household_id=hid)

    res = preview(config=cfg)
    states = {r["document_id"]: r["state"] for r in res["rows"]}
    assert states[d_in] == "already_in_repository" and d_in not in res["manifest"]
    assert states[d_missing] == "missing_source" and d_missing not in res["manifest"]


# --- guards ------------------------------------------------------------------

def test_apply_requires_confirm_and_backup(tmp_path):
    cfg = _cfg(tmp_path)
    hid = _household(f"HH {_TAG}")
    src = tmp_path / "legacy" / "g.pdf"; z, h = _write(src, b"G" * 25)
    _doc("g.pdf", str(src), z, h, household_id=hid)
    backup = tmp_path / "b.dump"; backup.write_text("x")
    with pytest.raises(RepairGuardError):
        apply(confirm=False, backup=str(backup), config=cfg)
    with pytest.raises(RepairGuardError):
        apply(confirm=True, backup=str(tmp_path / "nope.dump"), config=cfg)


def test_apply_fails_closed_on_count_drift(tmp_path):
    cfg = _cfg(tmp_path)
    hid = _household(f"HH {_TAG}")
    src = tmp_path / "legacy" / "c.pdf"; z, h = _write(src, b"C" * 25)
    _doc("c.pdf", str(src), z, h, household_id=hid)
    backup = tmp_path / "b.dump"; backup.write_text("x")
    with pytest.raises(RepairGuardError):
        apply(confirm=True, backup=str(backup), expect=999, config=cfg)


# --- apply: copy -> verify -> repoint, source retained, document_sources preserved -------------

def test_apply_relocates_repoints_retains_source_and_preserves_document_sources(tmp_path):
    cfg = _cfg(tmp_path)
    hid = _household(f"Robinson Household {_TAG}")
    pid = _person(f"Untouched Person {_TAG}")
    src = tmp_path / "Clients" / "Robinson, Alicia" / "2025.pdf"
    size, sha = _write(src, b"R" * 200)
    d800 = _doc("2025 Tax Return Documents (ROBINSON, SAMUEL M & ALICIA L).pdf", str(src), size, sha,
                household_id=hid)
    sp = tmp_path / "legacy" / "biz.pdf"; zp, hp = _write(sp, b"L" * 60)
    d799 = _doc("2025 Tax Return Documents (INTEGRITY COATINGS LLC).pdf", str(sp), zp, hp, person_id=pid)
    with engine.begin() as c:
        c.execute(_document_sources.insert().values(document_id=d800, source_system="Drake",
                                                    source_uri=str(src), source_hash=sha))
    ds_before = _count(_document_sources)

    live = len(preview(config=cfg)["manifest"])
    backup = tmp_path / "b.dump"; backup.write_text("x")
    res = apply(confirm=True, backup=str(backup), expect=live, config=cfg)
    assert res["counts"]["rows_inserted"] >= 1

    row = _doc_row(d800)
    assert row["household_id"] == hid and row["person_id"] is None            # ownership unchanged (still household)
    assert row["storage_uri"] != str(src) and "Households" in row["storage_uri"]   # repointed to dest
    from pathlib import Path
    assert Path(row["storage_uri"]).read_bytes() == b"R" * 200                # copy verified at destination
    assert src.exists()                                                        # source RETAINED (not moved)
    assert _doc_row(d799)["storage_uri"] == str(sp)                            # person-owned doc untouched
    assert _count(_document_sources) == ds_before                             # document_sources preserved

    # idempotent: re-run relocates nothing more; storage_uri stays at destination
    dest_uri = row["storage_uri"]
    res2 = apply(confirm=True, backup=str(backup), expect=len(preview(config=cfg)["manifest"]), config=cfg)
    assert res2["counts"]["rows_inserted"] == 0
    assert _doc_row(d800)["storage_uri"] == dest_uri


def _count(tbl):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(tbl)).scalar_one()
