"""Coverage for the READ-ONLY next-deterministic-batch planner.

Proves each folder resolves to EXACTLY ONE deterministic outcome via stable source identity/provenance,
that name-only / multi-candidate / shared-identity cases are HELD (never guessed on name), and that the
VAL6-style provenance trace maps only on a shared Drake stable id — not string similarity.
"""
import hashlib
import uuid

import pytest

from app.db import documents, engine, people
from scripts.migration.plan_next_linkage_batch import (
    build_indexes,
    plan,
    resolve_folder,
    stable_ids,
    trace_entity,
)

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


def _sc(sid, full, *, first=None, last=None, email=None, phone=None, system="Drake",
        srid=None, raw=None):
    return {"id": sid, "source_system": system, "source_record_id": srid, "full_name": full,
            "first_name": first, "last_name": last, "normalized_email": email,
            "normalized_phone": phone, "raw_data": raw}


def _person(pid, full, *, first=None, last=None, email=None, phone=None):
    return {"id": pid, "full_name": full, "first_name": first, "last_name": last,
            "normalized_email": email, "normalized_phone": phone}


# --------------------------------------------------------------------------- pure resolver

def test_safe_person_promotion_from_unique_identity():
    scs = [_sc(1, "Abigail Dargis", first="Abigail", last="Dargis", email="ab@x.com")]
    idx = build_indexes(scs, [])
    r = resolve_folder("Abigail Dargis", "source_exists_not_promoted", idx)
    assert r["category"] == "safe_person_promotion"
    assert r["proposed_target"] == "person:new" and str(r["source_contact_id"]) == "1"


def test_existing_person_link_via_stable_identity():
    # folder's source contact ties by email to exactly one person; names not provably different -> link.
    scs = [_sc(2, "Bob Smith", first="Bob", last="Smith", email="bob@x.com")]
    ppl = [_person(10, "Robert Smith", email="bob@x.com")]        # structured names absent -> not provable
    idx = build_indexes(scs, ppl)
    r = resolve_folder("Bob Smith", "source_exists_not_promoted", idx)
    assert r["category"] == "existing_person_link"
    assert r["proposed_target"] == "person:10"


def test_alternate_name_link_only_for_canonical_alternate_disposition():
    # SAME resolution as an existing-person link, but the canonical_alternate_name disposition means the
    # person exists under a differing name representation, so it is reported as the alternate-name link.
    scs = [_sc(3, "Robert Smith", first="Robert", last="Smith", email="rs@x.com")]
    ppl = [_person(11, "Bob Smith", email="rs@x.com")]
    idx = build_indexes(scs, ppl)
    r = resolve_folder("Robert Smith", "canonical_alternate_name", idx)
    assert r["category"] == "alternate_name_person_link"
    assert r["proposed_target"] == "person:11"


def test_business_candidate_from_drake_return_type():
    scs = [_sc(4, "Star City Heating", system="Drake", raw={"return_type": "1120"})]
    idx = build_indexes(scs, [])
    r = resolve_folder("Star City Heating", "source_exists_not_promoted", idx)
    assert r["category"] == "business_canonicalization"
    assert r["proposed_target"] == "relationship_entities:business"


def test_name_only_contact_is_held_not_guessed():
    scs = [_sc(5, "Zeta Client", first="Zeta", last="Client")]     # no email/phone -> no stable identity
    idx = build_indexes(scs, [])
    r = resolve_folder("Zeta Client", "source_exists_not_promoted", idx)
    assert r["category"] == "held_ambiguous"
    assert "name-only" in r["evidence"]


def test_shared_identity_is_held():
    scs = [_sc(6, "Spouse One", first="A", last="Fam", email="fam@x.com"),
           _sc(7, "Spouse Two", first="B", last="Fam", email="fam@x.com")]
    idx = build_indexes(scs, [])
    r = resolve_folder("Spouse One", "source_exists_not_promoted", idx)
    assert r["category"] == "held_ambiguous" and "shared" in r["evidence"]


def test_two_distinct_contacts_same_name_held():
    scs = [_sc(8, "John Doe", first="John", last="Doe", email="a@x.com"),
           _sc(9, "John Doe", first="John", last="Doe", email="b@x.com")]
    idx = build_indexes(scs, [])
    r = resolve_folder("John Doe", "source_exists_not_promoted", idx)
    assert r["category"] == "held_ambiguous" and "distinct source contacts" in r["evidence"]


def test_provably_different_names_held():
    # same surname, different first name sharing an identity -> spouse, never auto-linked.
    scs = [_sc(20, "Debra White", first="Debra", last="White", email="wh@x.com")]
    ppl = [_person(30, "Michael White", first="Michael", last="White", email="wh@x.com")]
    idx = build_indexes(scs, ppl)
    r = resolve_folder("Debra White", "source_exists_not_promoted", idx)
    assert r["category"] == "held_ambiguous" and "provably different" in r["evidence"]


def test_canonical_alternate_name_by_tokens_only_is_held():
    # a person's tokens overlap the folder but NO source contact anchors it -> fuzzy only -> held.
    ppl = [_person(40, "Jonathan Michael Baker", first="Jonathan", last="Baker")]
    idx = build_indexes([], ppl)
    r = resolve_folder("Jonathan Baker", "canonical_alternate_name", idx)
    assert r["category"] == "held_ambiguous" and "no stable-identity anchor" in r["evidence"]


def test_joint_folder_out_of_scope_held():
    idx = build_indexes([], [])
    r = resolve_folder("Michael and Debra White", "source_exists_not_promoted", idx)
    assert r["category"] == "held_ambiguous" and "joint" in r["evidence"]


# --------------------------------------------------------------------------- stable ids + trace

def test_stable_ids_extracts_srid_and_ein():
    ids = stable_ids("R123", {"EIN": "45-6789", "unrelated": "x"})
    assert ids == {"srid:r123", "ein:45-6789"}


def test_trace_maps_only_on_shared_stable_id():
    unlinked = [
        _sc(50, "VAL6, INC", system="Drake", srid="SE-VAL6", raw={"ein": "99-111"}),   # shares entity id
        _sc(51, "VAL6 SERVICES", system="Drake", srid="OTHER", raw={"ein": "22-222"}),  # only string sim
    ]
    entities = [{"id": 900, "entity_type": "business", "name": "SOUTH EAST VAL6 INC",
                 "details": {"origin": "canonical_repair", "source_record_ids": ["SE-VAL6"],
                             "source_contact_ids": [49]}}]
    tr = trace_entity("VAL6", unlinked, entities)
    verdicts = {f["source_contact_id"]: f["verdict"] for f in tr["findings"]}
    assert verdicts[50] == "safe_map"                       # shared srid:se-val6
    assert verdicts[51] == "held_string_similarity_only"    # different Drake identity


def test_trace_maps_when_contact_already_in_entity_provenance():
    unlinked = [_sc(60, "VAL6 HOLDINGS", system="Drake", srid="Z", raw={})]
    entities = [{"id": 901, "entity_type": "business", "name": "VAL6 HOLDINGS LLC",
                 "details": {"source_contact_ids": [60], "source_record_ids": []}}]
    tr = trace_entity("VAL6", unlinked, entities)
    assert tr["findings"][0]["verdict"] == "safe_map"


# --------------------------------------------------------------------------- DB-backed integration

def test_plan_reads_preview_and_writes_readonly(tmp_path):
    # a real person + a real document; prove the planner never writes and the doc stays unlinked.
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=f"P {_TAG}", first_name="P", last_name=_TAG,
                                               active=True).returning(people.c.id)).scalar_one()
    _C["people"].append(pid)
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=None, household_id=None, organization_id=None,
            original_name="x.pdf", stored_name=f"pnb-{_TAG}-{uuid.uuid4().hex}",
            storage_path="x", storage_uri="C:/legacy/x.pdf", size_bytes=10,
            sha256=hashlib.sha256(b"x").hexdigest(), status="active",
            tags={"source_system": "TaxDome Drive", "taxdome_folder": f"Nobody {_TAG}"})
            .returning(documents.c.id)).scalar_one()
    _C["documents"].append(did)

    (tmp_path / "exceptions.csv").write_text(
        "source_folder,resolution,reason,document_count,candidates\n"
        f"Nobody {_TAG},unmatched,x,1,\n", encoding="utf-8")

    res = plan(str(tmp_path))
    assert "outcome_counts" in res and "rows" in res
    # the seeded document is still unlinked (read-only guarantee)
    with engine.connect() as conn:
        from sqlalchemy import func, select
        linked = conn.execute(select(func.count()).select_from(documents).where(
            documents.c.id == did, documents.c.person_id.isnot(None))).scalar_one()
    assert linked == 0
