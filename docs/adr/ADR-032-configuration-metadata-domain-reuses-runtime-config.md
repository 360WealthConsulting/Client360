# ADR-032 — Enterprise Configuration as a governance-metadata domain that reuses the runtime config/env; never replaces runtime configuration, no runtime feature-toggle engine

## Status
Accepted

## Date
2026-07-22

## Decision owners
Platform Architecture; Domain Owner (Configuration); Security / Authorization (RBAC ownership);
Business Operations Owner (Michael Shelton — configuration/feature/edition requirements). Authorized
compliance reviewer: Not yet designated.

## Context
Runtime configuration already lives in `app/config.py` — typed env readers and boolean toggles
(`outbox_dispatcher_enabled`, `automation_enabled`, `automation_tick_interval_seconds`, the
`benefits_*`/`insurance_*` tunables), `ENVIRONMENT`/`IS_PRODUCTION`, `configuration_warnings()`, and
`validate_startup_configuration()` — plus `app/connectors/microsoft365/config.py` for M365. Feature
"flags" today are those env booleans; there is **no** feature-flag table/engine, **no** edition/
licensing infrastructure (the `license` hits are insurance-producer licenses), and **no** general
settings store (`security_configurations` is security-owned, D.25). Organizations are
`organization_profiles` (+ `relationship_entities`); users are `users`. Communications owns
`notification_preferences`/`notification_consents`.

There was **no** domain that governs *platform configuration* — the catalog of configuration items
and their versions/overrides, tenant/org/user preferences, feature flags/rollouts, editions/license
policies, platform options, administrative policies, and the change/snapshot record. The risk of
adding one is that it re-reads env / replaces the runtime config, becomes a runtime feature-toggle
engine, owns organizations/users, duplicates RBAC capabilities, or stores secrets.

## Decision
Enterprise Configuration is a new authoritative **platform-configuration domain** that owns
**configuration governance metadata only** and is **never the source of truth** for operational or
business entities.
- **Owns:** `configuration_categories` → `_sets` → `_items` → `_versions`, `_environment_overrides`,
  `_preferences` (tenant/organization/user via `scope`), `_feature_groups`/`_feature_flags`/
  `_feature_rollouts`, `_editions`/`_edition_capabilities`/`_license_policies`/`_edition_assignments`,
  `_platform_options`, `_administrative_policies`, `_runtime_setting_references`, `_snapshots`,
  `_changes`, and the **append-only** `configuration_events` ledger.
- **Reuses, never replaces the runtime config.** A configuration item / feature flag / runtime-setting
  reference *points at* an existing `app.config` function or env var (`runtime_setting_reference` /
  `env_var` / `loader_reference`) — Configuration **never re-reads env, never mutates
  `app.config`, and never changes runtime behavior**. Editing an item records governance metadata
  (value + version history); it does not change what the running process reads.
- **No runtime feature-toggle engine.** A flag's `enabled`/`rollout_percentage`/target roles/orgs/
  activation window are **governance metadata**; the runtime toggles remain the `app.config` env
  functions. Feature rollouts are a staged plan record, not an evaluator.
- **References organizations/users, never owns them.** Org/edition scope references
  `organization_profiles.id`; user preferences reference `users.id` and may carry a `reference`
  pointer to where the real preference lives (e.g. communications `notification_preferences`).
- **Edition capabilities reference RBAC, never replace it.** `edition_capabilities.capability_code`
  references the existing `capabilities.code` (validated to exist); assigning an edition **grants
  nothing at runtime** — RBAC (`role_capabilities`/`user_roles`) stays the sole authority for access.
- **Stores no secrets.** Sensitive configuration item values are withheld from responses unless the
  caller holds `configuration.audit` (server-side); runtime-setting references store only a human note,
  never a secret value.
- **Integrations:** **Automation** runs the `configuration_review` job (new dispatch handler +
  widened `JOB_TYPES` CHECK) — validating active items against their runtime-setting references and
  recording proposed configuration changes. **Security** governs configuration access (the
  `configuration.*` capabilities are RBAC-seeded); Configuration never replaces RBAC. **Observability**
  monitors configuration health (it may reference `observability_services`); Configuration stays
  authoritative for the metadata. **Analytics** consumes configuration statistics (enabled feature
  flags, configuration drift/overrides, active editions, pending changes); Configuration never depends
  on Analytics. **Timeline** receives approved, client-anchored lifecycle events only — configuration
  items are firm-level, so lifecycle events (configuration approved, feature activated, edition
  assigned, configuration archived) record to `configuration_events` and the guarded timeline publish
  skips them; **never emitted per setting update**.
- **Security of the domain itself:** capabilities `configuration.view/manage/execute/audit*/admin*`
  (`*` = sensitive), gated **in-route** (`/configuration` matches no middleware RULE). Record scope is
  enforced for organization-scoped preferences and edition assignments (ADR-004); sensitive
  configuration metadata stays server-side (ADR-005).

## Alternatives considered
1. **Have Configuration read/write `app.config` / env at runtime.** Rejected: the runtime config is
   the single source; Configuration governs metadata and references it. Wiring metadata → runtime is a
   separately-approved future change.
2. **Build a runtime feature-toggle evaluator.** Rejected this phase: flags are governance metadata;
   the env toggles remain authoritative. A real evaluator is a future ADR.
3. **Own organizations/users / grant capabilities via editions.** Rejected: Configuration references
   `organization_profiles`/`users`; RBAC remains the sole access authority; edition capabilities are
   references to `capabilities.code`.
4. **Reuse `security_configurations` (D.25).** Rejected: that table is security-owned (hardening
   posture). Configuration owns its own general config store.
5. **Emit a timeline/audit event per setting update.** Rejected: ADR-009 keeps the timeline curated;
   only approved lifecycle events are recorded (to the ledger, since config is firm-level).

## Reasons for the decision
The firm needs one authoritative model of *what is configurable, its versions and per-environment
overrides, which features/editions/licenses exist, and which configuration changes are pending/
approved* — with audit and analytics — without re-reading env, without replacing the runtime config,
without a runtime toggle engine, without owning organizations/users, and without duplicating RBAC. A
governance-metadata domain that references `app.config`/`capabilities`/`organization_profiles`
delivers this while preserving ADR-004, ADR-005, and ADR-009.

## Consequences
### Positive consequences
- One authoritative configuration-governance domain (catalog/preferences/features/editions/platform)
  referencing the existing runtime config, RBAC capabilities, and organizations/users.
- No re-read/replacement of the runtime config, no runtime toggle engine, no ownership of
  organizations/users, no duplicated RBAC, no stored secrets. Automation runs reviews; Analytics gains
  configuration metrics; the timeline is not spammed per setting.

### Negative consequences and tradeoffs
- Configuration items / feature flags / runtime-setting references are **descriptive metadata** —
  editing them does not change what the running process reads (wiring metadata → runtime enforcement
  is a future, separately-approved change).
- Edition assignment **grants nothing at runtime** — it is a governance record; RBAC still decides
  access.
- Configuration changes are a proposed→approved→applied *record*; "applied" is a metadata state, not
  an actuator.
- The D.22 `JOB_TYPES` CHECK constraints were widened again to admit `configuration_review` (a
  documented, reversible cross-domain migration touch).

## Enforcement
- `app/database/configuration_tables.py::define_configuration_tables` (registered in
  `app/database/schema.py`; reflected in `app/db.py`). Migration `y9c0d1e2f3a4` (19 tables +
  append-only `configuration_events` ledger with `prevent_configuration_event_mutation()` +
  `configuration_events_immutable` trigger + 5 `configuration.*` capabilities + widened automation
  `JOB_TYPES` + a configuration-category seed). Services `app/services/configuration/{common,catalog,
  preferences,features,editions,platform,scans,service}.py`. Routes `app/routes/configuration.py`
  (in-route `configuration.*` gating; `/configuration` matches no middleware RULE; sensitive item
  values gated by `configuration.audit`). Automation `configuration_review` handler in
  `app/services/automation/dispatch.py`. `app/config.py`, the env loaders, the M365 config, the RBAC
  tables, and the D.5 golden are untouched. Configuration is registered in `source_producer_modules`
  (must not import composition layers). Tests: `tests/test_configuration_platform.py`; manifest /
  platform-architecture / route-count guards updated.

## Exceptions
None currently approved. `administrator`/`record.read_all` scope bypass remains as defined by ADR-004.

## Revisit conditions
Wiring configuration metadata to runtime enforcement (a config service the running process reads),
building a runtime feature-flag evaluator, enforcing edition capabilities at the access layer, or
having Configuration own organizations/users would each warrant a new or superseding ADR.

## References
- `app/services/configuration/`, `app/routes/configuration.py`,
  `app/database/configuration_tables.py`, migration
  `migrations/versions/y9c0d1e2f3a4_configuration_platform.py`
- Reused infra: `app/config.py`, `app/connectors/microsoft365/config.py`, the RBAC
  `capabilities`/`roles`/`role_capabilities`, `organization_profiles`/`users`, the Automation
  dispatch, and the Analytics `Metric` registry
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_configuration_platform.py`; relates to ADR-004, ADR-005, ADR-009, ADR-016, ADR-027,
  ADR-030, ADR-031
