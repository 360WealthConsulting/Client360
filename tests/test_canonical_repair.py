"""Coverage for the canonical-population REPAIR job (PREVIEW + guarded APPLY)."""
import uuid

import pytest
from sqlalchemy import func, or_, select

from app.db import engine, households, metadata, people
from app.services.migration.base import Mode
from app.services.migration.canonical_repair import (
    CanonicalRepairJob,
    RepairGuardError,
    load_approved_set,
    plausible_link,
)
from app.services.migration.config import MigrationConfig


def test_plausible_link_rule():
    # unique identity + matching first/last -> included
    assert plausible_link("Jeffrey", "Fuller", "Jeffrey", "Fuller", 1) is True
    # shared identity (share>1) + matching first/last -> excluded (household-shared)
    assert plausible_link("Jeffrey", "Fuller", "Jeffrey", "Fuller", 2) is False
    # different first name, same last -> excluded (spouse)
    assert plausible_link("Betty", "Philips", "William", "Philips", 1) is False
    # missing structured names on one side + unique identity -> included (the production Jeffrey/Jessica case)
    assert plausible_link(None, None, "Jeffrey", "Fuller", 1) is True
    assert plausible_link("Jessica", "Zielske", None, None, 1) is True
    # missing names but SHARED identity -> still excluded (household exclusion never weakened)
    assert plausible_link(None, None, None, None, 2) is False

source_contacts = metadata.tables["source_contacts"]
person_source_links = metadata.tables["person_source_links"]
relationship_entities = metadata.tables["relationship_entities"]
_TAG = uuid.uuid4().hex[:8]
_A = "zzq" + ("".join(c for c in _TAG if c.isalpha()) + "abcd")[:4]   # letters-only marker (clean folder parsing)


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    tag, a = f"%{_TAG}%", f"%{_A}%"
    with engine.begin() as c:
        my_sc = select(source_contacts.c.id).where(or_(source_contacts.c.full_name.like(tag),
                                                       source_contacts.c.full_name.like(a)))
        my_ppl = select(people.c.id).where(or_(people.c.full_name.like(tag), people.c.full_name.like(a)))
        c.execute(person_source_links.delete().where(or_(
            person_source_links.c.source_contact_id.in_(my_sc), person_source_links.c.person_id.in_(my_ppl))))
        c.execute(people.delete().where(or_(people.c.full_name.like(tag), people.c.full_name.like(a))))
        c.execute(households.delete().where(households.c.name.like(a)))
        c.execute(relationship_entities.delete().where(relationship_entities.c.name.like(a)))
        c.execute(source_contacts.delete().where(or_(source_contacts.c.full_name.like(tag),
                                                    source_contacts.c.full_name.like(a))))


def _person(full_name, first=None, last=None, email=None):
    with engine.begin() as c:
        return c.execute(people.insert().values(full_name=full_name, first_name=first, last_name=last,
                         active=True, normalized_email=email).returning(people.c.id)).scalar_one()


def _sc(full_name, system="Wealthbox", first=None, last=None, email=None, record_id=None, raw=None):
    with engine.begin() as c:
        c.execute(source_contacts.insert().values(
            source_system=system, source_file="t", source_record_id=record_id or uuid.uuid4().hex,
            source_hash=uuid.uuid4().hex, full_name=full_name, first_name=first, last_name=last,
            email=email, normalized_email=email, raw_data=(raw or {})))


def _entity_counts():
    with engine.connect() as c:
        return {t.name: int(c.execute(select(func.count()).select_from(t)).scalar_one())
                for t in (people, person_source_links, households, relationship_entities)}


def _seed(tmp_path):
    _sc(f"Promo Uniquex {_TAG}", email=f"promo{_TAG}@x.com")                                   # promotion
    jeff = _person(f"Jeffrey Fullerx {_TAG}", first="Jeffrey", last=f"Fullerx{_TAG}", email=f"jf{_TAG}@x.com")
    _sc(f"Jeffrey Fullerx {_TAG}", first="Jeffrey", last=f"Fullerx{_TAG}", email=f"jf{_TAG}@x.com")   # plausible link
    _person(f"William Suspx {_TAG}", first="William", last=f"Suspx{_TAG}", email=f"su{_TAG}@x.com")
    _sc(f"Betty Suspx {_TAG}", first="Betty", last=f"Suspx{_TAG}", email=f"su{_TAG}@x.com")     # SUSPECT (diff first) -> excluded
    # same first+last as an existing person, BUT the email is shared with another contact -> suspect, excluded
    _person(f"Sam Sharedx {_TAG}", first="Sam", last=f"Sharedx{_TAG}", email=f"sh2{_TAG}@x.com")
    _sc(f"Sam Sharedx {_TAG}", first="Sam", last=f"Sharedx{_TAG}", email=f"sh2{_TAG}@x.com")     # would-be plausible...
    _sc(f"Pat Sharedx {_TAG}", first="Pat", last=f"Sharedx{_TAG}", email=f"sh2{_TAG}@x.com")     # ...shared identity -> both excluded
    _person(f"Aaa Zbb{_A}", first="Aaa", last=f"Zbb{_A}")                                       # household member
    _person(f"Ccc Zbb{_A}", first="Ccc", last=f"Zbb{_A}")                                       # household member
    _sc(f"Val6x Bizz{_A} INC", system="Drake", record_id=f"y2023-{_TAG}", raw={"return_type": "1120S"})  # business y1
    _sc(f"Val6x Bizz{_A} INC", system="Drake", record_id=f"y2024-{_TAG}", raw={"return_type": "1120S"})  # business y2 (dedupe)
    _sc(f"Familyx Trustt{_A}", system="Drake", record_id=f"t2024-{_TAG}", raw={"return_type": "1041"})    # trust
    (tmp_path / "exceptions.csv").write_text(
        f"source_folder,resolution,reason\nAaa and Ccc Zbb{_A},unmatched,x\n", encoding="utf-8")
    return jeff


EXPECT = {"promotions": 1, "links": 1, "households": 1, "businesses": 2}


def test_preview_is_readonly_and_plans_deterministic_set(tmp_path):
    _seed(tmp_path)
    before = _entity_counts()
    result = CanonicalRepairJob(MigrationConfig.from_env()).run(Mode.PREVIEW, preview_dir=str(tmp_path))
    assert _entity_counts() == before                                   # read-only
    c = result.counts
    assert (c["promotions"], c["links"], c["households"], c["businesses"]) == (1, 1, 1, 2)
    assert c["business_source_contacts"] == 3                           # 2 Drake business years + 1 trust
    cats = {r["category"] for r in result.reconciliation}
    assert "business_canonicalization" in cats and "trust_canonicalization" in cats
    link_names = {r["source_name"] for r in result.reconciliation if r["category"] == "existing_person_link"}
    assert f"Betty Suspx {_TAG}" not in link_names                      # suspect (different first name)
    assert f"Sam Sharedx {_TAG}" not in link_names                      # suspect (shared email identity)
    assert link_names == {f"Jeffrey Fullerx {_TAG}"}                    # exactly the validated plausible link


def test_apply_guards_fail_closed(tmp_path):
    _seed(tmp_path)
    job = CanonicalRepairJob(MigrationConfig.from_env())
    before = _entity_counts()
    with pytest.raises(RepairGuardError):                              # no confirm
        job.run(Mode.APPLY, preview_dir=str(tmp_path), confirm=False, backup=None, expect=EXPECT)
    bad = tmp_path / "empty.dump"; bad.write_text("")
    with pytest.raises(RepairGuardError):                              # empty backup
        job.run(Mode.APPLY, preview_dir=str(tmp_path), confirm=True, backup=str(bad), expect=EXPECT)
    good = tmp_path / "backup.dump"; good.write_text("PGDMP")
    with pytest.raises(RepairGuardError):                              # count drift
        job.run(Mode.APPLY, preview_dir=str(tmp_path), confirm=True, backup=str(good),
                expect={"promotions": 999, "links": 1, "households": 1, "businesses": 2})
    assert _entity_counts() == before                                  # no writes on any guard failure


def test_apply_writes_deterministic_set_and_is_idempotent(tmp_path):
    jeff = _seed(tmp_path)
    good = tmp_path / "backup.dump"; good.write_text("PGDMP")
    job = CanonicalRepairJob(MigrationConfig.from_env())

    job.run(Mode.APPLY, preview_dir=str(tmp_path), confirm=True, backup=str(good), expect=EXPECT)
    with engine.connect() as c:
        promo_sc = c.execute(select(source_contacts.c.id).where(
            source_contacts.c.full_name == f"Promo Uniquex {_TAG}")).scalar_one()
        assert c.execute(select(func.count()).select_from(person_source_links).where(
            person_source_links.c.source_contact_id == promo_sc)).scalar_one() == 1     # promoted + linked
        jf_sc = c.execute(select(source_contacts.c.id).where(
            source_contacts.c.full_name == f"Jeffrey Fullerx {_TAG}")).scalar_one()
        assert c.execute(select(person_source_links.c.person_id).where(
            person_source_links.c.source_contact_id == jf_sc)).scalar_one() == jeff       # linked to existing
        for suspect_name in (f"Betty Suspx {_TAG}", f"Sam Sharedx {_TAG}"):
            ssc = c.execute(select(source_contacts.c.id).where(
                source_contacts.c.full_name == suspect_name)).scalar_one()
            assert c.execute(select(func.count()).select_from(person_source_links).where(
                person_source_links.c.source_contact_id == ssc)).scalar_one() == 0        # suspect NOT linked
        hh = list(c.execute(select(people.c.household_id).where(
            people.c.full_name.in_([f"Aaa Zbb{_A}", f"Ccc Zbb{_A}"]))).scalars())
        assert len(set(hh)) == 1 and hh[0] is not None                                    # one household
        biz = c.execute(select(relationship_entities.c.details).where(
            relationship_entities.c.entity_type == "business",
            relationship_entities.c.name == f"Val6x Bizz{_A} INC")).scalar_one()
        assert len(biz["source_contact_ids"]) == 2                                        # Drake years deduped to one
        assert c.execute(select(func.count()).select_from(relationship_entities).where(
            relationship_entities.c.entity_type == "trust",
            relationship_entities.c.name == f"Familyx Trustt{_A}")).scalar_one() == 1

    after1 = _entity_counts()
    r2 = job.run(Mode.APPLY, preview_dir=str(tmp_path), confirm=True, backup=str(good), expect=EXPECT)
    assert _entity_counts() == after1                                                     # idempotent
    assert all(row["action"].startswith("skipped") for row in r2.reconciliation if row["action"])


def test_preview_after_apply_is_idempotent_and_freezes_scope(tmp_path):
    """The exact production invariant: PREVIEW -> APPLY(approved set) -> PREVIEW(--approved) must show the
    SAME approved totals, pending all zero, and a newly-eligible contact must NOT enter the frozen set."""
    _seed(tmp_path)
    good = tmp_path / "backup.dump"; good.write_text("PGDMP")
    job = CanonicalRepairJob(MigrationConfig.from_env())

    r_apply = job.run(Mode.APPLY, preview_dir=str(tmp_path), confirm=True, backup=str(good), expect=EXPECT)
    approved = load_approved_set(r_apply.run_dir)                       # freeze scope from the applied manifest

    # a NEW safe-promotion contact becomes eligible AFTER apply (repair altered matching state)
    _sc(f"Newly Eligiblex {_TAG}", email=f"newelig{_TAG}@x.com")

    r_prev = job.run(Mode.PREVIEW, preview_dir=str(tmp_path), approved=approved)
    c = r_prev.counts
    assert (c["promotions"], c["links"], c["households"], c["businesses"]) == (1, 1, 1, 2)   # frozen totals
    assert c["pending"] == {"promotions": 0, "links": 0, "households": 0, "businesses": 0}   # all applied
    assert c["newly_eligible_out_of_scope"]["promotions"] >= 1                               # excluded from set
