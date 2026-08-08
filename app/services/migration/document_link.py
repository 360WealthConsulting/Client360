"""Document canonical-link REPAIR — first guarded document-link APPLY (deterministic set only).

Sets the canonical link columns on ``documents`` for the APPROVED deterministic linkage set from a linkage
remediation preview's ``reconciliation.csv``:

    resolution "people"     -> documents.person_id       (FK people)
    resolution "households" -> documents.household_id     (FK households)
    resolution "businesses" -> documents.organization_id  (FK relationship_entities)

Scope is FROZEN to the approved reconciliation: ambiguous and unmatched documents are never touched, and a
document that has become newly resolvable since approval is never added. It updates ONLY those three link
columns — it does NOT move bytes, change ``storage_uri``, or touch ``document_sources``.

Guards (fail-closed BEFORE any write): explicit confirm, a verified DB backup, matching approved expected
counts, and NO drift — every approved document must exist and its link column must be either NULL (to be
set) or already equal to the approved entity; any conflicting value, missing document, or missing target
entity aborts the whole run. Idempotent; full before/after reconciliation.
"""
from __future__ import annotations

import csv
import os
from collections import Counter
from dataclasses import dataclass, field

from app.services.migration.base import MigrationJob, Mode, Outcome
from app.services.migration.canonical_repair import RepairGuardError

# resolution kind -> (documents link column, target entity table)
_KIND = {
    "people": ("person_id", "people"),
    "households": ("household_id", "households"),
    "businesses": ("organization_id", "relationship_entities"),
}


def load_approved_doc_links(path):
    """Load the FROZEN approved document->entity links from a linkage preview's reconciliation.csv (pass
    the run directory or the csv). Only deterministic resolutions (people/households/businesses) are kept;
    ambiguous/unmatched are ignored. Returns {document_id: (kind, entity_id)}."""
    csvpath = path if str(path).endswith(".csv") else os.path.join(path, "reconciliation.csv")
    out: dict[int, tuple[str, int]] = {}
    with open(csvpath, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            kind = (r.get("resolution") or "").strip()
            did, eid = (r.get("document_id") or "").strip(), (r.get("proposed_entity_id") or "").strip()
            if kind in _KIND and did.isdigit() and eid.isdigit():
                out[int(did)] = (kind, int(eid))
    return out


@dataclass
class LinkPlan:
    pending: list = field(default_factory=list)      # (doc_id, kind, field, entity_id)
    applied: Counter = field(default_factory=Counter)  # kind -> already-linked count
    drift: list = field(default_factory=list)        # (doc_id, kind, field, current, approved) conflicts
    missing_documents: list = field(default_factory=list)
    missing_entities: list = field(default_factory=list)  # (kind, entity_id)

    def _pending_by_kind(self):
        c: Counter = Counter()
        for _did, kind, _f, _e in self.pending:
            c[kind] += 1
        return c

    def guard_counts(self):
        """Stable frozen totals per kind (pending + already-applied). Idempotent: pending -> 0 post-apply."""
        pk = self._pending_by_kind()
        return {k: pk.get(k, 0) + self.applied.get(k, 0) for k in _KIND}

    def counts(self):
        pk = self._pending_by_kind()
        return {k: pk.get(k, 0) for k in _KIND}

    def clean(self):
        return not self.drift and not self.missing_documents and not self.missing_entities


class DocumentLinkJob(MigrationJob):
    source_system = "Document Link"
    supported_modes = frozenset({Mode.PREVIEW, Mode.APPLY})

    def _plan(self, conn, approved) -> LinkPlan:
        from sqlalchemy import select

        from app.db import metadata
        documents = metadata.tables["documents"]
        plan = LinkPlan()
        if not approved:
            return plan

        rows = {m["id"]: m for m in conn.execute(select(
            documents.c.id, documents.c.person_id, documents.c.household_id,
            documents.c.organization_id, documents.c.status).where(
            documents.c.id.in_(list(approved)))).mappings()}

        # validate target entities exist (fail-closed on any missing target)
        want: dict[str, set] = {k: set() for k in _KIND}
        for _did, (kind, eid) in approved.items():
            want[kind].add(eid)
        present: dict[str, set] = {}
        for kind, (_field, table) in _KIND.items():
            if want[kind]:
                t = metadata.tables[table]
                present[kind] = set(conn.execute(select(t.c.id).where(t.c.id.in_(list(want[kind])))).scalars())
            else:
                present[kind] = set()
        for kind in _KIND:
            for eid in want[kind]:
                if eid not in present[kind]:
                    plan.missing_entities.append((kind, eid))

        for did, (kind, eid) in approved.items():
            field_name = _KIND[kind][0]
            row = rows.get(did)
            if row is None:
                plan.missing_documents.append(did)
                continue
            cur = row[field_name]
            if cur == eid:
                plan.applied[kind] += 1
            elif cur is None:
                plan.pending.append((did, kind, field_name, eid))
            else:
                plan.drift.append((did, kind, field_name, cur, eid))
        return plan

    def _rows(self, plan, applied_actions=None) -> list[dict]:
        applied_actions = applied_actions or {}
        rows = []
        for did, kind, field_name, eid in plan.pending:
            rows.append({"document_id": did, "kind": kind, "link_column": field_name,
                         "old_value": "", "new_value": eid,
                         "action": applied_actions.get(did, "would_set_link")})
        for did, kind, field_name, cur, eid in plan.drift:
            rows.append({"document_id": did, "kind": kind, "link_column": field_name,
                         "old_value": cur, "new_value": eid, "action": "DRIFT_conflict"})
        return rows

    def _preview(self, approved=None, **_opts) -> Outcome:
        from app.db import engine
        with engine.connect() as conn:
            plan = self._plan(conn, approved)
        counts = plan.guard_counts()
        counts["total"] = sum(counts.values())
        counts["pending"] = plan.counts()
        counts["drift"] = len(plan.drift)
        counts["missing_documents"] = len(plan.missing_documents)
        counts["missing_entities"] = len(plan.missing_entities)
        notes = [
            "PREVIEW ONLY — no writes. Frozen to the approved reconciliation; ambiguous/unmatched never "
            "touched. Updates only person_id/household_id/organization_id — NOT storage_uri, NOT "
            "document_sources, NO byte movement.",
            "Totals are the frozen approved set (pending + already-applied); after APPLY pending -> 0.",
            "APPLY fails closed on any drift/missing document/missing target entity, or count mismatch.",
        ]
        if not approved:
            notes.append("No --approved manifest supplied: nothing to plan.")
        return Outcome(counts=counts, exceptions=[], reconciliation=self._rows(plan), notes=notes)

    def _apply(self, job_id=None, approved=None, confirm=False, backup=None, expect=None, **_opts) -> Outcome:
        if not confirm:
            raise RepairGuardError("APPLY requires explicit confirm=True.")
        if not backup or not os.path.isfile(backup) or os.path.getsize(backup) == 0:
            raise RepairGuardError(f"APPLY requires a verified non-empty DB backup file (got: {backup!r}).")
        if not approved:
            raise RepairGuardError("APPLY requires an --approved reconciliation manifest (frozen scope).")

        from sqlalchemy import update

        from app.db import engine, metadata
        documents = metadata.tables["documents"]
        applied_actions: dict = {}
        with engine.begin() as conn:
            plan = self._plan(conn, approved)
            if not plan.clean():
                raise RepairGuardError(
                    f"drift/missing detected — aborted before any write: drift={len(plan.drift)} "
                    f"missing_documents={len(plan.missing_documents)} missing_entities={len(plan.missing_entities)}")
            live = plan.guard_counts()
            if expect is not None and live != expect:
                raise RepairGuardError(f"count drift — approved {expect} but live {live}; aborted before any write.")

            for did, _kind, field_name, eid in plan.pending:
                conn.execute(update(documents).where(
                    documents.c.id == did, documents.c[field_name].is_(None)).values({field_name: eid}))
                applied_actions[did] = "set_link"

        counts = plan.guard_counts()
        counts["rows_inserted"] = len(applied_actions)      # link-column updates written
        return Outcome(counts=counts, exceptions=[], reconciliation=self._rows(plan, applied_actions),
                       notes=["APPLY complete: canonical link columns set for the frozen deterministic set. "
                              "No bytes moved, storage_uri/document_sources unchanged. Rollback: restore the "
                              "pre-apply DB backup provided to --backup."])
