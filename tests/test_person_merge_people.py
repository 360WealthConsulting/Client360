"""MDM-1 — canonical person merge (survivor ← duplicate) coverage.

Exercises preview_person_merge + merge_people: empty-shell merge, source-link reassignment + dedup,
household + opportunity-participant conflicts (consolidation), survivor field enrichment (fill-null only,
never overwrite), blocker detection, full rollback on failure, merge-history creation, event + audit
behavior, dry-run makes no changes, and idempotent refusal when the duplicate no longer exists. Temp
rows only; no bulk cleanup; no real production people.
"""
import uuid

import pytest
from sqlalchemy import text

from app.db import engine, people, person_source_links, source_contacts, users
from app.services.person_merge import MergeBlocked, merge_people, preview_person_merge

_TAG = "PMERGE"


@pytest.fixture
def actor():
    with engine.begin() as c:
        t = uuid.uuid4().hex[:8]
        return c.execute(users.insert().values(
            email=f"pm{t}@e.test", normalized_email=f"pm{t}@e.test", display_name="Merger",
            status="active").returning(users.c.id)).scalar_one()


def _person(**over):
    vals = {"first_name": "A", "last_name": _TAG, "full_name": f"A {_TAG} {uuid.uuid4().hex[:6]}",
            "active": True}
    vals.update(over)
    with engine.begin() as c:
        return c.execute(people.insert().values(**vals).returning(people.c.id)).scalar_one()


def _source_contact():
    with engine.begin() as c:
        return c.execute(source_contacts.insert().values(
            source_system="Wealthbox", source_file=f"{_TAG}.csv", source_hash=uuid.uuid4().hex,
            raw_data={}).returning(source_contacts.c.id)).scalar_one()


def _link(person_id, source_contact_id):
    with engine.begin() as c:
        c.execute(person_source_links.insert().values(
            person_id=person_id, source_contact_id=source_contact_id,
            match_method="manual_review", match_score=100, confirmed=True))


def _household():
    with engine.begin() as c:
        return c.execute(text("INSERT INTO households (name) VALUES (:n) RETURNING id"),
                         {"n": f"{_TAG} {uuid.uuid4().hex[:6]}"}).scalar_one()


def _hh_rel(household_id, person_id):
    with engine.begin() as c:
        c.execute(text("INSERT INTO household_relationships (household_id, person_id, relationship_type)"
                       " VALUES (:h, :p, 'member')"), {"h": household_id, "p": person_id})


def _opportunity():
    with engine.begin() as c:
        pl = c.execute(text("INSERT INTO opportunity_pipelines (code, name) VALUES (:c, :c) RETURNING id"),
                       {"c": f"{_TAG}{uuid.uuid4().hex[:6]}"}).scalar_one()
        st = c.execute(text("INSERT INTO opportunity_stages (pipeline_id, code, name) "
                            "VALUES (:pl, :c, :c) RETURNING id"),
                       {"pl": pl, "c": f"{_TAG}{uuid.uuid4().hex[:6]}"}).scalar_one()
        return c.execute(text("INSERT INTO opportunities (pipeline_id, stage_id, title) "
                              "VALUES (:pl, :st, :t) RETURNING id"),
                         {"pl": pl, "st": st, "t": f"{_TAG} opp"}).scalar_one()


def _participant(opportunity_id, person_id):
    with engine.begin() as c:
        c.execute(text("INSERT INTO opportunity_participants (opportunity_id, person_id) "
                       "VALUES (:o, :p)"), {"o": opportunity_id, "p": person_id})


def _relationship_entity(person_id):
    with engine.begin() as c:
        c.execute(text("INSERT INTO relationship_entities (entity_type, name, person_id) "
                       "VALUES ('individual', :n, :p)"), {"n": f"{_TAG} entity", "p": person_id})


def _exists(pid):
    with engine.connect() as c:
        return c.execute(text("SELECT 1 FROM people WHERE id = :p"), {"p": pid}).first() is not None


def _history(dup):
    with engine.connect() as c:
        return c.execute(text("SELECT * FROM person_merge_history WHERE merged_person_id = :d"),
                         {"d": dup}).mappings().all()


# --- basic + reassignment ----------------------------------------------------

def test_basic_empty_shell_merge(actor):
    survivor, dup = _person(), _person()
    s = merge_people(survivor, dup, reason="empty shell", actor_user_id=actor)
    assert s["applied"] is True
    assert _exists(survivor) and not _exists(dup)          # duplicate removed, survivor kept
    assert len(_history(dup)) == 1


def test_source_link_reassignment(actor):
    survivor, dup = _person(), _person()
    sc = _source_contact()
    _link(dup, sc)                                          # only the duplicate is linked
    s = merge_people(survivor, dup, reason="move link", actor_user_id=actor)
    assert s["reassigned"].get("person_source_links.person_id") == 1
    with engine.connect() as c:
        owner = c.execute(text("SELECT person_id FROM person_source_links WHERE source_contact_id=:s"),
                          {"s": sc}).scalar_one()
    assert owner == survivor


def test_duplicate_source_link_conflict_consolidates(actor):
    survivor, dup = _person(), _person()
    sc = _source_contact()
    _link(survivor, sc)
    _link(dup, sc)                                          # both linked to the SAME source contact
    s = merge_people(survivor, dup, reason="dedup link", actor_user_id=actor)
    assert s["consolidated"].get("person_source_links.person_id") == 1
    with engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM person_source_links WHERE source_contact_id=:s"),
                      {"s": sc}).scalar_one()
    assert n == 1                                           # one link kept, not duplicated


def test_household_relationship_conflict_consolidates(actor):
    survivor, dup = _person(), _person()
    hh = _household()
    _hh_rel(hh, survivor)
    _hh_rel(hh, dup)                                        # both in the same household
    s = merge_people(survivor, dup, reason="dedup hh", actor_user_id=actor)
    assert s["consolidated"].get("household_relationships.person_id") == 1
    with engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM household_relationships WHERE household_id=:h "
                           "AND person_id=:p"), {"h": hh, "p": survivor}).scalar_one()
    assert n == 1


def test_opportunity_participant_conflict_consolidates(actor):
    survivor, dup = _person(), _person()
    opp = _opportunity()
    _participant(opp, survivor)
    _participant(opp, dup)                                  # both participants of the same opportunity
    s = merge_people(survivor, dup, reason="dedup opp", actor_user_id=actor)
    assert s["consolidated"].get("opportunity_participants.person_id") == 1
    with engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM opportunity_participants WHERE opportunity_id=:o"),
                      {"o": opp}).scalar_one()
    assert n == 1


# --- survivorship ------------------------------------------------------------

def test_survivor_field_enrichment_fills_nulls(actor):
    survivor = _person(primary_email=None, primary_phone=None)
    dup = _person(primary_email="found@e.test", primary_phone="555-0100")
    s = merge_people(survivor, dup, reason="enrich", actor_user_id=actor)
    assert s["profile_filled"].get("primary_email") == "found@e.test"
    with engine.connect() as c:
        row = c.execute(text("SELECT primary_email, primary_phone FROM people WHERE id=:i"),
                        {"i": survivor}).mappings().first()
    assert row["primary_email"] == "found@e.test" and row["primary_phone"] == "555-0100"


def test_populated_survivor_field_not_overwritten(actor):
    survivor = _person(primary_email="keep@e.test")
    dup = _person(primary_email="other@e.test")
    prev = preview_person_merge(survivor, dup)
    assert any("survivor keeps 'keep@e.test'" in w for w in prev["warnings"])
    merge_people(survivor, dup, reason="no overwrite", actor_user_id=actor)
    with engine.connect() as c:
        email = c.execute(text("SELECT primary_email FROM people WHERE id=:i"),
                          {"i": survivor}).scalar_one()
    assert email == "keep@e.test"                          # survivor value preserved


# --- blockers ----------------------------------------------------------------

def test_blocker_when_both_own_relationship_entity(actor):
    survivor, dup = _person(), _person()
    _relationship_entity(survivor)
    _relationship_entity(dup)                               # unique(person_id) → cannot both point to one
    prev = preview_person_merge(survivor, dup)
    assert prev["safe_to_merge"] is False and prev["blockers"]
    with pytest.raises(MergeBlocked):
        merge_people(survivor, dup, reason="conflict", actor_user_id=actor)
    assert _exists(dup)                                    # refused — duplicate untouched


# --- rollback / atomicity ----------------------------------------------------

def test_full_rollback_on_failure(actor, monkeypatch):
    survivor, dup = _person(), _person()
    sc = _source_contact()
    _link(dup, sc)

    def _boom(*a, **k):
        raise RuntimeError("event bus down")
    monkeypatch.setattr("app.services.events.publisher.publish_safe", _boom)

    with pytest.raises(RuntimeError):
        merge_people(survivor, dup, reason="will fail", actor_user_id=actor)
    # Everything rolled back: duplicate still exists, its link never moved, no history written.
    assert _exists(dup)
    with engine.connect() as c:
        owner = c.execute(text("SELECT person_id FROM person_source_links WHERE source_contact_id=:s"),
                          {"s": sc}).scalar_one()
    assert owner == dup and len(_history(dup)) == 0


# --- history + event + audit -------------------------------------------------

def test_merge_history_records_snapshot_and_summary(actor):
    survivor = _person(primary_email=None)
    dup = _person(primary_email="fill@e.test")
    merge_people(survivor, dup, reason="history check", actor_user_id=actor)
    rows = _history(dup)
    assert len(rows) == 1
    h = rows[0]
    assert h["survivor_person_id"] == survivor and h["reason"] == "history check"
    assert h["actor_user_id"] == actor
    assert h["pre_merge_snapshot"]["duplicate"]["primary_email"] == "fill@e.test"
    assert "profile_filled" in h["merge_summary"]


def test_event_and_audit_written(actor, monkeypatch):
    survivor, dup = _person(), _person()
    calls = []
    import app.services.events.publisher as pub
    orig = pub.publish_safe

    def _spy(event_type, payload=None, **kw):
        calls.append((event_type, payload))
        return orig(event_type, payload, **kw)
    monkeypatch.setattr(pub, "publish_safe", _spy)

    merge_people(survivor, dup, reason="event+audit", actor_user_id=actor)
    assert any(et == "people.person_merged" and p.get("merged_person_id") == dup for et, p in calls)
    with engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM audit_events WHERE action='person.merged' "
                           "AND entity_id=:e"), {"e": str(survivor)}).scalar_one()
    assert n >= 1


# --- dry-run + idempotent refusal --------------------------------------------

def test_dry_run_makes_no_changes(actor):
    survivor, dup = _person(), _person()
    sc = _source_contact()
    _link(dup, sc)
    report = merge_people(survivor, dup, reason="dry", actor_user_id=actor, dry_run=True)
    assert report["dry_run"] is True and report["applied"] is False
    assert _exists(dup) and len(_history(dup)) == 0        # nothing mutated
    with engine.connect() as c:
        owner = c.execute(text("SELECT person_id FROM person_source_links WHERE source_contact_id=:s"),
                          {"s": sc}).scalar_one()
    assert owner == dup


def test_idempotent_refusal_when_duplicate_gone(actor):
    survivor = _person()
    with pytest.raises(ValueError):
        merge_people(survivor, 99999999, reason="gone", actor_user_id=actor)
    with pytest.raises(ValueError):                        # identical ids rejected too
        merge_people(survivor, survivor, reason="self", actor_user_id=actor)


# --- deployment-order safety: preview works before pmh01; applied merge refuses ---------------

def test_preview_works_and_merge_refuses_without_pmh01(actor):
    """Regression (PR #185): the read-only preview must run against a schema that has NOT applied
    pmh01, and an applied merge must refuse clearly. We rename person_merge_history away so a fresh
    connection genuinely sees the pre-pmh01 schema, then restore it."""
    survivor, dup = _person(), _person()
    sc = _source_contact()
    _link(dup, sc)
    with engine.begin() as c:
        c.execute(text("ALTER TABLE person_merge_history RENAME TO person_merge_history_bak"))
    try:
        report = preview_person_merge(survivor, dup)          # no history table present
        assert report["safe_to_merge"] is True                # preview does not depend on pmh01
        assert report["source_links_would_move"] == 1
        with pytest.raises(MergeBlocked, match="pmh01"):      # applied merge refuses clearly
            merge_people(survivor, dup, reason="no pmh01", actor_user_id=actor)
        assert _exists(survivor) and _exists(dup)             # refused → nothing mutated
        with engine.connect() as c:
            owner = c.execute(text("SELECT person_id FROM person_source_links WHERE source_contact_id=:s"),
                              {"s": sc}).scalar_one()
        assert owner == dup                                   # duplicate's link never moved
    finally:
        with engine.begin() as c:
            c.execute(text("ALTER TABLE person_merge_history_bak RENAME TO person_merge_history"))
