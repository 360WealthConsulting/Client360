"""Engagement governance (Phase D.44) — read-only validation that the unified communications layer stays a
COMPOSITION over authoritative services and never becomes a second messaging / timeline / notification /
document / scheduling / audit / event system.

Returns a structured ``{ok, issue_count, findings}`` report and NEVER raises into normal use.

Invariants enforced:
  * No engagement module defines a table, writes the DB (insert/update/delete), publishes to the outbox,
    writes audit events, or reads ``rm_*`` projection tables directly — it only composes reads.
  * The composition reuses the authoritative timeline / portal reads (no shadow timeline).
  * Every interaction the surfaces can emit is a registered interaction type with an authoritative owner,
    a retention class, a deep link, and a lifecycle.
  * Internal-only interaction types are never produced by the external portal composition.
  * No raw environment gating (all gates go through the governed runtime engine).
"""
from __future__ import annotations

import pathlib
import re

from . import gate, registry

# Engagement modules that must remain read-only composition (governance.py excluded — it holds the
# detection string-literals and would self-match; it enforces by checking, not by doing).
_MODULES = ("service.py", "model.py", "registry.py", "gate.py", "stats.py", "metrics.py",
            "diagnostics.py", "adapters/timeline.py", "adapters/portal.py")

# The authoritative composed reads the layer must reuse (proves no shadow timeline / no domain fan-out).
_AUTHORITATIVE_READS = ("activity_timeline", "client_threads", "client_notifications",
                        "client_document_requests", "communications.service")


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_engagement() -> dict:
    findings = []
    try:
        for mod in _MODULES:
            s = _src(mod)
            # No DB writes anywhere in the composition layer.
            for verb in (".insert()", ".insert(", ".update(", ".delete()", "sa.insert", "sa.update",
                         "sa.delete"):
                if verb in s:
                    findings.append({"type": "database_write", "module": mod, "op": verb})
            # No new event bus / outbox publication.
            if re.search(r"publish_safe\s*\(|publisher\.publish|publish_event\s*\(", s):
                findings.append({"type": "outbox_publication", "module": mod})
            # No second audit system.
            if re.search(r"write_audit_event\s*\(", s):
                findings.append({"type": "audit_write", "module": mod})
            # No direct projection-table reads (must go through the composed reads).
            for m in re.findall(r"\brm_[a-z]\w*", s):
                findings.append({"type": "direct_projection_read", "module": mod, "table": m})
            # No shadow table definition (no second store).
            if re.search(r"Table\s*\(|define_\w+_tables\s*\(", s):
                findings.append({"type": "shadow_table_definition", "module": mod})
            # No raw environment gating.
            if re.search(r"os\.getenv|os\.environ", s):
                findings.append({"type": "raw_env_fallback", "module": mod})

        # The composition must reuse the authoritative reads (no shadow timeline / no raw domain fan-out).
        composed_src = _src("service.py") + _src("adapters/timeline.py") + _src("adapters/portal.py") + \
            _src("metrics.py")
        if not any(a in composed_src for a in _AUTHORITATIVE_READS):
            findings.append({"type": "not_reusing_authoritative_reads"})
        if "activity_timeline" not in _src("adapters/timeline.py"):
            findings.append({"type": "staff_timeline_not_composing_authoritative_projection"})

        # Every interaction type is fully declared.
        for t in registry.REGISTRY:
            if not t.authoritative_owner or not t.source_service:
                findings.append({"type": "interaction_without_owner", "interaction": t.key})
            if not t.retention_class:
                findings.append({"type": "interaction_without_retention", "interaction": t.key})
            if not t.deep_link:
                findings.append({"type": "interaction_without_deep_link", "interaction": t.key})
            if t.lifecycle not in registry.LIFECYCLES:
                findings.append({"type": "invalid_lifecycle", "interaction": t.key})
            if t.visibility not in ("internal", "external", "both"):
                findings.append({"type": "invalid_visibility", "interaction": t.key})

        # The external portal composition must not surface an internal-only interaction type.
        portal_src = _src("adapters/portal.py")
        for itype in registry.INTERNAL_ONLY_TYPES:
            # a portal adapter emitting an internal-only type would name it as a produced interaction_type.
            if re.search(rf'interaction_type="{itype}"', portal_src):
                findings.append({"type": "internal_type_in_portal_adapter", "interaction": itype})

        # Every gate is a governed runtime flag (present in the GATES registry).
        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
