"""Document Intelligence governance (Phase D.50) — read-only validation that the document-intelligence layer
stays a COMPOSITION over the authoritative document systems, and never becomes a second DMS, OCR engine,
indexing/search engine, archive, document database, metadata store, or records repository. Returns
``{ok, issue_count, findings}`` and NEVER raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes audit events
    — it only composes reads (no shadow document/metadata/index/archive store).
  * No second DMS / OCR / index — the layer never writes documents, runs OCR (`ocr`, `tesseract`,
    `extract_text`, `to_tsvector`), or builds an index; it only reports the Document Platform's own status.
  * No document mutation — it never calls a Document Platform / Governance mutation (`create_document`,
    `update_document`, `set_status`, `archive`, `soft_delete`, `restore`, `apply_retention`,
    `create_retention_policy`, `execute_deletion`, `place_legal_hold`, …).
  * No second metrics registry — this layer defines no ``Metric``/``_DEFS``.
  * Every document class + retention policy + panel + dashboard is fully declared; every panel names an
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

_AUTHORITATIVE_READS = ("document_platform", "governance.retention", "compliance_intelligence")

# Mutating/authoritative-owner entry points this layer must NEVER call (would duplicate a DMS/records engine).
_FORBIDDEN_CALLS = (
    "create_document(", "update_document(", "set_status(", ".archive(", "soft_delete(", ".restore(",
    "apply_retention(", "create_retention_policy(", "execute_deletion(", "place_legal_hold(",
    "create_deletion_request(", "link_entity(",
)

# Second-OCR / second-index tells this layer must never contain.
_FORBIDDEN_ENGINE = ("tesseract", "pytesseract", "extract_text(", "to_tsvector", "pdfminer", "pypdf",
                     "textract")


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_document_intelligence() -> dict:
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
            for tell in _FORBIDDEN_ENGINE:
                if tell in s:
                    findings.append({"type": "second_ocr_or_index", "module": mod, "tell": tell})

        # The composition must reference the authoritative document reads.
        composed = _src("service.py") + _src("panels.py")
        if not any(a in composed for a in _AUTHORITATIVE_READS):
            findings.append({"type": "not_reusing_authoritative_reads"})
        # The authoritative document owner (Document Platform) must be composed (no second DMS).
        if "document_platform" not in composed:
            findings.append({"type": "not_reusing_document_platform"})

        # Explainability enforcement present.
        if "is_explainable" not in _src("model.py") or "is_explainable" not in _src("panels.py"):
            findings.append({"type": "explainability_not_enforced"})

        # Registry completeness + single ownership.
        for dc in registry.DOCUMENT_REGISTRY:
            if not dc.owner or not dc.storage_source or not dc.metadata_source or not dc.classification \
                    or not dc.retention_policy or not dc.lifecycle or not dc.deep_links:
                findings.append({"type": "document_class_incomplete", "document_class": dc.key})
            if not dc.runtime_gate:
                findings.append({"type": "document_class_missing_gate", "document_class": dc.key})
            if not registry.retention_policy_registered(dc.retention_policy):
                findings.append({"type": "document_class_unknown_retention", "document_class": dc.key,
                                 "retention_policy": dc.retention_policy})
        for rp in registry.RETENTION_REGISTRY:
            if not rp.owner or not rp.retention_period or not rp.archive_owner or not rp.disposition_policy \
                    or not rp.governing_regulation or not rp.runtime_gate:
                findings.append({"type": "retention_policy_incomplete", "retention_policy": rp.key})
        for d in registry.INTELLIGENCE_DASHBOARDS:
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
        for label, keys in (("document_class", [d.key for d in registry.DOCUMENT_REGISTRY]),
                            ("retention_policy", [r.key for r in registry.RETENTION_REGISTRY]),
                            ("panel", [p.key for p in registry.PANEL_REGISTRY]),
                            ("dashboard", [d.key for d in registry.INTELLIGENCE_DASHBOARDS])):
            if len(keys) != len(set(keys)):
                findings.append({"type": "duplicate_registry_ownership", "registry": label})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
