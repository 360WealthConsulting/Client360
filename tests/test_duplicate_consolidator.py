"""MDM-2 — duplicate-person consolidation orchestration coverage.

Exercises app/services/mdm/consolidator.py, which orchestrates the MDM-1 engine (never bypasses it):
preview-only (no changes), apply, resume/idempotent rerun, ambiguous-group skip, blocked-group skip,
CSV report generation, and no-duplicate-merge. Every merge goes through merge_people(); every group is
scoped with restrict_ids so unrelated people are never touched.
"""
import uuid

import pytest
from sqlalchemy import text

from app.db import engine, people, person_source_links, source_contacts, users
from app.services.mdm import consolidator


@pytest.fixture
def actor():
    with engine.begin() as c:
        t = uuid.uuid4().hex[:8]
        return c.execute(users.insert().values(
            email=f"mdm2{t}@e.test", normalized_email=f"mdm2{t}@e.test", display_name="MDM2",
            status="active").returning(users.c.id)).scalar_one()


@pytest.fixture
def group():
    """A duplicate group name shared by everyone created in a test (so they group together)."""
    return f"Consol {uuid.uuid4().hex[:8]}"


def _person(full_name, **over):
    vals = {"first_name": "C", "last_name": "Consol", "full_name": full_name, "active": True}
    vals.update(over)
    with engine.begin() as c:
        return c.execute(people.insert().values(**vals).returning(people.c.id)).scalar_one()


def _sc(**over):
    """Insert a source_contact (columns + raw_data) and return its id."""
    vals = {"source_system": "Wealthbox", "source_file": "wb.csv", "source_hash": uuid.uuid4().hex,
            "raw_data": {}}
    vals.update(over)
    with engine.begin() as c:
        return c.execute(source_contacts.insert().values(**vals).returning(source_contacts.c.id)).scalar_one()


def _link(pid, sc_id):
    with engine.begin() as c:
        c.execute(person_source_links.insert().values(
            person_id=pid, source_contact_id=sc_id, match_method="manual_review",
            match_score=100, confirmed=True))


def _relationship_entity(pid):
    with engine.begin() as c:
        c.execute(text("INSERT INTO relationship_entities (entity_type, name, person_id) "
                       "VALUES ('individual', 'e', :p)"), {"p": pid})


def _exists(pid):
    with engine.connect() as c:
        return c.execute(text("SELECT 1 FROM people WHERE id=:p"), {"p": pid}).first() is not None


def _history_count(dup):
    with engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM person_merge_history WHERE merged_person_id=:d"),
                         {"d": dup}).scalar_one()


def _run(ids, *, apply=False, actor=None, report=None):
    return consolidator.consolidate(apply=apply, actor_user_id=actor, restrict_ids=ids,
                                    report_path=report)


# --- preview only ------------------------------------------------------------

def test_preview_makes_no_changes(group, actor):
    survivor = _person(group, primary_email="rich@e.test", primary_phone="555-1")
    dup = _person(group)                                    # empty shell → clear survivor is the rich one
    s = _run([survivor, dup], apply=False, actor=actor)
    assert s["groups"] == 1 and s["merged"] == 0
    row = next(r for r in s["rows"] if r["merged_person_id"] == dup)
    assert row["status"] == "would_merge" and row["safe_to_merge"] is True
    assert _exists(survivor) and _exists(dup) and _history_count(dup) == 0   # nothing mutated


# --- apply -------------------------------------------------------------------

def test_apply_merges_clear_survivor(group, actor):
    survivor = _person(group, primary_email="rich@e.test", primary_phone="555-1")
    dup = _person(group)
    s = _run([survivor, dup], apply=True, actor=actor)
    assert s["merged"] == 1 and s["ambiguous"] == 0 and s["blocked"] == 0
    assert _exists(survivor) and not _exists(dup) and _history_count(dup) == 1


def test_apply_merges_multiple_empty_shells_into_rich(group, actor):
    survivor = _person(group, primary_email="rich@e.test", primary_phone="555-1")
    d1, d2 = _person(group), _person(group)
    s = _run([survivor, d1, d2], apply=True, actor=actor)
    assert s["merged"] == 2
    assert _exists(survivor) and not _exists(d1) and not _exists(d2)


# --- resume / idempotency ----------------------------------------------------

def test_resume_and_idempotent_rerun(group, actor):
    survivor = _person(group, primary_email="rich@e.test")
    dup = _person(group)
    first = _run([survivor, dup], apply=True, actor=actor)
    assert first["merged"] == 1
    second = _run([survivor, dup], apply=True, actor=actor)      # rerun
    assert second["merged"] == 0                                # nothing to repeat
    assert _history_count(dup) == 1                             # no second history row


# --- ambiguous ---------------------------------------------------------------

def test_ambiguous_all_empty_group_skipped(group, actor):
    a, b = _person(group), _person(group)                       # both empty → no clear survivor
    s = _run([a, b], apply=True, actor=actor)
    assert s["ambiguous"] == 1 and s["merged"] == 0
    assert _exists(a) and _exists(b)                            # never guessed → both kept


def test_ambiguous_equal_richness_skipped(group, actor):
    a = _person(group, primary_email="a@e.test")
    b = _person(group, primary_email="b@e.test")               # tie on score → ambiguous
    s = _run([a, b], apply=True, actor=actor)
    assert s["ambiguous"] == 1 and s["merged"] == 0
    assert _exists(a) and _exists(b)


# --- blocked -----------------------------------------------------------------

def test_blocked_group_is_skipped(group, actor):
    # Same email on both (no identity conflict → survivor selected), but both own a relationship_entity,
    # so the MDM-1 engine blocks the merge → status blocked (never bypassed).
    survivor = _person(group, primary_email="same@e.test", primary_phone="555-1")
    dup = _person(group, primary_email="same@e.test")
    _relationship_entity(survivor)
    _relationship_entity(dup)                                  # both own relationship_entities → blocker
    s = _run([survivor, dup], apply=True, actor=actor)
    assert s["blocked"] == 1 and s["merged"] == 0
    row = next(r for r in s["rows"] if r["merged_person_id"] == dup)
    assert row["status"] == "blocked" and row["safe_to_merge"] is False and row["blocker"]
    assert _exists(survivor) and _exists(dup)                  # blocked → nothing merged


# --- report generation -------------------------------------------------------

def test_report_generation(group, actor, tmp_path):
    survivor = _person(group, primary_email="rich@e.test")
    dup = _person(group)
    report = str(tmp_path / "mdm2_merge_report.csv")
    _run([survivor, dup], apply=True, actor=actor, report=report)
    import csv
    with open(report) as fh:
        rows = list(csv.DictReader(fh))
    assert set(rows[0].keys()) == {"survivor_person_id", "merged_person_id", "group_name", "reason",
                                   "safe_to_merge", "status", "blocker", "warning", "survivor_score",
                                   "survivor_evidence", "duplicate_evidence", "conflicting_identifiers",
                                   "selection_reason"}
    merged = [r for r in rows if r["merged_person_id"] == str(dup)]
    assert merged and merged[0]["status"] == "merged" and merged[0]["survivor_person_id"] == str(survivor)
    assert merged[0]["survivor_score"] and merged[0]["selection_reason"]


# --- engine is used, not bypassed --------------------------------------------

def test_consolidator_uses_merge_engine():
    import inspect
    src = inspect.getsource(consolidator)
    assert "merge_people(" in src and "preview_person_merge(" in src   # orchestrates, not reimplements
    assert consolidator.AUTOMATIC_SURVIVOR_RULE_COUNT == 10


# --- deployment-order safety: preview works before pmh01; apply refuses -----------------------

def test_preview_and_apply_without_pmh01(group, actor, tmp_path):
    """Regression (PR #186): preview must run against a pre-pmh01 schema and still generate a report
    with no mutations; apply must refuse clearly before any mutation. Rename person_merge_history away
    so a fresh connection genuinely sees the pre-pmh01 schema, then restore it."""
    from app.services.person_merge import MergeBlocked
    survivor = _person(group, primary_email="rich@e.test", primary_phone="555-1")
    dup = _person(group)
    report = str(tmp_path / "pre_pmh01.csv")
    with engine.begin() as c:
        c.execute(text("ALTER TABLE person_merge_history RENAME TO person_merge_history_bak"))
    try:
        # Preview works without the history table and writes a report; nothing is mutated.
        s = _run([survivor, dup], apply=False, actor=actor, report=report)
        assert s["groups"] == 1
        row = next(r for r in s["rows"] if r["merged_person_id"] == dup)
        assert row["status"] == "would_merge" and row["safe_to_merge"] is True
        import csv
        with open(report) as fh:
            assert any(r["merged_person_id"] == str(dup) for r in csv.DictReader(fh))
        assert _exists(survivor) and _exists(dup)              # no records mutated

        # Apply refuses clearly, before any mutation.
        with pytest.raises(MergeBlocked, match="pmh01"):
            _run([survivor, dup], apply=True, actor=actor)
        assert _exists(survivor) and _exists(dup)              # still nothing merged
    finally:
        with engine.begin() as c:
            c.execute(text("ALTER TABLE person_merge_history_bak RENAME TO person_merge_history"))


# --- source-contact-derived scoring (PR #186 fix) --------------------------------------------

def test_austin_style_source_email_selects_unique_survivor(group, actor):
    """Canonical rows sparse; one linked source_contact carries email/phone in raw_data (the Austin
    case). That person must win; the empty shells merge into it."""
    survivor = _person(group)                                  # canonical empty
    _link(survivor, _sc(raw_data={"home_email": "austinweaver4743@gmail.com",
                                  "home_phone": "4345928548", "contact_type": "Prospect",
                                  "contact_source": "Referral"}))
    shells = [_person(group) for _ in range(5)]
    for s in shells:
        _link(s, _sc(raw_data={}))                            # empty source contacts
    r = _run([survivor, *shells], apply=True, actor=actor)
    assert r["merged"] == 5 and r["ambiguous"] == 0 and r["blocked"] == 0
    assert _exists(survivor) and all(not _exists(s) for s in shells)


def test_canonical_empty_but_source_populated_selects_survivor(group, actor):
    survivor = _person(group)                                  # empty canonical row
    _link(survivor, _sc(email="found@x.com", phone="5551234567"))
    dup = _person(group)                                       # totally empty
    r = _run([survivor, dup], apply=True, actor=actor)
    assert r["merged"] == 1 and _exists(survivor) and not _exists(dup)


def test_conflicting_source_emails_are_ambiguous(group, actor):
    a, b = _person(group), _person(group)
    _link(a, _sc(email="a@x.com"))
    _link(b, _sc(email="b@y.com"))                            # different real emails → conflict
    r = _run([a, b], apply=True, actor=actor)
    assert r["ambiguous"] == 1 and r["merged"] == 0
    assert _exists(a) and _exists(b)
    row = next(x for x in r["rows"] if x["merged_person_id"] in (a, b))
    assert row["conflicting_identifiers"] and row["status"] == "ambiguous"


def test_conflicting_source_phones_are_ambiguous(group, actor):
    a, b = _person(group), _person(group)
    _link(a, _sc(raw_data={"home_phone": "4345928548"}))
    _link(b, _sc(raw_data={"home_phone": "2125551212"}))      # different phones → conflict
    r = _run([a, b], apply=True, actor=actor)
    assert r["ambiguous"] == 1 and r["merged"] == 0
    assert _exists(a) and _exists(b)


def test_all_empty_source_contacts_are_ambiguous(group, actor):
    a, b = _person(group), _person(group)
    _link(a, _sc(raw_data={}))
    _link(b, _sc(raw_data={}))                                # no usable identity evidence
    r = _run([a, b], apply=True, actor=actor)
    assert r["ambiguous"] == 1 and r["merged"] == 0
    assert _exists(a) and _exists(b)


def test_same_source_email_across_shells_deterministic_survivor(group, actor):
    people_ids = [_person(group) for _ in range(3)]
    for pid in people_ids:
        _link(pid, _sc(email="shared@x.com"))                 # same email everywhere → consistent, safe
    r = _run(people_ids, apply=True, actor=actor)
    assert r["merged"] == 2 and r["ambiguous"] == 0           # one deterministic survivor, two merged
    assert sum(1 for pid in people_ids if _exists(pid)) == 1
