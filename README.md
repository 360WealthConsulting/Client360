# Client360

Client360 is the unified client intelligence and practice-management platform
for 360 Wealth Consulting and 360 Tax Solutions.

## Release status

**Release 0.9 — Epic 4 Foundation**

Release 0.9 delivers the integrated Microsoft 365, Relationship, Portfolio,
identity, authorization, audit, timeline, search, dashboard, and Client
Workspace foundations. The database supports both clean installation and
upgrade from the previous `main` schema through one linear Alembic history.

See [Release 0.9 Notes](docs/RELEASE_0.9.md) and the
[RC1.2 Validation Report](docs/RC1_RELEASE_VALIDATION.md).

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

Release 0.9 includes managed-identity adapters, capability-composed roles,
team and record assignments, session controls, record-level authorization, and
immutable audit events. See
[Identity, Authorization, and Audit](docs/IDENTITY_AUTHORIZATION_AUDIT.md).

## Practice Management roadmap

The architecture for replacing the firm's daily Wealthbox and TaxDome
workflows is defined in the
[Epic 4 Practice Management Platform](docs/EPIC_4_PRACTICE_MANAGEMENT_PLATFORM.md).
Sprint 4.2 implementation remains intentionally paused until Release 0.9 is
fully published and the next sprint is approved.
