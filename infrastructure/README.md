# infrastructure/

Approved top-level location for infrastructure-as-code and deployment assets
(containers, environment manifests, CI helpers) as they are introduced by
approved backlog items.

**Status (E1.1):** placeholder. No infrastructure code is added here yet — the
existing CI (`.github/workflows/`) and test tooling (`scripts/`) remain
authoritative. Populating this directory is future, approved work; it is created
now only to establish the approved top-level structure additively (ADR-013).

**Never commit** secrets, credentials, or PII.
