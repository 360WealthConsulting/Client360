"""Projection governance (Phase D.36) — validation of the read-model registry.

Read-only validation that the projection model is coherent and honours the read-model invariants: every
projection has an owner + a subscriber, every subscribed event type is a registered domain-event
contract, the registry matches the in-code definitions (no schema/version drift), no projection is
lagging or non-deterministic, no dependency cycles or duplicates, and — critically — **no projection
reads an authoritative table** (a projection may only touch its read-model table + the outbox). It reads
the registries + the projection source read-only and returns a structured report. Never raises, never
edits.
"""
from __future__ import annotations

from app.database.projection_tables import READ_MODEL_TABLES

from . import registry
from .definitions import PROJECTION_DEFINITIONS
from .engine import LAG_THRESHOLD, _lag

_ALLOWED_TABLES = set(READ_MODEL_TABLES) | {"outbox_events", "projection_state", "projection_definitions"}


def _has_cycle(graph) -> list[str]:
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    bad = []

    def visit(n, stack):
        color[n] = GREY
        for dep in graph.get(n, ()):
            if dep not in color:
                continue
            if color[dep] == GREY:
                bad.extend(stack + [n, dep])
            elif color[dep] == WHITE:
                visit(dep, stack + [n])
        color[n] = BLACK

    for n in graph:
        if color[n] == WHITE:
            visit(n, [])
    return sorted(set(bad))


def validate() -> dict:
    """Run every projection-governance check → ``{ok, issue_count, findings, coverage}``. Never raises."""
    findings = []
    try:
        rows = registry.list_definitions()
        defs = PROJECTION_DEFINITIONS
        contracts = _contract_types()
        from sqlalchemy import select

        from app.db import engine, projection_state
        c = engine.connect()
        try:
            state_rows = {s["projection_id"]: dict(s)
                          for s in c.execute(select(projection_state)).mappings()}
            _validate_definitions(rows, defs, contracts, state_rows, c, findings)
        finally:
            c.close()

        # 8. dependency cycles.
        for pid in _has_cycle(registry.dependency_graph()):
            findings.append({"type": "projection_dependency_cycle", "projection": pid})

        # 10. a projection reading an authoritative table (source scan) — read models build ONLY from
        #     events + their read table; touching an authoritative table would make them a shadow read.
        for tbl in _authoritative_tables_referenced():
            findings.append({"type": "projection_reading_authoritative_tables", "table": tbl})

        cov = registry.coverage()
    except Exception as exc:   # never raise into a caller
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}],
                "coverage": {"coverage_pct": 0.0}}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings, "coverage": cov}


def _validate_definitions(rows, defs, contracts, state_rows, c, findings):
        seen_ids, seen_tables = set(), {}
        for r in rows:
            pid = r["projection_id"]
            # 9. duplicate projections (id or read table).
            if pid in seen_ids:
                findings.append({"type": "duplicate_projection", "projection": pid})
            seen_ids.add(pid)
            if r["read_table"] in seen_tables:
                findings.append({"type": "duplicate_projection", "projection": pid,
                                 "conflicts_with": seen_tables[r["read_table"]], "table": r["read_table"]})
            else:
                seen_tables[r["read_table"]] = pid

            if r["status"] != "active":
                continue
            # 1. owner.
            if not r.get("owner"):
                findings.append({"type": "projection_without_owner", "projection": pid})
            # 2. subscriber (subscribed events).
            subs = r.get("subscribed_events") or []
            if not subs:
                findings.append({"type": "projection_without_subscriber", "projection": pid})
            # 3. subscriber without projection (a subscribed event type with no registered contract).
            for et in subs:
                if et != "*" and et not in contracts:
                    findings.append({"type": "subscriber_without_projection", "projection": pid,
                                     "event_type": et})
            # 4. schema drift (registry vs in-code definition).
            cd = defs.get(pid)
            if cd is not None and cd.schema_version != r["schema_version"]:
                findings.append({"type": "projection_schema_drift", "projection": pid,
                                 "registry": r["schema_version"], "code": cd.schema_version})
            # 5. version drift (built data schema vs current definition).
            st = state_rows.get(pid)
            if st and cd is not None and st["schema_version"] != cd.schema_version:
                findings.append({"type": "projection_version_drift", "projection": pid,
                                 "built": st["schema_version"], "code": cd.schema_version})
            # 6. lag (an established projection that has fallen behind).
            if st and st["rebuild_count"] > 0 and cd is not None:
                lg = _lag(c, cd, st["last_processed_event_id"])
                if lg > LAG_THRESHOLD:
                    findings.append({"type": "projection_lag", "projection": pid, "lag": lg})
            # 7. replay mismatch (a recorded validation found non-determinism).
            if st and st.get("last_validation_ok") == "mismatch":
                findings.append({"type": "projection_replay_mismatch", "projection": pid})


def _contract_types() -> set:
    try:
        from app.services.events.contracts import EVENT_CONTRACTS
        return set(EVENT_CONTRACTS)
    except Exception:
        return set()


def _authoritative_tables_referenced() -> list[str]:
    """Every table name a projection handler touches must be its read-model table or the outbox — scan
    ``definitions.py`` for ``_tbl("…")`` references and flag any that is not allowed."""
    import pathlib
    import re
    src = pathlib.Path(__file__).with_name("definitions.py").read_text()
    referenced = set(re.findall(r"""_tbl\(\s*["']([\w]+)["']""", src))
    return sorted(t for t in referenced if t not in _ALLOWED_TABLES)


def record_validation(*, actor_user_id=None) -> dict:
    """Run projection governance validation and record a firm-level ``governance_validated`` audit event."""
    report = validate()
    _audit("projection.governance_validated", actor_user_id, {"issue_count": report["issue_count"]})
    return report


def _audit(action, actor_user_id, metadata):
    try:
        import uuid

        from app.security.audit import write_audit_event
        write_audit_event(action=action, entity_type="projection", entity_id="0",
                          actor_user_id=actor_user_id, request_id=str(uuid.uuid4()), metadata=metadata or {})
    except Exception:
        pass
