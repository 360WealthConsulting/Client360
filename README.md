# Client360

Client360 is the unified client intelligence and practice-management platform
for 360 Wealth Consulting and 360 Tax Solutions.

## Release status

**Release 0.9.5 — Tax Engagement Intake & Client Collaboration**

Release 0.9.5 adds the front half of the tax engagement lifecycle: versioned
engagement letters, organizers, conditional questionnaires, document
checklists, missing-information tracking, portal collaboration, reminders,
readiness dashboards, and automatic workflow advancement.

See [Release 0.9.5 Notes](docs/RELEASE_0.9.5.md),
[Sprint 5.2 RC Validation](docs/SPRINT_5_2_RELEASE_VALIDATION.md),
[Tax Engagement Intake](docs/TAX_ENGAGEMENT_INTAKE.md),
[Release 0.9.4 Notes](docs/RELEASE_0.9.4.md),
[Sprint 5.1 RC Validation](docs/SPRINT_5_1_RELEASE_VALIDATION.md),
[Tax Domain Foundation](docs/TAX_DOMAIN_FOUNDATION.md),
[Epic 5 Architecture](docs/EPIC_5_TAX_PRACTICE_PLATFORM.md),
[Release 0.9.3 Notes](docs/RELEASE_0.9.3.md),
[Release 1.0 Readiness](docs/RELEASE_1_0_READINESS.md),
[Client Portal Architecture](docs/CLIENT_PORTAL.md),
[RC4 Validation](docs/SPRINT_4_4_RELEASE_VALIDATION.md),
[Release 0.9.2 Notes](docs/RELEASE_0.9.2.md),
[Workflow Automation Architecture](docs/WORKFLOW_PROCESS_AUTOMATION.md),
[RC3 Validation](docs/SPRINT_4_3_RELEASE_VALIDATION.md),
[Release 0.9.1 Notes](docs/RELEASE_0.9.1.md),
[Work Management Architecture](docs/WORK_MANAGEMENT_PLATFORM.md), and the
[Release 0.9 Notes](docs/RELEASE_0.9.md).

Epic 5 is in implementation. Sprints 5.1 and 5.2 are released. Sprint 5.3 is in
draft review with the full tax return lifecycle, review routing, client
approvals, filing state, production queues, and dashboards. See
[Tax Return Lifecycle](docs/TAX_RETURN_LIFECYCLE.md).

## Mission

Create a unified client intelligence platform for 360 Wealth Consulting and 360 Tax Solutions.

## Objectives

- Import every prospect and client ever received
- Merge duplicate records
- Track households and businesses
- Track tax, wealth, and insurance relationships
- Track referrals
- Track revenue and AUM
- Search all historical client information
- Identify planning opportunities
- Maintain complete client history

## Data Sources

- Dave Ramsey Leads
- Schwab
- QuickBooks
- Drake
- TaxDome
- Wealthbox
- Outlook
- Excel
- PDFs
- Miscellaneous historical records

## Microsoft 365 intelligence

Client360 synchronizes Outlook mail and calendar meetings into canonical client
timelines. See [Microsoft Calendar Intelligence](docs/MICROSOFT_CALENDAR_SYNC.md)
for matching, unmatched review, manual testing, and deployment details.

Client360 links SharePoint and OneDrive metadata to canonical clients without
downloading duplicate files. See
[Microsoft Document Intelligence](docs/MICROSOFT_DOCUMENT_SYNC.md).

## Relationship intelligence

Client360 models family, household, professional, business, trust, estate, and
beneficiary connections as a normalized graph. See
[Relationship Intelligence Engine](docs/RELATIONSHIP_ENGINE.md).

## Portfolio intelligence

Client360 normalizes Schwab account, position, cash, performance, billing, and
beneficiary data into household-level portfolio intelligence. See
[Schwab Portfolio Intelligence](docs/SCHWAB_PORTFOLIO_ENGINE.md).

## Identity and security

Client360 includes managed-identity adapters, capability-composed roles,
team and record assignments, session controls, record-level authorization, and
immutable audit events. See
[Identity, Authorization, and Audit](docs/IDENTITY_AUTHORIZATION_AUDIT.md).

## Practice Management roadmap

The architecture for replacing the firm's daily Wealthbox and TaxDome
workflows is defined in the
[Epic 4 Practice Management Platform](docs/EPIC_4_PRACTICE_MANAGEMENT_PLATFORM.md).
Sprint 4.2 Operational Work Management is delivered in Release 0.9.1, and
Sprint 4.3 Workflow and Process Automation is delivered in Release 0.9.2.
Sprint 4.4 Client Portal and Secure Collaboration is delivered in Release
0.9.3. Epic 5 Tax Practice Platform is in technical design; no Epic 5
implementation has started. See
[Epic 5 Tax Practice Platform](docs/EPIC_5_TAX_PRACTICE_PLATFORM.md).
