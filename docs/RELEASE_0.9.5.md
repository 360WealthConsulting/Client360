# Client360 Release 0.9.5 — Tax Engagement Intake & Client Collaboration

Released July 14, 2026 from merge commit
`811b7030ec195de03a3cb83062688174e86a0eb4`.

## Overview

Release 0.9.5 replaces the front half of TaxDome's tax workflow with native
Client360 intake. New tax engagements now receive versioned letters, organizers,
questionnaires, document checklists, missing-item tracking, portal actions,
notifications, readiness metrics, and automatic workflow advancement.

## Schema

- Schema version and Alembic head: `h860c8e6a7b8`.
- Parent: Release v0.9.4 / `g750b7d5f6a7`.
- New tables: `engagement_letter_templates`, `tax_engagement_letters`,
  `tax_organizer_templates`, `tax_organizers`,
  `tax_questionnaire_templates`, `tax_questionnaire_questions`,
  `tax_questionnaires`, `tax_questionnaire_answers`,
  `tax_checklist_templates`, `tax_checklist_template_items`,
  `tax_checklist_items`, and `tax_missing_items`.
- Exactly one Alembic head is maintained.

## Engagement intake

- Published template definitions are immutable and versioned.
- In-flight engagements retain launch-time snapshots.
- Engagement letters record portal-account acceptance, time, and evidence.
- Individual and business organizers support year-specific saved progress.
- Questionnaires support stable keys, required answers, conditional visibility,
  saved progress, and completion validation.
- Required and optional checklist definitions generate existing portal document
  requests with due dates.
- Upload confirmation uses the existing document/version system and resolves
  linked missing-information records automatically.

## Portal and APIs

Clients can view intake status, accept letters, save and complete organizers,
answer conditional questionnaires, upload requested documents through existing
portal requests, and monitor overall progress.

Thirteen versioned staff and portal operations are available under
`/api/v1/tax/intake` and `/api/v1/portal/tax/intake`. Staff receive readiness,
missing-item, and overdue metrics through `/tax/intake`; clients use
`/portal/tax-intake` and the existing portal dashboard.

## Platform integration

- Existing tax engagement creation launches intake automatically.
- Existing workflow steps advance only after objective intake gates pass.
- Existing portal document requests and document versions own uploads.
- Existing notification providers deliver idempotent engagement, organizer,
  missing-document, questionnaire, and completion notifications.
- Existing timeline, immutable audit, capability authorization, record scope,
  household grants, assignments, and queues remain authoritative.
- Daily intake reminders run at 9:00 AM Eastern through the existing scheduler.

## Validation

- Full suite: 69 passed; focused RC6 suite: 39 passed.
- Clean PostgreSQL base-to-head migration passed.
- Upgrade from v0.9.4, downgrade, Sprint-artifact cleanup, sentinel preservation,
  and re-upgrade passed.
- Compilation, Uvicorn startup, 142-route registration, 13 intake API contracts,
  staff and portal templates, staff/portal isolation, workflow advancement,
  documents, notifications, timeline, and immutable audit checks passed.

See [Sprint 5.2 Release Validation](SPRINT_5_2_RELEASE_VALIDATION.md) for the
complete evidence and risk assessment.

## Known limitations and launch gates

- Seeded client-facing letter, organizer, questionnaire, and checklist content
  requires firm legal and tax approval before production client use.
- An authenticated staff/client staging walkthrough requires the production
  OIDC provider.
- Rich browser-based template authoring and compound condition builders remain
  future work.
- Email, SMS, and push notification delivery remain disabled by default.
- Downgrade removes Sprint 5.2 intake state and requires backup planning after
  production intake begins.

## Recommended Sprint 5.3

Build return lifecycle and production-stage automation: received, ready to
prepare, preparation, manager review, partner review, client approval, e-file
authorization, filing, rejection handling, delivery, amendments, lifecycle
timelines, stage queues, and production reporting. Reuse the existing workflow,
approval, assignment, queue, portal, notification, document, timeline, audit,
and authorization platforms.
