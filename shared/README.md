# shared/

Approved top-level location for cross-cutting, language-agnostic contracts
(e.g., event-envelope schemas, OpenAPI documents) shared across the application
and any future clients.

**Status (E1.1):** placeholder. No contracts are added here yet — the event
model and API contracts are introduced by later approved backlog items. This
directory is created now only to establish the approved top-level structure
additively (ADR-013). It is intentionally **not** a Python package.

**Never commit** secrets, credentials, or PII.
