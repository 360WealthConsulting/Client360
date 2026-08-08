"""Coverage for the READ-ONLY canonical-population remediation preview."""
import uuid

import pytest
from sqlalchemy import func, select

from app.db import engine, households, metadata, people
from app.services.migration.base import Mode, ModeNotSupported
from app.services.migration.canonical_population import (
    CanonicalPopulationPreviewJob,
    business_kind,
    classify_contact,
)
from app.services.migration.config import MigrationConfig

source_contacts = metadata.tables["source_contacts"]
person_source_links = metadata.tables["person_source_links"]
documents = metadata.tables["documents"]
_TAG = uuid.uuid4().hex[:8]
_C = {"people": [], "source_contacts": [], "person_source_links": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        for tbl, key in ((person_source_links, "person_source_links"),
                         (source_contacts, "source_contacts"), (people, "people")):
            if _C[key]:
                c.execute(tbl.delete().where(tbl.c.id.in_(_C[key])))
    for k in _C:
        _C[k].clear()


def _person(full_name, email=None):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True,
                        normalized_email=email).returning(people.c.id)).scalar_one()
    _C["people"].append(pid); return pid


def _sc(full_name, system="Wealthbox", email=None, phone=None, raw=None):
    with engine.begin() as c:
        sid = c.execute(source_contacts.insert().values(
            source_system=system, source_file="t", source_record_id=uuid.uuid4().hex,
            source_hash=uuid.uuid4().hex, full_name=full_name, normalized_email=email,
            normalized_phone=phone, raw_data=(raw or {})).returning(source_contacts.c.id)).scalar_one()
    _C["source_contacts"].append(sid); return sid


def _link(pid, sid):
    with engine.begin() as c:
        i = c.execute(person_source_links.insert().values(person_id=pid, source_contact_id=sid,
                      match_method="email", match_score=95, confirmed=True).returning(person_source_links.c.id)).scalar_one()
    _C["person_source_links"].append(i); return i


def _counts():
    with engine.connect() as c:
        return {t.name: int(c.execute(select(func.count()).select_from(t)).scalar_one())
                for t in (people, source_contacts, person_source_links, households, documents)}


# --------------------------------------------------------------------------- pure logic

def test_business_kind():
    assert business_kind("Wealthbox", {"type": "Company"}) == "business"
    assert business_kind("Wealthbox", {"type": "Person"}) is None
    assert business_kind("Drake", {"return_type": "1120S"}) == "business"
    assert business_kind("Drake", {"return_type": "1041"}) == "trust"
    assert business_kind("Drake", {"return_type": "1040"}) is None
    assert business_kind("Wealthbox", None) is None


def test_classify_contact_branches():
    by_email = {"e1": [11], "dup": [21, 22]}
    by_phone = {}
    ec = {"shared": 2}
    pc = {}

    def cc(sc):
        return classify_contact(sc, by_email, by_phone, ec, pc)[0]

    assert cc({"source_system": "Wealthbox", "raw_data": {"type": "Company"}, "full_name": "X"}) == "business_company_candidate"
    assert cc({"source_system": "Wealthbox", "raw_data": {}, "normalized_email": "e1", "normalized_phone": None}) == "existing_person_link"
    assert cc({"source_system": "Wealthbox", "raw_data": {}, "normalized_email": "dup", "normalized_phone": None}) == "ambiguous_identity"
    assert cc({"source_system": "Wealthbox", "raw_data": {}, "normalized_email": "shared", "normalized_phone": None}) == "ambiguous_identity"
    assert cc({"source_system": "Wealthbox", "raw_data": {}, "normalized_email": "unique", "normalized_phone": None}) == "safe_person_promotion"
    assert cc({"source_system": "Wealthbox", "raw_data": {}, "normalized_email": None, "normalized_phone": None}) == "unresolved"


# --------------------------------------------------------------------------- read-only DB preview

def test_preview_classifies_and_is_readonly(tmp_path):
    p1 = _person(f"Linkme Personx {_TAG}", email=f"link{_TAG}@x.com")
    _link(p1, _sc(f"Linkme Personx {_TAG}", email=f"link{_TAG}@x.com"))   # already linked -> counts as linked
    _sc(f"Linkme Personx {_TAG}", email=f"link{_TAG}@x.com")             # unlinked, matches p1 -> existing_person_link
    _sc(f"Promoteme Uniqueperson {_TAG}", email=f"prom{_TAG}@x.com")     # unique identity -> safe_person_promotion
    _sc(f"Star City Heating {_TAG}", raw={"type": "Company"})            # business_company_candidate
    _sc(f"Val6X {_TAG}", system="Drake", raw={"return_type": "1120S"})   # business (Drake)
    _sc(f"Nameonly Nobody {_TAG}")                                       # unresolved
    _sc("Shared Onex", email=f"share{_TAG}@x.com"); _sc("Shared Twox", email=f"share{_TAG}@x.com")  # collide -> ambiguous
    # duplicate canonical people (same normalized name)
    _person(f"Dupe Personx {_TAG}"); _person(f"Dupe Personx {_TAG}")
    # a joint household candidate: both members are unique canonical people
    _person(f"Aaa Bbb{_TAG}"); _person(f"Ccc Bbb{_TAG}")

    (tmp_path / "exceptions.csv").write_text(
        "source_folder,resolution,reason\n"
        f"Promoteme Uniqueperson {_TAG},unmatched,x\n"
        f"Aaa and Ccc Bbb{_TAG},unmatched,x\n", encoding="utf-8")

    before = _counts()
    result = CanonicalPopulationPreviewJob(MigrationConfig.from_env()).run(Mode.PREVIEW, preview_dir=str(tmp_path))
    after = _counts()
    c = result.counts

    assert before == after                                              # strictly read-only
    assert c["existing_person_link"] >= 1
    assert c["safe_person_promotion"] >= 1
    assert c["business_company_candidate"] >= 2
    assert c["ambiguous_identity"] >= 2
    assert c["unresolved"] >= 1
    assert c["linked"] >= 1
    assert c["duplicate_person_groups_by_name"] >= 1
    assert c["household_derivation_candidates"] >= 1
    assert c["folders_newly_resolvable_person"] >= 1
    assert c["drake_return_type_distribution"].get("1120S", 0) >= 1
    # reconciliation rows carry the required provenance mapping
    row = next(r for r in result.reconciliation if r["source_name"] == f"Star City Heating {_TAG}")
    assert row["proposed_action"] == "business_company_candidate"
    assert row["proposed_canonical_target"] == "relationship_entities:business"


def test_apply_refused():
    with pytest.raises(ModeNotSupported):
        CanonicalPopulationPreviewJob(MigrationConfig.from_env()).run(Mode.APPLY)
