"""Runtime configuration governance (Phase D.31) — validation of runtime metadata + authority.

Validates the D.27 runtime metadata that backs the migrated/retired application behaviors: that every
authoritative behavior has its complete runtime definition present and evaluating enabled, that there
are no orphan/unused definitions or invalid edition mappings, and that no authoritative behavior
references a deprecated definition. It reads the runtime metadata and the behavior registry read-only
(the runtime engine remains the sole evaluator) and returns a structured governance report. It never
raises and never edits metadata.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import capabilities, engine, runtime_behaviors

from . import features, metadata_reader
from .common import now, record_event, write_audit

# The exact runtime definitions each authoritative behavior depends on (definition coverage source).
AUTHORITATIVE_DEFINITIONS = {
    "analytics.executive_metrics": {"flags": ("analytics.executive_metrics",)},
    "microsoft365.sync": {"flags": ("microsoft365.sync",)},
    "microsoft365.sharepoint_scope": {"config": ("microsoft365.sharepoint_site_ids",)},
    "benefits.detector_windows": {"config": (
        "benefits.new_hire_window_days", "benefits.open_enrollment_warning_days",
        "benefits.census_grace_days", "benefits.document_grace_days", "benefits.renewal_warning_days")},
    "advisor_workspace.sections": {"flags": (
        "advisor_workspace.section.work", "advisor_workspace.section.tasks",
        "advisor_workspace.section.exceptions")},
}

# Per-instance behaviors whose key space is unbounded — their prefixes are legitimate (not orphans).
_INSTANCE_PREFIXES = ("automation.job.", "reporting.module.")


def _authoritative_behaviors():
    with engine.connect() as c:
        return [dict(r) for r in c.execute(select(runtime_behaviors).where(
            runtime_behaviors.c.authoritative.is_(True))).mappings()]


def validate() -> dict:
    """Run every governance check and return ``{ok, issue_count, findings, coverage}``. Never raises."""
    findings = []
    try:
        flags = {f["code"]: f for f in metadata_reader.read_flags()}
        items = {i["code"]: i for i in metadata_reader.read_active_items()}
        rollouts = metadata_reader.read_active_rollouts()
        behaviors = _authoritative_behaviors()
        with engine.connect() as c:
            known_caps = set(c.scalars(select(capabilities.c.code)))
        referenced = set()

        # 1. every authoritative behavior must have its complete runtime definition present + enabled.
        covered = 0
        for b in behaviors:
            spec = AUTHORITATIVE_DEFINITIONS.get(b["code"], {})
            ok = True
            for fcode in spec.get("flags", ()):
                referenced.add(fcode)
                flag = flags.get(fcode)
                if flag is None:
                    findings.append({"type": "missing_definition", "behavior": b["code"], "definition": fcode})
                    ok = False
                elif not features.evaluate_flag(flag, active_rollouts=rollouts)["enabled"]:
                    findings.append({"type": "authoritative_definition_disabled", "behavior": b["code"],
                                     "definition": fcode})
                if flag is not None and flag.get("status") in ("deprecated", "archived"):
                    findings.append({"type": "deprecated_definition_reference", "behavior": b["code"],
                                     "definition": fcode})
            for icode in spec.get("config", ()):
                referenced.add(icode)
                if icode not in items:
                    findings.append({"type": "missing_definition", "behavior": b["code"], "definition": icode})
                    ok = False
            if b["code"] not in AUTHORITATIVE_DEFINITIONS:
                findings.append({"type": "missing_definition_spec", "behavior": b["code"]})
                ok = False
            covered += 1 if ok else 0

        # 2. unused / orphan definitions: active runtime flags not referenced by an authoritative
        #    behavior and not a legitimate per-instance prefix.
        for code, flag in flags.items():
            if code in referenced or any(code.startswith(p) for p in _INSTANCE_PREFIXES):
                continue
            if flag["status"] == "active" and flag["enabled"] and _looks_runtime(code):
                findings.append({"type": "unused_definition", "definition": code})

        # 3. invalid edition mappings / orphan capabilities.
        for ec in metadata_reader.read_edition_capabilities():
            if ec["capability_code"] not in known_caps:
                findings.append({"type": "orphan_capability", "capability_code": ec["capability_code"],
                                 "edition_id": ec["edition_id"]})

        # 4. invalid edition assignments (assignment referencing a non-active/missing edition).
        editions = {e["id"]: e for e in metadata_reader.read_editions()}
        for a in metadata_reader.read_edition_assignments():
            ed = editions.get(a["edition_id"])
            if ed is None or ed["status"] == "retired":
                findings.append({"type": "invalid_edition_mapping", "assignment_id": a["id"],
                                 "edition_id": a["edition_id"]})

        coverage = {"authoritative": len(behaviors), "covered": covered,
                    "coverage_pct": round((covered / len(behaviors)) * 100, 1) if behaviors else 100.0}
    except Exception as exc:   # never raise into a caller
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}],
                "coverage": {"authoritative": 0, "covered": 0, "coverage_pct": 0.0}}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings,
            "coverage": coverage}


def _looks_runtime(code: str) -> bool:
    """A flag that plausibly backs an application behavior (dotted namespace like ``x.y``)."""
    return "." in code and not code.startswith(("test", "e2_", "e2e"))


def record_validation(*, actor_user_id=None) -> dict:
    """Run governance validation and record a firm-level ``governance_validation_completed`` event."""
    report = validate()
    with engine.begin() as c:
        record_event(c, entity_type="governance", entity_id=0,
                     event_type="governance_validation_completed", actor_user_id=actor_user_id,
                     payload={"ok": report["ok"], "issue_count": report["issue_count"],
                              "coverage_pct": report["coverage"]["coverage_pct"], "at": now().isoformat()})
    write_audit("runtime.governance_validated", entity_type="governance", entity_id=0,
                actor_user_id=actor_user_id, metadata={"issue_count": report["issue_count"]})
    return report
