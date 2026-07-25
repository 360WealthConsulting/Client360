# Change Evidence Registry (Phase D.63)

`CHANGE_EVIDENCE_REGISTRY` in `app/services/change_management/registry.py` is a declarative catalog of the **20
change-evidence entries**. Metadata only — it references evidence, never generates or certifies it. Thirteen
are configured (CI-produced, self-verified, or composed); seven are genuinely absent (`not_configured`).

## Configured

| Entry | Owner | Freshness | Note |
| --- | --- | --- | --- |
| ci_build_result | continuous_integration | per_commit | referenced, not live-read |
| e2e_result | continuous_integration | per_commit | referenced, not live-read |
| documentation_advisory_result | continuous_integration | per_commit | referenced |
| architecture_guard_result | continuous_integration | per_commit | referenced |
| governance_result | change_management | live | composed self-scan |
| migration_head_verification | observability.health | live | `_expected_head` |
| route_count_verification | architecture_manifest | live | `app.routes` + manifest |
| adr_verification | architecture_manifest | live | `docs/adr` + manifest |
| regression_result | continuous_integration | per_commit | referenced |
| code_quality_result | continuous_integration | per_commit | referenced |
| security_scan | security_operations | continuous | `security_summary` (security.view) |
| maintenance_window_record | observability.alerts | continuous | `alerts.metrics` |
| incident_correlation | observability.incidents | continuous | `incidents.metrics` |

**CI-produced evidence is referenced per-commit, not live-read** — a green build panel reflects the pipeline's
last recorded evidence and explicitly disclaims that it certifies production. **Self-verified live** evidence
(migration head, route count, ADR) is the app comparing declared-vs-live.

## Not configured (no authoritative owner — honest)

| Entry | Why not_configured |
| --- | --- |
| pull_request_approval | no live git / PR reader |
| deployment_verification | no deployment-execution owner |
| runtime_smoke_test | no smoke-test owner |
| rollback_test | no rollback owner |
| production_signoff | no production-verification owner |
| release_notes | no live git reader |
| post_change_review | no post-change-review owner |

## The honesty invariants

Every evidence panel enforces: **a green build is not production certification, a merged pull request is not
deployment, a version tag does not prove rollout, a clean migration check does not prove application health,
and an absent incident does not prove a successful change.**

## References
- `app/services/change_management/registry.py` (`CHANGE_EVIDENCE_REGISTRY`, `_ev`)
- `docs/ENTERPRISE_CHANGE_MANAGEMENT.md`, `docs/CHANGE_GOVERNANCE.md`, ADR-068
