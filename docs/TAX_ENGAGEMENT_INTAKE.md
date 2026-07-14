# Tax Engagement Intake and Client Collaboration

## Scope

Sprint 5.2 implements the front half of the tax engagement lifecycle. It turns a
Release v0.9.4 tax return into a coordinated engagement letter, year-specific
organizer, conditional questionnaire, document checklist, missing-item queue,
portal experience, notifications, and automatic workflow progression.

## Architecture

The intake service is an orchestration layer. It owns intake templates,
launch-time snapshots, answers, checklist state, and missing-item state. It
reuses existing Client360 records for every cross-cutting concern:

- `workflow_instances` and `workflow_steps` for process execution;
- `record_assignments` for preparer/team ownership;
- `portal_document_requests`, `documents`, and `document_versions` for uploads;
- `portal_notifications` and provider-neutral notification hooks for reminders;
- `timeline_events` for client history;
- immutable `audit_events` for evidence;
- portal grants and tax capabilities for record-level access.

No tax-preparation, e-signature, email, or SMS vendor is embedded in the intake
domain. Templates are versioned; published template headers are immutable; each
engagement stores a complete launch snapshot so later versions cannot change an
in-flight intake.

## Intake launch

Creating a tax engagement automatically selects published templates by return
audience, creates the engagement letter, organizer, questionnaire, checklist,
missing items, and portal document requests, then sends idempotent engagement
and organizer notifications. Repeated manual launch requests return the existing
intake rather than duplicating it.

The seeded templates cover individual and business intake. Other Release v0.9.4
return categories use the published fallback checklist until category-specific
template versions are approved.

## Milestones and workflow advancement

Four readiness gates are calculated from source records:

1. engagement letter accepted;
2. organizer completed;
3. all visible required questionnaire questions answered;
4. all required checklist documents received.

The first three gates complete the existing `intake` workflow step. The document
gate then completes the existing `documents` step, activating preparation. The
service never creates a parallel workflow engine and does not bypass approval or
dependency enforcement.

## Questionnaire logic

Questions have stable keys, response types, display order, required flags,
configuration, and equality-based conditions. Hidden conditional questions are
not treated as required. Answers are upserted for saved progress; completion is
rejected while any visible required answer is absent.

## Documents and missing information

Checklist templates define required/optional items, due-day offsets, and
conditions. Each launched item links to an existing portal document request.
Portal upload confirmation automatically synchronizes checklist and missing-item
state. Optional missing documents do not block preparer readiness.

The daily scheduler sends idempotent in-app reminders for overdue missing items
and incomplete questionnaires. Email, SMS, and push providers remain disabled
unless separately configured through the existing provider layer.

## APIs

Staff APIs under `/api/v1/tax/intake` provide:

- dashboard and readiness metrics;
- published template catalog;
- intake detail and idempotent launch;
- organizer and questionnaire progress/completion;
- document-state synchronization;
- controlled reminder processing.

Portal APIs under `/api/v1/portal/tax/intake` provide scoped intake listing,
letter acceptance, organizer progress, questionnaire progress, and document
state synchronization. Upload bytes continue through the existing portal
document-request endpoint.

## Security

Staff routes require `tax.intake.read` or `tax.intake.write`. Collections use
the Release v0.9.4 tax record filter. Portal operations validate person and
household grants plus `tasks` or `documents` permission. Acceptance, completion,
launch, and workflow milestones publish immutable audit evidence.

## Operational checklist

1. Review and publish firm-approved letter, organizer, questionnaire, and
   checklist versions.
2. Confirm portal grants include task and document permissions.
3. Launch a representative individual and business engagement in staging.
4. Accept the letter, save/resume responses, exercise a conditional question,
   and upload every required document.
5. Confirm preparation activates only when all four gates pass.
6. Review notification wording and the daily 9:00 AM Eastern reminder schedule.

## Known limitations

- Seeded content is a structural baseline and requires legal/tax review before
  client use.
- The UI presents status and progress; rich browser-based template authoring and
  drag-and-drop questionnaire design are future work.
- Equality conditions are supported; compound expression builders are deferred.
- Email/SMS/push delivery remains disabled by default.
- Engagement e-signature uses acceptance evidence here; provider-backed signing
  can be layered onto the existing signature-provider abstraction later.
