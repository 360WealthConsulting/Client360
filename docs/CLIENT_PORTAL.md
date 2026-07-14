# Client Portal and Secure Collaboration

## Architecture

The Client Portal is a separate security boundary within Client360. Portal accounts are not staff `users`, do not receive staff roles or capabilities, and cannot enter staff routes. The shared domain layer remains canonical: people, households, documents, workflows, meetings, timeline events, and audit events are reused rather than copied.

Portal APIs are versioned under `/api/v1/portal`. Jinja pages and APIs call the same scoped service functions so a future mobile client receives the same isolation guarantees.

## Identity and sessions

`portal_accounts` links a client identity to a canonical person. Invitations contain only hashed, expiring tokens. Invitation acceptance requires an MFA-verified identity-provider subject before the account becomes active. Password-reset tokens are hashed, one-time, short lived, and hand control back to the configured identity provider; Client360 does not store client passwords.

Portal sessions are distinct from staff sessions. Tokens are random, stored only as SHA-256 hashes, expire after eight hours by default, can be revoked, and record IP address, user agent, last activity, and a registered device fingerprint. Device fingerprints are hashed. Production authentication requires an external MFA-capable identity provider.

Public invitation acceptance never trusts a client-supplied subject or MFA flag. A configured `PortalIdentityProvider` must validate the opaque identity assertion and return a verified subject and MFA result. No production provider is enabled by default.

## Household and delegated access

Every portal query starts with active `portal_access_grants`. A grant identifies a household, optional person, access type, effective period, and explicit permissions.

Supported access types are:

- `self`: the client's own person and household context;
- `joint`: joint household access;
- `trusted`: a trusted contact with explicitly granted permissions;
- `delegated`: family or representative access to the granted household.

Joint, trusted, and delegated grants expand to current people in that household. A self grant retains household context but exposes only the linked person's private threads, requests, and documents. Cross-household requests are rejected even if a caller guesses a record ID. Grants can be ended without deleting audit history.

## Secure messaging

Threads belong to a household and optionally a person. Messages have exactly one portal or staff sender, support document attachments and append-only read receipts, and publish client-visible activity to the timeline. Messages and receipts are database immutable.

Staff messages can use `client` or `internal` visibility. Portal message queries always filter to `client`; internal notes are never returned to portal users or included in client notification payloads.

## Documents and requests

Document requests can be created independently or linked to workflow instances and steps. They support due dates, open/uploaded/approved/rejected states, upload confirmation, document ownership validation, version records, staff approval, timeline publication, and audit events. Uploaded content is stored through the existing Client360 document service.

## Client workflow tasks

A workflow step is portal-visible only when it is in an authorized household/person scope and either waits on `client` or has `assignment_config.audience = client` in its immutable definition snapshot. Clients can view and complete active steps; workflow dependency activation continues through the existing Sprint 4.3 engine.

## Notifications

Notifications use provider-neutral hooks. `in_app` is enabled by default. Email, SMS, and push providers are present as disabled hooks and record a disabled delivery result until configured. Stable idempotency keys prevent repeat delivery records.

## E-signature

`SignatureProvider` defines create, status, and cancel operations. The registry has no production provider enabled. `signature_requests` stores vendor-neutral request state and workflow links; provider completion events publish to the client timeline. DocuSign, Adobe Sign, or another provider can be added without changing workflow business logic.

## API contracts

- Authentication: invitation acceptance, password-reset request/consume, logout
- Dashboard: tasks, requests, recent threads, workflow progress, meetings, documents, notifications
- Messages: thread creation/listing, client-visible message listing, sending, attachments, read receipts
- Documents and requests: scoped lists and request upload
- Tasks: scoped lists and completion
- Notifications: list and mark read
- Profile: current portal identity

All endpoints use `/api/v1/portal`. Mutation responses are audited and include request IDs through middleware.

## Manual testing

1. Create an invitation through `invite_portal_account` for a non-production test client.
2. Accept it with an MFA-verified test-provider subject and confirm a device/session record.
3. Confirm the dashboard contains only the granted household.
4. Create a secure thread, send staff and client messages, and verify an internal note never appears in portal results.
5. Create a workflow-linked document request, upload a test file, approve it as staff, and verify version and timeline records.
6. Launch a workflow with a client-facing step, complete it in the portal, and verify downstream activation.
7. Verify in-app notification delivery and disabled email/SMS/push results.
8. Revoke the portal session and confirm subsequent API access is rejected.

Never use production client data for portal acceptance testing until the production identity provider, MFA policy, email domain, retention rules, and security monitoring are approved.
