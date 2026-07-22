"""Enterprise Integration platform (Phase D.24).

The authoritative integration domain for external-system connectivity metadata — providers,
connectors (instance/config/status), credential references, synchronization profiles/runs/conflicts,
webhook endpoints/subscriptions/deliveries, API clients/usage/rate-limits, event
definitions/subscriptions, and import/export profiles. It owns **integration metadata only** and is
**never a source of truth**; canonical domains remain authoritative. It **reuses** the existing
importers (``import_jobs``), Microsoft 365 OAuth/sync-health, the Fernet encryption helpers, the
transactional **outbox** as the event bus, the notification retry-policy shape, and the automation
dispatch registry — **never duplicating provider logic, never storing a plaintext secret, and with no
external broker**. Webhook delivery is metadata only (no outbound HTTP this phase). Automation
executes scheduled synchronization; Data Governance governs imported data quality; Analytics consumes
integration statistics; approved, client-anchored lifecycle events reach the Timeline.
"""
