"""Repository Relocation Service — PREVIEW + RECONCILE (read-only) coverage.

Proves the source-agnostic relocation preview: plans a human-readable destination for every canonical
documents row from canonical fields alone (area by person/household/organization, category, year,
filename carrying the document id); detects needs_relocation / already_in_repository / missing_source;
guarantees no destination collisions via the embedded document id; makes ZERO writes (no storage_uri
change, no import_jobs row); and refuses APPLY/ROLLBACK before any database access. Temp files + temp
rows only.
"""
import dataclasses
import datetime
import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db import documents, engine, households, metadata, people
from app.services.migration.base import Mode, ModeNotSupported
from app.services.migration.config import MigrationConfig
from app.services.migration.naming import RepositoryNaming
from app.services.migration.relocation import RepositoryRelocationJob

import_jobs = metadata.tables["import_jobs"]
relationship_entities = metadata.tables["relationship_entities"]
_TAG = uuid.uuid4().hex[:8]
_CREATED = {"documents": [], "people": [], "households": [], "relationship_entities": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _CREATED["documents"]:
            c.execute(documents.delete().where(documents.c.id.in_(_CREATED["documents"])))
        if _CREATED["relationship_entities"]:
            c.execute(relationship_entities.delete().where(
                relationship_entities.c.id.in_(_CREATED["relationship_entities"])))
        if _CREATED["people"]:
            c.execute(people.delete().where(people.c.id.in_(_CREATED["people"])))
        if _CREATED["households"]:
            c.execute(households.delete().where(households.c.id.in_(_CREATED["households"])))
    for k in _CREATED:
        _CREATED[k].clear()


def _org(name, entity_type="business"):
    with engine.begin() as c:
        oid = c.execute(relationship_entities.insert().values(
            entity_type=entity_type, name=name, active=True).returning(relationship_entities.c.id)).scalar_one()
    _CREATED["relationship_entities"].append(oid)
    return oid


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


def _doc(person_id, original_name, storage_uri, size, sha, *, classification=None, category=None,
         effective_date=None, household_id=None, organization_id=None, tags=None):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, household_id=household_id, organization_id=organization_id,
            original_name=original_name, stored_name=f"reloc-{_TAG}-{uuid.uuid4().hex}",
            storage_path=storage_uri, storage_uri=storage_uri, size_bytes=size, sha256=sha,
            classification=classification, category=category, effective_date=effective_date,
            tags=tags or {}, status="active").returning(documents.c.id)).scalar_one()
    _CREATED["documents"].append(did)
    return did


def _write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data), hashlib.sha256(data).hexdigest()


def _cfg(tmp_path):
    (tmp_path / "out").mkdir(exist_ok=True)
    (tmp_path / "dest").mkdir(exist_ok=True)
    return dataclasses.replace(MigrationConfig.from_env(),
                               migration_root=tmp_path / "out", migration_dest_root=tmp_path / "dest")


def _rows_by_id(result):
    return {r["document_id"]: r for r in result.reconciliation}


def _import_jobs_count():
    with engine.connect() as c:
        return int(c.execute(select(func.count()).select_from(import_jobs)).scalar_one())


# --------------------------------------------------------------------------- naming (pure)

def test_naming_routes_person_household_org_and_embeds_id():
    n = RepositoryNaming()
    people_map = {7: "Smith, John"}; hh_map = {3: "Smith Household"}
    p = n.plan({"id": 40122, "person_id": 7, "original_name": "Form 1040.pdf",
                "classification": "tax", "effective_date": datetime.date(2024, 4, 15)},
               people=people_map, households=hh_map)
    assert (p.area, p.entity, p.category, p.year, p.filename) == \
           ("Clients", "Smith, John", "Tax", "2024", "Form 1040 [40122].pdf")
    # household_id wins over person_id -> Households
    h = n.plan({"id": 5, "person_id": 7, "household_id": 3, "original_name": "joint.pdf", "classification": "estate"},
               people=people_map, households=hh_map)
    assert h.area == "Households" and h.entity == "Smith Household" and h.category == "Estate"
    # organization_id wins over both, with a stable fallback name when no org directory is available
    o = n.plan({"id": 9, "person_id": 7, "organization_id": 55, "original_name": "acct.pdf"})
    assert o.area == "Businesses" and o.entity == "Organization 55"
    # no effective_date -> Undated; unknown classification -> Title-cased
    u = n.plan({"id": 2, "person_id": 7, "original_name": "note.txt", "classification": "misc-thing"})
    assert u.year == "Undated" and u.category == "Misc-Thing"


# --------------------------------------------------------------------------- preview (read-only, DB)

def test_preview_plans_destinations_and_makes_no_writes(tmp_path):
    cfg = _cfg(tmp_path)
    pid = _person(f"Smith {_TAG}", first="John", last=f"Smith{_TAG}")
    hid = _household(f"Smith HH {_TAG}")
    _person(f"Jane {_TAG}", first="Jane", last=f"Smith{_TAG}", household_id=hid)
    src1 = tmp_path / "legacy" / "a.pdf"; sz1, sha1 = _write(src1, b"x" * 120)
    src2 = tmp_path / "legacy" / "b.pdf"; sz2, sha2 = _write(src2, b"y" * 80)
    d1 = _doc(pid, "Form 1040.pdf", str(src1), sz1, sha1, classification="tax",
              effective_date=datetime.date(2024, 1, 2), tags={"source_system": "TaxDome Drive"})
    d2 = _doc(pid, "Trust.pdf", str(src2), sz2, sha2, classification="estate", household_id=hid,
              tags={"source_system": "TaxDome Drive"})

    before_jobs = _import_jobs_count()
    result = RepositoryRelocationJob(cfg).run(Mode.PREVIEW)
    rows = _rows_by_id(result)

    r1 = rows[d1]
    assert r1["state"] == "needs_relocation" and r1["area"] == "Clients"
    assert r1["entity"] == f"Smith{_TAG}, John" and r1["category"] == "Tax" and r1["year"] == "2024"
    assert r1["filename"] == f"Form 1040 [{d1}].pdf"
    assert r1["proposed_destination"].endswith(str(Path("Clients") / f"Smith{_TAG}, John" / "Tax" / "2024" / f"Form 1040 [{d1}].pdf"))
    r2 = rows[d2]
    assert r2["area"] == "Households" and r2["entity"] == f"Smith HH {_TAG}" and r2["category"] == "Estate"

    c = result.counts
    assert c["documents_total"] >= 2 and c["relocatable_bytes"] >= sz1 + sz2
    assert c["by_area"].get("Clients", 0) >= 1 and c["by_area"].get("Households", 0) >= 1

    # READ-ONLY: no import_jobs row opened, storage_uri unchanged for both docs
    assert _import_jobs_count() == before_jobs
    with engine.connect() as conn:
        uris = dict(conn.execute(select(documents.c.id, documents.c.storage_uri)
                                 .where(documents.c.id.in_([d1, d2]))).all())
    assert uris[d1] == str(src1) and uris[d2] == str(src2)


def test_preview_detects_already_in_repository(tmp_path):
    cfg = _cfg(tmp_path)
    pid = _person(f"Repo {_TAG}", first="Al", last=f"Repo{_TAG}")
    inside = cfg.migration_dest_root / "Clients" / f"Repo{_TAG}, Al" / "Tax" / "2023" / "x.pdf"
    sz, sha = _write(inside, b"z" * 64)
    did = _doc(pid, "x.pdf", str(inside), sz, sha, classification="tax",
               effective_date=datetime.date(2023, 6, 1))
    result = RepositoryRelocationJob(cfg).run(Mode.PREVIEW)
    assert _rows_by_id(result)[did]["state"] == "already_in_repository"


def test_preview_flags_missing_source(tmp_path):
    cfg = _cfg(tmp_path)
    pid = _person(f"Gone {_TAG}", first="Bo", last=f"Gone{_TAG}")
    did = _doc(pid, "ghost.pdf", str(tmp_path / "nope" / "ghost.pdf"), 10, "0" * 64)
    result = RepositoryRelocationJob(cfg).run(Mode.PREVIEW)
    assert _rows_by_id(result)[did]["state"] == "missing_source"
    assert any(e["document_id"] == did for e in result.exceptions)


def test_no_destination_collisions_via_document_id(tmp_path):
    cfg = _cfg(tmp_path)
    pid = _person(f"Dup {_TAG}", first="Sam", last=f"Dup{_TAG}")
    # identical name/category/year but distinct ids -> distinct destinations, zero collisions
    s1 = tmp_path / "l" / "one.pdf"; z1, h1 = _write(s1, b"a" * 10)
    s2 = tmp_path / "l" / "two.pdf"; z2, h2 = _write(s2, b"b" * 10)
    d1 = _doc(pid, "Statement.pdf", str(s1), z1, h1, classification="tax", effective_date=datetime.date(2024, 3, 3))
    d2 = _doc(pid, "Statement.pdf", str(s2), z2, h2, classification="tax", effective_date=datetime.date(2024, 3, 3))
    result = RepositoryRelocationJob(cfg).run(Mode.PREVIEW)
    rows = _rows_by_id(result)
    assert rows[d1]["proposed_destination"] != rows[d2]["proposed_destination"]
    assert result.counts["destination_collisions"] == 0


def test_preview_is_source_agnostic(tmp_path):
    cfg = _cfg(tmp_path)
    pid = _person(f"Multi {_TAG}", first="Kim", last=f"Multi{_TAG}")
    made = []
    for i, system in enumerate(["TaxDome Drive", "SharePoint", "Wealthbox", "Client360 Local"]):
        s = tmp_path / "src" / f"{i}.pdf"; z, h = _write(s, bytes([65 + i]) * (10 + i))
        made.append(_doc(pid, f"f{i}.pdf", str(s), z, h, tags={"source_system": system}))
    result = RepositoryRelocationJob(cfg).run(Mode.PREVIEW)
    rows = _rows_by_id(result)
    assert all(m in rows for m in made)                       # every origin handled by the same pipeline
    assert {rows[m]["source_system"] for m in made} == {"TaxDome Drive", "SharePoint", "Wealthbox", "Client360 Local"}


# --------------------------------------------------------------------------- reconcile (read-only)

def test_reconcile_reports_baseline(tmp_path):
    cfg = _cfg(tmp_path)
    pid = _person(f"Rec {_TAG}", first="Ed", last=f"Rec{_TAG}")
    s = tmp_path / "leg" / "r.pdf"; z, h = _write(s, b"q" * 200)
    did = _doc(pid, "r.pdf", str(s), z, h)
    result = RepositoryRelocationJob(cfg).run(Mode.RECONCILE)
    rows = _rows_by_id(result)
    assert rows[did]["in_repository"] is False and rows[did]["size_actual"] == z
    assert result.counts["outside_repository"] >= 1 and result.counts["size_verified"] >= 1


# --------------------------------------------------------------------------- fail-closed apply/rollback

def test_apply_needs_guards_and_rollback_unsupported(tmp_path):
    from app.services.migration.relocation import RepairGuardError
    cfg = _cfg(tmp_path)
    pid = _person(f"NoApply {_TAG}", first="Vi", last=f"NoApply{_TAG}")
    s = tmp_path / "leg" / "n.pdf"; z, h = _write(s, b"n" * 15)
    did = _doc(pid, "n.pdf", str(s), z, h)
    with pytest.raises(ModeNotSupported):                             # ROLLBACK still unbuilt
        RepositoryRelocationJob(cfg).run(Mode.ROLLBACK)
    with pytest.raises(RepairGuardError):                            # APPLY refused without confirm/backup/approved
        RepositoryRelocationJob(cfg).run(Mode.APPLY, confirm=False)
    # no bytes moved / storage_uri unchanged
    with engine.connect() as conn:
        assert conn.execute(select(documents.c.storage_uri).where(documents.c.id == did)).scalar_one() == str(s)


def test_business_document_uses_relationship_entity_name(tmp_path):
    """A document linked via organization_id must route to Businesses/<real relationship_entities name>,
    not a generic 'Organization <id>' label."""
    cfg = _cfg(tmp_path)
    pid = _person(f"Owner {_TAG}", first="Amy", last=f"Owner{_TAG}")
    org = _org(f"Star City Heating {_TAG}", entity_type="business")
    src = tmp_path / "legacy" / "1120s.pdf"; sz, sha = _write(src, b"z" * 64)
    did = _doc(pid, "1120S Return.pdf", str(src), sz, sha, classification="tax",
               effective_date=datetime.date(2024, 3, 1), organization_id=org)   # org link wins over person
    result = RepositoryRelocationJob(cfg).run(Mode.PREVIEW)
    row = _rows_by_id(result)[did]
    assert row["area"] == "Businesses"
    assert row["entity"] == f"Star City Heating {_TAG}"                # real canonical name, not "Organization <id>"
    assert "Organization" not in row["entity"]
    assert row["proposed_destination"].endswith(str(
        Path("Businesses") / f"Star City Heating {_TAG}" / "Tax" / "2024" / f"1120S Return [{did}].pdf"))


# --------------------------------------------------------------------------- guarded APPLY

def test_relocation_apply_copies_verifies_repoints_retains_source(tmp_path):
    from app.services.migration.relocation import load_approved_relocation
    cfg = _cfg(tmp_path)
    p = _person(f"Smith {_TAG}", first="John", last=f"Smith{_TAG}")
    h = _household(f"Jones HH {_TAG}")
    b = _org(f"Acme LLC {_TAG}", entity_type="business")
    s_p = tmp_path / "legacy" / "p.pdf"; zp, hp = _write(s_p, b"P" * 100)
    s_h = tmp_path / "legacy" / "h.pdf"; zh, hh_ = _write(s_h, b"H" * 80)
    s_b = tmp_path / "legacy" / "b.pdf"; zb, hb = _write(s_b, b"B" * 60)
    s_f = tmp_path / "legacy" / "f.pdf"; zf, hf = _write(s_f, b"F" * 40)
    dp = _doc(p, "Return.pdf", str(s_p), zp, hp, classification="tax", effective_date=datetime.date(2024, 1, 1))
    dh = _doc(None, "Joint.pdf", str(s_h), zh, hh_, classification="estate", household_id=h,
              effective_date=datetime.date(2023, 1, 1))
    db = _doc(None, "1120S.pdf", str(s_b), zb, hb, classification="tax", organization_id=b,
              effective_date=datetime.date(2024, 1, 1))
    df = _doc(None, "Unfiled.pdf", str(s_f), zf, hf)                   # Firm -> excluded
    backup = tmp_path / "bk.dump"; backup.write_text("PGDMP")
    job = RepositoryRelocationJob(cfg)

    approved = load_approved_relocation(job.run(Mode.PREVIEW).run_dir)
    assert set(approved) == {dp, dh, db}                              # Firm document excluded from scope
    expect = {"Clients": 1, "Households": 1, "Businesses": 1}
    r = job.run(Mode.APPLY, approved=approved, confirm=True, backup=str(backup), expect=expect)
    assert r.counts["rows_inserted"] == 3 and r.counts["total"] == 3

    import hashlib
    with engine.connect() as c:
        for did, srcpath, sha in [(dp, s_p, hp), (dh, s_h, hh_), (db, s_b, hb)]:
            newuri = c.execute(select(documents.c.storage_uri).where(documents.c.id == did)).scalar_one()
            assert newuri.startswith(str(cfg.migration_dest_root))    # repointed into Content
            assert Path(newuri).exists()                              # destination written
            assert hashlib.sha256(Path(newuri).read_bytes()).hexdigest() == sha   # verified copy
            assert srcpath.exists()                                   # SOURCE RETAINED (never deleted)
        assert c.execute(select(documents.c.storage_uri).where(documents.c.id == df)).scalar_one() == str(s_f)

    # idempotent re-apply: nothing moved
    r2 = job.run(Mode.APPLY, approved=approved, confirm=True, backup=str(backup), expect=expect)
    assert r2.counts["rows_inserted"] == 0 and r2.counts["skipped_already_relocated"] == 3


def test_relocation_apply_guards_fail_closed(tmp_path):
    from app.services.migration.relocation import RepairGuardError, load_approved_relocation
    cfg = _cfg(tmp_path)
    p = _person(f"G {_TAG}", first="Al", last=f"G{_TAG}")
    s = tmp_path / "legacy" / "g.pdf"; z, sha = _write(s, b"g" * 20)
    _doc(p, "g.pdf", str(s), z, sha, classification="tax", effective_date=datetime.date(2024, 1, 1))
    backup = tmp_path / "bk.dump"; backup.write_text("PGDMP")
    job = RepositoryRelocationJob(cfg)
    approved = load_approved_relocation(job.run(Mode.PREVIEW).run_dir)
    ok = {"Clients": 1, "Households": 0, "Businesses": 0}

    with pytest.raises(RepairGuardError):                            # no confirm
        job.run(Mode.APPLY, approved=approved, confirm=False, backup=None, expect=ok)
    with pytest.raises(RepairGuardError):                            # empty backup
        (tmp_path / "e.dump").write_text("")
        job.run(Mode.APPLY, approved=approved, confirm=True, backup=str(tmp_path / "e.dump"), expect=ok)
    with pytest.raises(RepairGuardError):                            # count drift
        job.run(Mode.APPLY, approved=approved, confirm=True, backup=str(backup),
                expect={"Clients": 999, "Households": 0, "Businesses": 0})
    with pytest.raises(RepairGuardError):                            # missing source -> fail closed, no writes
        s.unlink()
        job.run(Mode.APPLY, approved=approved, confirm=True, backup=str(backup), expect=ok)
