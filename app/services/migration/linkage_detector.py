"""Unresolved-subject linkage detector (PR-3).

Turns unresolved ingestion subjects into review work by raising ONE Exception Engine exception per subject
in the ``linkage`` domain (migration lnkg01). It reuses the existing engine (queue / assignment / SLA /
audit / capability model) and the PR-2 evidence assembler — it creates NO new queue framework and
DUPLICATES NO matching or evidence logic.

Detector contract (subject-generic): a subject descriptor is
``{source_system, subject_type, subject_key, display_name}``. The initial source is the current TaxDome
unresolved-folder population (``discover_folder_subjects``); acquired-firm / scanned-paper / CRM sources
feed the SAME ``detect`` contract later by supplying their own descriptors.

Dedupe / idempotency: one exception per normalized ``(source_system, subject_type, subject_key)`` via the
engine's ``dedupe_key`` (its partial-unique index is the backstop). Reruns never create duplicate open
exceptions. A subject that already has a reusable APPROVED resolution in ``folder_resolution_decisions``
is skipped (no exception). Resolved/reopened lifecycle is handled by the engine's own dedupe behaviour.

Scope: current unresolved documents only (person_id / household_id / organization_id ALL NULL). It never
touches already-linked/relocated documents, never classifies documents, and creates review work only.

Write scope: in PREVIEW, ZERO writes. In production create, ONLY Exception Engine records (exceptions +
exception_events, via ``raise_exception``). NO folder_resolution_decisions writes, NO canonical writes,
NO person_source_links writes, NO document owner FK changes, NO file movement, NO storage_uri /
document_sources changes, NO relocation.
"""
from __future__ import annotations

from app.services.migration.evidence_assembler import assemble_subject, build_context
from app.services.resolution_knowledge import get_reusable_resolution

SUBJECT_SYSTEM = "TaxDome Drive"
SUBJECT_TYPE = "folder"
LINKAGE_CODE = "linkage.unresolved_subject"

_CLOSED = ("resolved", "cancelled")


def _norm(value) -> str:
    return " ".join((value or "").split()).casefold()


def dedupe_key(source_system, subject_type, subject_key) -> str:
    """Stable per-subject dedupe key: linkage:<system>:<type>:<normalized key>."""
    return f"linkage:{_norm(source_system)}:{_norm(subject_type)}:{_norm(subject_key)}"


# --------------------------------------------------------------------------- subject discovery (read-only)

def discover_folder_subjects(conn) -> list[dict]:
    """The current TaxDome unresolved-folder population — one descriptor per distinct folder whose
    documents are all unlinked (person/household/organization all NULL)."""
    from sqlalchemy import and_, distinct, select

    from app.db import documents
    from app.importers.taxdome_drive import _name_key
    folder = documents.c.tags["taxdome_folder"].astext
    where = and_(documents.c.person_id.is_(None), documents.c.household_id.is_(None),
                 documents.c.organization_id.is_(None), documents.c.status != "deleted",
                 documents.c.tags["source_system"].astext == SUBJECT_SYSTEM, folder.isnot(None))
    names = [r[0] for r in conn.execute(select(distinct(folder)).where(where)) if r[0]]
    return [{"source_system": SUBJECT_SYSTEM, "subject_type": SUBJECT_TYPE,
             "subject_key": _name_key(name), "display_name": name} for name in sorted(names)]


def _existing_dedupe_state(conn, keys):
    """Return (active_keys, resolved_keys) among the given dedupe keys — for accurate preview accounting."""
    from sqlalchemy import select

    from app.db import metadata
    exceptions = metadata.tables["exceptions"]
    active, resolved = set(), set()
    if not keys:
        return active, resolved
    rows = conn.execute(select(exceptions.c.dedupe_key, exceptions.c.status).where(
        exceptions.c.dedupe_key.in_(list(keys)))).all()
    for dk, status in rows:
        if status in _CLOSED:
            if status == "resolved":
                resolved.add(dk)
        else:
            active.add(dk)
    return active, resolved


# --------------------------------------------------------------------------- detect

def detect(*, preview=True, principal=None, actor_user_id=None, subjects=None, context=None,
           limit=None, request_id=None):
    """Detect unresolved subjects and (unless preview) raise one linkage exception per subject.

    Returns a summary dict. PREVIEW performs ZERO writes; production create writes ONLY Exception Engine
    records via raise_exception (skipping subjects with a reusable approved resolution)."""
    from app.db import engine

    if not preview and principal is None:
        raise ValueError("production create requires a principal with exception.write")

    context = context or build_context()

    with engine.connect() as conn:
        subjects = list(subjects) if subjects is not None else discover_folder_subjects(conn)
        if limit is not None:
            subjects = subjects[:limit]
        keys = {dedupe_key(s["source_system"], s["subject_type"], s["subject_key"]) for s in subjects}
        active_keys, resolved_keys = _existing_dedupe_state(conn, keys)

        plan = []           # (subject, dedupe_key, action, bundle_or_None, error_or_None)
        for s in subjects:
            dk = dedupe_key(s["source_system"], s["subject_type"], s["subject_key"])
            reusable = get_reusable_resolution(s["source_system"], s["subject_type"], s["subject_key"],
                                               conn=conn)
            if reusable is not None:
                plan.append((s, dk, "skip_reusable", None, None))
                continue
            try:
                bundle = assemble_subject(
                    source_system=s["source_system"], subject_type=s["subject_type"],
                    subject_key=s["subject_key"], display_name=s["display_name"],
                    context=context, conn=conn)
            except Exception as exc:  # noqa: BLE001 — a malformed subject must not abort the batch
                plan.append((s, dk, "error", None, str(exc)))
                continue
            if dk in active_keys:
                action = "already_open"
            elif dk in resolved_keys:
                action = "would_reopen"
            else:
                action = "would_create"
            plan.append((s, dk, action, bundle, None))

    summary = {
        "preview": preview,
        "source_system": SUBJECT_SYSTEM,
        "total_subjects": len(subjects),
        "would_create": sum(1 for _s, _k, a, _b, _e in plan if a == "would_create"),
        "already_open": sum(1 for _s, _k, a, _b, _e in plan if a == "already_open"),
        "would_reopen": sum(1 for _s, _k, a, _b, _e in plan if a == "would_reopen"),
        "skipped_reusable": sum(1 for _s, _k, a, _b, _e in plan if a == "skip_reusable"),
        "held_no_candidates": sum(1 for _s, _k, a, b, _e in plan
                                  if b is not None and b["evidence_flags"]["no_candidates"]),
        "errors": sum(1 for _s, _k, a, _b, e in plan if e is not None),
        "error_details": [{"subject_key": s["subject_key"], "error": e}
                          for s, _k, a, _b, e in plan if e is not None],
    }

    if preview:
        summary["subjects"] = [
            {"subject_key": s["subject_key"], "display_name": s["display_name"], "action": a,
             "confidence": (b["confidence"] if b else None),
             "deterministic_outcome": (b["deterministic_outcome"] if b else None),
             "held_reason": (b["held_reason"] if b else None)}
            for s, _k, a, b, _e in plan]
        return summary

    # ---- production create: Exception Engine records ONLY ----
    from app.services.exception_engine import raise_exception
    created, reopened, idempotent, exception_ids = 0, 0, 0, []
    for s, dk, action, bundle, _error in plan:
        if action in ("skip_reusable", "error"):
            continue
        row = raise_exception(
            code=LINKAGE_CODE, principal=principal, actor_user_id=actor_user_id, source="system",
            dedupe_key=dk, request_id=request_id,
            title=f"Unresolved: {s['display_name']}",
            description=(bundle["held_reason"] or bundle["match_reason"] or
                         "Unresolved ingestion subject needs a canonical owner."),
            related_entity_type="linkage_subject",
            metadata={"detector": LINKAGE_CODE, "subject": {
                "source_system": s["source_system"], "subject_type": s["subject_type"],
                "subject_key": s["subject_key"], "display_name": s["display_name"]},
                "evidence": bundle})
        exception_ids.append(row["id"])
        if action == "would_create":
            created += 1
        elif action == "would_reopen":
            reopened += 1
        else:
            idempotent += 1
    summary.update({"created": created, "reopened": reopened, "idempotent_existing": idempotent,
                    "exception_ids": exception_ids})
    return summary
