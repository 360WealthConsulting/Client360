# Epic 5 — Tax Practice Platform

Status: Proposed technical design for review  
Source baseline: Client360 Release v0.9.3  
Current Alembic head: `f640a6c4e5f6`  
Implementation authorized by this document: none

## 1. Executive summary

Epic 5 will replace the firm's day-to-day TaxDome tax-practice functions with a
native Client360 tax operating platform. Tax records will reuse canonical
people, households, businesses, trusts, estates, relationships, documents,
timeline events, assignments, queues, workflow templates, approvals, portal
grants, notifications, provider adapters, and immutable audit events.

The tax domain must remain independent of Drake, UltraTax, Lacerte, CCH,
TaxDome, transcript vendors, e-file transmitters, and notification providers.
External systems acquire or deliver data through adapters. All matching,
lifecycle, workflow, security, reporting, deadline, and client-collaboration
logic operates on normalized Client360 models.

Epic 5 is divided into nine implementation sprints. Every sprint ends with one
linear Alembic head, clean-install and prior-release upgrade validation, full
regression testing, documentation, and a draft pull request. No sprint may
silently introduce a second assignment, queue, workflow, portal, document,
notification, or authorization engine.

## 2. Architectural principles

### 2.1 Canonical ownership

- `people`, `households`, `relationship_entities`, and `relationships` remain
  the source of truth for taxpayers, spouses, dependents, owners, trustees,
  executors, beneficiaries, officers, payroll contacts, and professionals.
- A tax engagement is the durable service relationship. A tax matter is the
  year/jurisdiction/return-unit of production within that engagement.
- Existing Client360 documents remain canonical binaries. Tax tables attach
  classification, checklist, request, tax-year, and source-fact metadata.
- Existing workflow instances and steps execute tax work. Tax models reference
  them; they do not contain a competing state machine.
- Existing assignments and queues own staff/team work distribution.
- Existing portal accounts and grants govern client access.
- Existing timeline and immutable audit events remain the enterprise history.

### 2.2 Provider-neutral integration

Tax acquisition uses `TaxPreparationProvider`, `TranscriptProvider`,
`EFileProvider`, and optional `TaxPaymentProvider` ports. Drake is the first
planned concrete acquisition adapter. UltraTax, Lacerte, and CCH receive stable
interfaces and contract tests without vendor implementations in early sprints.
Adapters emit normalized envelopes and provenance; they never update workflow,
assignment, or portal tables directly.

### 2.3 Security and data minimization

Tax data is sensitive. New capabilities are composed into roles rather than
hard-coded. Every API query must apply capability, office/team, assignment,
record, household, and delegated-portal scope. Transcript credentials, tax
identifiers, and provider secrets are never stored in general JSON, audit
metadata, logs, or client-visible responses. Sensitive identifiers are
encrypted or tokenized and masked by default.

### 2.4 Time and deadline model

All statutory deadlines are derived from versioned rule definitions with a
jurisdiction, return type, tax period, authority, legal basis, effective range,
weekend/holiday policy, extension rule, and source citation. Calculated
deadlines are persisted with the rule version used, then can be overridden only
with a reason and immutable audit event.

### 2.5 Idempotency and provenance

Imports, transcript pulls, e-file acknowledgements, portal submissions,
notifications, and generated workflow actions require stable idempotency keys.
Normalized facts retain provider, source record ID, acquisition time, import
run, and source document or payload hash.

## 3. Shared domain model

The target model is introduced incrementally. Proposed aggregate groups are:

- Firm configuration: `offices`, `office_memberships`, `tax_service_catalog`,
  `filing_jurisdictions`, `tax_deadline_rules`, `tax_deadlines`.
- Engagements: `tax_engagements`, `tax_engagement_parties`,
  `tax_engagement_services`, `tax_matters`, `tax_return_units`.
- Intake: `tax_organizer_templates`, `tax_organizers`,
  `tax_questionnaire_definitions`, `tax_questionnaire_responses`,
  `engagement_letter_templates`, `engagement_letters`.
- Documents: `tax_document_checklist_templates`, `tax_document_checklist_items`,
  `tax_document_links`, `tax_document_classifications`, `tax_missing_items`.
- Payments and exceptions: `tax_extensions`, `tax_estimated_payments`,
  `tax_notices`, `tax_notice_actions`, `tax_amendments`.
- Production: `tax_review_cycles`, `tax_review_findings`, `tax_efile_submissions`,
  `tax_efile_events`, `tax_delivery_events`.
- Integrations: `tax_provider_connections`, `tax_import_runs`,
  `tax_external_links`, `tax_transcript_requests`, `tax_transcript_artifacts`,
  `tax_normalized_facts`.
- Reporting: versioned operational snapshots or materialized views only when
  measurement proves live queries insufficient.

Names are proposals. Each sprint must confirm existing schema conventions and
produce an ADR for material deviations.

## 4. Reused Client360 architecture

| Need | Reused capability |
|---|---|
| Work ownership | `record_assignments`, assignment rules/history, team and supervisor roles |
| Process execution | Versioned workflow templates, dependencies, conditions, approvals, escalations, triggers, snapshots |
| Operational queues | `work_queues` criteria and authorized queue service |
| Client collaboration | Portal accounts, household grants, secure messages, requests, tasks, notifications |
| Documents | Canonical document storage, version records, Microsoft document links, review status |
| Communications | Microsoft mail/calendar matching and canonical timeline events |
| Authorization | Capabilities, roles, teams, office extensions, record scope, immutable denials/audit |
| History | `timeline_events` plus append-only `audit_events` and workflow events |
| Integrations | Provider/adapter pattern and normalized import runs |
| Reporting | Work capacity, SLA, dashboard, portfolio rollup, and API conventions |

## 5. Sprint plan and recommended order

1. **Sprint 5.1 — Tax Domain, Offices, Jurisdictions, and Deadlines**
2. **Sprint 5.2 — Return Lifecycle and Automatic Workflow Generation**
3. **Sprint 5.3 — Organizers, Questionnaires, and Engagement Letters**
4. **Sprint 5.4 — Tax Document Intelligence and Missing Information**
5. **Sprint 5.5 — Extensions, Estimates, Notices, and Amendments**
6. **Sprint 5.6 — Review, Approval, E-file, Delivery, and Compliance**
7. **Sprint 5.7 — Secure Tax Portal and Client Collaboration**
8. **Sprint 5.8 — Tax Provider and IRS Transcript Integration**
9. **Sprint 5.9 — Production Reporting, Capacity, AI Extensions, and Release Readiness**

This order minimizes rework: canonical identifiers and deadlines precede
workflow templates; workflows precede intake and document automation;
exceptions precede review/e-file; portal contracts follow stable tax services;
provider data maps into stable aggregates; reporting and AI use all completed
operational facts.

---

## 6. Sprint 5.1 — Tax Domain, Offices, Jurisdictions, and Deadlines

### Objectives

Establish the normalized tax foundation for individual, business, trust,
estate, nonprofit, and payroll work. Model multi-office ownership, engagement
parties, tax years/periods, filing jurisdictions, service types, return units,
and versioned statutory deadline rules without implementing production workflows.

### Database changes

Add offices and effective-dated memberships; tax service catalog; filing
jurisdictions; tax engagements and parties; engagement services; tax matters;
return units; deadline-rule versions; calculated deadlines and overrides.
Return units support 1040-family, corporate, S corporation, partnership,
fiduciary, estate, exempt organization, information, payroll, sales/use, and
configurable future forms. Use canonical relationship entities for non-person
filers. Add unique natural keys for engagement/service/period/jurisdiction/form
and checks for valid period ranges.

### Services

Tax engagement CRUD, party validation, service activation, period factory,
jurisdiction resolver, deadline calculation, holiday/calendar policy, override
service, and tax record-scope resolver. Deadline calculations must be pure and
explainable with rule/version output.

### APIs

`/api/v1/tax/engagements`, `/matters`, `/return-units`, `/jurisdictions`,
`/deadlines`, and office-scoped reference endpoints. Provide stable filters,
pagination, masked identifiers, and mobile-ready response schemas.

### UI

Tax engagement tab in Client Workspace; engagement/return-unit detail; office,
period, jurisdiction, form, due-date, status, and ownership displays; basic tax
search filters. No lifecycle action UI yet.

### Workflow integration

Define tax entity types and event envelopes only. Do not create a second state
machine or generate workflows until Sprint 5.2.

### Portal integration

No client pages. Mark which engagement parties and fields may later be portal
visible; default all tax records to staff-only.

### Assignment integration

Register tax engagement, tax matter, and return unit as supported assignment
entities. Reuse primary, secondary, supervisor, user, team, and history services.

### Queue integration

Define queue taxonomy and normalized filter fields but seed only safe foundation
queues such as unassigned tax work and deadline-data exceptions.

### Timeline integration

Publish engagement opened/closed, service added/ended, return unit created, and
deadline overridden. Do not publish sensitive identifiers or raw tax facts.

### Document integration

Allow canonical document links to engagement/matter/return-unit context without
classification. No duplicate binary storage.

### Microsoft integration

Add tax entity references to normalized mail/calendar event metadata. Matching
continues through existing people/households; no tax-specific mailbox polling.

### Reporting

Foundation counts by office, service, entity type, period, jurisdiction,
assignment, and due-date window. Validate query plans; do not add snapshots yet.

### Security

Add `tax.read`, `tax.write`, `tax.deadline.manage`, and sensitive tax-identifier
capabilities. Enforce office plus record scope. Encrypt/tokenize identifiers,
mask output, and append immutable audit events for reads of designated sensitive fields.

### Testing strategy

Unit tests for periods/deadlines/holidays; integration tests for CRUD, natural
keys, assignments, scope, office isolation, masking, timeline/audit, APIs, and
all entity types. Test deadline rules using published authority examples.

### Migration strategy

One additive revision with parent `f640a6c4e5f6`; no changes to portal or
workflow history. Validate empty base-to-head, v0.9.3 upgrade, downgrade to
v0.9.3, re-upgrade, one head, and sentinel preservation.

### Dependencies

Release v0.9.3 identity, relationships, assignment, timeline, audit, document,
and portal schemas.

### Acceptance criteria

Every supported filer/service/period/jurisdiction can be represented; deadline
calculation is versioned and explainable; office/record isolation passes;
existing data is unchanged; no workflow duplication exists.

### Risks

Form taxonomy sprawl, incorrect legal deadlines, entity ambiguity, SSN/EIN
exposure, and multi-office leakage. Mitigate with configurable catalogs,
authority-reviewed rules, tokenization, and negative scope tests.

### Recommended implementation order

Approve catalogs and security policy; add office/reference tables; add
engagement/matter/return aggregates; implement deadlines; register assignment
entities; add scoped services/APIs; add workspace UI/reporting; finish migration
and security validation.

---

## 7. Sprint 5.2 — Return Lifecycle and Automatic Workflow Generation

### Objectives

Implement return lifecycle management and automatic generation of versioned
workflows for every major return type, including seasonal assignment and queue behavior.

### Database changes

Add lifecycle state definitions/transitions if configuration is required, but
store execution in existing workflows. Add return-unit links to workflow
instances, lifecycle event projections, generation rules, and idempotency keys.
Avoid duplicating workflow steps or approval rows.

### Services

Tax lifecycle orchestrator, state-transition policy, workflow-template resolver,
automatic launch, seasonal assignment-rule inputs, deadline/SLA projection,
reopen/cancel coordination, and lifecycle read model. State changes must be
derived from or atomically coordinated with workflow events.

### APIs

Lifecycle actions, launch/relaunch, permitted transitions, progress, blocking
reasons, due-date risk, and batch generation under `/api/v1/tax/returns`.

### UI

Tax production board, return workspace, lifecycle actions, progress graph,
blocking reason, assignment, deadlines, approvals, and timeline. Bulk actions
require explicit capability and confirmation.

### Workflow integration

Seed immutable templates for 1040, 1065, 1120, 1120-S, 1041, 706, 990,
payroll, extension, notice, and configurable state/local returns. Templates use
dependencies, parallel document/preparation branches, conditions, independent
review, SLAs, escalations, and execution snapshots.

### Portal integration

Mark client-facing steps via existing `assignment_config.audience=client`; do
not expose the full internal workflow graph.

### Assignment integration

Use existing assignment rules for preparer, reviewer, manager, partner, office,
and specialty team. Record every automatic and manual reassignment.

### Queue integration

Seed reusable queues: not started, organizer pending, documents missing, ready
to prepare, in preparation, manager review, partner review, waiting on client,
ready to e-file, rejected, ready to deliver, overdue, and SLA risk.

### Timeline integration

Publish material lifecycle milestones, not every internal step. Include source
workflow/version and actor without sensitive return content.

### Document integration

Workflow steps may create document-request/checklist hooks but Sprint 5.4 owns
classification and missing-information semantics.

### Microsoft integration

Calendar deadlines and review meetings use canonical events; mail events may
trigger authorized workflow actions through existing event envelopes.

### Reporting

Return counts by lifecycle, office, team, preparer, reviewer, due date, SLA,
form, and period; workflow cycle-time and bottleneck metrics.

### Security

Reuse `work.read/write/approve`, tax capabilities, office/record scope, and
segregation of duties. Partner approval and transition overrides are sensitive capabilities.

### Testing strategy

Template validation; lifecycle transition matrix; idempotent generation;
parallel/conditional steps; automatic assignments; queues; reopen/cancel;
deadline/SLA; authorization; timeline/audit; all seeded return types; regression.

### Migration strategy

Add only tax-to-workflow link/configuration tables and seeded template versions.
Parent the single Sprint 5.1 head; preserve all in-flight non-tax workflows.

### Dependencies

Sprint 5.1 canonical tax IDs/deadlines and Release 0.9.2 workflow/work engines.

### Acceptance criteria

Each major return type launches one idempotent, version-pinned workflow; valid
actions and queues reflect execution; assignments and approvals reuse platform
services; lifecycle cannot bypass authorization or audit.

### Risks

Dual state divergence, template proliferation, accidental batch generation,
and seasonal reassignment overload. Mitigate with a single orchestrator,
idempotency, dry-run previews, bounded batches, and reconciliation jobs.

### Recommended implementation order

Approve lifecycle matrix; seed draft templates; implement orchestrator and
idempotent generation; connect assignments/queues; add APIs and UI; validate
all return types, transitions, segregation, deadlines, and reporting.

---

## 8. Sprint 5.3 — Organizers, Questionnaires, and Engagement Letters

### Objectives

Build versioned annual organizers, conditional questionnaires, engagement
letters, client/staff completion, signature-ready delivery, and intake workflow automation.

### Database changes

Add organizer/questionnaire/letter template versions, sections/questions,
response instances, response provenance, engagement-letter instances, parties,
status, rendered artifact links, consent/acceptance, and signature-request links.

### Services

Template publication/immutability, conditional question evaluation, prior-year
roll-forward with explicit client confirmation, answer validation, completion
scoring, letter rendering, signature-provider request creation, intake workflow triggers.

### APIs

Staff template and instance APIs plus portal-scoped organizer, questionnaire,
letter, consent, save-draft, submit, and status endpoints.

### UI

Staff template/version editor, organizer assignment/status, response review,
letter preview; portal organizer wizard, autosave, validation, letter review,
acceptance/signature status, accessible mobile layouts.

### Workflow integration

Organizer sent/submitted, questionnaire exceptions, and letter signed activate
existing workflow steps. Retries are idempotent; submission snapshots are immutable.

### Portal integration

Use portal identity/grants and client task visibility. Joint/delegated response
authority is explicit per organizer and letter party; internal review notes remain hidden.

### Assignment integration

Assign intake exceptions and response review through existing rules; route
special topics to tax, payroll, estate, or advisory teams.

### Queue integration

Organizer not sent, in progress, overdue, submitted, response exception,
engagement letter pending, signature failed, and ready for intake review.

### Timeline integration

Publish organizer/letter milestones only. Never publish individual answers or
tax identifiers to general timeline summaries.

### Document integration

Render letters and final organizer snapshots into canonical documents with
versions, hashes, retention class, and portal visibility. Attachments become
document requests/links, not inline binary blobs.

### Microsoft integration

Email hooks use provider-neutral notifications; matched Outlook replies may
create staff review events but do not become authoritative organizer responses.

### Reporting

Completion rate, aging, signature status, exception topics, portal adoption,
and intake conversion by office/team/service.

### Security

Question/answer field classification, encryption for designated sensitive
answers, client authority checks, draft privacy, signed-artifact immutability,
and audit of template publication, access, submission, and staff override.

### Testing strategy

Version immutability, branching questions, autosave/submit, roll-forward,
joint/delegated authority, signatures, workflow triggers, portal isolation,
document rendering, accessibility templates, authorization, and migrations.

### Migration strategy

Add intake tables on the Sprint 5.2 head. Published templates are immutable;
existing portal/session/workflow rows remain unchanged.

### Dependencies

Sprints 5.1–5.2, portal identity/grants, document versions, signature abstraction,
workflow triggers, notifications.

### Acceptance criteria

A client can securely complete a version-pinned organizer and questionnaire,
accept/sign a letter, resume drafts, and trigger intake work without seeing
internal notes or another taxpayer's data.

### Risks

Questionnaire complexity, sensitive answer leakage, ambiguous joint authority,
and render/signature mismatch. Mitigate with constrained schemas, field policy,
explicit parties, immutable snapshots, and artifact hash reconciliation.

### Recommended implementation order

Define/publish template schemas; implement draft/response services; add portal
save/submit; add letter rendering and signature links; connect workflows,
documents, assignments, and queues; finish staff UI and reporting.

---

## 9. Sprint 5.4 — Tax Document Intelligence and Missing Information

### Objectives

Deliver return-type/year-specific document checklists, client/staff uploads,
source-document classification, missing-information tracking, and review queues.

### Database changes

Add checklist template versions/items, instantiated checklist items,
tax-document links, classification labels/confidence/source/reviewer, extraction
provenance, missing-item records, resolution events, and duplicate-group references.

### Services

Checklist generator, return/document matcher, deterministic classification,
optional AI classifier port, duplicate detection using existing hashes,
missing-item calculator, review/override, portal request generator, workflow event publisher.

### APIs

Checklist, classification, missing item, review, bulk classify, request, resolve,
and exception endpoints with confidence/provenance and safe download links.

### UI

Tax document workspace, checklist progress, classification review, unmatched
documents, missing-information panel, side-by-side metadata, bulk review, and portal request status.

### Workflow integration

Checklist completion activates preparation; missing critical items block steps;
classification/review events are idempotent automation triggers.

### Portal integration

Create scoped requests through the existing portal, support versioned upload,
confirmation, rejection/re-request, due dates, and client-safe status. Never expose classifier internals.

### Assignment integration

Assign classification and missing-information follow-up to existing users/teams;
use automatic rules by office, return type, document type, and exception.

### Queue integration

Unclassified, low confidence, duplicate review, missing critical, waiting on
client, uploaded awaiting review, rejected, and checklist complete.

### Timeline integration

Publish document requested/received/accepted and checklist complete; exclude
document contents and extracted sensitive facts.

### Document integration

Canonical documents remain binaries. Tax links carry year/form/category;
versions and Microsoft-managed links are reused. Define quarantine/scanning states.

### Microsoft integration

Microsoft documents and attachments enter the same matching/classification
pipeline with original SharePoint/OneDrive links and provider provenance.

### Reporting

Checklist completion, missing-item aging, request turnaround, classification
accuracy, manual review volume, document volume by type/source, and bottlenecks.

### Security

Document capability and tax/portal scope both apply. Quarantined content is not
downloadable. AI/classifier inputs are minimized and provider use requires approval.

### Testing strategy

Checklist generation, classification contracts, confidence thresholds,
duplicates, missing/resolution, portal isolation, workflow blocking, Microsoft
links, versions, quarantine, authorization, timeline/audit, migration, regression.

### Migration strategy

Add metadata/link tables only; do not move or rewrite canonical documents.
Backfill is an explicit resumable job with dry-run counts and idempotency.

### Dependencies

Sprints 5.1–5.3, canonical documents, portal requests, Microsoft document sync,
workflow/queue/assignment services.

### Acceptance criteria

Every active return can generate an explainable checklist; received documents
are classified or queued; missing information drives authorized client/staff
work; no binary duplication or cross-client exposure occurs.

### Risks

Misclassification, malicious files, duplicate confusion, AI data leakage, and
high review volume. Mitigate with confidence gates, quarantine, hashes, human
review, provider policy, and workload metrics.

### Recommended implementation order

Define checklist/classification catalogs; instantiate checklists; add document
links and deterministic matching; add missing-item calculations; integrate
portal requests and workflow blocking; add review UI, metrics, and optional AI port.

---

## 10. Sprint 5.5 — Extensions, Estimates, Notices, and Amendments

### Objectives

Support exception-heavy tax work: federal/state extensions, estimated payments,
IRS/state/local notices, responses, deadlines, amended returns, and related client communications.

### Database changes

Add extension requests/status/payment estimates; estimated-payment schedules,
vouchers and confirmations; notice authority/type/date/response deadline;
notice actions; amendment reasons, original-return links, affected jurisdictions,
and financial-impact metadata. Sensitive amounts use fixed precision and policy controls.

### Services

Extension eligibility/deadline, estimate schedule, payment status, notice intake
and deadline calculation, notice triage, response plan, amendment orchestration,
workflow generation, escalation, and reconciliation.

### APIs

Extensions, estimates, payments, notices, actions, amendments, deadlines,
approvals, batch monitoring, and portal-safe status endpoints.

### UI

Extension dashboard, estimated-payment calendar, notice workspace, scanned
notice viewer, response checklist, amendment comparison, due-date risk, manager escalation.

### Workflow integration

Seed federal/state extension, estimate, IRS notice, state notice, payroll notice,
and amendment templates with independent approvals and statutory SLAs.

### Portal integration

Clients can view safe summaries, upload notices, acknowledge vouchers, approve
responses where configured, and receive in-app reminders. Internal analysis remains hidden.

### Assignment integration

Route by authority, office, entity type, specialty, amount/risk threshold,
manager, and partner. Preserve reassignment history.

### Queue integration

Extension decision, payment information missing, awaiting approval, voucher due,
notice untriaged, response due, authority follow-up, amendment preparation,
manager review, partner review, and overdue statutory deadline.

### Timeline integration

Publish extension filed/accepted/rejected, estimate delivered/confirmed, notice
received/responded/resolved, and amendment filed. Mask amounts based on capability.

### Document integration

Link notices, envelopes, transcripts, vouchers, proofs, responses, amended
returns, and authority acknowledgements to canonical documents and checklist items.

### Microsoft integration

Match notice/authority emails and response meetings; optionally create review
events from matched mail while retaining authoritative status in tax models.

### Reporting

Extensions by reason/status, estimate compliance, notice inventory/aging,
response SLA, amendment volume/cause, authority rejection trends, and exposure by office.

### Security

Sensitive amount/notice capabilities, partner approval thresholds, immutable
deadline overrides, portal-safe serializers, and audit of view/download/decision.

### Testing strategy

Federal/state deadline rules, payment schedules, notice triage, amendment links,
workflows, approvals, portals, queues, escalations, Microsoft events, masking,
audit, migrations, and edge dates.

### Migration strategy

Add exception aggregates on the Sprint 5.4 head. No conversion of historical
notes without reviewed import mappings and provenance.

### Dependencies

Deadlines, workflows, documents, portal, assignments, queues, approvals, notifications.

### Acceptance criteria

All extension/estimate/notice/amendment work has an accountable owner, statutory
deadline, workflow, document evidence, authorized client status, and immutable history.

### Risks

Incorrect deadlines, missed notices, unconfirmed payments, amount exposure, and
authority status ambiguity. Mitigate with reviewed rules, escalations, evidence,
masked output, and reconciliation.

### Recommended implementation order

Implement extensions and estimates first; add notice intake/deadlines; add
amendment links; seed workflows and queues; add portal collaboration and
Microsoft event links; add dashboards, reconciliation, and statutory-rule tests.

---

## 11. Sprint 5.6 — Review, Approval, E-file, Delivery, and Compliance

### Objectives

Implement preparer/manager/partner review, findings, independent approvals,
client approvals, e-file lifecycle, acknowledgements/rejections, delivery,
retention, and compliance reporting.

### Database changes

Add review cycles/findings/resolutions, e-file submissions/events, client
authorization references, delivery events, retention classes/holds if not
already platform-wide, and compliance evidence links. Reuse `work_approvals`.

### Services

Review-cycle coordinator, finding severity/resolution, segregation checks,
approval policy, e-file provider port, acknowledgement state reducer,
rejection remediation, delivery package, retention/evidence pack, reconciliation.

### APIs

Review/findings, approval requests/decisions, client authorization, e-file
submission/events/status, delivery, compliance evidence, and exception reports.

### UI

Preparer handoff, manager and partner review queues, finding workspace,
comparison/checklist, approval panel, e-file monitor, rejection repair, delivery
status, compliance evidence view.

### Workflow integration

Review and e-file are workflow steps with independent approval. Submission is
an idempotent automation action; provider events advance existing steps through
validated transitions. Reopen creates a new review cycle.

### Portal integration

Clients review safe return summaries/artifacts, approve or reject, complete
signature authorization, view e-file status, and retrieve delivery packages.

### Assignment integration

Use manager/partner/specialist roles and team capacity. The preparer cannot be
the independent reviewer where policy requires separation.

### Queue integration

Ready for manager review, findings open, ready for partner review, client
approval pending, ready to e-file, transmitted, rejected, accepted, ready to
deliver, delivery failed, and compliance exception.

### Timeline integration

Publish review approved, client approved/rejected, e-file transmitted/accepted/
rejected, and return delivered. Do not publish detailed findings.

### Document integration

Version and hash review copies, signature authorizations, filing copies,
acknowledgements, rejection diagnostics, delivery packages, and compliance evidence.

### Microsoft integration

Delivery and rejection communications may use notification/email adapters;
matched Outlook messages remain supporting communication, not e-file authority.

### Reporting

Reviewer productivity, findings/rework, first-pass yield, review aging, partner
queue, submission volume, acceptance/rejection rates, delivery SLA, and compliance exceptions.

### Security

`tax.review`, `tax.approve`, `tax.efile`, `tax.deliver`, and compliance
capabilities; database/service segregation; client-approval authority; artifact
download audit; retention/legal-hold enforcement; no provider credentials in payloads.

### Testing strategy

Reviewer independence, approval routing, finding lifecycle, client approval,
provider contract, duplicate submission, out-of-order events, rejection/retry,
delivery, retention, portal isolation, queues, audit, migrations, regression.

### Migration strategy

Add production/e-file aggregates and links. E-file events are append-only.
Provider cutover requires replay/reconciliation tooling and a rollback plan.

### Dependencies

Sprints 5.1–5.5, approvals, workflow actions, portal signatures, documents, notifications.

### Acceptance criteria

No return can be transmitted without required reviews/client authorization;
duplicate or out-of-order events are safe; acknowledgements reconcile; delivery
and compliance evidence are complete and authorized.

### Risks

Unauthorized filing, duplicate transmission, provider outage, lost
acknowledgement, weak segregation, and retention failure. Mitigate with policy
gates, idempotency, event ledger, reconciliation, independent review, and holds.

### Recommended implementation order

Implement review cycles/findings; enforce manager/partner approvals; add client
authorization; implement e-file port and event reducer; add rejection/retry and
delivery; add compliance evidence, reporting, portal views, and reconciliation.

---

## 12. Sprint 5.7 — Secure Tax Portal and Client Collaboration

### Objectives

Complete the tax-specific client experience across organizers, requests,
messages, tasks, approvals, estimates, notices, e-file status, delivery, and notifications.

### Database changes

Prefer none beyond portal preference/consent extensions, tax notification
preferences, communication purpose/retention metadata, and explicit authority
records if earlier models are insufficient. Do not duplicate tax records in portal tables.

### Services

Portal tax-dashboard composer, client-safe serializer/policy, delegated-authority
resolver, tax notification policy, secure thread routing, consent/preference,
delivery receipt, and portal activation readiness checks.

### APIs

Extend `/api/v1/portal/tax` for engagements, organizers, requests, tasks,
payments, notices, approvals, signatures, filing status, delivery, preferences,
and support. Maintain mobile-ready pagination and stable error schemas.

### UI

Tax portal landing page, annual tax checklist, organizer, secure tax messages,
document requests/uploads, estimates, notice status, approval/signature, filing
status, delivery center, settings, and accessible responsive states.

### Workflow integration

Client actions complete only explicitly client-facing steps and emit idempotent
events. Internal dependencies, reviewers, findings, queues, and notes remain hidden.

### Portal integration

Harden self/joint/trusted/delegated rules for tax authority. A grant to view
household documents does not automatically authorize signing or filing approval.

### Assignment integration

Client messages/requests route to existing assigned preparer/team/office with
supervisor fallback. No portal-specific assignment table.

### Queue integration

Portal message awaiting staff, client response due, upload awaiting review,
approval pending, signature pending, notification failed, and delegated-access exception.

### Timeline integration

Publish client-visible milestone actions and staff communication summaries;
exclude answers, return data, internal notes, and security/device details.

### Document integration

Apply scanning/quarantine, versions, safe preview/download, watermark policy,
delivery receipts, retention, and scoped delegated access.

### Microsoft integration

Staff replies may be coordinated with Outlook through provider hooks, but secure
portal threads remain authoritative and internal notes remain segregated.

### Reporting

Portal activation/adoption, organizer completion, upload turnaround, client task
aging, message response, notification delivery, signature completion, support volume.

### Security

Production identity adapter, MFA, session/device controls, rate limits, bot
protection, delegated authority, consent, field-level serialization, internal
note exclusion, file security, download audit, penetration and accessibility gates.

### Testing strategy

End-to-end role/grant matrix; self/joint/trusted/delegated signing authority;
portal/staff token isolation; messages; documents; quarantine; workflows;
approvals; notifications; responsive/accessibility; rate limits; audit; migration.

### Migration strategy

Minimal additive preference/authority changes. Preserve all Release 0.9.3
portal accounts, grants, sessions, messages, requests, and notifications.

### Dependencies

Sprints 5.1–5.6 and completion of Release 1.0 portal provider/security gates.

### Acceptance criteria

Authorized clients complete the complete tax collaboration journey without
seeing internal/staff/other-client data; signing authority is explicit; all
actions are traceable; public launch gates have evidence.

### Risks

Delegated overreach, internal-note leakage, malicious uploads, weak identity,
notification exposure, and accessibility failure. Mitigate with deny-by-default
policy, schema serializers, scanning, provider verification, and independent testing.

### Recommended implementation order

Finalize client-safe serializers and authority matrix; extend portal APIs;
build tax pages; connect messages/documents/tasks/approvals; add preferences and
notifications; complete browser, accessibility, security, and provider-gate validation.

---

## 13. Sprint 5.8 — Tax Provider and IRS Transcript Integration

### Objectives

Implement provider-neutral tax acquisition, the first Drake adapter, interface
contracts for UltraTax/Lacerte/CCH, and IRS transcript request/import architecture.

### Database changes

Add provider connections/config references, import runs, external links,
normalized facts/provenance, reconciliation exceptions, transcript requests,
consent/authorization references, transcript artifacts, cursors, and status events.

### Services

Provider registry, acquisition envelope, schema versioning, matcher, normalizer,
validator, idempotent upsert, reconciliation, quarantine, retry/backoff,
transcript authorization, request scheduler, parser ports, and fact conflict policy.

### APIs

Connection status, controlled import, dry run, mapping/reconciliation review,
run metrics, external links, transcript request/status/artifacts, retry/cancel,
and provider-health endpoints. Never return credentials.

### UI

Integration administration, import run/reconciliation, unmatched entities,
mapping exceptions, transcript authorization/status, provider health, and audit history.

### Workflow integration

Normalized provider events can activate workflows through existing triggers.
Adapters cannot directly mutate workflow state. Transcript facts may satisfy
checklists only after configured validation/review.

### Portal integration

Clients can grant/revoke transcript authorization and see safe request status;
raw transcripts and facts require explicit portal policy.

### Assignment integration

Provider/mapping/reconciliation exceptions use existing assignment rules by
office, provider, return type, and severity.

### Queue integration

Import failed, unmatched taxpayer, mapping conflict, reconciliation variance,
transcript authorization pending, provider delayed, transcript parse review,
and stale connection.

### Timeline integration

Publish import/transcript milestones and resolved exceptions; never publish raw
return/transcript data, credentials, or sensitive identifiers.

### Document integration

Store source exports and transcript artifacts as protected canonical documents
with hashes, provenance, retention, quarantine, and restricted download.

### Microsoft integration

Provider alerts arriving by mail may link to integration runs, but structured
provider events remain authoritative. No credential or transcript exchange by email.

### Reporting

Run volume/duration/failure, mapping success, reconciliation variance, data
freshness, transcript cycle time, provider SLA, retry count, and exception aging.

### Security

Secrets manager references, provider-specific least privilege, transcript
consent, encryption, field classification, restricted capabilities, audit,
payload redaction, retention, vendor/privacy review, and rate limits.

### Testing strategy

Contract suites for all provider interfaces; Drake fixture imports; duplicate/
changed/deleted records; mapping; reconciliation; malformed/quarantined input;
retry; credential redaction; transcript authorization/parser fixtures; scopes;
workflow events; migrations; regression.

### Migration strategy

Add integration/provenance tables. Historical TaxDome/Drake data migration is a
separate, resumable, reversible acquisition plan with dry-run reports and no
silent source deletion.

### Dependencies

Stable tax model from Sprints 5.1–5.7, provider architecture, documents,
matching, workflows, assignments, security, portal consent.

### Acceptance criteria

Drake data flows through the same normalized services as future providers;
UltraTax/Lacerte/CCH pass interface contract tests; transcript requests are
authorized, traceable, idempotent, and secure; exceptions are operationally visible.

### Risks

Vendor format drift, ambiguous source identifiers, credentials, transcript
regulation, rate limits, and reconciliation gaps. Mitigate with schema versions,
contracts, secrets management, consent, backoff, and explicit exception queues.

### Recommended implementation order

Freeze normalized envelopes and contract tests; add connection/import ledgers;
implement Drake dry-run and reconciliation; implement IRS transcript ports;
add exception queues/UI; validate security, retries, provenance, and future-provider contracts.

---

## 14. Sprint 5.9 — Production Reporting, Capacity, AI Extensions, and Release Readiness

### Objectives

Deliver tax dashboards, production/compliance reporting, staff productivity,
deadline calendar, capacity planning, seasonal balancing, multi-office and
partner/manager oversight, governed AI extension points, and Epic 5 release validation.

### Database changes

Prefer indexed views/queries. Add snapshot definitions/runs only for proven
performance or historical trend requirements; add report catalog, scheduled
report runs, export audit, metric definitions, and AI recommendation evidence/
decision records if platform equivalents do not exist.

### Services

Tax production metrics, deadline calendar, capacity forecast, seasonal demand,
workload balancing recommendations, productivity with explainable denominators,
office/manager/partner rollups, compliance reports, export service, AI context
builder, rule/recommendation port, evidence and human-decision capture.

### APIs

Dashboards, production funnel, deadlines, capacity, workload, productivity,
exceptions, compliance, scheduled reports, exports, AI recommendations/evidence,
and health/readiness under `/api/v1/tax/reporting`.

### UI

Advisor/preparer, manager, partner, office, firm, deadline, capacity, seasonal,
provider, portal, and compliance dashboards; report catalog; drill-down with
scope-aware totals; export controls and readiness console.

### Workflow integration

Metrics derive from workflow events/snapshots. Recommendations may propose but
never silently approve, file, deliver, reassign, or override deadlines.

### Portal integration

Portal reporting is limited to the client's authorized status and activity.
Firm productivity, internal capacity, findings, and AI recommendations are staff-only.

### Assignment integration

Capacity recommendations use existing assignments and history. Accepted
rebalancing calls the existing reassignment service and records actor/reason.

### Queue integration

All dashboard drill-downs resolve to reusable authorized queues; avoid separate
report-only definitions that disagree with operations.

### Timeline integration

Reports do not flood timelines. Material accepted recommendations or compliance
actions publish concise events with evidence links.

### Document integration

Scheduled/exported reports become protected canonical documents only when
retention or distribution requires it. Exports are watermarked, scoped, and audited.

### Microsoft integration

Deadline calendar can publish approved calendar views; scheduled reports may
use approved delivery hooks. Microsoft data contributes only within existing scope.

### Reporting

Tax pipeline, returns by stage/form/office, deadlines, extensions, notices,
review queues, e-file, delivery, missing information, portal adoption, staff
throughput/cycle time/rework, capacity, seasonal forecast, provider health,
audit/compliance exceptions, and production reconciliation.

### Security

Metric-level minimum cohort policy where appropriate, office/record scope,
sensitive export capability, immutable export audit, AI prompt/data policy,
source attribution, evaluation, human approval, retention, and no cross-client leakage.

### Testing strategy

Metric fixtures and reconciliation to operational rows; authorization and
negative drill-down; deadline/calendar; capacity/forecast; seasonal balancing;
exports; performance; AI evidence and forbidden actions; accessibility;
production-scale migration; backup/restore; full Epic regression and RC report.

### Migration strategy

Add only justified report/AI governance tables and indexes. Validate production-
sized timing/locks, clean install, full sequential upgrade from v0.9.3, downgrade
where safe, restore rehearsal, one head, and sentinel/reconciliation preservation.

### Dependencies

All prior Epic 5 sprints and the Release 1.0 readiness gates.

### Acceptance criteria

Operational and reported counts reconcile; every drill-down preserves scope;
deadline/capacity views are actionable; productivity definitions are approved;
AI outputs are explainable and human-controlled; Epic 5 passes release validation.

### Risks

Misleading productivity metrics, slow queries, report/data divergence, biased or
leaking AI context, excessive exports, and seasonal forecast error. Mitigate
with metric governance, reconciliation, query budgets, evidence, human approval,
export controls, confidence intervals, and performance tests.

### Recommended implementation order

Approve metric definitions; build reconciled live queries; add deadline/capacity
and productivity views; add governed exports/snapshots only where measured;
add AI evidence ports; complete production-scale, security, recovery, and RC validation.

## 15. Cross-sprint testing and quality gates

Every sprint must run:

- unit and PostgreSQL integration tests;
- full existing regression suite;
- Python compilation and application lifespan startup;
- route registration, OpenAPI generation, and template rendering;
- capability, office, team, record, household, delegated, and portal isolation;
- internal-note and sensitive-field exclusion tests;
- timeline, immutable audit, assignment, queue, workflow, and notification tests;
- clean-database migration, prior-head upgrade, downgrade/re-upgrade, sentinel preservation;
- `alembic heads` with exactly one result;
- static dependency/security scans when CI is available.

Release-candidate validation additionally requires production-volume performance,
lock timing, backup/restore, accessibility, browser, provider sandbox, privacy,
penetration, operational runbook, and disaster-recovery evidence.

## 16. Migration and backward-compatibility policy

- Sprint 5.1 begins at `f640a6c4e5f6`; each later revision has exactly one parent.
- Do not rewrite applied v0.9.x revisions.
- Prefer additive tables/nullable columns, explicit backfills, and expand/contract
  changes when zero-downtime deployment is required.
- Never use `metadata.create_all()` as a production prerequisite.
- Data backfills are resumable, observable, idempotent, and independently reversible.
- Downgrade limitations and destructive effects must be explicit before approval.
- Preserve v0.9.3 portal, workflow, assignment, document, relationship, portfolio,
  Microsoft, timeline, and audit data through every supported upgrade.
- Historical TaxDome migration is acquisition work with provenance and
  reconciliation, not direct table copying.

## 17. Epic acceptance criteria

Epic 5 is complete only when:

1. All supported tax services, entity types, periods, jurisdictions, deadlines,
   returns, exceptions, reviews, filings, deliveries, and client actions are normalized.
2. Staff can operate the full tax pipeline through authorized assignments,
   queues, workflows, approvals, dashboards, documents, messages, and reports.
3. Clients can securely complete organizers, requests, tasks, approvals,
   signatures, notice collaboration, and delivery within explicit authority.
4. Major return types have immutable, tested workflow templates.
5. Drake and transcript acquisition use provider-neutral interfaces; future
   UltraTax/Lacerte/CCH adapters require no tax-domain rewrite.
6. Production and compliance reports reconcile to operational data.
7. Tax-specific audit, privacy, retention, security, and segregation controls pass.
8. Clean install and sequential upgrade from v0.9.3 have one Alembic head and
   preserve sentinel/production-equivalent data.
9. Release 1.0 operational and production gates have documented evidence and approval.

## 18. Decisions requiring approval before Sprint 5.1

- Canonical distinction between engagement, matter, return unit, and filing.
- Initial supported form/jurisdiction catalog and authoritative deadline sources.
- Office model and whether office membership extends the existing team model or
  requires a separate regulated/reporting dimension.
- Tax identifier encryption/tokenization and key-management service.
- Data retention, legal hold, transcript authorization, and portal signing authority.
- Initial role/capability grants for preparer, reviewer, manager, partner,
  payroll, notice, transcript, e-file, and compliance work.
- Drake acquisition scope and TaxDome historical migration/coexistence plan.
- Production identity, signature, notification, file-security, and transcript provider decisions.

Approval of this design authorizes planning of Sprint 5.1 only. It does not
authorize implementation, migrations, vendor activation, or production launch.
