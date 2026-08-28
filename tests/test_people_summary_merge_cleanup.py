"""Regression: a person merge must retire the merged person's rm_people_summary row for good.

The defect: merge_people deleted the row inside the merge transaction, but people.summary is
rebuilt FROM THE EVENT STREAM. Any backlogged or replayed event for the retired person is applied
after that delete, and both projection helpers INSERT when the row is absent — so the row came
back. The durable fix retires the row in the projection handler, keyed on merged_person_id, so
every process/rebuild/replay reaches the same state.

Everything here is synthetic. No production people, ids, names or dates.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from app.db import engine, people, users
from app.services.events import publisher
from app.services.person_merge import merge_people
from app.services.projections import engine as projection_engine

_TAG = "RMCLEAN"


@pytest.fixture
def actor():
    with engine.begin() as c:
        t = uuid.uuid4().hex[:8]
        return c.execute(users.insert().values(
            email=f"rc{t}@e.test", normalized_email=f"rc{t}@e.test", display_name="Merger",
            status="active").returning(users.c.id)).scalar_one()


def _person(**over):
    vals = {"first_name": "A", "last_name": _TAG,
            "full_name": f"A {_TAG} {uuid.uuid4().hex[:6]}", "active": True}
    vals.update(over)
    with engine.begin() as c:
        return c.execute(people.insert().values(**vals).returning(people.c.id)).scalar_one()


def _summary_for(person_id):
    """Put a REAL summary row there the only supported way: a subscribed event, then process."""
    with engine.begin() as c:
        publisher.publish("people.person_created", {"person_id": person_id,
                                                    "match_method": "manual"},
                          conn=c, producer="people.promotion",
                          subject_ref=f"person:{person_id}")
    projection_engine.process("people.summary")


def _row(person_id):
    with engine.connect() as c:
        return c.execute(text("SELECT * FROM rm_people_summary WHERE person_id = :p"),
                         {"p": person_id}).mappings().one_or_none()


def _merge(survivor, duplicate, actor_id):
    return merge_people(survivor, duplicate, reason=f"{_TAG} test", actor_user_id=actor_id)


# --- the reported defect --------------------------------------------------------------------

def test_the_merged_persons_summary_row_is_removed_and_the_survivors_remains(actor):
    survivor, duplicate = _person(), _person()
    _summary_for(survivor)
    _summary_for(duplicate)
    assert _row(survivor) is not None and _row(duplicate) is not None, "precondition: both exist"

    _merge(survivor, duplicate, actor)

    assert _row(duplicate) is None, "the retired person's read-model row must be gone"
    survivor_row = _row(survivor)
    assert survivor_row is not None, "the survivor's row must remain"
    assert survivor_row["merge_count"] == 1
    assert survivor_row["last_event_type"] == "people.identity_merged"


def test_a_backlogged_event_for_the_retired_person_does_not_resurrect_the_row(actor):
    """The exact production ordering: unprocessed events for the duplicate exist at merge time."""
    survivor, duplicate = _person(), _person()
    # Published but NOT projected — this is the backlog that the post-merge drain replays.
    with engine.begin() as c:
        for pid in (duplicate, survivor):
            publisher.publish("people.person_created", {"person_id": pid, "match_method": "manual"},
                              conn=c, producer="people.promotion", subject_ref=f"person:{pid}")
    assert _row(duplicate) is None, "precondition: the backlog has not been drained yet"

    _merge(survivor, duplicate, actor)

    assert _row(duplicate) is None, \
        "draining the backlog after the merge must not re-create the retired person's row"
    assert _row(survivor) is not None


def test_the_identity_merged_event_is_actually_persisted_with_the_retired_id(actor):
    """publish_safe swallows EventError, so a contract violation would fail silently."""
    survivor, duplicate = _person(), _person()
    _merge(survivor, duplicate, actor)
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT payload->'payload' AS p FROM outbox_events "
            "WHERE name = 'people.identity_merged'")).mappings().all()
    matching = [r["p"] for r in rows if r["p"].get("person_id") == survivor]
    assert len(matching) == 1, "the subscribed event must reach the outbox, not be swallowed"
    assert matching[0]["merged_person_id"] == duplicate


# --- idempotency / replay -------------------------------------------------------------------

@pytest.mark.parametrize("again", ["process", "rebuild", "replay"])
def test_reprocessing_never_recreates_the_retired_row(actor, again):
    survivor, duplicate = _person(), _person()
    _summary_for(survivor)
    _summary_for(duplicate)
    _merge(survivor, duplicate, actor)

    getattr(projection_engine, again)("people.summary")

    assert _row(duplicate) is None, f"{again} must reach the same state, not resurrect the row"
    assert _row(survivor) is not None


def test_the_delete_is_a_no_op_when_the_retired_row_is_already_absent(actor):
    survivor, duplicate = _person(), _person()
    _summary_for(survivor)          # duplicate deliberately has NO summary row
    assert _row(duplicate) is None

    _merge(survivor, duplicate, actor)
    projection_engine.process("people.summary")

    assert _row(duplicate) is None
    assert _row(survivor)["merge_count"] == 1


def test_a_survivor_without_a_prior_summary_still_gains_one(actor):
    survivor, duplicate = _person(), _person()
    _summary_for(duplicate)         # only the duplicate has a row
    assert _row(survivor) is None

    _merge(survivor, duplicate, actor)

    assert _row(duplicate) is None
    assert _row(survivor) is not None, "the merge event must create the survivor's row"


# --- blast radius ---------------------------------------------------------------------------

def test_no_unrelated_persons_summary_is_touched(actor):
    survivor, duplicate, bystander = _person(), _person(), _person()
    _summary_for(survivor)
    _summary_for(duplicate)
    _summary_for(bystander)
    before = dict(_row(bystander))

    _merge(survivor, duplicate, actor)
    projection_engine.rebuild("people.summary")

    after = _row(bystander)
    assert after is not None, "an unrelated person's row must never be deleted"
    assert after["person_id"] == before["person_id"]


def test_a_source_contact_only_merge_retires_nobody(actor):
    """merge_source_contacts publishes the same event with merged_person_id=None."""
    person = _person()
    _summary_for(person)
    with engine.begin() as c:
        publisher.publish("people.identity_merged",
                          {"person_id": person, "source_contact_count": 2,
                           "merged_person_id": None},
                          conn=c, producer="people.merge", subject_ref=f"person:{person}")
    projection_engine.process("people.summary")

    assert _row(person) is not None, "a null merged_person_id must delete nothing"
    assert _row(person)["merge_count"] == 1


def test_a_malformed_event_naming_the_survivor_cannot_delete_the_survivor(actor):
    person = _person()
    _summary_for(person)
    with engine.begin() as c:
        publisher.publish("people.identity_merged",
                          {"person_id": person, "source_contact_count": 0,
                           "merged_person_id": person},
                          conn=c, producer="people.merge", subject_ref=f"person:{person}")
    projection_engine.process("people.summary")

    assert _row(person) is not None, "the survivor must never delete its own row"


# --- failure / rollback -----------------------------------------------------------------------

def test_a_rolled_back_merge_leaves_the_projection_logically_valid(actor):
    survivor, duplicate = _person(), _person()
    _summary_for(survivor)
    _summary_for(duplicate)

    with pytest.raises(ValueError):
        merge_people(survivor, 9_000_000 + int(uuid.uuid4().int % 100_000),
                     reason=f"{_TAG} rollback", actor_user_id=actor)

    # Nothing was merged, so both people still exist and both rows must still be there.
    assert _row(survivor) is not None and _row(duplicate) is not None
    with engine.connect() as c:
        # Scoped to THIS survivor: the shared test database accumulates sibling events.
        assert c.execute(text(
            "SELECT count(*) FROM outbox_events WHERE name = 'people.identity_merged' "
            "AND (payload->'payload'->>'person_id')::int = :s"), {"s": survivor}).scalar() == 0


def test_a_projection_failure_after_commit_is_recoverable_by_later_processing(actor, monkeypatch):
    """The merge is authoritative; the read model may lag but must converge on the next run."""
    survivor, duplicate = _person(), _person()
    _summary_for(survivor)
    _summary_for(duplicate)

    def _boom(*a, **k):
        raise RuntimeError("projection unavailable")

    monkeypatch.setattr(projection_engine, "process", _boom)
    summary = _merge(survivor, duplicate, actor)
    assert summary.get("projection_warning"), "the merge must report, not fail"
    monkeypatch.undo()

    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM people WHERE id = :d"),
                         {"d": duplicate}).scalar() == 0, "the merge itself still committed"

    projection_engine.process("people.summary")
    assert _row(duplicate) is None, "a later run must converge to the correct state"
    assert _row(survivor)["merge_count"] == 1


# --- historical compatibility -----------------------------------------------------------------
#
# Why the tests above all passed while production acceptance failed: every one of them creates its
# events by RUNNING the current merge code, so every identity_merged they produce already carries
# merged_person_id. None of them ever saw an event emitted by the previous release. These tests
# build the legacy envelope by hand, which is the only way to exercise that path.

def _legacy_event(name, payload, subject):
    """An outbox row shaped exactly as the pre-merged_person_id release wrote it."""
    envelope = {"event_type": name, "payload": payload, "event_id": str(uuid.uuid4()),
                "schema_version": 1, "occurred_at": "2026-08-01T00:00:00+00:00",
                "correlation_id": None, "causation_id": None, "subject_ref": f"person:{subject}",
                "producer": "people.merge", "metadata": {}}
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO outbox_events (event_id, name, payload, status, attempts, available_at,"
            " created_at) VALUES (:i, :n, CAST(:p AS json), 'pending', 0, now(), now())"),
            {"i": envelope["event_id"], "n": name, "p": json.dumps(envelope)})


def _retire_authoritatively(survivor, duplicate):
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO person_merge_history (survivor_person_id, merged_person_id, reason,"
            " merge_method, pre_merge_snapshot, merge_summary, created_at)"
            " VALUES (:s, :d, 'legacy', 'governed', '{}', '{}', now())"),
            {"s": survivor, "d": duplicate})
        c.execute(text("DELETE FROM people WHERE id = :d"), {"d": duplicate})


def _legacy_merge(survivor, duplicate):
    """The exact event pair the previous release emitted: person_merged carries the retired id,
    identity_merged does NOT."""
    _legacy_event("people.person_merged",
                  {"survivor_person_id": survivor, "merged_person_id": duplicate}, survivor)
    _legacy_event("people.identity_merged",
                  {"person_id": survivor, "source_contact_count": 6}, survivor)
    _retire_authoritatively(survivor, duplicate)


def test_a_legacy_merge_without_merged_person_id_still_retires_the_row():
    """The production defect: identity_merged predates the field, so the row must be retired via
    people.person_merged, which has carried the id since the original merge engine."""
    survivor, duplicate = _person(), _person()
    _summary_for(survivor)
    _summary_for(duplicate)
    _legacy_merge(survivor, duplicate)

    projection_engine.rebuild("people.summary")

    assert _row(duplicate) is None, \
        "a full rebuild must retire a person merged before identity_merged carried the id"
    assert _row(survivor) is not None


def test_a_legacy_merge_converges_identically_under_process_rebuild_and_replay():
    survivor, duplicate = _person(), _person()
    _summary_for(survivor)
    _summary_for(duplicate)
    _legacy_merge(survivor, duplicate)

    seen = []
    for op_name in ("process", "rebuild", "replay", "rebuild"):
        getattr(projection_engine, op_name)("people.summary")
        row = _row(survivor)
        seen.append((op_name, _row(duplicate) is None, row["merge_count"], row["update_count"]))

    assert all(retired for _, retired, _, _ in seen), f"not deterministic: {seen}"
    assert {mc for _, _, mc, _ in seen} == {1}, \
        f"merge_count must not accumulate across replays: {seen}"


def test_replaying_a_legacy_merge_does_not_double_count_the_survivor():
    survivor, duplicate = _person(), _person()
    _summary_for(survivor)
    _legacy_merge(survivor, duplicate)
    projection_engine.rebuild("people.summary")
    first = _row(survivor)["merge_count"]

    projection_engine.replay("people.summary")

    assert _row(survivor)["merge_count"] == first == 1


def test_a_legacy_merge_leaves_unrelated_people_untouched():
    survivor, duplicate, bystander = _person(), _person(), _person()
    for pid in (survivor, duplicate, bystander):
        _summary_for(pid)
    _legacy_merge(survivor, duplicate)

    projection_engine.rebuild("people.summary")

    assert _row(bystander) is not None, "an unrelated person's row must never be deleted"
    assert _row(duplicate) is None


def test_person_merged_never_deletes_the_survivor_even_if_it_names_itself():
    person = _person()
    _summary_for(person)
    _legacy_event("people.person_merged",
                  {"survivor_person_id": person, "merged_person_id": person}, person)

    projection_engine.process("people.summary")

    assert _row(person) is not None, "the survivor must never delete its own row"


def test_person_merged_without_a_merged_id_deletes_nothing():
    person = _person()
    _summary_for(person)
    _legacy_event("people.person_merged", {"survivor_person_id": person}, person)

    projection_engine.process("people.summary")

    assert _row(person) is not None


def test_the_new_format_merge_still_works_end_to_end(actor):
    """The current release must keep converging through the normal merge path."""
    survivor, duplicate = _person(), _person()
    _summary_for(survivor)
    _summary_for(duplicate)

    _merge(survivor, duplicate, actor)
    projection_engine.rebuild("people.summary")

    assert _row(duplicate) is None
    assert _row(survivor)["merge_count"] == 1, "still exactly one, not doubled by two handlers"
