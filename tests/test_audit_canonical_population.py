"""Coverage for the READ-ONLY canonical-population audit tool."""
import uuid

import pytest
from sqlalchemy import func, select

from app.db import engine, households, metadata, people
from scripts.migration.audit_canonical_population import (
    audit,
    build_name_index,
    classify_folder,
    search_term,
)

source_contacts = metadata.tables["source_contacts"]
person_source_links = metadata.tables["person_source_links"]
match_queue = metadata.tables["match_queue"]
relationship_entities = metadata.tables["relationship_entities"]
documents = metadata.tables["documents"]
_TAG = uuid.uuid4().hex[:8]
_C = {"people": [], "households": [], "relationship_entities": [], "source_contacts": [],
      "person_source_links": [], "match_queue": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        for tbl, key in ((match_queue, "match_queue"), (person_source_links, "person_source_links"),
                         (relationship_entities, "relationship_entities"), (source_contacts, "source_contacts"),
                         (people, "people"), (households, "households")):
            if _C[key]:
                c.execute(tbl.delete().where(tbl.c.id.in_(_C[key])))
    for k in _C:
        _C[k].clear()


def _person(full_name):
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, active=True).returning(people.c.id)).scalar_one()
    _C["people"].append(pid); return pid


def _sc(full_name, source_system, raw=None):
    with engine.begin() as c:
        sid = c.execute(source_contacts.insert().values(
            source_system=source_system, source_file="t.zip", source_record_id=uuid.uuid4().hex,
            source_hash=uuid.uuid4().hex, full_name=full_name, raw_data=(raw or {})).returning(source_contacts.c.id)).scalar_one()
    _C["source_contacts"].append(sid); return sid


def _link(pid, sid):
    with engine.begin() as c:
        i = c.execute(person_source_links.insert().values(person_id=pid, source_contact_id=sid,
                      match_method="email", confirmed=True).returning(person_source_links.c.id)).scalar_one()
    _C["person_source_links"].append(i); return i


def _queue(sid, status="pending"):
    with engine.begin() as c:
        i = c.execute(match_queue.insert().values(source_contact_id=sid, status=status,
                      match_method="email", match_score=0.9).returning(match_queue.c.id)).scalar_one()
    _C["match_queue"].append(i); return i


def _counts():
    with engine.connect() as c:
        return {t.name: int(c.execute(select(func.count()).select_from(t)).scalar_one())
                for t in (people, households, relationship_entities, source_contacts,
                          person_source_links, match_queue, documents)}


# --------------------------------------------------------------------------- pure classification

def test_classify_dispositions():
    people_key, people_tok = build_name_index([
        (1, "John Smith", None, None), (2, "John Smith", None, None),   # duplicate name-key
        (3, "Abigail R Dargis", None, None),                            # middle INITIAL
        (4, "Andrew Tribbett", None, None),                             # a joint member
        (5, "Unique Personx", None, None),                              # unique exact
        (6, "Marcus Aurelius Kwan", None, None),                        # middle NAME, not an initial
    ])
    sc_key, sc_tok = build_name_index([(10, "Solo Sourceonly", None, None), (11, "VAL6, INC", None, None)])

    def cl(f):
        return classify_folder(f, people_key, people_tok, sc_key, sc_tok)

    assert cl("John Smith") == "canonical_ambiguous"
    assert cl("Unique Personx") == "canonical_unique_resolver_gap"
    assert cl("Solo Sourceonly") == "source_exists_not_promoted"

    # A single-letter middle initial is NOT identity, so "Abigail R Dargis" and the folder
    # "Abigail Dargis" key identically and this is an EXACT unique canonical match. It used to
    # expect ``canonical_alternate_name``, which was correct only while ``_name_key`` kept the
    # initial and the two keys therefore differed; the anchoring fix drops single-letter tokens
    # deliberately (SharePoint files "CASHMAN, KIMBERLY S" where the CRM holds "Cashman, Kimberly").
    assert cl("Abigail Dargis") == "canonical_unique_resolver_gap"

    # The boundary of that rule, and the ``canonical_alternate_name`` branch the line above used to
    # cover: a MULTI-LETTER middle name is substantive and is never dropped, so "Marcus Kwan" does
    # not key to "Marcus Aurelius Kwan" and can only reach the token-overlap fallback.
    assert cl("Marcus Aurelius Kwan") == "canonical_unique_resolver_gap"
    assert cl("Marcus Kwan") == "canonical_alternate_name"

    assert cl("Andrew and Amanda Tribbett") == "joint_partial_uncanonicalized"
    assert cl("Bob and Alice Zzznone") == "joint_absent"
    assert cl("Zznobody Uniquename") == "truly_absent"
    assert cl("VAL6, INC") == "source_exists_not_promoted"       # business in source, not promoted


# --------------------------------------------------------------------------- read-only DB audit

def test_audit_readonly_provenance_and_dispositions(tmp_path):
    uniq = _person(f"Uniqueperson {_TAG}")
    linked_sc = _sc(f"Uniqueperson {_TAG}", f"WBTEST{_TAG}")
    _link(uniq, linked_sc)
    _person(f"Dupname {_TAG}"); _person(f"Dupname {_TAG}")            # ambiguous
    src_only = _sc(f"Sourceonly {_TAG}", f"WBTEST{_TAG}")
    _queue(src_only, "pending")
    _sc(f"VAL6X{_TAG}, INC", f"WBTEST{_TAG}", raw={"type": "Company"})

    (tmp_path / "exceptions.csv").write_text(
        "source_folder,resolution,reason\n"
        f"Dupname {_TAG},ambiguous,x\n"
        f"Uniqueperson {_TAG},unmatched,x\n"
        f"Sourceonly {_TAG},unmatched,x\n"
        f"Uniqueperson {_TAG} and Someone Elsezz,unmatched,x\n"
        f"Zznobody {_TAG}xx,unmatched,x\n", encoding="utf-8")

    before = _counts()
    result = audit(str(tmp_path), engine=engine)
    after = _counts()

    assert before == after                                            # strictly read-only
    d = result["dispositions"]
    assert d["canonical_ambiguous"] >= 1
    assert d["canonical_unique_resolver_gap"] >= 1
    assert d["source_exists_not_promoted"] >= 1
    assert d["joint_partial_uncanonicalized"] >= 1
    assert d["truly_absent"] >= 1
    assert 2 in result["would_match_candidates"].values()            # the duplicate name maps to 2 people
    prov = result["provenance"]
    assert prov["people_total"] >= 3
    assert any(sysname == f"WBTEST{_TAG}" for sysname, _ in prov["people_by_source_system"])
    assert any(st == "pending" for st, _ in prov["match_queue_by_status"])

    hits = search_term(engine, f"VAL6X{_TAG}")                        # G: business found in source only
    assert len(hits["source_contacts"]) >= 1 and len(hits["people"]) == 0
