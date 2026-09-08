"""Non-destructive rebuild of ``drake_identity``.

THE DEFECT THIS REPLACES

``scripts/build_drake_identity.py`` rebuilt the table with ``DELETE FROM drake_identity`` followed by
an ``INSERT ... SELECT`` whose column list was ``identifier_hash, first_year, last_year,
return_count, taxpayer_name, spouse_name, confidence``. ``primary_person_id`` was not in that list,
so every run discarded the person linkage on all 1,802 identities -- 931 of them linked at the time
of writing, including links established by human adjudication through the review queue and by
individually authorised manual repair. Nothing warned, and the table looked freshly built.

WHICH FIELDS ARE REBUILDABLE AND WHICH ARE NOT

The table mixes two kinds of column, and the old statement treated them alike:

* Derived from Drake source data, and safe to recompute on every run --
  ``first_year``, ``last_year``, ``return_count``, ``taxpayer_name``, ``spouse_name``.
  These are aggregates over the Drake ``source_contacts`` rows for one identifier hash. Recomputing
  them is the entire point of a rebuild.

* Persistent state that Drake does not know about, and that no rebuild may invent or discard --
  ``primary_person_id`` (linkage: written by the identity review approval route, by household
  remediation, by the person-merge registry, and by authorised manual repair),
  ``confidence`` (adjudication: this builder writes NULL because grouping contacts by hash resolves
  nobody; the review path later writes the evaluator's evidence-derived score), and
  ``created_at`` (when the identity was first observed -- resetting it rewrites history).

``identifier_hash`` is the primary key and the identity itself.

WHY UPSERT, AND NOT A SNAPSHOT-AND-RESTORE

Keying the upsert on ``identifier_hash`` -- already the primary key -- makes the safety property
structural rather than procedural: persistent state is never detached from its row, so it cannot be
restored onto the wrong identifier. A snapshot/delete/restore design would reintroduce the very
window this module removes, and would depend on the restore step being correct to avoid transferring
a link between identifiers. A staging table would need schema this fix does not require.

IDENTITIES THAT DISAPPEAR FROM SOURCE DATA ARE RETAINED, NOT DELETED

An upsert alone never removes anything, which is deliberate. Deleting an identity that has gone from
the source is exactly the destructive behaviour being removed here, and it is unrecoverable when the
identity carries a person link. ``drake_identity`` has no status or tombstone column and this fix
does not add one, so a stale identity is kept and reported: unlinked ones as information, linked ones
called out separately because a linked identity vanishing from source is a data-quality signal a
human should see. Whether such rows should eventually be retired is a schema question, not a rebuild
question.

FAIL-CLOSED INVARIANTS

The rebuild runs in one transaction and raises -- rolling everything back -- if the source yields no
identities at all (a truncated or failed import must not be mistaken for an empty world), if any
pre-existing identity's persistent state changed, or if any identity would be left pointing at a
person id that does not exist. ``drake_identity.primary_person_id`` still has no foreign key; that
belongs to the later schema work, so the check is enforced here instead of by the database.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text

from app.db import engine

#: Recomputed from Drake source data on every rebuild.
DERIVED_COLUMNS = ("first_year", "last_year", "return_count", "taxpayer_name", "spouse_name")

#: Never written by a rebuild. Linkage, adjudication and first-observed history.
PERSISTENT_COLUMNS = ("primary_person_id", "confidence", "created_at")

_SOURCE_IDENTITIES = """
    SELECT
        raw_data->>'identifier_hash'                                   AS identifier_hash,
        MIN((raw_data->>'tax_year')::integer)                          AS first_year,
        MAX((raw_data->>'tax_year')::integer)                          AS last_year,
        COUNT(*)                                                       AS return_count,
        MAX(CASE WHEN raw_data->>'role' = 'taxpayer' THEN full_name END) AS taxpayer_name,
        MAX(CASE WHEN raw_data->>'role' = 'spouse'   THEN full_name END) AS spouse_name
    FROM source_contacts
    WHERE source_system = 'Drake'
      AND raw_data->>'identifier_hash' IS NOT NULL
    GROUP BY raw_data->>'identifier_hash'
"""

# confidence is listed in the INSERT (NULL for a genuinely new identity) but is absent from the
# DO UPDATE, so an adjudicated score survives. created_at is absent from both: the column default
# stamps a new row and an existing row keeps the timestamp it already had.
_UPSERT = f"""
    INSERT INTO drake_identity (
        identifier_hash, first_year, last_year, return_count,
        taxpayer_name, spouse_name, confidence
    )
    SELECT
        s.identifier_hash, s.first_year, s.last_year, s.return_count,
        s.taxpayer_name, s.spouse_name, NULL
    FROM ({_SOURCE_IDENTITIES}) AS s
    ON CONFLICT (identifier_hash) DO UPDATE SET
        first_year    = EXCLUDED.first_year,
        last_year     = EXCLUDED.last_year,
        return_count  = EXCLUDED.return_count,
        taxpayer_name = EXCLUDED.taxpayer_name,
        spouse_name   = EXCLUDED.spouse_name
"""

_PERSISTENT_STATE = """
    SELECT identifier_hash, primary_person_id, confidence, created_at
    FROM drake_identity
"""

_DANGLING = """
    SELECT d.identifier_hash, d.primary_person_id
    FROM drake_identity d
    WHERE d.primary_person_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM people p WHERE p.id = d.primary_person_id)
    ORDER BY d.identifier_hash
"""


class RebuildRefused(RuntimeError):
    """An invariant could not be maintained. The transaction must not commit."""


@dataclass
class RebuildReport:
    source_identities: int = 0
    existing_before: int = 0
    inserted: int = 0
    refreshed: int = 0
    total_after: int = 0
    links_preserved: int = 0
    stale_retained: list[str] = field(default_factory=list)
    stale_retained_linked: list[str] = field(default_factory=list)

    @property
    def stale_total(self) -> int:
        return len(self.stale_retained) + len(self.stale_retained_linked)

    def lines(self) -> list[str]:
        out = [
            f"source identities            {self.source_identities}",
            f"identities before            {self.existing_before}",
            f"inserted (new)               {self.inserted}",
            f"refreshed (existing)         {self.refreshed}",
            f"identities after             {self.total_after}",
            f"person links preserved       {self.links_preserved}",
            f"stale retained (unlinked)    {len(self.stale_retained)}",
            f"stale retained (LINKED)      {len(self.stale_retained_linked)}",
        ]
        for identifier_hash in self.stale_retained_linked:
            out.append(f"  REVIEW: {identifier_hash} is linked but absent from source data")
        return out


def rebuild_drake_identities(connection) -> RebuildReport:
    """Refresh derived identity fields in place, preserving all persistent state.

    Runs entirely within ``connection``'s transaction. Raises ``RebuildRefused`` without writing
    anything the caller commits if an invariant cannot be held.
    """
    before = {
        row["identifier_hash"]: dict(row)
        for row in connection.execute(text(_PERSISTENT_STATE)).mappings()
    }
    report = RebuildReport(existing_before=len(before))

    source_hashes = {
        row[0] for row in connection.execute(text(
            f"SELECT identifier_hash FROM ({_SOURCE_IDENTITIES}) AS s"))
    }
    report.source_identities = len(source_hashes)
    if not source_hashes:
        raise RebuildRefused(
            "Drake source contacts yielded no identities. A truncated or failed import must not be "
            "mistaken for an empty world; refusing to rebuild.")

    report.inserted = len(source_hashes - before.keys())
    report.refreshed = len(source_hashes & before.keys())

    connection.execute(text(_UPSERT))

    for identifier_hash in sorted(before.keys() - source_hashes):
        if before[identifier_hash]["primary_person_id"] is None:
            report.stale_retained.append(identifier_hash)
        else:
            report.stale_retained_linked.append(identifier_hash)

    after = {
        row["identifier_hash"]: dict(row)
        for row in connection.execute(text(_PERSISTENT_STATE)).mappings()
    }
    report.total_after = len(after)
    report.links_preserved = sum(1 for r in after.values() if r["primary_person_id"] is not None)

    missing = before.keys() - after.keys()
    if missing:
        raise RebuildRefused(
            f"{len(missing)} identity row(s) disappeared during the rebuild: "
            f"{sorted(missing)[:5]}")

    changed = [
        (identifier_hash, column, before[identifier_hash][column], after[identifier_hash][column])
        for identifier_hash in before
        for column in PERSISTENT_COLUMNS
        if before[identifier_hash][column] != after[identifier_hash][column]
    ]
    if changed:
        raise RebuildRefused(
            f"a rebuild changed persistent state on {len(changed)} column value(s); "
            f"first: {changed[0]}")

    expected_total = len(before.keys() | source_hashes)
    if report.total_after != expected_total:
        raise RebuildRefused(
            f"expected {expected_total} identities after the rebuild, found {report.total_after}")

    dangling = connection.execute(text(_DANGLING)).all()
    if dangling:
        raise RebuildRefused(
            f"{len(dangling)} identity row(s) would point at a person that does not exist; "
            f"first: {dangling[0][0]} -> person {dangling[0][1]}")

    return report


def main() -> RebuildReport:
    """Run one rebuild in its own transaction. Any refusal rolls the whole rebuild back."""
    with engine.begin() as connection:
        return rebuild_drake_identities(connection)
