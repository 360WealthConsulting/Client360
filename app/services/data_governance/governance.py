"""Data Governance governance (Phase D.52) — read-only validation that the data-governance layer stays a
COMPOSITION over the authoritative data owners, and never becomes a second master-data platform, identity
system, metadata repository, synchronization engine, entity-resolution engine, or merge engine. Returns
``{ok, issue_count, findings}`` and NEVER raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes audit events
    — it only composes reads (no shadow master-data / identity / metadata / lineage store).
  * No duplicate identity system / master data store / merge engine — the layer never calls a merge /
    identity / lineage / catalog mutation (`merge_source_contacts`, `resolve_link_to_person`,
    `resolve_create_person`, `record_merge_decision`, `scan_duplicates`, `create_candidate`,
    `record_lineage`, `create_domain`, `create_element`, `create_rule`, `create_survivorship_rule`,
    `run_check`, `run_all_active_checks`, `create_case`).
  * No second metrics registry — this layer defines no ``Metric``/``_DEFS``.
  * Every governed entity + stewardship role + panel + dashboard is fully declared; every panel names an
    authoritative owner + source + deep link; no duplicate ownership.
  * Every panel is explainable (explanation + source + deep link) — enforced by the model + compute layer.
  * No raw environment gating.
"""
from __future__ import annotations

import pathlib
import re

from . import gate, registry

# governance.py excluded from the self-scan (it holds the detection string-literals).
_MODULES = ("service.py", "model.py", "registry.py", "gate.py", "stats.py", "metrics.py",
            "diagnostics.py", "panels.py")

_AUTHORITATIVE_READS = ("governance.catalog", "governance.quality", "governance.mdm", "governance.retention",
                        "governance.service", "matching", "events")

# Mutating/execution entry points this layer must NEVER call (would duplicate an MDM/identity/merge engine).
_FORBIDDEN_CALLS = (
    "merge_source_contacts(", "resolve_link_to_person(", "resolve_create_person(", "record_merge_decision(",
    "scan_duplicates(", "create_candidate(", "record_lineage(", "create_domain(", "create_element(",
    "create_rule(", "create_survivorship_rule(", "run_check(", "run_all_active_checks(", "run_stale_scan(",
    "create_case(", "set_case_status(", "review_due_retention(",
)


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_data_governance() -> dict:
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
            if re.search(r"^_DEFS\s*=|class\s+Metric\b", s, re.M):
                findings.append({"type": "second_metrics_registry", "module": mod})
            for call in _FORBIDDEN_CALLS:
                if call in s:
                    findings.append({"type": "duplicate_engine_call", "module": mod, "call": call})

        # The composition must reference the authoritative governance reads.
        composed = _src("service.py") + _src("panels.py")
        if not any(a in composed for a in _AUTHORITATIVE_READS):
            findings.append({"type": "not_reusing_authoritative_reads"})
        # The authoritative governance package must be composed (no second MDM/identity/metadata store).
        if "governance." not in composed:
            findings.append({"type": "not_reusing_governance_owner"})

        # Explainability enforcement present.
        if "is_explainable" not in _src("model.py") or "is_explainable" not in _src("panels.py"):
            findings.append({"type": "explainability_not_enforced"})

        # Registry completeness + single ownership.
        for e in registry.MASTER_DATA_REGISTRY:
            if not e.authoritative_owner or not e.identity_owner or not e.metadata_owner \
                    or not e.stewardship_owner or not e.lineage_owner or not e.deep_links:
                findings.append({"type": "entity_incomplete", "entity": e.key})
            if not e.runtime_gate:
                findings.append({"type": "entity_missing_gate", "entity": e.key})
            if not registry.stewardship_registered(e.stewardship_owner):
                findings.append({"type": "entity_unknown_stewardship", "entity": e.key,
                                 "stewardship_owner": e.stewardship_owner})
        for st in registry.STEWARDSHIP_REGISTRY:
            if not st.business_owner or not st.technical_owner or not st.validation_owner \
                    or not st.approval_owner or not st.runtime_gate:
                findings.append({"type": "stewardship_incomplete", "stewardship": st.key})
        for d in registry.GOVERNANCE_DASHBOARDS:
            if not d.owner or not d.audience or not d.runtime_gate or not d.navigation or not d.panels:
                findings.append({"type": "dashboard_incomplete", "dashboard": d.key})
            if not d.required_capabilities or not d.governing_services:
                findings.append({"type": "dashboard_missing_caps_or_services", "dashboard": d.key})
            if d.lifecycle not in registry.LIFECYCLES:
                findings.append({"type": "invalid_dashboard_lifecycle", "dashboard": d.key})
            for pkey in d.panels:
                if not registry.panel_registered(pkey):
                    findings.append({"type": "dashboard_panel_unregistered", "dashboard": d.key,
                                     "panel": pkey})
        for p in registry.PANEL_REGISTRY:
            if not p.owner or not p.source or not p.deep_link or not p.explainability:
                findings.append({"type": "panel_incomplete", "panel": p.key})
            if not p.permission:
                findings.append({"type": "panel_without_permission", "panel": p.key})
            if p.lifecycle not in registry.LIFECYCLES:
                findings.append({"type": "invalid_panel_lifecycle", "panel": p.key})
        for label, keys in (("entity", [e.key for e in registry.MASTER_DATA_REGISTRY]),
                            ("stewardship", [s.key for s in registry.STEWARDSHIP_REGISTRY]),
                            ("panel", [p.key for p in registry.PANEL_REGISTRY]),
                            ("dashboard", [d.key for d in registry.GOVERNANCE_DASHBOARDS])):
            if len(keys) != len(set(keys)):
                findings.append({"type": "duplicate_registry_ownership", "registry": label})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
