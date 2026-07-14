# Client360

Client360 is the unified client intelligence and practice-management platform
for 360 Wealth Consulting and 360 Tax Solutions.

## Release status

**Release 0.9.7 — Security Hardening**

Release 0.9.7 is a security-hardening release that fixes the confirmed,
independently verified authorization, record-scope, and workflow-permission
defects from the RC8/RC9 architecture review before Epic 5 Sprint 5.4 begins.
It adds no new features and preserves least privilege, immutable audit, and
record-level authorization. See
[Security Hardening 0.9.7](docs/SECURITY_HARDENING_0.9.7.md),
[RC10 Validation](docs/RC10_VALIDATION.md),
[RC9 Architecture Verification](docs/RC9_ARCHITECTURE_VERIFICATION.md), and
[RC8 Architecture Review](docs/RC8_ARCHITECTURE_REVIEW.md).

**Release 0.9.6 — Tax Return Lifecycle & Production Automation**

Release 0.9.6 adds the production half of the tax engagement lifecycle: a
canonical 15-state return pipeline, preparer/manager/partner review routing,
client portal approvals, provider-neutral e-filing, production queues, and
dashboards.

See [Release 0.9.6 Notes](docs/RELEASE_0.9.6.md),
[Tax Return Lifecycle](docs/TAX_RETURN_LIFECYCLE.md),
[Release 0.9.5 Notes](docs/RELEASE_0.9.5.md),
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

Epic 5 is in implementation. Sprints 5.1, 5.2, and 5.3 are released. Sprint 5.4
(tax document intelligence and missing information) is in draft review — a
deterministic, authorization-aware document matching engine that replaces
substring-based matching (RC8 H13) with mandatory human review of ambiguous
matches. See [Revised Epic 5 Plan](docs/EPIC_5_REVISED_PLAN.md),
[Tax Document Intelligence](docs/SPRINT_5_4_TAX_DOCUMENT_INTELLIGENCE.md), and
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
0.9.3. Epic 5 Tax Practice Platform is in implementation; Sprints 5.1, 5.2,
and 5.3 are released. See
[Epic 5 Tax Practice Platform](docs/EPIC_5_TAX_PRACTICE_PLATFORM.md).
