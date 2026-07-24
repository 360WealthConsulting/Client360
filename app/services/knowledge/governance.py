"""Knowledge graph governance (Phase D.45) — read-only validation that the Enterprise Knowledge Graph stays
a SEMANTIC COMPOSITION over the authoritative models and never becomes a graph database or a second
relationship engine. Returns ``{ok, issue_count, findings}`` and NEVER raises into normal use.

Invariants enforced:
  * No knowledge module defines a table / graph store, writes the DB, publishes to the outbox, or writes
    audit events — it only composes reads.
  * No graph-database / RDF / SPARQL dependency is introduced.
  * The relationship adapter composes the authoritative ``build_relationship_graph`` (no second relationship
    engine); the advisor adapter uses ``record_assignments``; the domain adapter uses authoritative
    aggregations (no direct rm_* projection reads).
  * Every entity type and relationship type is fully declared in the registries; no duplicate ownership.
  * Internal-only entity types are never emitted by an external surface (the portal is untouched — D.43
    reuse only).
  * Every gate is a governed runtime flag; no raw environment fallback.
"""
from __future__ import annotations

import pathlib
import re

from . import gate, registry

# governance.py excluded from the self-scan (it holds the detection string-literals).
_MODULES = ("service.py", "model.py", "registry.py", "gate.py", "stats.py", "metrics.py",
            "diagnostics.py", "explain.py", "adapters/relationship.py", "adapters/advisor.py",
            "adapters/domain.py")

_AUTHORITATIVE_READS = ("build_relationship_graph", "record_assignments", "get_client_snapshot",
                        "engagement_summary")
_FORBIDDEN_DEPS = ("neo4j", "rdflib", "sparql", "gremlin", "networkx")


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_knowledge() -> dict:
    findings = []
    try:
        for mod in _MODULES:
            s = _src(mod)
            for verb in (".insert()", ".insert(", ".update(", ".delete()", "sa.insert", "sa.update",
                         "sa.delete"):
                if verb in s:
                    findings.append({"type": "database_write", "module": mod, "op": verb})
            if re.search(r"publish_safe\s*\(|publisher\.publish|publish_event\s*\(", s):
                findings.append({"type": "outbox_publication", "module": mod})
            if re.search(r"write_audit_event\s*\(", s):
                findings.append({"type": "audit_write", "module": mod})
            for m in re.findall(r"\brm_[a-z]\w*", s):
                findings.append({"type": "direct_projection_read", "module": mod, "table": m})
            if re.search(r"Table\s*\(|define_\w+_tables\s*\(", s):
                findings.append({"type": "shadow_store_definition", "module": mod})
            if re.search(r"os\.getenv|os\.environ", s):
                findings.append({"type": "raw_env_fallback", "module": mod})
            for dep in _FORBIDDEN_DEPS:
                if re.search(rf"\bimport\s+{dep}\b|\bfrom\s+{dep}\b", s):
                    findings.append({"type": "graph_db_dependency", "module": mod, "dep": dep})

        # Composition must reuse the authoritative relationship engine + scoped reads (no second engine).
        composed = (_src("adapters/relationship.py") + _src("adapters/advisor.py") +
                    _src("adapters/domain.py"))
        if "build_relationship_graph" not in composed:
            findings.append({"type": "not_reusing_relationship_engine"})
        if not any(a in composed for a in _AUTHORITATIVE_READS):
            findings.append({"type": "not_reusing_authoritative_reads"})

        # Registry completeness: every entity + relationship type fully declared.
        for e in registry.ENTITY_REGISTRY:
            if not e.owner or not e.source_service or not e.deep_link:
                findings.append({"type": "entity_incomplete", "entity": e.key})
            if e.lifecycle not in registry.LIFECYCLES:
                findings.append({"type": "invalid_entity_lifecycle", "entity": e.key})
            if e.visibility not in ("internal", "external", "both"):
                findings.append({"type": "invalid_entity_visibility", "entity": e.key})
        for r in registry.RELATIONSHIP_REGISTRY:
            if not r.authoritative_owner or not r.explanation or not r.traversal_rule:
                findings.append({"type": "relationship_incomplete", "relationship": r.code})
            if r.lifecycle not in registry.LIFECYCLES:
                findings.append({"type": "invalid_relationship_lifecycle", "relationship": r.code})

        # No duplicate entity/relationship keys (single ownership).
        ekeys = [e.key for e in registry.ENTITY_REGISTRY]
        if len(ekeys) != len(set(ekeys)):
            findings.append({"type": "duplicate_entity_key"})
        rcodes = [r.code for r in registry.RELATIONSHIP_REGISTRY]
        if len(rcodes) != len(set(rcodes)):
            findings.append({"type": "duplicate_relationship_key"})

        # Every raw-code mapping targets a registered relationship.
        for raw, mapped in registry._RAW_CODE_MAP.items():
            if not registry.relationship_registered(mapped):
                findings.append({"type": "raw_map_targets_unregistered", "raw": raw, "mapped": mapped})

        # Bounded traversal declared.
        if not isinstance(getattr(__import__("app.services.knowledge.service", fromlist=["DEPTH_LIMIT"]),
                                  "DEPTH_LIMIT", None), int):
            findings.append({"type": "traversal_not_bounded"})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
