"""Policy governance (Phase D.32) — validation of the policy registry + its runtime definitions.

Read-only validation that the declarative policy registry is coherent: no duplicate policies, no
unreachable or orphan policies, no circular dependencies, no missing runtime definitions for
authoritative policies, no deprecated references, and no invalid capability references. It reads the
registry, the in-code definitions, the D.27 runtime metadata, and the RBAC capability catalog
read-only (the runtime engine remains the sole evaluator) and returns a structured governance report.
It never raises and never edits metadata or the registry.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import capabilities, engine

from ..runtime import metadata_reader
from ..runtime.common import now, record_event, write_audit
from . import registry
from .definitions import POLICY_DEFINITIONS

_ACTIVE_STATES = ("active", "in_domain")


def _has_cycle(graph: dict) -> list[str]:
    """Return the codes involved in any dependency cycle (empty if acyclic)."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    offenders: list[str] = []

    def visit(node, stack):
        color[node] = GREY
        for dep in graph.get(node, ()):  # dep may be outside graph (caught elsewhere)
            if dep not in color:
                continue
            if color[dep] == GREY:
                offenders.extend(stack + [node, dep])
            elif color[dep] == WHITE:
                visit(dep, stack + [node])
        color[node] = BLACK

    for n in graph:
        if color[n] == WHITE:
            visit(n, [])
    return sorted(set(offenders))


def validate() -> dict:
    """Run every policy-governance check and return ``{ok, issue_count, findings, coverage}``. Never
    raises."""
    findings = []
    try:
        rows = registry.list_policies()
        by_code = {r["code"]: r for r in rows}
        defs = POLICY_DEFINITIONS
        flags = {f["code"]: f for f in metadata_reader.read_flags()}
        items = {i["code"] for i in metadata_reader.read_active_items()}
        deprecated_flags = {c for c, f in flags.items() if f.get("status") in ("deprecated", "archived")}
        with engine.connect() as c:
            known_caps = set(c.scalars(select(capabilities.c.code)))

        # 1. orphan policies: a registry row with no in-code definition (can never be evaluated).
        for r in rows:
            if r["code"] not in defs and r["status"] in _ACTIVE_STATES:
                findings.append({"type": "orphan_policy", "policy": r["code"]})

        # 2. unreachable policies: an active in-code definition with no active/in_domain registry row.
        for code in defs:
            row = by_code.get(code)
            if row is None or row["status"] not in _ACTIVE_STATES:
                findings.append({"type": "unreachable_policy", "policy": code})

        # 3. duplicate policies: two active policies consuming the same fixed runtime definition.
        seen_feature: dict[str, str] = {}
        for r in rows:
            if r["status"] != "active" or r.get("per_instance"):
                continue
            for kind, colname in (("feature", "consumes_feature"), ("config", "consumes_config")):
                key = r.get(colname)
                if not key:
                    continue
                token = f"{kind}:{key}"
                if token in seen_feature:
                    findings.append({"type": "duplicate_policy", "policy": r["code"],
                                     "conflicts_with": seen_feature[token], "definition": key})
                else:
                    seen_feature[token] = r["code"]

        # 4. circular dependencies in the policy graph.
        for code in _has_cycle(registry.dependency_graph()):
            findings.append({"type": "circular_dependency", "policy": code})

        # 5. missing runtime definitions for authoritative (requires_definition) policies.
        authoritative = covered = 0
        for r in rows:
            if r["status"] not in _ACTIVE_STATES or not r.get("requires_definition") or r.get("per_instance"):
                continue
            authoritative += 1
            feat, cfg = r.get("consumes_feature"), r.get("consumes_config")
            present = ((feat is None or feat in flags) and (cfg is None or cfg in items)
                       and (feat is not None or cfg is not None))
            if present:
                covered += 1
            else:
                findings.append({"type": "missing_runtime_definition", "policy": r["code"],
                                 "definition": feat or cfg})

        # 6. deprecated references: depends_on a deprecated/retired policy, or consumes a deprecated flag.
        for r in rows:
            if r["status"] not in _ACTIVE_STATES:
                continue
            for dep in (r.get("depends_on") or []):
                drow = by_code.get(dep)
                if drow is None:
                    findings.append({"type": "unreachable_policy", "policy": r["code"], "missing_dependency": dep})
                elif drow["status"] in ("deprecated", "retired"):
                    findings.append({"type": "deprecated_reference", "policy": r["code"], "dependency": dep})
            if r.get("consumes_feature") in deprecated_flags:
                findings.append({"type": "deprecated_reference", "policy": r["code"],
                                 "definition": r["consumes_feature"]})

        # 7. invalid capability references.
        for r in rows:
            if r["status"] in ("deprecated", "retired"):
                continue
            for cap in (r.get("required_capabilities") or []):
                if cap not in known_caps:
                    findings.append({"type": "invalid_capability_reference", "policy": r["code"],
                                     "capability": cap})

        cov = registry.coverage()
        cov["authoritative"] = authoritative
        cov["definition_covered"] = covered
        cov["definition_coverage_pct"] = round((covered / authoritative) * 100, 1) if authoritative else 100.0
    except Exception as exc:   # never raise into a caller
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}],
                "coverage": {"coverage_pct": 0.0}}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings, "coverage": cov}


def record_validation(*, actor_user_id=None) -> dict:
    """Run policy governance validation and record a firm-level ``policy_governance_validated`` event."""
    report = validate()
    with engine.begin() as c:
        record_event(c, entity_type="policy", entity_id=0, event_type="policy_governance_validated",
                     actor_user_id=actor_user_id,
                     payload={"ok": report["ok"], "issue_count": report["issue_count"],
                              "coverage_pct": report["coverage"].get("coverage_pct"),
                              "at": now().isoformat()})
    write_audit("policy.governance_validated", entity_type="policy", entity_id=0,
                actor_user_id=actor_user_id, metadata={"issue_count": report["issue_count"]})
    return report
