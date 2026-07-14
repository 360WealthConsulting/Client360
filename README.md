# Client360

Client360 is the unified client intelligence and practice-management platform
for 360 Wealth Consulting and 360 Tax Solutions.

## Release status

**Release 0.9.2 — Workflow and Process Automation**

Release 0.9.2 adds immutable versioned workflow templates, execution snapshots,
dependency and conditional processing, parallel work, lifecycle controls,
independent approvals, SLA escalation, domain-event triggers, and idempotent
automation. It builds on the Release 0.9.1 assignments, queues, My Work, Team
Work, capacity, and authorization foundation.

See [Release 0.9.2 Notes](docs/RELEASE_0.9.2.md),
[Workflow Automation Architecture](docs/WORKFLOW_PROCESS_AUTOMATION.md),
[RC3 Validation](docs/SPRINT_4_3_RELEASE_VALIDATION.md),
[Release 0.9.1 Notes](docs/RELEASE_0.9.1.md),
[Work Management Architecture](docs/WORK_MANAGEMENT_PLATFORM.md), and the
[Release 0.9 Notes](docs/RELEASE_0.9.md).

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
Sprint 4.4 remains unstarted pending architecture and scope approval.
