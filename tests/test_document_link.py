"""Coverage for the guarded document canonical-link APPLY (deterministic set only)."""
import uuid

import pytest
from sqlalchemy import func, select

from app.db import documents, engine, households, metadata, people
from app.services.migration.base import Mode
from app.services.migration.config import MigrationConfig
from app.services.migration.document_link import (
    DocumentLinkJob,
    RepairGuardError,
    load_approved_doc_links,
)

relationship_entities = metadata.tables["relationship_entities"]
_TAG = uuid.uuid4().hex[:8]
_C = {"documents": [], "people": [], "households": [], "relationship_entities": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        for tbl, key in ((documents, "documents"), (relationship_entities, "relationship_entities"),
                         (people, "people"), (households, "households")):
            if _C[key]:
                c.execute(tbl.delete().where(tbl.c.id.in_(_C[key])))
    for k in _C:
        _C[k].clear()


def _person(name):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=name, active=True).returning(people.c.id)).scalar_one()
    _C["people"].append(pid); return pid


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _C["households"].append(hid); return hid


def _org(name):
    with engine.begin() as c:
        oid = c.execute(relationship_entities.insert().values(entity_type="business", name=name,
                        active=True).returning(relationship_entities.c.id)).scalar_one()
    _C["relationship_entities"].append(oid); return oid


def _doc(name):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=name, stored_name=f"dl-{_TAG}-{uuid.uuid4().hex}", storage_path="x",
            size_bytes=1, sha256="0" * 64, status="active").returning(documents.c.id)).scalar_one()
    _C["documents"].append(did); return did


def _write_manifest(tmp_path, rows):
    lines = ["document_id,resolution,proposed_entity_type,proposed_entity_id,source_folder,original_name"]
    for did, res, etype, eid in rows:
        lines.append(f"{did},{res},{etype},{eid},F,doc.pdf")
    (tmp_path / "reconciliation.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed(tmp_path):
    p1 = _person(f"P1 {_TAG}"); h1 = _household(f"H1 {_TAG}"); b1 = _org(f"B1 {_TAG}")
    dp1, dp2 = _doc(f"p1 {_TAG}"), _doc(f"p2 {_TAG}")
    dh1, dh2 = _doc(f"h1 {_TAG}"), _doc(f"h2 {_TAG}")
    db1 = _doc(f"b1 {_TAG}")
    d_amb, d_unm = _doc(f"amb {_TAG}"), _doc(f"unm {_TAG}")
    _write_manifest(tmp_path, [
        (dp1, "people", "person", p1), (dp2, "people", "person", p1),
        (dh1, "households", "household", h1), (dh2, "households", "household", h1),
        (db1, "businesses", "organization", b1),
        (d_amb, "ambiguous", "", ""), (d_unm, "unmatched", "", ""),   # must be ignored
    ])
    return {"p1": p1, "h1": h1, "b1": b1, "dp1": dp1, "dp2": dp2, "dh1": dh1, "dh2": dh2,
            "db1": db1, "d_amb": d_amb, "d_unm": d_unm}


EXPECT = {"people": 2, "households": 2, "businesses": 1}


def test_load_manifest_filters_deterministic(tmp_path):
    s = _seed(tmp_path)
    approved = load_approved_doc_links(str(tmp_path))
    assert set(approved) == {s["dp1"], s["dp2"], s["dh1"], s["dh2"], s["db1"]}   # ambiguous/unmatched excluded
    assert approved[s["dp1"]] == ("people", s["p1"]) and approved[s["db1"]] == ("businesses", s["b1"])


def test_preview_is_readonly_and_counts(tmp_path):
    _seed(tmp_path)
    approved = load_approved_doc_links(str(tmp_path))
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(documents).where(
            documents.c.person_id.isnot(None))).scalar_one()
    r = DocumentLinkJob(MigrationConfig.from_env()).run(Mode.PREVIEW, approved=approved)
    c = r.counts
    assert (c["people"], c["households"], c["businesses"], c["total"]) == (2, 2, 1, 5)
    assert c["pending"] == {"people": 2, "households": 2, "businesses": 1} and c["drift"] == 0
    with engine.connect() as conn:
        after = conn.execute(select(func.count()).select_from(documents).where(
            documents.c.person_id.isnot(None))).scalar_one()
    assert after == before                                            # read-only


def test_apply_guards_fail_closed(tmp_path):
    s = _seed(tmp_path)
    approved = load_approved_doc_links(str(tmp_path))
    job = DocumentLinkJob(MigrationConfig.from_env())
    with pytest.raises(RepairGuardError):
        job.run(Mode.APPLY, approved=approved, confirm=False, backup=None, expect=EXPECT)
    good = tmp_path / "b.dump"; good.write_text("PGDMP")
    with pytest.raises(RepairGuardError):                            # count drift
        job.run(Mode.APPLY, approved=approved, confirm=True, backup=str(good),
                expect={"people": 999, "households": 2, "businesses": 1})
    # conflicting existing link (already set to a DIFFERENT valid person) -> DRIFT -> abort
    other = _person(f"Other {_TAG}")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == s["dp1"]).values(person_id=other))
    with pytest.raises(RepairGuardError):
        job.run(Mode.APPLY, approved=approved, confirm=True, backup=str(good), expect=EXPECT)


def test_apply_sets_links_idempotently_and_leaves_others(tmp_path):
    s = _seed(tmp_path)
    approved = load_approved_doc_links(str(tmp_path))
    good = tmp_path / "b.dump"; good.write_text("PGDMP")
    job = DocumentLinkJob(MigrationConfig.from_env())

    r1 = job.run(Mode.APPLY, approved=approved, confirm=True, backup=str(good), expect=EXPECT)
    assert r1.counts["rows_inserted"] == 5
    with engine.connect() as c:
        row = dict(c.execute(select(documents.c.person_id, documents.c.household_id,
                   documents.c.organization_id).where(documents.c.id == s["dp1"])).mappings().one())
        assert row["person_id"] == s["p1"]
        assert c.execute(select(documents.c.household_id).where(documents.c.id == s["dh1"])).scalar_one() == s["h1"]
        assert c.execute(select(documents.c.organization_id).where(documents.c.id == s["db1"])).scalar_one() == s["b1"]
        # ambiguous/unmatched documents untouched
        for did in (s["d_amb"], s["d_unm"]):
            arow = dict(c.execute(select(documents.c.person_id, documents.c.household_id,
                        documents.c.organization_id).where(documents.c.id == did)).mappings().one())
            assert arow == {"person_id": None, "household_id": None, "organization_id": None}

    # idempotent re-apply: pending 0, nothing written
    r2 = job.run(Mode.APPLY, approved=approved, confirm=True, backup=str(good), expect=EXPECT)
    assert r2.counts["rows_inserted"] == 0
    prev = DocumentLinkJob(MigrationConfig.from_env()).run(Mode.PREVIEW, approved=approved)
    assert prev.counts["pending"] == {"people": 0, "households": 0, "businesses": 0}
    assert (prev.counts["people"], prev.counts["households"], prev.counts["businesses"]) == (2, 2, 1)
