"""Advisor AI Assist diagnostics (Phase D.42) — read-only operational telemetry.

Reports provider availability, active model, prompt versions, registered capabilities, source adapters,
and aggregate request/refusal/latency/citation counters. NEVER exposes prompt contents, secrets, or
client-sensitive payloads.
"""
from __future__ import annotations

from .common import assist_stats
from .context import _LABEL
from .provider import provider_diagnostics
from .registry import ASSISTANTS


def assist_diagnostics(principal=None) -> dict:
    stats = assist_stats()
    try:
        from app.services.runtime import consumption
        enabled = consumption.feature_enabled("advisor.ai_assist", default=True, shim=True)
    except Exception:
        enabled = True
    return {
        "feature_enabled": enabled,
        "provider": provider_diagnostics(),
        "capabilities": [
            {"identifier": a.identifier, "lifecycle": a.lifecycle, "prompt_version": a.prompt_version,
             "required_capability": a.required_capability, "model": a.model,
             "sources": list(a.required_sources)}
            for a in ASSISTANTS.values()],
        "source_adapters": sorted(_LABEL.values()),
        "requests": stats["requests"],
        "success": stats["success"],
        "failures": stats["failures"],
        "refusals": stats["refusals"],
        "refusal_rate": stats["refusal_rate"],
        "by_refusal": stats["by_refusal"],
        "timeouts": stats["timeouts"],
        "provider_failures": stats["provider_failures"],
        "malformed": stats["malformed"],
        "unsupported_questions": stats["unsupported_questions"],
        "unsupported_rate": (round(stats["unsupported_questions"] / stats["requests"] * 100, 1)
                             if stats["requests"] else None),
        "citation_coverage": stats["citation_coverage"],
        "avg_latency_ms": stats["avg_latency_ms"],
        "by_capability": stats["by_capability"],
        "record_scope_validated": True,
        "sensitive_field_exclusion": ["note_bodies", "contact_pii", "account_numbers"],
    }
