"""Coverage for the READ-ONLY relocation drift diagnostic."""
import dataclasses
import datetime
import hashlib
import uuid

import pytest
from sqlalchemy import update

from app.db import documents, engine, people
from app.services.migration.base import Mode
from app.services.migration.config import MigrationConfig
from app.services.migration.relocation import RepositoryRelocationJob
from scripts.migration.diagnose_relocation_drift import diagnose

_TAG = uuid.uuid4().hex[:8]
_C = {"documents": [], "people": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _C["documents"]:
            c.execute(documents.delete().where(documents.c.id.in_(_C["documents"])))
        if _C["people"]:
            c.execute(people.delete().where(people.c.id.in_(_C["people"])))
    for k in _C:
        _C[k].clear()


def _person(first, last):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=f"{first} {last}", first_name=first,
                        last_name=last, active=True).returning(people.c.id)).scalar_one()
    _C["people"].append(pid); return pid


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data), hashlib.sha256(data).hexdigest()


def _doc(person_id, name, uri, size, sha):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=person_id, original_name=name, stored_name=f"drift-{_TAG}-{uuid.uuid4().hex}",
            storage_path=uri, storage_uri=uri, size_bytes=size, sha256=sha, classification="tax",
            effective_date=datetime.date(2024, 1, 1), status="active").returning(documents.c.id)).scalar_one()
    _C["documents"].append(did); return did


def _cfg(tmp_path):
    (tmp_path / "out").mkdir(exist_ok=True); (tmp_path / "dest").mkdir(exist_ok=True)
    return dataclasses.replace(MigrationConfig.from_env(),
                               migration_root=tmp_path / "out", migration_dest_root=tmp_path / "dest")


def test_diagnose_classifies_each_drift_kind(tmp_path):
    cfg = _cfg(tmp_path)
    p1 = _person("John", f"Renamed{_TAG}")
    p2 = _person("Amy", f"Owner{_TAG}")
    p3 = _person("Zoe", f"Moved{_TAG}")
    p4 = _person("Sue", f"Stable{_TAG}")
    files = {}
    for key, pid in (("dest", p1), ("owner", p2), ("src", p3), ("ok", p4)):
        s = tmp_path / "legacy" / f"{key}.pdf"; z, h = _write(s, key.encode() * 40)
        files[key] = (_doc(pid, f"{key}.pdf", str(s), z, h), s)
    d_dest, d_owner, d_src, d_ok = (files[k][0] for k in ("dest", "owner", "src", "ok"))

    job = RepositoryRelocationJob(cfg)
    frozen = job.run(Mode.PREVIEW).run_dir                            # freeze all four as Clients/needs_relocation

    # introduce drift AFTER freezing
    with engine.begin() as c:
        c.execute(update(people).where(people.c.id == p1).values(last_name=f"Newname{_TAG}"))  # dest changes
        c.execute(update(documents).where(documents.c.id == d_owner).values(person_id=None))    # owner removed -> Firm
        c.execute(update(documents).where(documents.c.id == d_src).values(
            storage_uri=str(tmp_path / "elsewhere" / "moved.pdf")))                             # source path changed

    res = diagnose(frozen, cfg=cfg)
    by = {r["document_id"]: r for r in res["drifted"]}

    assert by[d_dest]["drift_reason"] == "destination_drift"
    assert by[d_dest]["current_destination"] != by[d_dest]["frozen_destination"]
    assert by[d_owner]["drift_reason"] == "owner_removed" and by[d_owner]["person_id"] is None
    assert by[d_src]["drift_reason"] == "source_drift"
    assert by[d_src]["current_storage_uri"] != by[d_src]["frozen_source"]
    assert d_ok not in by                                             # the unchanged document is not drifted
    assert res["drift_count"] >= 3


def test_diagnose_reports_missing_document(tmp_path):
    cfg = _cfg(tmp_path)
    p = _person("Del", f"Eted{_TAG}")
    s = tmp_path / "legacy" / "d.pdf"; z, h = _write(s, b"d" * 20)
    did = _doc(p, "d.pdf", str(s), z, h)
    job = RepositoryRelocationJob(cfg)
    frozen = job.run(Mode.PREVIEW).run_dir
    with engine.begin() as c:
        c.execute(documents.delete().where(documents.c.id == did))
    _C["documents"].clear()                                           # already deleted
    res = diagnose(frozen, cfg=cfg)
    by = {r["document_id"]: r for r in res["drifted"]}
    assert by[did]["drift_reason"] == "missing_document" and by[did]["current_source_exists"] is False
