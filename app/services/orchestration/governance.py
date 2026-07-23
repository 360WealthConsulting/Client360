"""Workflow governance (Phase D.33) — validation of the orchestration registry + its definitions.

Read-only validation that every declarative workflow definition is well-formed and coherent: no
unreachable stages, no orphan transitions, no unproductive circular transitions, no duplicate workflow
ids, no missing policy references (a routing policy that the Runtime Policy Engine does not know), no
missing runtime dependencies, no invalid ownership, and no invalid completion paths. It reads the
registry, the in-code definitions, the policy registry, and the D.27 runtime metadata read-only (the
runtime engine remains the sole evaluator; the policy engine remains the sole decision engine) and
returns a structured governance report. It never raises and never edits anything.
"""
from __future__ import annotations

from . import registry, state
from .common import write_audit
from .definitions import ORCHESTRATION_DEFINITIONS

_ACTIVE_STATES = ("active", "in_domain")


def _unproductive_cycles(definition) -> list[str]:
    """Stages in a cycle from which no completion stage is reachable (a trap). Well-formed definitions
    always reach completion, so this is empty for them."""
    offenders = []
    stage_names = set(definition.stage_names)
    for name in stage_names:
        # a self-or-mutual cycle that cannot reach completion
        succ = {t["to"] for t in definition.transitions if t["from"] == name}
        if name in _closure(definition, succ) and not state.can_reach_terminal(definition, name):
            offenders.append(name)
    return offenders


def _closure(definition, start_set) -> set:
    seen, frontier = set(), list(start_set)
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for t in definition.transitions:
            if t["from"] == cur:
                frontier.append(t["to"])
    return seen


def validate() -> dict:
    """Run every workflow-governance check → ``{ok, issue_count, findings, coverage}``. Never raises."""
    findings = []
    try:
        rows = registry.list_definitions()
        by_code = {r["code"]: r for r in rows}
        defs = ORCHESTRATION_DEFINITIONS
        known_policies = _known_policies()
        runtime_defs = _runtime_definitions()

        # 0. duplicate workflow ids (a registry row with no code definition is also flagged).
        seen_codes = set()
        for r in rows:
            if r["code"] in seen_codes:
                findings.append({"type": "duplicate_workflow_id", "definition": r["code"]})
            seen_codes.add(r["code"])
            if r["code"] not in defs and r["status"] in _ACTIVE_STATES:
                findings.append({"type": "orphan_definition", "definition": r["code"]})

        for code, d in defs.items():
            row = by_code.get(code)
            if row is None or row["status"] not in _ACTIVE_STATES:
                findings.append({"type": "unreachable_definition", "definition": code})
                continue

            stage_names = set(d.stage_names)
            reachable = state.reachable_stages(d)

            # 1. unreachable stages.
            for name in stage_names - reachable:
                findings.append({"type": "unreachable_stage", "definition": code, "stage": name})

            # 2. orphan transitions (from/to not a declared stage).
            for t in d.transitions:
                if t["from"] not in stage_names:
                    findings.append({"type": "orphan_transition", "definition": code,
                                     "transition": f"{t['from']}-{t['action']}->{t['to']}", "missing": t["from"]})
                if t["to"] not in stage_names:
                    findings.append({"type": "orphan_transition", "definition": code,
                                     "transition": f"{t['from']}-{t['action']}->{t['to']}", "missing": t["to"]})

            # 3. circular (unproductive) transitions.
            for name in _unproductive_cycles(d):
                findings.append({"type": "circular_transition", "definition": code, "stage": name})

            # 4. missing policy references (a routing policy the policy engine does not know).
            for pcode in set(d.policy_refs) | set(d.transition_policies):
                if pcode not in known_policies:
                    findings.append({"type": "missing_policy_reference", "definition": code, "policy": pcode})

            # 5. missing runtime dependencies (a runtime feature/config not present in the metadata).
            #    Per-instance bases (automation.job, reporting.module) have unbounded key spaces and are
            #    never fully seeded — they are legitimate, not missing.
            for rref in d.runtime_refs:
                if rref not in runtime_defs and not any(rref.startswith(p) for p in _INSTANCE_PREFIXES):
                    findings.append({"type": "missing_runtime_dependency", "definition": code, "runtime": rref})

            # 6. invalid ownership.
            if not d.owner:
                findings.append({"type": "invalid_ownership", "definition": code})

            # 7. invalid completion paths.
            if not d.completion_stages:
                findings.append({"type": "invalid_completion_path", "definition": code, "detail": "no completion stage"})
            for cs in d.completion_stages:
                if cs not in stage_names:
                    findings.append({"type": "invalid_completion_path", "definition": code,
                                     "detail": f"completion stage {cs!r} not declared"})
                elif cs not in reachable:
                    findings.append({"type": "invalid_completion_path", "definition": code,
                                     "detail": f"completion stage {cs!r} unreachable"})
            for name in reachable:
                if not state.is_terminal_stage(d, name) and not state.can_reach_terminal(d, name):
                    findings.append({"type": "invalid_completion_path", "definition": code,
                                     "detail": f"stage {name!r} cannot reach a terminal outcome"})

        cov = registry.coverage()
    except Exception as exc:   # never raise into a caller
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}],
                "coverage": {"coverage_pct": 0.0}}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings, "coverage": cov}


_INSTANCE_PREFIXES = ("automation.job", "reporting.module")


def _known_policies() -> set:
    try:
        from app.services.policy import registry as policy_registry
        return {p["code"] for p in policy_registry.list_policies()}
    except Exception:
        return set()


def _runtime_definitions() -> set:
    try:
        from app.services.runtime import metadata_reader
        return ({f["code"] for f in metadata_reader.read_flags()}
                | {i["code"] for i in metadata_reader.read_active_items()})
    except Exception:
        return set()


def record_validation(*, actor_user_id=None) -> dict:
    """Run governance validation and record a firm-level ``governance_validated`` event to the shared
    audit hash-chain (a major lifecycle event; routine transitions are never recorded)."""
    report = validate()
    write_audit("orchestration.governance_validated", entity_type="orchestration",
                entity_id=0, actor_user_id=actor_user_id, metadata={"issue_count": report["issue_count"]})
    return report
