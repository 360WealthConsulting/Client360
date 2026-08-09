"""Coverage for the READ-ONLY relocation by_area vs approved-set reconciliation."""
import hashlib
import uuid

import pytest

from app.db import documents, engine, people
from scripts.migration.diagnose_area_state import analyze, area_state_breakdown

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


def test_area_state_breakdown_pure():
    rows = [
        {"document_id": "1", "area": "Clients", "state": "needs_relocation"},
        {"document_id": "2", "area": "Clients", "state": "missing_source"},
        {"document_id": "3", "area": "Clients", "state": "already_in_repository"},
        {"document_id": "4", "area": "Households", "state": "needs_relocation"},
        {"document_id": "5", "area": "Businesses", "state": "needs_relocation"},
        {"document_id": "6", "area": "Firm", "state": "missing_source"},          # not owned -> not excluded-owned
    ]
    area_state, excluded = area_state_breakdown(rows)
    assert area_state["Clients"] == {"needs_relocation": 1, "missing_source": 1, "already_in_repository": 1}
    assert {r["document_id"] for r in excluded} == {"2", "3"}                     # owned, non-needs_relocation
    # reconciliation: Clients by_area (3) = approved needs_relocation (1) + excluded (2)
    total = sum(area_state["Clients"].values())
    approved = area_state["Clients"]["needs_relocation"]
    assert total == 3 and approved == 1 and (total - approved) == 2


def test_analyze_enriches_excluded_with_name_and_source_existence(tmp_path):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=f"P {_TAG}", active=True).returning(people.c.id)).scalar_one()
    _C["people"].append(pid)
    src = tmp_path / "legacy" / "keep.pdf"
    src.parent.mkdir(parents=True, exist_ok=True); src.write_bytes(b"x" * 30)
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=pid, original_name="Kept Return.pdf", stored_name=f"as-{_TAG}",
            storage_path=str(src), storage_uri=str(src), size_bytes=30,
            sha256=hashlib.sha256(b"x" * 30).hexdigest(), status="active").returning(documents.c.id)).scalar_one()
    _C["documents"].append(did)

    (tmp_path / "reconciliation.csv").write_text(
        "document_id,area,state,current_storage_uri,proposed_destination\n"
        f"{did},Clients,already_in_repository,{src},D:/Client360/Content/Clients/x\n"
        "999999,Clients,needs_relocation,/legacy/n.pdf,D:/Client360/Content/Clients/y\n", encoding="utf-8")

    res = analyze(str(tmp_path), enrich=True)
    assert res["area_state"]["Clients"] == {"already_in_repository": 1, "needs_relocation": 1}
    excl = {int(r["document_id"]): r for r in res["excluded"]}
    assert did in excl and 999999 not in excl                        # only the non-needs_relocation owned row
    assert excl[did]["original_name"] == "Kept Return.pdf"           # enriched from DB
    assert excl[did]["source_exists"] is True                        # stat of the real file
    assert excl[did]["state"] == "already_in_repository"
