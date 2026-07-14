# Sprint 4.4 Release Candidate Validation

## Release candidate

- Pull request: #12
- Release candidate: RC4
- Branch: `feature/client-portal-secure-collaboration`
- Base release: v0.9.2
- Base migration: `e530f5b3d4e5`
- RC migration head: `f640a6c4e5f6`
- Validation date: July 14, 2026

## Overall result

**PASS — recommended for merge with public activation disabled.** The portal code, data isolation, migration, rollback, and provider abstractions are ready to merge. The portal must not be exposed to production clients until an approved MFA-capable identity provider, file scanning, retention, monitoring, penetration testing, and accessibility review are complete.

## Automated validation

| Area | Result | Evidence |
|---|---|---|
| Portal authentication | Pass | Portal accounts require active status, an MFA-verified provider subject, hashed sessions, expiration, and revocation. |
| Invitation flow | Pass | Hashed expiring invitation, MFA rejection, one-time acceptance, and account activation tested. |
| Delegated access | Pass | Delegated grant expands authorized household members and rejects another household. |
| Trusted-contact access | Pass | Explicit trusted grant and shared-household expansion tested. |
| Joint access | Pass | Explicit joint grant and both household people tested. |
| Self-only isolation | Pass | Self grant excludes another household member's private person, thread, task, request, and document scope. |
| Session isolation | Pass | Staff tokens do not resolve as portal sessions; portal tokens do not resolve as staff sessions. Device and revocation behavior tested. |
| Secure messaging | Pass | Portal/staff messages, client visibility, append-only storage, status, timeline, and audit tested. |
| Attachments | Pass | Authorized document attachment succeeds; cross-household attachment rolls back and is rejected. |
| Read receipts | Pass | Idempotent receipt creation and database immutability tested. |
| Document requests/uploads | Pass | Creation, scoped ownership, upload confirmation, version record, approval, timeline, and audit tested. |
| Workflow tasks | Pass | Client-facing selection, dashboard visibility, completion, and underlying workflow-state update tested. |
| Notifications | Pass | In-app delivery and idempotency tested; email hook records disabled status. SMS/push use the same disabled adapter. |
| E-signature abstraction | Pass | Fake provider registry, vendor-neutral request, completion event, persistence, and timeline publication tested. |
| Timeline publication | Pass | Messages, requests, uploads, approvals, signatures, and task-related flows publish canonical events. |
| Immutable audit | Pass | Portal audit records exist and PostgreSQL rejects mutation. Portal route mutations are generically audited. |
| Authorization boundaries | Pass | Portal identities never receive staff roles/capabilities; service and middleware boundaries are distinct. |
| Household privacy | Pass | Self, shared-household, and cross-household negative cases tested. |
| Internal-note exclusion | Pass | Internal staff message exists in the ledger but is absent from every portal message result. |
| API validation | Pass | OpenAPI includes versioned authentication, dashboard, profile, message, document, request, task, and notification contracts. |
| Startup/routes | Pass | FastAPI lifespan startup completed; 124 routes registered. |
| Templates | Pass | Login, dashboard, messages, documents, requests, tasks, notifications, and settings parsed and rendered. |
| Clean migration | Pass | Empty PostgreSQL database migrated from base to `f640a6c4e5f6`. |
| v0.9.2 upgrade | Pass | Database at `e530f5b3d4e5` upgraded to RC4. |
| Downgrade/re-upgrade | Pass | RC4 downgraded to `e530f5b3d4e5` and upgraded again. |
| Sentinel preservation | Pass | Client, assignment, task, document, and workflow remained present (1/1 each). |
| Migration lineage | Pass | Exactly one Alembic head: `f640a6c4e5f6`. |
| Full suite | Pass | 61 automated tests passed. |
| Python compilation | Pass | Application, migrations, and tests compiled successfully. |

## Manual validation

- Reviewed all eight portal pages using representative empty and populated contexts.
- Reviewed dashboard composition for client tasks, document requests, threads, workflow status, meetings, documents, and notifications.
- Walked invitation acceptance, MFA rejection, session creation, device registration, password-reset handoff, and revocation at the service boundary.
- Reviewed self versus joint/trusted/delegated scope expansion and cross-household negative paths.
- Confirmed internal notes remain staff-only while client-visible messages produce timeline and audit events.
- Confirmed the draft PR targets `main` and remains unmerged.

## Migration and data safety

The migration is additive from v0.9.2 and creates 15 tables without changing existing columns. Release v0.9.2 client, assignment, task, document, and workflow data is untouched. Downgrade removes portal identities, sessions, messages, requests, notifications, and signature records; it preserves every pre-existing CRM and workflow record.

Portal messages, receipts, and existing security audit events use database immutability triggers. Portal tokens, invitation tokens, reset tokens, and device fingerprints are stored only as hashes. Exactly one Alembic head remains.

## Known issues and operational gates

- No production portal identity provider is configured. Public activation must remain disabled until an MFA-capable provider adapter is approved and tested.
- No e-signature provider is enabled; only the provider-neutral interface and event model are present.
- Email, SMS, and push hooks are intentionally disabled. In-app notification is the only enabled channel.
- Staff administration for invitations and access grants is service-based; a dedicated administration UI is not included.
- File malware scanning, content disarm, quarantine, retention, and production object-storage controls depend on deployment infrastructure and require validation.
- No full browser automation dependency is installed. Route/OpenAPI and direct Jinja rendering passed; production browser, responsive, accessibility, and assistive-technology testing remain required.
- Penetration testing, rate limiting, bot protection, deliverability, privacy review, incident monitoring, and live-provider testing remain public-launch gates.
- The local urllib3 LibreSSL warning remains non-blocking; production should use a supported OpenSSL runtime.

## Production readiness and recommendation

RC4 is **merge-ready** and migration-ready. Merge PR #12 after code-owner and security architecture review. Treat the portal as **feature-disabled in production** until the external identity provider and operational security gates above are complete. Merging the schema and disabled provider architecture does not authorize public portal launch.
