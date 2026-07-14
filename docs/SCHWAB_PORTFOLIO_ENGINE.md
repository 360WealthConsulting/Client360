# Schwab Portfolio Intelligence Engine

## Architecture

Schwab CSV files enter through a custodian adapter and are converted into canonical portfolio records before persistence. Matching, change detection, household rollups, timeline publishing, search, dashboard metrics, and advisor rules consume only the canonical model. A future Schwab API adapter therefore replaces the acquisition layer without changing business logic.

`Schwab CSV/API -> PortfolioSourceAdapter -> PortfolioBatch -> Import Service -> Normalized Tables -> Intelligence Services/UI`

## Normalized data

The migration adds custodians, account registrations, securities, current holdings, dated positions, tax lots, transactions, cash, performance and billing snapshots, beneficiaries, import runs, and household snapshots. Existing accounts gain normalized custodian/registration links plus import and review timestamps.

Imports are idempotent at two levels: SHA-256 file hashes prevent repeat file processing and natural-key upserts update changed account/holding/transaction records. Timeline external IDs prevent duplicate business events.

## Supported exports

The adapter recognizes account master/status, positions, transactions, cash, billing, and performance exports using flexible Schwab header aliases. Cost basis flows through holdings and position snapshots; lot storage is available for an expanded lot adapter. Account matching uses normalized email when present and never creates people automatically.

## Operations

Run a controlled import with `POST /portfolio/import/schwab?path=<managed CSV path>`. Only CSV files under `01 Raw Imports/Schwab` are accepted. Portfolio search is available at `/portfolio/search` with `q`, `min_aum`, `registration`, `high_cash`, `missing_beneficiary`, and `concentration` filters.

After an import, review unmatched accounts, household assignments, registration normalization, beneficiaries, allocation classifications, and timeline events. The Client Workspace Portfolio tab shows client and household AUM, cash, accounts, registrations, allocation, holdings, custodian, and last import date.

## Security and future API

No tax identifiers or full account details are placed in timeline metadata. The API adapter should authenticate outside the portfolio domain and emit the same canonical records. Secrets and raw payloads must remain outside normalized tables.
