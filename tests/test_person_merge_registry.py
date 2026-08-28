"""The merge registry must stay complete as the schema grows.

``merge_people`` deletes the duplicate person at the end. Anything still pointing at that id is
either destroyed by a CASCADE, silently nulled by a SET NULL, or — for a SOFT reference with no
database constraint at all — left dangling with no error whatsoever. The registry is what prevents
that, so a new table with a person reference must be a deliberate decision, not an omission.

This test introspects the LIVE schema. It is the check that would have caught
``payroll_employees.person_id`` (a real FK, ON DELETE SET NULL, absent from the registry) and the
six soft references that carry no FK.
"""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import text

from app.db import engine

SERVICE = Path(__file__).resolve().parents[1] / "app" / "services" / "person_merge.py"

_ENTRY_RE = re.compile(
    r'\("([a-z_0-9]+)",\s*"([a-z_0-9]+)",\s*'
    r'"(simple|dedup|conflict_singular|block_if_present|read_model)"')

#: References the merge deliberately does NOT rewrite, each with the reason it is exempt.
_EXEMPT = {
    # Merge history must keep the RETIRED id verbatim — that is the whole point of the record, and
    # it is why the table carries no foreign key.
    ("person_merge_history", "merged_person_id"),
    ("person_merge_history", "survivor_person_id"),
}

_HARD_FK_SQL = """
SELECT tc.table_name, kcu.column_name, rc.delete_rule
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name
JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name
JOIN information_schema.referential_constraints rc ON rc.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND ccu.table_name = 'people' AND ccu.column_name = 'id'
"""

_SOFT_REF_SQL = """
SELECT table_name, column_name
FROM information_schema.columns
WHERE table_schema = 'public'
  AND (column_name LIKE '%person_id%' OR column_name LIKE '%_person')
"""


def _registry():
    return {(t, c): strategy for t, c, strategy in _ENTRY_RE.findall(SERVICE.read_text())}


def _hard_fks():
    with engine.connect() as conn:
        return {(r[0], r[1]): r[2] for r in conn.execute(text(_HARD_FK_SQL))}


def _person_columns():
    with engine.connect() as conn:
        return {(r[0], r[1]) for r in conn.execute(text(_SOFT_REF_SQL))}


def test_every_hard_foreign_key_to_people_is_in_the_registry():
    missing = sorted(set(_hard_fks()) - set(_registry()) - _EXEMPT)
    assert not missing, (
        "foreign keys to people.id with no merge strategy — a merge would CASCADE or SET NULL "
        f"them silently: {missing}")


def test_every_soft_person_reference_is_in_the_registry():
    """A column with no FK is the dangerous case: nothing in the database catches it."""
    soft = _person_columns() - set(_hard_fks())
    missing = sorted(soft - set(_registry()) - _EXEMPT)
    assert not missing, (
        "person references with NO database foreign key and no merge strategy — these dangle "
        f"silently after the duplicate is deleted: {missing}")


def test_the_dangerous_delete_rules_are_all_handled():
    """SET NULL and CASCADE are the rules that destroy data quietly rather than erroring."""
    registry = _registry()
    for (table, column), rule in _hard_fks().items():
        if rule in ("SET NULL", "CASCADE") and (table, column) not in _EXEMPT:
            assert (table, column) in registry, (
                f"{table}.{column} is ON DELETE {rule} and unhandled — deleting the duplicate "
                "would destroy or orphan it without raising")


def test_payroll_employees_is_covered():
    """Regression: this real FK (ON DELETE SET NULL) was missing and would have orphaned payroll."""
    assert ("payroll_employees", "person_id") in _registry()
    assert _hard_fks().get(("payroll_employees", "person_id")) == "SET NULL"


def test_the_previously_uncovered_soft_references_are_covered():
    registry = _registry()
    for table, column in (("person_notes", "person_id"),
                          ("person_permanent_notes", "person_id"),
                          ("drake_identity", "primary_person_id"),
                          ("drake_identity_match_candidates", "person_id"),
                          ("orchestration_instances", "person_id"),
                          ("rm_people_summary", "person_id")):
        assert (table, column) in registry, f"{table}.{column} is still uncovered"


def test_merge_history_stays_exempt_and_keeps_the_retired_id():
    """Rewriting history would erase the very lineage the redirect depends on."""
    registry = _registry()
    for entry in _EXEMPT:
        assert entry not in registry, f"{entry} must NOT be rewritten by a merge"


def test_each_strategy_matches_the_tables_real_uniqueness():
    """Strategies were chosen from the schema, not guessed. Pin the reasoning."""
    registry = _registry()
    # UNIQUE(person_id) → only one may exist, so a clash needs a human decision.
    assert registry[("person_permanent_notes", "person_id")] == "conflict_singular"
    # UNIQUE(identifier_hash, person_id) → reassign unless it would collide.
    assert registry[("drake_identity_match_candidates", "person_id")] == "dedup"
    # Derived projection with counters → discard, never move.
    assert registry[("rm_people_summary", "person_id")] == "read_model"
    # No unique index involves person_id on any of these.
    for table, column in (("payroll_employees", "person_id"), ("person_notes", "person_id"),
                          ("drake_identity", "primary_person_id"),
                          ("orchestration_instances", "person_id")):
        assert registry[(table, column)] == "simple"


def test_the_uniqueness_assumptions_still_hold_in_the_schema():
    """If someone adds UNIQUE(person_id) to a 'simple' table, the strategy becomes wrong."""
    sql = """
    SELECT array_to_string(array_agg(a.attname ORDER BY k.ord), ',')
    FROM pg_index ix
    JOIN pg_class t ON t.oid = ix.indrelid
    JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
    WHERE t.relname = :t AND ix.indisunique
    GROUP BY ix.indexrelid
    """
    with engine.connect() as conn:
        for table, column in (("payroll_employees", "person_id"), ("person_notes", "person_id"),
                              ("orchestration_instances", "person_id")):
            uniques = [row[0] for row in conn.execute(text(sql), {"t": table})]
            assert not any(column in u.split(",") for u in uniques), (
                f"{table} gained a UNIQUE index covering {column}; 'simple' is no longer safe "
                f"(indexes: {uniques})")
