# Retention Registry (Phase D.50)

The **retention registry** (`RETENTION_REGISTRY` in `app/services/document_intelligence/registry.py`) is the
declarative catalog of the firm's records-retention policies and, for each, the **authoritative owner** it is
measured against. It is metadata only: the Document Intelligence layer owns no retention store, computes no
expiration dates, and executes no disposition — it references the owners and explains the result with a deep
link.

## Retention policies

Each policy declares its `owner` (the Document Platform retention-policy store — the authoritative period
source), `retention_period`, `archive_owner` (Governance retention — the disposition/legal-hold owner),
`disposition_policy` (review / archive / delete — the deterministic action on expiry), `governing_regulation`,
and `runtime_gate`.

| Policy | Retention period | Disposition | Governing regulation |
| --- | --- | --- | --- |
| `irs` | 7 years after filing | review | IRS §6501 / federal tax recordkeeping |
| `sec` | 6 years (2 readily accessible) | archive | SEC Rule 17a-4 / Advisers Act 204-2 |
| `finra` | 6 years | archive | FINRA Rule 4511 / SEC 17a-4 |
| `state_insurance` | 5 years (varies by state) | review | State insurance record-retention statutes |
| `internal_operations` | 3 years | review | Internal operations records policy |
| `client_communications` | 3–6 years | archive | SEC 17a-4 / FINRA 4511 (communications) |

## Ownership boundaries (never re-implemented here)

- **Retention policies** (period + action on expiry) are owned by the Document Platform
  (`document_retention_policies`; `list_retention_policies`, `apply_retention` derives `expiration_date`
  deterministically). The registry names the period for explainability; the layer never creates or applies a
  policy.
- **Retention assignments, legal holds, and disposition** (archival / deletion requests, review of due
  retention) are owned by Governance retention (`app/services/governance/retention.py`). The registry names
  it as each policy's `archive_owner`; the layer **never** places a hold, creates a deletion request, or
  executes disposition — governance forbids those calls.
- **Archive** is a Document Platform lifecycle state (`archived`) plus a Governance disposition request type
  (`request_type="archival"`) — not a separate cold-storage system.

## How the registry is used

The retention + archive dashboards compose:

- **`retention_policies`** — the Document Platform policy store (count + policy list).
- **`retention_assignments`** — Governance retention assignments by status.
- **`retention_metrics`** — Governance active legal holds + pending disposition reviews.
- **`archived_documents`** — documents in the archived lifecycle state.
- **`archive_readiness`** — Governance retention assignments eligible for archival / expired.
- **`disposition_requests`** — open Governance archival / deletion requests.

Governance validates that every policy declares all six fields (owner, retention period, archive owner,
disposition policy, governing regulation, runtime gate), that every document class points at a registered
retention policy, and that the layer contains no retention/disposition **mutation**.

See [DOCUMENT_INTELLIGENCE.md](DOCUMENT_INTELLIGENCE.md), [RECORDS_LIFECYCLE.md](RECORDS_LIFECYCLE.md), and
[ADR-055](adr/ADR-055-document-intelligence.md).
