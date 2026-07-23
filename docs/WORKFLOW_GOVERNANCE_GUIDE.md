# Workflow Governance Guide (Phase D.33)

`app/services/orchestration/governance.py::validate()` is a **read-only** validator of the workflow
registry and its definitions. It reads the registry, the in-code definitions, the policy registry, and
the D.27 runtime metadata read-only (the runtime engine remains the sole evaluator; the policy engine
remains the sole decision engine) and returns `{ok, issue_count, findings, coverage}`. It never raises
and never edits anything.

## Finding types

| Finding | Meaning |
|---|---|
| `unreachable_stage` | A declared stage not reachable from `initial_stage` via the transition graph. |
| `orphan_transition` | A transition whose `from`/`to` is not a declared stage. |
| `circular_transition` | A stage in a cycle from which no terminal outcome is reachable (a trap). Legitimate re-entry (e.g. `waiting ↔ active` that can still reach completion) is not flagged. |
| `duplicate_workflow_id` | Two registry rows with the same definition code. |
| `orphan_definition` | A registry row (active/in_domain) with no in-code definition. |
| `unreachable_definition` | An in-code definition with no active/in_domain registry row. |
| `missing_policy_reference` | A routing `policy` (or `policy_refs` entry) the Runtime Policy Engine does not know. |
| `missing_runtime_dependency` | A `runtime_refs` feature/config absent from the runtime metadata (per-instance bases like `automation.job` are exempt). |
| `invalid_ownership` | A definition with no `owner`. |
| `invalid_completion_path` | No completion stage, a completion stage undeclared/unreachable, or a non-terminal stage that cannot reach a terminal outcome. |
| `governance_check_error` | The validator caught an unexpected error (never raised). |

## Coverage in the report

`registry.coverage()` reports domain coverage (the ten orchestration domains with a registered
definition ÷ ten = **100%**) and adoption (`active` ÷ migratable, `in_domain` excluded as documented
exceptions — **100%**).

## Runtime + policy integration

Governance consumes the policy registry and the runtime metadata read-only — it never triggers a
configuration evaluation and never makes a business decision. An `active` definition whose routing
policy is missing, or whose runtime dependency is absent, is flagged (but never auto-fixed).

## Recording

`governance.record_validation()` runs validation and records an `orchestration.governance_validated`
event to the shared audit hash-chain (a major lifecycle event). Routine transitions are never recorded.

## Routes

`GET /orchestration/governance` (`workflow.audit`) — the report.
`POST /orchestration/governance/validate` (`workflow.admin`) — run + record.

## Current state

0 governance issues · domain coverage 100% · adoption 100% (2 active / 13 in-domain).
