"""Enterprise Configuration domain (Phase D.27) — authoritative platform-configuration domain.

Owns configuration governance metadata only: configuration categories/sets/items/versions,
environment overrides, tenant/organization/user preferences, feature groups/flags/rollouts,
editions/edition-capabilities/license-policies/edition-assignments, platform options, administrative
policies, runtime-setting references, snapshots, and changes — plus an append-only
``configuration_events`` audit ledger. It owns no business records and is never the source of truth
for operational or business entities.

Reuses (never replaces) the existing runtime configuration (``app.config``) and environment-variable
infrastructure (runtime-setting references only *point at* them), the RBAC ``capabilities`` (edition
capabilities reference existing capability codes), the Automation dispatch, the Analytics metric
registry, and the audit hash-chain. It references ``organization_profiles``/``users`` and never owns
them.
"""
