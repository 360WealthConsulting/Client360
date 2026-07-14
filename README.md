# 360 Client Management Database

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

## Status

Project started: July 2026

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
