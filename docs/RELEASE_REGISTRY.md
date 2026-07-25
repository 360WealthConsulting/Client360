# Release Registry (Phase D.63)

`RELEASE_REGISTRY` in `app/services/change_management/registry.py` is a declarative catalog of the **15
release-evidence entries**. Metadata only — it defines no CI/CD or Git platform and never alters Git state.
Seven entries are **self-verifiable** (configured); eight are genuinely absent (`not_configured`) because the
platform has no live git / CI / deployment reader — reported honestly, never fabricated.

## Configured (self-verifiable)

| Entry | Owner | Read surface | Expected verification |
| --- | --- | --- | --- |
| release_line | architecture_manifest | `manifest.meta` | release/0.13.0 |
| migration_head | observability.health | `_expected_head` | single alembic head |
| route_count | architecture_manifest | `app.routes` + `manifest.meta.route_count` | live == manifest |
| adr_count | architecture_manifest | `docs/adr` + manifest | sequential ADRs |
| client360_section_count | client360 | `client360.registry.SECTIONS` | guarded count |
| executive_dashboard_count | executive_intelligence | `DASHBOARD_REGISTRY` | guarded count |
| documentation_status | knowledge_management | `knowledge_summary` | docs advisory |

These are **self-verified live**: the layer compares the manifest-declared values against the live values the
app can observe (`len(app.routes)`, `_expected_head()`, the ADR glob, the live registries) and reports
declared-vs-live drift.

## Not configured (no authoritative owner — honest)

| Entry | Why not_configured |
| --- | --- |
| branch | no live git reader in the platform |
| pull_request | no live git / PR reader |
| merge_commit | no live git reader; **merged is not deployed** |
| version_tag | no live git reader; **a version tag does not prove rollout** |
| ci_status | CI evidence is referenced per-commit, not live-read |
| deployment_status | no deployment-execution / status owner |
| rollback_artifact | no rollback owner |
| production_verification_status | no production-verification owner; **green CI is not production certification** |

## References
- `app/services/change_management/registry.py` (`RELEASE_REGISTRY`, `_re`)
- `docs/ENTERPRISE_CHANGE_MANAGEMENT.md`, `docs/CHANGE_GOVERNANCE.md`, ADR-068
