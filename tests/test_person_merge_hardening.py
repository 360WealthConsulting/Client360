"""Hardening coverage for merge_people: newly registered references, provenance, entity rename,
re-import protection and the merged-person redirect.

Everything here uses synthetic records. No production people, ids, addresses or dates.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.db import engine, people, person_source_links, source_contacts, users
from app.services.person_merge import MergeBlocked, merge_people, preview_person_merge

_TAG = "PMHARD"


@pytest.fixture
def actor():
    with engine.begin() as c:
        t = uuid.uuid4().hex[:8]
        return c.execute(users.insert().values(
            email=f"h{t}@e.test", normalized_email=f"h{t}@e.test", display_name="Merger",
            status="active").returning(users.c.id)).scalar_one()


def _person(**over):
    vals = {"first_name": "A", "last_name": _TAG,
            "full_name": f"A {_TAG} {uuid.uuid4().hex[:6]}", "active": True}
    vals.update(over)
    with engine.begin() as c:
        return c.execute(people.insert().values(**vals).returning(people.c.id)).scalar_one()


def _pair():
    return _person(), _person()


def _sql(statement, **params):
    with engine.begin() as c:
        return c.execute(text(statement), params)


def _fetch(statement, **params):
    with engine.connect() as c:
        return c.execute(text(statement), params).mappings().all()


def _merge(survivor, duplicate, actor_id):
    return merge_people(survivor, duplicate, reason=f"{_TAG} test", actor_user_id=actor_id)


# --- newly registered HARD reference (was ON DELETE SET NULL and unhandled) ---------------------

def _payroll_employee(person_id):
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        org = c.execute(text(
            "INSERT INTO relationship_entities (entity_type, name, active) "
            "VALUES ('business', :n, true) RETURNING id"), {"n": f"Org {_TAG} {tag}"}).scalar_one()
        acct = c.execute(text(
            "INSERT INTO payroll_accounts (organization_id, status) "
            "VALUES (:o, 'active') RETURNING id"), {"o": org}).scalar_one()
        return c.execute(text(
            "INSERT INTO payroll_employees "
            "(payroll_account_id, organization_id, person_id, full_name, employment_status, "
            " compensation_period, retirement_plan_participant) "
            "VALUES (:a, :o, :p, :n, 'active', 'annual', false) RETURNING id"),
            {"a": acct, "o": org, "p": person_id, "n": f"Emp {_TAG} {tag}"}).scalar_one()


def test_a_payroll_employee_follows_the_survivor_and_is_never_orphaned(actor):
    survivor, duplicate = _pair()
    emp = _payroll_employee(duplicate)

    _merge(survivor, duplicate, actor)

    rows = _fetch("SELECT person_id FROM payroll_employees WHERE id = :e", e=emp)
    assert rows and rows[0]["person_id"] == survivor, \
        "the payroll link was nulled by ON DELETE SET NULL instead of reassigned"


# --- newly registered SOFT references (no database FK to catch them) ---------------------------

def test_client_notes_follow_the_survivor(actor):
    survivor, duplicate = _pair()
    body = f"note {uuid.uuid4().hex[:8]}"
    _sql("INSERT INTO person_notes (person_id, note_type, body) VALUES (:p, 'general', :b)",
         p=duplicate, b=body)

    _merge(survivor, duplicate, actor)

    rows = _fetch("SELECT person_id FROM person_notes WHERE body = :b", b=body)
    assert rows and rows[0]["person_id"] == survivor, "a client note was orphaned"


def test_a_permanent_note_moves_when_only_the_duplicate_has_one(actor):
    survivor, duplicate = _pair()
    body = f"permanent {uuid.uuid4().hex[:8]}"
    _sql("INSERT INTO person_permanent_notes (person_id, body, source) VALUES (:p, :b, 'staff')",
         p=duplicate, b=body)

    _merge(survivor, duplicate, actor)

    rows = _fetch("SELECT person_id FROM person_permanent_notes WHERE body = :b", b=body)
    assert rows and rows[0]["person_id"] == survivor


def test_two_permanent_notes_block_the_merge_for_a_human_decision(actor):
    """UNIQUE(person_id): only one can survive, so the choice is not the machine's to make."""
    survivor, duplicate = _pair()
    for pid in (survivor, duplicate):
        _sql("INSERT INTO person_permanent_notes (person_id, body, source) "
             "VALUES (:p, :b, 'staff')", p=pid, b=f"perm {uuid.uuid4().hex[:6]}")

    report = preview_person_merge(survivor, duplicate)
    assert report["safe_to_merge"] is False
    assert any("person_permanent_notes" in b for b in report["blockers"])
    with pytest.raises(MergeBlocked):
        _merge(survivor, duplicate, actor)
    assert _fetch("SELECT id FROM people WHERE id = :p", p=duplicate), "duplicate was removed"


def test_drake_identity_follows_the_survivor(actor):
    survivor, duplicate = _pair()
    ident = uuid.uuid4().hex
    _sql("INSERT INTO drake_identity (identifier_hash, primary_person_id) VALUES (:h, :p)",
         h=ident, p=duplicate)

    _merge(survivor, duplicate, actor)

    rows = _fetch("SELECT primary_person_id FROM drake_identity WHERE identifier_hash = :h", h=ident)
    assert rows and rows[0]["primary_person_id"] == survivor


def test_drake_match_candidates_dedup_on_the_shared_identity(actor):
    """UNIQUE(identifier_hash, person_id): a collision must consolidate, not raise."""
    survivor, duplicate = _pair()
    shared, only_dup = uuid.uuid4().hex, uuid.uuid4().hex
    for pid in (survivor, duplicate):
        _sql("INSERT INTO drake_identity_match_candidates "
             "(identifier_hash, person_id, score, reasons, status) "
             "VALUES (:h, :p, 1, '{}', 'pending')", h=shared, p=pid)
    _sql("INSERT INTO drake_identity_match_candidates "
         "(identifier_hash, person_id, score, reasons, status) "
         "VALUES (:h, :p, 1, '{}', 'pending')", h=only_dup, p=duplicate)

    _merge(survivor, duplicate, actor)

    shared_rows = _fetch("SELECT person_id FROM drake_identity_match_candidates "
                         "WHERE identifier_hash = :h", h=shared)
    assert [r["person_id"] for r in shared_rows] == [survivor], "the unique key was corrupted"
    moved = _fetch("SELECT person_id FROM drake_identity_match_candidates "
                   "WHERE identifier_hash = :h", h=only_dup)
    assert moved and moved[0]["person_id"] == survivor


def test_orchestration_instances_follow_the_survivor(actor):
    survivor, duplicate = _pair()
    code = f"def-{uuid.uuid4().hex[:8]}"
    _sql("INSERT INTO orchestration_instances (definition_code, status, person_id) "
         "VALUES (:c, 'active', :p)", c=code, p=duplicate)

    _merge(survivor, duplicate, actor)

    rows = _fetch("SELECT person_id FROM orchestration_instances WHERE definition_code = :c", c=code)
    assert rows and rows[0]["person_id"] == survivor


def test_the_read_model_row_is_discarded_not_moved(actor):
    """rm_people_summary is a derived rollup; moving its counters would corrupt the survivor's."""
    survivor, duplicate = _pair()
    for pid, count in ((survivor, 5), (duplicate, 99)):
        _sql("INSERT INTO rm_people_summary (person_id, update_count) VALUES (:p, :n)",
             p=pid, n=count)

    summary = _merge(survivor, duplicate, actor)

    rows = _fetch("SELECT person_id, update_count FROM rm_people_summary WHERE person_id IN "
                  "(:s, :d)", s=survivor, d=duplicate)
    assert [(r["person_id"], r["update_count"]) for r in rows] == [(survivor, 5)], \
        "the duplicate's derived counters leaked onto the survivor"
    assert "rm_people_summary.person_id" in summary["consolidated"]


def test_a_lone_read_model_row_is_still_discarded_and_rebuilds(actor):
    survivor, duplicate = _pair()
    _sql("INSERT INTO rm_people_summary (person_id, update_count) VALUES (:p, 7)", p=duplicate)

    _merge(survivor, duplicate, actor)

    assert _fetch("SELECT id FROM rm_people_summary WHERE person_id = :p", p=duplicate) == []


# --- the final re-scan is the last line of defence ---------------------------------------------

def test_the_final_rescan_catches_a_table_missing_from_the_registry(actor, monkeypatch):
    """The failure that matters: a table nobody registered.

    The re-scan reads the SCHEMA, not the registry, so removing an entry does not blind it. The
    merge must refuse and leave the duplicate intact rather than delete it and orphan the rows."""
    from app.services import person_merge as pm

    survivor, duplicate = _pair()
    body = f"n {uuid.uuid4().hex[:6]}"
    _sql("INSERT INTO person_notes (person_id, note_type, body) VALUES (:p, 'general', :b)",
         p=duplicate, b=body)

    monkeypatch.setattr(pm, "_REGISTRY",
                        [e for e in pm._REGISTRY if e[0] != "person_notes"])

    with pytest.raises(MergeBlocked) as exc:
        pm.merge_people(survivor, duplicate, reason="rescan", actor_user_id=actor)

    assert "person_notes.person_id" in str(exc.value)
    assert _fetch("SELECT id FROM people WHERE id = :p", p=duplicate), \
        "the duplicate was deleted despite an unhandled reference"
    kept = _fetch("SELECT person_id FROM person_notes WHERE body = :b", b=body)
    assert kept and kept[0]["person_id"] == duplicate, "the note was damaged by a refused merge"


def test_merge_history_is_exempt_from_the_rescan(actor):
    """History keeps the retired id by design; scanning it would make every merge impossible."""
    from app.services.person_merge import _RESCAN_EXEMPT

    assert ("person_merge_history", "merged_person_id") in _RESCAN_EXEMPT
    survivor, duplicate = _pair()
    _merge(survivor, duplicate, actor)                # a second merge would fail if not exempt
    rows = _fetch("SELECT survivor_person_id FROM person_merge_history "
                  "WHERE merged_person_id = :d", d=duplicate)
    assert rows and rows[0]["survivor_person_id"] == survivor


# --- relationship entity display name ----------------------------------------------------------

def _person_entity(person_id, name):
    with engine.begin() as c:
        return c.execute(text(
            "INSERT INTO relationship_entities (entity_type, person_id, name, active) "
            "VALUES ('person', :p, :n, true) RETURNING id"), {"p": person_id, "n": name}).scalar_one()


def test_the_survivor_entity_name_is_refreshed_from_the_canonical_record(actor):
    survivor, duplicate = _pair()
    stale = f"Person {survivor}"                       # the snapshot fallback, as seen in production
    _person_entity(survivor, stale)

    summary = _merge(survivor, duplicate, actor)

    rows = _fetch("SELECT name FROM relationship_entities WHERE person_id = :p "
                  "AND entity_type = 'person'", p=survivor)
    assert rows and rows[0]["name"] != stale
    assert not rows[0]["name"].startswith("Person "), "an internal id remains the display name"
    assert rows[0]["name"] == summary["entity_display_name"]


def test_a_nameless_survivor_entity_reads_unnamed_person_never_person_id(actor):
    survivor = _person(full_name=None, first_name=None, last_name=None)
    duplicate = _person(full_name=None, first_name=None, last_name=None)
    _person_entity(survivor, f"Person {survivor}")

    _merge(survivor, duplicate, actor)

    rows = _fetch("SELECT name FROM relationship_entities WHERE person_id = :p "
                  "AND entity_type = 'person'", p=survivor)
    assert rows and rows[0]["name"] == "Unnamed person"


def test_other_entities_are_not_renamed(actor):
    survivor, duplicate = _pair()
    other = _person()
    other_name = f"Untouched {_TAG} {uuid.uuid4().hex[:6]}"
    _person_entity(other, other_name)
    _person_entity(survivor, f"Person {survivor}")

    _merge(survivor, duplicate, actor)

    rows = _fetch("SELECT name FROM relationship_entities WHERE person_id = :p", p=other)
    assert rows and rows[0]["name"] == other_name, "an unrelated entity was renamed"


# --- source-link provenance ---------------------------------------------------------------------

def _contact():
    with engine.begin() as c:
        return c.execute(source_contacts.insert().values(
            source_system="SyntheticCRM", source_file=f"{_TAG}.csv",
            source_hash=uuid.uuid4().hex, raw_data={}).returning(
            source_contacts.c.id)).scalar_one()


def _link(person_id, contact_id, *, method, score, confirmed):
    _sql("INSERT INTO person_source_links "
         "(person_id, source_contact_id, match_method, match_score, confirmed) "
         "VALUES (:p, :c, :m, :s, :f)",
         p=person_id, c=contact_id, m=method, s=score, f=confirmed)


def test_a_confirmed_duplicate_link_wins_over_an_unconfirmed_survivor_link(actor):
    survivor, duplicate = _pair()
    contact = _contact()
    _link(survivor, contact, method="auto_promote", score=50, confirmed=False)
    _link(duplicate, contact, method="human_confirmed", score=90, confirmed=True)

    summary = _merge(survivor, duplicate, actor)

    rows = _fetch("SELECT person_id, match_method, match_score, confirmed "
                  "FROM person_source_links WHERE source_contact_id = :c", c=contact)
    assert len(rows) == 1, "a duplicate source link was created"
    assert rows[0]["person_id"] == survivor
    assert rows[0]["confirmed"] is True and rows[0]["match_method"] == "human_confirmed"
    assert summary["source_link_provenance"], "the collision was not recorded"
    assert summary["source_link_provenance"][0]["kept_from"] == "duplicate"


def test_a_stronger_survivor_link_is_kept(actor):
    survivor, duplicate = _pair()
    contact = _contact()
    _link(survivor, contact, method="human_confirmed", score=95, confirmed=True)
    _link(duplicate, contact, method="auto_promote", score=40, confirmed=False)

    summary = _merge(survivor, duplicate, actor)

    rows = _fetch("SELECT match_method, confirmed FROM person_source_links "
                  "WHERE source_contact_id = :c", c=contact)
    assert len(rows) == 1
    assert rows[0]["confirmed"] is True and rows[0]["match_method"] == "human_confirmed"
    assert summary["source_link_provenance"][0]["kept_from"] == "survivor"


def test_both_links_are_preserved_in_merge_history(actor):
    survivor, duplicate = _pair()
    contact = _contact()
    _link(survivor, contact, method="auto_promote", score=50, confirmed=False)
    _link(duplicate, contact, method="human_confirmed", score=90, confirmed=True)

    _merge(survivor, duplicate, actor)

    rows = _fetch("SELECT merge_summary FROM person_merge_history "
                  "WHERE merged_person_id = :d", d=duplicate)
    blob = str(rows[-1]["merge_summary"])
    assert "human_confirmed" in blob and "auto_promote" in blob, \
        "provenance that existed before the merge was not recorded"


# --- re-import protection -----------------------------------------------------------------------

def test_promote_unlinked_cannot_recreate_the_retired_duplicate(actor):
    """person_source_links is the authoritative protection: a linked contact is never promoted."""
    from app.matching.promote import promote_unlinked

    survivor, duplicate = _pair()
    contact = _contact()
    _link(duplicate, contact, method="auto_promote", score=100, confirmed=False)

    _merge(survivor, duplicate, actor)

    linked = _fetch("SELECT person_id FROM person_source_links WHERE source_contact_id = :c",
                    c=contact)
    assert [r["person_id"] for r in linked] == [survivor]

    before = _fetch("SELECT count(*) AS n FROM people")[0]["n"]
    promote_unlinked(source_system="SyntheticCRM")
    after = _fetch("SELECT count(*) AS n FROM people")[0]["n"]

    assert after == before, "promote_unlinked recreated a person for an already-linked contact"
    still = _fetch("SELECT person_id FROM person_source_links WHERE source_contact_id = :c",
                   c=contact)
    assert [r["person_id"] for r in still] == [survivor]


# --- merged-person redirect ---------------------------------------------------------------------

def _principal(caps=("client.read", "record.read_all")):
    from app.security.models import Principal
    return Principal(0, "staff@example.com", "Staff", frozenset(caps))


def _client_request(person_id):
    from types import SimpleNamespace
    return SimpleNamespace(
        state=SimpleNamespace(request_id=f"req-{uuid.uuid4().hex[:6]}", principal=None,
                              demo_mode=False),
        client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"},
        query_params={}, session={}, url=SimpleNamespace(path=f"/client/{person_id}"))


def test_a_merged_person_url_redirects_to_the_survivor(actor):
    from app.routes.client360 import client_workspace

    survivor, duplicate = _pair()
    _merge(survivor, duplicate, actor)

    response = client_workspace(_client_request(duplicate), duplicate,
                                principal=_principal())

    assert response.status_code == 303
    assert response.headers["location"] == f"/client/{survivor}"


def test_a_person_who_was_never_merged_still_gets_the_normal_404():
    from app.routes.client360 import client_workspace

    response = client_workspace(_client_request(99_000_777), 99_000_777,
                                principal=_principal())
    assert response.status_code == 404
    assert "location" not in response.headers


def test_an_existing_but_out_of_scope_person_is_not_redirected(actor):
    """Only a RETIRED duplicate redirects — never an inactive or unreachable live record."""
    from app.routes.client360 import client_workspace

    live = _person()
    narrow = _principal(caps=("client.read",))        # no record.read_all

    response = client_workspace(_client_request(live), live, principal=narrow)

    assert response.status_code == 404, "a live person was redirected"


def test_the_redirect_is_withheld_when_the_survivor_is_out_of_scope(actor):
    """No existence oracle: an unreachable survivor keeps the ordinary 404."""
    from app.routes.client360 import client_workspace

    survivor, duplicate = _pair()
    _merge(survivor, duplicate, actor)
    narrow = _principal(caps=("client.read",))        # cannot see the survivor

    response = client_workspace(_client_request(duplicate), duplicate, principal=narrow)

    assert response.status_code == 404
    assert "location" not in response.headers


def test_an_ambiguous_lineage_does_not_redirect(actor):
    """Two survivors recorded for one retired id is not a decision this may make silently."""
    survivor_a, survivor_b = _pair()
    retired = 99_000_888
    for survivor in (survivor_a, survivor_b):
        _sql("INSERT INTO person_merge_history "
             "(survivor_person_id, merged_person_id, reason, merge_method) "
             "VALUES (:s, :d, 'synthetic', 'manual_review')", s=survivor, d=retired)
    from app.routes.client360 import client_workspace

    response = client_workspace(_client_request(retired), retired, principal=_principal())
    assert response.status_code == 404


# --- rm_people_summary read-model lifecycle ------------------------------------------------------
#
# rm_people_summary is a DISPOSABLE projection rebuilt from outbox_events by
# app/services/projections/engine.py. Its people.summary definition subscribes to
# people.person_created / person_updated / identity_merged — and merge_people published
# people.person_merged, which NOTHING subscribes to. A merge therefore left the survivor's row
# untouched, or absent entirely, with no later tick or rebuild ever correcting it.

_SUMMARY = "SELECT person_id, merge_count, last_event_type FROM rm_people_summary WHERE person_id = :p"


def _summary(person_id):
    rows = _fetch(_SUMMARY, p=person_id)
    return dict(rows[0]) if rows else None


def test_the_survivor_projection_exists_and_records_the_merge(actor):
    """Neither person has a row: the merge must still leave the survivor represented."""
    survivor, duplicate = _pair()
    assert _summary(survivor) is None

    result = _merge(survivor, duplicate, actor)

    row = _summary(survivor)
    assert row is not None, "the survivor has no read-model row after the merge"
    assert row["merge_count"] == 1
    assert row["last_event_type"] == "people.identity_merged"
    assert result["projection_refreshed"]["projection"] == "people.summary"


def test_a_duplicate_only_projection_is_deleted_and_the_survivor_gains_one(actor):
    survivor, duplicate = _pair()
    _sql("INSERT INTO rm_people_summary (person_id, update_count) VALUES (:p, 4)", p=duplicate)

    _merge(survivor, duplicate, actor)

    assert _summary(duplicate) is None, "the retired projection row survived"
    assert _summary(survivor) is not None, "the survivor was left without a row"


def test_a_survivor_only_projection_is_updated_not_left_stale(actor):
    survivor, duplicate = _pair()
    _sql("INSERT INTO rm_people_summary (person_id, update_count, merge_count) "
         "VALUES (:p, 3, 0)", p=survivor)

    _merge(survivor, duplicate, actor)

    row = _summary(survivor)
    assert row["merge_count"] == 1, "the survivor's row is stale — the merge is invisible"
    assert row["last_event_type"] == "people.identity_merged"


def test_both_projections_present_survivor_keeps_its_own_counters(actor):
    """The duplicate's counters must never be carried onto the survivor."""
    survivor, duplicate = _pair()
    _sql("INSERT INTO rm_people_summary (person_id, update_count, merge_count) "
         "VALUES (:p, 2, 0)", p=survivor)
    _sql("INSERT INTO rm_people_summary (person_id, update_count, merge_count) "
         "VALUES (:p, 99, 99)", p=duplicate)

    _merge(survivor, duplicate, actor)

    row = _fetch("SELECT update_count, merge_count FROM rm_people_summary WHERE person_id = :p",
                 p=survivor)[0]
    assert row["update_count"] == 2, "the duplicate's update counter leaked onto the survivor"
    assert row["merge_count"] == 1, "the merge was not counted exactly once"
    assert _summary(duplicate) is None


def test_the_merge_fact_is_in_the_outbox_so_a_rebuild_reproduces_it(actor):
    """Determinism: the read model must be reconstructable from events, not only from this call."""
    survivor, duplicate = _pair()
    _merge(survivor, duplicate, actor)

    # outbox_events stores the type in `name`; the envelope (subject_ref) lives inside `payload`.
    rows = _fetch("SELECT payload FROM outbox_events WHERE name = 'people.identity_merged'")
    subjects = [str(r["payload"].get("subject_ref")) for r in rows]
    assert f"person:{survivor}" in subjects, \
        "the subscribed event was not published — a rebuild would lose the merge"


def test_the_projection_is_refreshed_through_the_existing_engine(actor):
    """No projection logic is reimplemented inside person_merge."""
    import inspect

    from app.services import person_merge as pm

    src = inspect.getsource(pm.merge_people)
    assert "projection_engine.process(\"people.summary\")" in src
    assert "rm_people_summary" not in src, "person_merge builds the read model itself"


def test_canonical_field_fill_is_reflected_and_the_survivor_row_follows(actor):
    """A merge that changes the survivor's canonical profile still leaves one coherent row."""
    survivor = _person(full_name=None, first_name=None, last_name=None)
    duplicate = _person(full_name=f"Filled {_TAG} {uuid.uuid4().hex[:6]}")

    _merge(survivor, duplicate, actor)

    people_rows = _fetch("SELECT full_name FROM people WHERE id = :p", p=survivor)
    assert people_rows[0]["full_name"].startswith("Filled "), "the null field was not filled"
    assert _summary(survivor) is not None
    assert _summary(duplicate) is None


def test_a_blocked_merge_leaves_the_projection_untouched(actor):
    """Rollback safety: a refused merge must not delete or create any read-model row."""
    survivor, duplicate = _pair()
    for pid in (survivor, duplicate):
        _sql("INSERT INTO person_permanent_notes (person_id, body, source) "
             "VALUES (:p, :b, 'staff')", p=pid, b=f"perm {uuid.uuid4().hex[:6]}")
    _sql("INSERT INTO rm_people_summary (person_id, update_count) VALUES (:p, 8)", p=duplicate)

    with pytest.raises(MergeBlocked):
        _merge(survivor, duplicate, actor)

    row = _fetch("SELECT update_count FROM rm_people_summary WHERE person_id = :p", p=duplicate)
    assert row and row[0]["update_count"] == 8, \
        "a refused merge still destroyed the duplicate's projection row"
    assert _summary(survivor) is None, "a refused merge created a survivor row"


def test_the_projection_delete_rolls_back_with_a_failed_merge(actor, monkeypatch):
    """The read-model delete lives INSIDE the merge transaction, so a later failure undoes it."""
    from app.services import person_merge as pm

    survivor, duplicate = _pair()
    _sql("INSERT INTO rm_people_summary (person_id, update_count) VALUES (:p, 6)", p=duplicate)

    def _boom(conn, survivor_id):
        raise RuntimeError("synthetic failure after the projection delete")

    monkeypatch.setattr(pm, "_refresh_person_entity_name", _boom)
    with pytest.raises(RuntimeError):
        pm.merge_people(survivor, duplicate, reason="rollback", actor_user_id=actor)

    row = _fetch("SELECT update_count FROM rm_people_summary WHERE person_id = :p", p=duplicate)
    assert row and row[0]["update_count"] == 6, "the projection delete was not rolled back"
    assert _fetch("SELECT id FROM people WHERE id = :p", p=duplicate), "the duplicate was deleted"
