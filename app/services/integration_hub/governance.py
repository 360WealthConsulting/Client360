"""Integration Hub governance (Phase D.53) — read-only validation that the integration-hub layer stays a
COMPOSITION over the authoritative integration owners, and never becomes a second integration platform, ESB,
API gateway, synchronization engine, webhook processor, message broker, or event bus. Returns
``{ok, issue_count, findings}`` and NEVER raises into normal use.

Invariants enforced:
  * No module defines a table / persistence, writes the DB, publishes to the outbox, or writes audit events
    — it only composes reads (no shadow integration / connector / sync / webhook store).
  * No mutation / no synchronization trigger / no API invocation — the layer never calls an integration /
    sync / webhook / API / connector mutation (`run_sync`, `run_due_syncs`, `create_connector`,
    `create_endpoint`, `record_delivery`, `verify_endpoint`, `create_api_client`, `create_provider`,
    `set_connector_status`, `publish(`, `publish_safe(`, `invoke_port`, `get_microsoft_access_token`,
    `record_sync_health`).
  * No second metrics registry — this layer defines no ``Metric``/``_DEFS``.
  * Every integration + connector + panel + dashboard is fully declared; every panel names an authoritative
    owner + source + deep link; no duplicate ownership.
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

_AUTHORITATIVE_READS = ("integration.service", "integration.sync", "integration.connectors",
                        "integration.webhooks", "integration.api", "integration.events", "events")

# Mutating/execution entry points this layer must NEVER call (would duplicate an integration/sync engine).
_FORBIDDEN_CALLS = (
    "run_sync(", "run_due_syncs(", "create_connector(", "create_provider(", "create_endpoint(",
    "record_delivery(", "verify_endpoint(", "create_subscription(", "create_api_client(",
    "set_connector_status(", "publish_safe(", "publish_event(", "invoke_port(",
    "get_microsoft_access_token(", "record_sync_health(", "apply_signature_event(",
    "create_signature_request(",
)


def _src(rel):
    try:
        return (pathlib.Path(__file__).parent / rel).read_text()
    except OSError:
        return ""


def validate_integration_hub() -> dict:
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
            # No outbound HTTP client (would be a second API gateway / connector).
            if re.search(r"\bhttpx\b|\brequests\.(get|post|put|delete)\b|aiohttp", s):
                findings.append({"type": "outbound_http_client", "module": mod})

        # The composition must reference the authoritative integration reads.
        composed = _src("service.py") + _src("panels.py")
        if not any(a in composed for a in _AUTHORITATIVE_READS):
            findings.append({"type": "not_reusing_authoritative_reads"})
        # The authoritative Integration Platform must be composed (no second integration platform).
        if "integration." not in composed:
            findings.append({"type": "not_reusing_integration_platform"})

        # Explainability enforcement present.
        if "is_explainable" not in _src("model.py") or "is_explainable" not in _src("panels.py"):
            findings.append({"type": "explainability_not_enforced"})

        # Registry completeness + single ownership.
        for i in registry.INTEGRATION_REGISTRY:
            if not i.authoritative_owner or not i.connection_owner or not i.authentication_owner \
                    or not i.synchronization_owner or not i.provider_type or not i.deep_links:
                findings.append({"type": "integration_incomplete", "integration": i.key})
            if not i.runtime_gate:
                findings.append({"type": "integration_missing_gate", "integration": i.key})
        for c in registry.CONNECTOR_REGISTRY:
            if not c.protocol or not c.authentication or not c.polling_owner or not c.webhook_owner \
                    or not c.retry_owner or not c.monitoring_owner or not c.runtime_gate:
                findings.append({"type": "connector_incomplete", "connector": c.key})
        for d in registry.INTEGRATION_DASHBOARDS:
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
        for label, keys in (("integration", [i.key for i in registry.INTEGRATION_REGISTRY]),
                            ("connector", [c.key for c in registry.CONNECTOR_REGISTRY]),
                            ("panel", [p.key for p in registry.PANEL_REGISTRY]),
                            ("dashboard", [d.key for d in registry.INTEGRATION_DASHBOARDS])):
            if len(keys) != len(set(keys)):
                findings.append({"type": "duplicate_registry_ownership", "registry": label})

        if not gate.GATES:
            findings.append({"type": "no_governed_gates"})
    except Exception as exc:
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}]}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings}
