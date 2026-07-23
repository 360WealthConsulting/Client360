# Policy Governance (Phase D.32)

`app/services/policy/governance.py::validate()` is a **read-only** validator of the policy registry and
its runtime definitions. It reads the registry, the in-code definitions, the D.27 runtime metadata, and
the RBAC capability catalog read-only (the runtime engine remains the sole evaluator) and returns a
structured report `{ok, issue_count, findings, coverage}`. It never raises and never edits metadata or
the registry.

## Finding types

| Finding | Meaning |
|---|---|
| `duplicate_policy` | Two active policies consume the same fixed runtime definition (an ambiguous decision owner). |
| `unreachable_policy` | An active in-code definition has no active/`in_domain` registry row (can never be reached), **or** a policy `depends_on` a policy that does not exist. |
| `orphan_policy` | A registry row (active/in_domain) has no in-code definition (can never be evaluated). |
| `circular_dependency` | The `depends_on` graph contains a cycle. |
| `missing_runtime_definition` | An authoritative policy (`requires_definition`, not per-instance) consumes a feature/config absent from the runtime metadata. |
| `deprecated_reference` | An active policy `depends_on` a deprecated/retired policy, or consumes a deprecated/archived feature flag. |
| `invalid_capability_reference` | A policy's `required_capabilities` names a capability not present in the RBAC catalog. |
| `governance_check_error` | The validator caught an unexpected error (never raised). |

## Coverage in the report

Beyond the registry coverage (decision-area coverage, adoption), the report adds **definition
coverage** — authoritative policies (those requiring a runtime definition, excluding per-instance)
whose complete runtime definition is present ÷ authoritative. Current: **100%**.

## Runtime integration

Every governance run respects the same invariants as evaluation: it consumes the runtime metadata
read-only, respects runtime authority (an authoritative policy's seeded D.31 definition must be
present), and reuses the runtime metadata reader — it never triggers a configuration evaluation.

## Recording

`governance.record_validation()` runs validation and records a firm-level
`policy_governance_validated` event to the D.28 `runtime_events` ledger (entity_type `policy`) + the
shared audit hash-chain. Routine evaluations are never recorded — only this major lifecycle event.

## Routes

`GET /runtime/policy/governance` (`runtime.audit`) — the report.
`POST /runtime/policy/governance/validate` (`runtime.admin`) — run + record.

## Current state

0 governance issues · decision-area coverage 100% · adoption 100% · definition coverage 100%.
