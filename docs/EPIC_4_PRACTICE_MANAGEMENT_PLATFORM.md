# Epic 4 — Practice Management Platform

Status: Proposed technical design — implementation requires approval

Objective: Replace Wealthbox and TaxDome as the firm's secure, auditable day-to-day operating platform.

Scope: Architecture and implementation sequence only. This document does not authorize Sprint 4.1 implementation.

## 1. Outcomes and boundaries

Epic 4 delivers one operating system for wealth, tax, and insurance work:

- employees work from assigned queues, workflows, communications, documents, and client records;
- clients use a secure portal for requests, documents, questionnaires, messages, signatures, and scheduling;
- managers can see capacity, deadlines, blockers, service levels, and incomplete client actions;
- every material action is permission-checked and audit logged;
- existing Client360 people, households, tasks, activities, documents, timeline, Microsoft Graph, matching, and scheduler capabilities remain the system of record rather than being duplicated.

The epic does not yet implement tax-return parsing, portfolio intelligence, generalized AI recommendations, native accounting, or a custom e-signature cryptography platform. It creates extension points for those capabilities.

## 2. Current-state architecture to reuse

| Existing capability | Reuse in Epic 4 | Required evolution |
|---|---|---|
| FastAPI routes and Jinja workspaces | Staff and portal web surfaces | Separate staff and portal route namespaces, shared application services, authorization dependencies |
| PostgreSQL and Alembic | Transactional source of truth | Enforced migration discipline, row ownership, indexes, retention metadata |
| `people`, `households`, `household_relationships` | Client identity and access scope | Add companies, contact roles, portal memberships, household-level grants |
| Tasks and activities | Work items and activity history | Add assignment to teams, workflow linkage, dependencies, SLAs, queues, comments |
| Documents service | Canonical document record and safe downloads | Storage-provider abstraction, versions, requests, permissions, OCR/indexing, portal sharing |
| Timeline service | Unified client history | Publish workflow, message, signature, portal, call, and document events through the existing idempotent publisher |
| Microsoft OAuth, mail, calendar, Graph clients | Employee communications and scheduling | Correlation to conversations/workflows; delegated mailbox and calendar policies |
| Scheduler | Low-volume polling and deadlines | Durable job records, retries, idempotency, monitoring; retain APScheduler initially |
| Match/review patterns | Human review before canonical changes | Reuse for migration exceptions, unmatched messages/documents, and ambiguous portal invitations |
| Search | Staff lookup | Add permission-aware indexed search across work, messages, documents, companies, and OCR text |

### Required architectural rules

1. Route handlers validate input and authorize; application services contain business logic; repositories/data-access functions own SQL.
2. All automation is idempotent. Each job, workflow transition, external message, document, and signature envelope has a stable external or business key.
3. Portal access is deny-by-default and granted through explicit memberships. A relationship alone never grants portal access.
4. Sensitive files are never served from public paths. Downloads use authorization checks and short-lived delivery responses or signed URLs.
5. Audit events are append-only and separate from the client-facing timeline. The timeline answers “what happened for the client”; the audit log answers “who did what, when, from where, and to which protected record.”
6. Third-party vendors are isolated behind adapters for SMS, e-signature, object storage, OCR, and notifications.
7. Workflow definitions are versioned. Running workflow instances remain bound to the definition version with which they started.
8. No automatic creation or merging of canonical people from communications, imports, or portal activity.

## 3. Recommended implementation sequence

```text
4.1 Firm Identity, RBAC & Audit
  ├── 4.2 CRM Completeness & Communications
  └── 4.3 Workflow Foundation
          └── 4.4 Practice Operations & Dashboards
                  └── 4.5 Client Portal Core
                          └── 4.6 Secure Collaboration, E-sign & Scheduling
                                  └── 4.7 Document Intelligence & Search
                                          └── 4.8 Migration, Compliance & Cutover
```

Sprint 4.2 may overlap late 4.3 work after the authorization and audit contracts from 4.1 stabilize. Portal work waits until workflows and work queues exist; otherwise portal actions would create a second task/request model that later has to be replaced. OCR waits until document permissions, versioning, and sharing are stable so extracted text inherits the correct access controls.

## 4. Sprint designs

## Sprint 4.1 — Firm Identity, Teams, Authorization, and Audit

### Goal

Create the security and organizational foundation required by every staff and client-facing feature.

### Database changes

- `users`: employee identity, normalized email, status, authentication subject, last login, MFA state.
- `teams`: Wealth, Tax, Insurance, Operations, Compliance, and configurable future teams.
- `team_memberships`: user/team role, effective and inactive dates.
- `roles`, `permissions`, `role_permissions`, `user_roles`: normalized RBAC model.
- `record_assignments`: advisor, service owner, tax preparer, reviewer, and other assignments to people or households.
- `audit_events`: actor, action, entity type/id, request correlation ID, timestamp, IP/user agent, redacted before/after metadata.
- `user_sessions` or authentication-provider session references, including revocation timestamps.
- Add `created_by_user_id` and `updated_by_user_id` to mutable operational records where attribution is presently free text.
- Index active memberships, assignment lookup, audit entity/time, and normalized email.

### Services

- Authentication adapter supporting a managed OIDC provider; keep provider-specific code outside domain services.
- Authorization policy service with explicit permissions and record-scope checks.
- Team and record-assignment services.
- Append-only audit publisher with field redaction and request correlation.
- Current-user/request-context dependency for FastAPI.

### UI

- Sign-in/sign-out and access-denied views.
- Staff administration for users, teams, roles, and record assignments.
- Read-only audit viewer limited to compliance/administrators.
- Current team/role context in application navigation.

### APIs

- `/api/v1/session` for current identity and effective permissions.
- CRUD APIs for users, teams, memberships, roles, and assignments.
- Permission-filtered audit query API; no audit update/delete API.
- Health endpoint extensions for authentication configuration.

### Testing strategy

- Unit tests for permission matrices, team scope, inactive memberships, and redaction.
- Integration tests proving unauthenticated access is rejected and cross-team/record access is denied.
- Audit completeness tests for create, update, download, login, and denied access.
- Migration upgrade/downgrade tests and unique normalized-email constraints.
- Security tests for session fixation, CSRF on browser mutations, secure cookie flags, and open redirects.

### Dependencies

- Select OIDC provider and MFA policy.
- Define initial employees, teams, roles, and least-privilege permission matrix.
- Decide development/testing authentication bypass policy; never enable it in production.

### Acceptance criteria

- Every non-health staff route requires authentication.
- Role and record scope are enforced server-side, not only hidden in UI.
- Wealth, Tax, Insurance, Operations, and Compliance teams can be configured without code changes.
- Every protected mutation and document access produces a redacted, immutable audit event.
- Administrators can disable a user and revoke active access.
- Existing person, household, task, document, and timeline behavior remains available to authorized staff.

## Sprint 4.2 — CRM Completeness and Communication Hub

### Goal

Close the remaining Wealthbox replacement gaps for organizations, referrals, calls, meetings, emails, and communication history.

### Database changes

- `organizations`: businesses, professional firms, employers, referral partners, and vendors.
- `organization_contacts`: person/organization role and effective dates.
- `referral_sources` and `referrals`: source, referred client/prospect, owner, status, dates, attribution.
- `conversations`: channel, subject, client/household scope, owner, status, last activity.
- `conversation_participants`: internal users, canonical people, and unmatched external addresses.
- `messages`: provider ID, direction, channel, body/preview, sent/received time, delivery status, conversation ID.
- `call_logs`: direction, number, duration, outcome, notes, related person/household/workflow.
- `meeting_records`: calendar event linkage, purpose, attendees, outcome, follow-up status.
- Provider identity/deduplication constraints and normalized phone/email indexes.

### Services

- Organization and referral lifecycle services.
- Conversation threading/correlation service shared by email, SMS, and secure portal messages.
- Communication matching that reuses normalized email and unmatched-review patterns.
- Call logging and meeting outcome services.
- Timeline adapters that publish communication events without duplicating message bodies.

### UI

- Companies and referral-source directories with linked contacts and clients.
- Client Workspace Communication tab combining emails, meetings, calls, and later secure messages.
- Quick call log and meeting outcome forms.
- Unmatched communication review queue using established inbox-review interaction patterns.
- Referral pipeline report with ownership and conversion status.

### APIs

- Organization, contact-role, referral, conversation, message-summary, call-log, and meeting-record APIs.
- Microsoft sync endpoints reuse current Graph connectors and become permission protected.
- Provider webhook contract reserved for future telephony/SMS delivery events.

### Testing strategy

- Matching and ambiguity tests for normalized email/phone.
- Provider-ID deduplication and conversation-threading tests.
- Permission tests for communication bodies and referral reports.
- Timeline publishing tests ensuring retries do not duplicate events.
- Integration tests across Microsoft mail/calendar adapters with mocked Graph responses.

### Dependencies

- Sprint 4.1 authentication, users, teams, assignments, and audit.
- Confirm whether phone/SMS vendor selection belongs in 4.6; Sprint 4.2 supports manual calls without vendor dependency.
- Approved communication retention and sensitive-body display policy.

### Acceptance criteria

- Staff can manage organizations, professional contacts, referral sources, referrals, calls, and meeting outcomes.
- A client’s communication history shows matched email, calendar meetings, and logged calls chronologically.
- Repeated Microsoft synchronization creates no duplicate messages, meetings, or timeline entries.
- Ambiguous communication is queued for review and never creates a contact automatically.
- Referral ownership, source, stage, and conversion are reportable.

## Sprint 4.3 — Versioned Workflow and Automation Engine

### Goal

Build the reusable orchestration engine that replaces Wealthbox workflows and TaxDome pipelines.

### Database changes

- `workflow_definitions`: name, business line, status, current version.
- `workflow_definition_versions`: immutable JSON or normalized graph definition, publication metadata.
- `workflow_step_definitions`: type, sequence/dependencies, assignment rule, due-date rule, required inputs.
- `workflow_instances`: definition version, person/household, status, owner/team, started/completed/cancelled times.
- `workflow_step_instances`: state, assignee, due date, completion evidence, blocked reason.
- `workflow_events`: append-only state transitions with business/idempotency key.
- `automation_rules`: trigger, conditions, actions, active dates, version.
- `job_runs`: durable job state, attempts, next retry, error category, correlation ID.
- Extend `tasks` with workflow/step linkage, team assignment, dependency status, SLA timestamps, and completion evidence.

### Services

- Definition validation and publication service.
- Workflow runtime/state machine with explicit allowed transitions.
- Assignment rules: named user, client owner, team queue, round-robin, or role.
- Business-calendar due-date and SLA service.
- Automation dispatcher for task, document request, notification, meeting, and workflow actions.
- Durable scheduler facade using existing APScheduler to claim persisted jobs; preserves a future move to a separate worker.

### UI

- Workflow template builder using structured steps and conditions; no arbitrary executable code.
- Workflow launch, progress, blocked-step, reassignment, and cancellation views.
- Client Workspace Workflows tab.
- Staff queue views for assigned, team, waiting, blocked, overdue, and completed work.

### APIs

- Definition draft, validate, publish, archive, and version APIs.
- Instance launch, transition, reassign, cancel, and history APIs.
- Job retry/dead-letter administration API restricted to operations administrators.
- Webhook/event ingestion contract using signed requests and idempotency keys.

### Testing strategy

- State-machine property tests for legal/illegal transitions and terminal states.
- Version immutability tests proving running instances do not change after template publication.
- Assignment, business-calendar, dependency, retry, and idempotency tests.
- Concurrency tests preventing two workers from completing the same step.
- End-to-end tests for Prospect, New Client Onboarding, Annual Review, Service Request, and Tax Return workflows.

### Dependencies

- Sprint 4.1 users, teams, permissions, assignments, and audit.
- Business calendars, holiday rules, SLA definitions, and workflow owners.
- Five initial workflow maps approved by operational owners.

### Acceptance criteria

- Administrators can draft, validate, publish, and archive versioned workflow templates.
- Published templates cannot be edited in place.
- Launching a workflow creates correctly assigned and dated steps/tasks exactly once.
- Dependencies, waiting states, reassignment, cancellation, retries, and audit history work predictably.
- Prospect, onboarding, annual review, service request, and tax-return workflows pass approved scenario tests.

## Sprint 4.4 — Practice Operations, Service Levels, and Management Dashboards

### Goal

Turn workflows into the daily operating surface for every employee and manager.

### Database changes

- `work_queues`: configurable saved queue definitions and visibility.
- `service_level_policies`: business line, work type, response/completion targets, escalation rules.
- `work_item_status_history`: timestamps required for cycle-time and waiting-time calculations.
- `capacity_settings`: user/team working capacity, planned absence, effective dates.
- `escalations`: triggered policy, work item, owner, acknowledged/resolved state.
- Optional daily `practice_metric_snapshots` for historical reporting without expensive live aggregation.

### Services

- Permission-aware queue/query service.
- SLA clock that pauses in defined waiting states.
- Escalation and manager-notification service.
- Workload/capacity calculation service.
- Practice metrics: today, overdue, waiting on client, missing documents, pending signatures, throughput, age, and cycle time.
- Saved filters and export service with audit logging.

### UI

- Personalized home pages for each employee: today’s work, overdue, waiting, recently completed.
- Team dashboards for Wealth, Tax, Insurance, Operations, and Compliance.
- Manager workload and bottleneck views with drill-through to source records.
- Client Workspace service summary showing active workflows, requests, owners, deadlines, and blockers.
- Queue bulk assignment and controlled status actions.

### APIs

- Current-user work queue, team queue, manager metrics, SLA, escalation, and capacity APIs.
- Saved-view APIs with ownership and team sharing.
- Export endpoints use background jobs for large reports.

### Testing strategy

- Time-zone, holiday, pause/resume, and escalation-boundary tests.
- Query correctness and permission filtering across teams and assigned client books.
- Load tests for dashboard aggregation at target production volumes.
- Bulk-action atomicity/partial-failure tests.
- Metric reconciliation tests against source workflow events.

### Dependencies

- Sprints 4.1 and 4.3.
- Approved definitions for overdue, waiting on client, service levels, capacity, and escalation recipients.
- Production volume estimates and dashboard freshness target.

### Acceptance criteria

- Every employee can identify today’s assigned work and its client, deadline, workflow, and next action from one page.
- Managers can see team backlog, overdue work, waiting clients, missing documents, pending signatures, capacity, and bottlenecks.
- Dashboard totals reconcile to workflow/task source records.
- Unauthorized records never appear in counts, exports, search results, or drill-downs.
- SLA breaches escalate once, remain auditable, and can be acknowledged/resolved.

## Sprint 4.5 — Client Portal Core, Requests, and Onboarding

### Goal

Deliver a secure client-facing portal that operates on the same workflows, tasks, documents, and people as staff Client360.

### Database changes

- `portal_users`: client authentication subject, normalized email, status, invitation and verification state.
- `portal_memberships`: explicit person/household grant, role, permissions, effective/inactive dates.
- `portal_invitations`: hashed single-use token, expiry, inviter, accepted/revoked timestamps.
- `document_requests`: client/household, workflow step, requested category, instructions, due date, status.
- `questionnaire_definitions`, `questionnaire_versions`, `questionnaire_submissions`, `questionnaire_answers`.
- `portal_tasks`: presentation/access mapping to canonical workflow/task records rather than a separate task engine.
- `consents`: terms/privacy/electronic-delivery version and acceptance evidence.
- Extend documents with version, visibility, shared/revoked time, request linkage, malware-scan state, and storage key.

### Services

- Separate portal authentication and explicit membership authorization.
- Invitation, activation, recovery, MFA, and session-revocation services.
- Secure document upload/download with file validation, malware scanning adapter, and object-storage abstraction.
- Document request and questionnaire services linked to workflow step completion.
- Portal-safe client/task/workflow projection service that excludes internal notes and restricted metadata.

### UI

- Responsive portal sign-in, MFA, invitation acceptance, and account recovery.
- Portal dashboard: open tasks, document requests, questionnaires, appointments, recent documents.
- Secure upload/download and request completion.
- Versioned questionnaires and onboarding checklist.
- Household member/access management limited by policy; no implicit access from family relationships.

### APIs

- `/portal/api/v1` namespace with separate authentication dependencies and rate limits.
- Invitation acceptance, session, dashboard, task, document, request, questionnaire, and consent endpoints.
- Upload uses constrained file type/size, integrity hash, and scan state.
- No general-purpose portal access to staff APIs.

### Testing strategy

- Cross-household isolation and revoked-membership tests.
- Invitation expiry/replay, MFA, recovery, session revocation, CSRF, and rate-limit tests.
- Malicious filename, MIME mismatch, oversized file, malware-state, and download authorization tests.
- Questionnaire version and partial-save tests.
- End-to-end onboarding workflow where portal completion advances staff workflow steps exactly once.
- Accessibility testing against WCAG 2.2 AA target for core journeys.

### Dependencies

- Sprints 4.1, 4.3, and 4.4.
- Client identity provider/MFA decision, production domain, email delivery provider, object storage, malware scanning, terms/privacy language, retention policy.
- Portal access policy for spouses, trustees, powers of attorney, adult children, and business users.

### Acceptance criteria

- Invited clients can activate MFA-protected access and see only explicitly granted people/households.
- Clients can upload requested documents, download shared documents, complete tasks/questionnaires, and see onboarding progress.
- Staff can request, track, remind, revoke, and audit portal work from existing workflows.
- Internal notes, other households, staff-only documents, and restricted fields are never exposed.
- Portal actions update canonical Client360 records and timeline/audit history without duplicate models or events.

## Sprint 4.6 — Secure Messaging, SMS, E-signature, and Scheduling

### Goal

Complete the high-value collaboration capabilities needed to retire TaxDome and consolidate client communication.

### Database changes

- Reuse Sprint 4.2 conversations/messages; add secure-message channel and portal read receipts.
- `notification_preferences`, `notification_deliveries`, and provider delivery/error fields.
- `signature_envelopes`, `signature_recipients`, `signature_documents`, `signature_events`.
- `appointment_types`, `availability_rules`, `appointment_bookings` with Microsoft event linkage.
- `sms_consents`: phone, purpose, opt-in source/evidence, opt-out time, quiet hours/time zone.
- Provider webhook event table with signature validation status and idempotency key.

### Services

- Secure message service with thread membership and staff/client projections.
- Notification orchestration: secure message contains sensitive content; email/SMS contains only a safe notification and portal link.
- E-sign adapter and envelope lifecycle service; Client360 stores evidence and provider references, not custom signatures.
- Scheduling/availability adapter reusing Microsoft Calendar and conflict checks.
- SMS adapter with consent, opt-out, delivery status, and quiet-hours enforcement.

### UI

- Shared secure inbox for staff and portal clients.
- Signature request composer, recipient order, status, decline/void, and completed-document access.
- Appointment type administration and client self-scheduling/rescheduling/cancellation.
- Communication preferences and SMS consent management.
- Dashboard/workflow cards for unread messages, pending signatures, and upcoming appointments.

### APIs

- Secure conversation/message endpoints with attachment rules.
- Signed provider webhooks for e-signature, SMS, and scheduling updates.
- Signature envelope create/send/void/remind/status endpoints.
- Appointment availability, booking, rescheduling, and cancellation endpoints.
- Provider retries and webhook replay restricted to administrators.

### Testing strategy

- Conversation membership and attachment authorization tests.
- Provider contract tests using recorded/sanitized fixtures.
- Webhook signature, replay, out-of-order event, retry, and idempotency tests.
- SMS consent/STOP, quiet-hours, and sensitive-content tests.
- Calendar time-zone, daylight-saving, conflict, reschedule, and cancellation tests.
- E-sign recipient order, decline, void, completion, and evidence retention tests.

### Dependencies

- Sprints 4.2 and 4.5.
- Vendor selection and legal/security review for transactional email, SMS, and e-signature.
- Microsoft calendar permissions and scheduling policies.
- Approved consent, message-retention, signature-evidence, and notification-content policies.

### Acceptance criteria

- Clients and authorized staff can exchange auditable secure messages and attachments.
- Email/SMS notifications do not disclose sensitive message or document content.
- Staff can request signatures, track recipients, remind, void, and retrieve completed documents/evidence.
- Clients can book approved appointment types against real Microsoft availability without double booking.
- SMS is sent only with valid consent, observes opt-outs/quiet hours, and records delivery outcomes.

## Sprint 4.7 — Document Intelligence, OCR, and Permission-aware Search

### Goal

Make every authorized document discoverable and classifiable while preserving portal and staff access boundaries.

### Database changes

- `document_versions`: immutable version metadata, storage key, hash, source, scan state.
- `document_classifications`: type, confidence, method, reviewer, reviewed time.
- `document_text`: OCR/extracted text reference, page count, language, extraction version, redaction state.
- `document_index_jobs`: status, attempts, provider/model version, error category.
- `document_entities`: optional typed extractions such as tax year, form type, names, issuers; provenance required.
- PostgreSQL full-text search vectors and indexes; introduce external search only after measured need.
- Retention, legal-hold, disposition, and superseded-document metadata.

### Services

- Content-hash deduplication and immutable versioning.
- OCR adapter supporting PDFs and images, with durable jobs and retry/dead-letter handling.
- Deterministic/rules-first classification for 1040, W-2, K-1, trust, will, insurance, identity, estate plan, advisory agreement, and ADV; low confidence enters review.
- Permission-aware indexing/search service applying staff role, record assignment, portal membership, document visibility, and legal hold.
- Redaction and retention policy hooks.

### UI

- Unified document library in Client Workspace and portal.
- OCR/classification review queue modeled after existing unmatched-review patterns.
- Search with snippets, client/household, category, tax year, source, date, and access filters.
- Version history, classification confidence, processing state, retention/hold indicators.
- Staff document request and workflow linkage retained from 4.5.

### APIs

- Version upload/list/restore-metadata endpoints; content remains immutable.
- OCR/reindex/retry and classification review endpoints.
- Permission-aware search and faceting endpoints.
- Bulk retention/legal-hold operations restricted and audit logged.

### Testing strategy

- Golden-file OCR/classification tests using synthetic, non-client fixtures.
- Search relevance and metadata-filter tests.
- Permission-leak tests at query, count, facet, snippet, cache, and download layers.
- Duplicate hash/versioning, retry, corrupted/encrypted PDF, and large-file tests.
- Retention and legal-hold behavior tests.
- Performance tests at projected document/page volume.

### Dependencies

- Sprints 4.1 and 4.5; 4.6 for signed-document ingestion.
- OCR vendor/on-premises decision, data-processing agreement, regional storage, cost thresholds.
- Approved taxonomy, retention schedule, legal-hold roles, identity-document restrictions, and acceptable confidence thresholds.

### Acceptance criteria

- Supported PDFs/images are scanned, versioned, OCR processed, classified, and indexed through durable jobs.
- Authorized staff can search document text and metadata; portal users search only explicitly shared content.
- Low-confidence or failed documents enter a review queue without blocking unrelated processing.
- Search counts, facets, snippets, caches, and downloads reveal no unauthorized information.
- Document versions, classifications, reviewer decisions, retention changes, and access are auditable.

## Sprint 4.8 — Migration, Compliance Validation, and Production Cutover

### Goal

Prove operational equivalence, migrate safely, and retire Wealthbox/TaxDome only after controlled parallel operation.

### Database changes

- `migration_runs`: source, extract version, file hash, status, counts, reconciliation totals.
- `migration_records`: source ID to canonical ID, disposition, validation status, error/review reason.
- `cutover_checklists`: capability, owner, evidence, approval, rollback state.
- `data_quality_findings`: severity, entity, rule, assignee, resolution.
- `operational_incidents`: severity, affected capability, timestamps, resolution and postmortem linkage.

### Services

- Adapter-based Wealthbox and TaxDome migration orchestrator reusing existing import jobs and matching/review patterns.
- Reconciliation service for people, households, companies, tasks, workflow state, communications, documents, portal users, signatures, and activity counts.
- Data-quality rules and exception queues; never silently discard or auto-merge ambiguous records.
- Backup/restore verification, disaster recovery, monitoring, alerting, and operational runbooks.
- Parallel-run and cutover readiness scoring.

### UI

- Migration control center with per-run reconciliation and exception queues.
- Cutover checklist/dashboard with capability owners and evidence.
- Admin views for failed jobs, provider health, webhook backlog, storage/OCR status, and incidents.
- Staff/client onboarding guidance and in-product support links.

### APIs

- Restricted migration upload/run/reconcile/retry endpoints.
- Operational readiness and service health APIs.
- Read-only reconciliation exports with audit logs.
- No public source-system deletion API.

### Testing strategy

- Rehearsal migrations from sanitized production-shaped exports.
- Count, checksum, financial/control-total, relationship, document-hash, and workflow-state reconciliation.
- Restore drills, recovery-time/recovery-point measurement, failover, provider outage, backlog recovery, and alert tests.
- Security assessment, dependency scan, secrets scan, penetration test, accessibility review, and privacy/retention validation.
- Role-based user acceptance testing across advisors, tax, insurance, operations, compliance, and representative portal clients.
- Parallel-run comparison for at least one complete operational cycle defined by leadership.

### Dependencies

- Sprints 4.1–4.7 accepted.
- Executable Wealthbox/TaxDome export agreements and source retention plan.
- Compliance, legal, security, business continuity, training, and client communication approvals.
- Named cutover owner, rollback authority, support escalation tree, and success metrics.

### Acceptance criteria

- Migration rehearsals reconcile approved entities and documents to agreed tolerances with every exception assigned.
- No unresolved critical/high security findings remain.
- Backup restoration and provider-outage recovery meet approved objectives.
- Each business line completes UAT and signs the cutover checklist.
- Parallel operations demonstrate functional coverage and stable daily work without critical dependency on the legacy systems.
- Wealthbox/TaxDome are made read-only or retired only after executive, operations, compliance, and technology approval; rollback remains available for the agreed window.

## 5. Cross-epic data and service architecture

### Entity ownership

- People and households remain canonical client identity.
- Organizations model non-person entities; relationship intelligence may later provide richer graph edges without replacing organization/contact roles.
- Tasks remain canonical work items. Workflow steps link to or generate tasks rather than introduce a second task system.
- Documents remain canonical file records. Versions, requests, OCR, signatures, and portal sharing extend them.
- Conversations/messages become canonical communication records across Microsoft mail, portal, SMS, and future providers.
- Timeline events remain client history; audit events remain security/compliance evidence.

### Integration adapter contracts

Each external provider implements a narrow contract:

- Identity: authenticate, retrieve claims, revoke or invalidate sessions where supported.
- Storage: put, get delivery reference, delete subject to retention, verify integrity.
- Malware scan: submit, query result, quarantine.
- Notification: send safe notification, retrieve delivery state.
- SMS: send, delivery state, inbound message, consent/opt-out event.
- E-sign: create/send/void/remind envelope, fetch evidence/completed document, verify webhook.
- OCR: submit content, retrieve text/layout/confidence, report version/cost.
- Calendar: availability, create/reschedule/cancel booking.

Domain services accept provider-neutral commands/results. Provider payloads are retained only when required, encrypted where appropriate, and never drive business decisions without normalization.

### Background execution

APScheduler remains the trigger mechanism initially, but durable work lives in `job_runs`. A worker claims jobs using database locking, records attempts, uses exponential backoff, and sends exhausted work to an administrative dead-letter queue. This design permits a later move to a dedicated queue/worker without rewriting workflow, document, messaging, or integration services.

### API standards

- Versioned JSON APIs under `/api/v1` and isolated portal APIs under `/portal/api/v1`.
- Consistent error envelope, pagination, filtering, correlation IDs, and idempotency-key support for external/mutating operations.
- Browser forms use CSRF protection; APIs use explicit authentication and authorization dependencies.
- OpenAPI documents contracts but does not expose internal administration endpoints to portal clients.

## 6. Non-functional requirements

### Security and privacy

- MFA for staff and portal clients; least privilege; explicit household grants.
- Encryption in transit and at rest; secrets in a managed secret store.
- Field-level redaction for audit metadata and logs.
- Antivirus/malware quarantine before documents become downloadable or processable.
- Rate limiting, session revocation, secure cookies, CSRF protection, signed webhooks, and replay prevention.
- Vendor security review and data-processing agreements before transmitting client content.

### Reliability and operations

- Target availability, RPO, and RTO must be approved in Sprint 4.1 and verified in 4.8.
- Structured logs, metrics, traces/correlation IDs, provider health, job backlog, error rate, and SLA breach alerts.
- Idempotent retries and reconciliation for every provider integration.
- Feature flags for portal, messaging, signature, OCR, and migration rollout.

### Performance targets to finalize in Sprint 4.1

- Staff and portal page/API p95 latency.
- Search freshness and p95 query latency.
- Dashboard freshness.
- Maximum document size/page count and processing time.
- Concurrent employee/client sessions and daily message/document/workflow volume.

### Compliance and records management

- Retention schedule by record/document/message category.
- Legal holds override disposition.
- Audit export is permission restricted and tamper evident.
- Electronic consent and signature evidence are versioned and retained.
- Internal notes and sensitive compliance records have separate visibility policies.

## 7. Testing program across all sprints

Each sprint must include:

- unit tests for domain rules and deterministic calculations;
- database integration tests for constraints, transactions, concurrency, and migrations;
- API tests for validation, authentication, authorization, idempotency, and audit output;
- provider contract tests using synthetic/sanitized fixtures;
- browser tests for primary staff and portal journeys;
- regression tests for existing people, households, tasks, documents, matching, Microsoft sync, timeline, and search;
- security-negative tests proving denied access and absence of information leakage;
- compile/static checks and the full automated suite before a draft pull request.

No production client data or credentials may be committed to fixtures, screenshots, logs, or pull requests.

## 8. Decisions required before Sprint 4.1

Leadership and architecture review must resolve:

1. Managed identity provider for staff and clients, MFA methods, session duration, and account recovery policy.
2. Initial permission matrix, record assignment model, privileged roles, and segregation-of-duties requirements.
3. Production hosting, database, object storage, backups, secret management, monitoring, RPO/RTO, and availability targets.
4. Whether portal and staff identities share one identity tenant while remaining separate application audiences.
5. Retention, legal hold, audit access, internal-note visibility, client household access, and representative/POA policies.
6. Preferred vendors and review process for transactional email, malware scanning, SMS, e-signature, and OCR.
7. Service-level calendars, holidays, pause states, escalation rules, and workflow owners.
8. Wealthbox and TaxDome export availability, historical depth, source-system read-only window, and cutover tolerance.

## 9. Epic definition of done

Epic 4 is complete only when:

- staff authentication, authorization, assignments, and audit controls are production approved;
- primary client/contact/referral and communication workflows operate in Client360;
- versioned workflows drive prospect, onboarding, annual review, service, compliance, and tax operations;
- every employee and manager can operate from accurate, permission-aware queues and dashboards;
- clients can securely exchange documents/messages, complete work, sign, and schedule;
- documents are versioned, scanned, classified, indexed, and searched without permission leakage;
- migration, reconciliation, security, disaster recovery, UAT, training, parallel-run, and cutover approvals are complete;
- legacy systems are retired by an explicit business decision, not merely because feature development ended.

## 10. Approval gate

Approval of this document authorizes planning and implementation of Sprint 4.1 only. Each later sprint should begin from the accepted output of its dependencies and conclude with a draft pull request, migration/test evidence, manual validation steps, and unresolved decisions. No Sprint 4.1 application code should be written until this design and its open decisions are reviewed.
