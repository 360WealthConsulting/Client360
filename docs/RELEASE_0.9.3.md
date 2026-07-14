# Client360 Release 0.9.3 — Client Portal and Secure Collaboration

Release date: July 14, 2026

## Overview

Release 0.9.3 delivers Sprint 4.4 of the Epic 4 Practice Management Platform.
It adds the first Client360 client-facing security boundary and collaboration
layer while reusing canonical households, people, documents, workflows,
meetings, timelines, and immutable audit records.

The release is merge- and migration-ready. Public portal activation remains
disabled until the production identity provider and operational security gates
in `RELEASE_1_0_READINESS.md` are complete.

## Capabilities delivered

- Separate portal accounts, MFA-verified invitation acceptance, password-reset
  handoff, device records, hashed sessions, expiration, and revocation.
- Explicit self, joint, trusted-contact, and delegated household grants.
- Dashboard for client tasks, document requests, secure threads, workflow
  progress, meetings, shared documents, and notifications.
- Secure portal/staff messaging, scoped document attachments, append-only read
  receipts, message status, timeline events, and immutable audit records.
- Internal staff notes that are structurally excluded from portal results.
- Individual and workflow-linked document requests, due dates, uploads,
  version history, confirmation, and staff approval.
- Client-visible workflow steps and completion through the Release 0.9.2 engine.
- Provider-neutral notification hooks with in-app enabled and email/SMS/push disabled.
- Provider-neutral e-signature request, status, completion-event, timeline, and
  workflow-link architecture with no vendor enabled.
- Eight portal pages and 16 versioned portal API paths.

## Database and migration

- Schema version: Release 0.9.3
- Alembic head: `f640a6c4e5f6`
- Parent: Release 0.9.2 head `e530f5b3d4e5`
- New tables: 15
- Migration lineage: exactly one head
- Supported paths: clean base-to-head, Release 0.9.2 upgrade, downgrade to
  Release 0.9.2, and re-upgrade

New tables cover portal accounts, grants, invitations, reset tokens, devices,
sessions, threads, participants, messages, receipts, attachments, document
requests, document versions, notifications, and signature requests.

## Validation

- 61 automated tests passed.
- Python compilation and FastAPI lifespan startup passed.
- 124 routes and 16 portal API paths registered.
- Eight portal templates rendered successfully.
- Clean migration and Release 0.9.2 upgrade/downgrade/re-upgrade passed.
- Sentinel client, assignment, task, document, and workflow data survived.
- Portal/staff sessions, self/shared household privacy, attachments, internal
  notes, immutable receipts/audit, workflows, notifications, and signatures passed.

## Known limitations and gates

- No production portal identity or e-signature provider is configured.
- Email, SMS, and push providers remain disabled.
- Invitation and grant administration remains service-based.
- File scanning, quarantine, retention, production object storage, rate
  limiting, bot protection, penetration testing, accessibility, monitoring,
  privacy review, and live-provider validation remain public-launch gates.
- The development urllib3/LibreSSL warning remains non-blocking; production
  should use a supported OpenSSL runtime.

## Recommended Sprint 4.5

Tax Practice Operations: tax-year cases, organizers, returns, extensions,
estimates, notices, amendments, tax document checklists, client tasks,
preparation/review/approval controls, deadlines, queues, dashboards, and
provider-neutral Drake/TaxDome acquisition adapters.

Sprint 4.5 is not included and has not started.
