# Client Portal Visibility Registry (Phase D.43)

`app/portal/visibility.py` is the **single declarative source** of every external-visibility decision. It
keeps those decisions out of templates and makes them testable. Governance
(`app/portal/governance.py`) verifies completeness and that no forbidden field is ever externally visible.
See [`ADR-048`](adr/ADR-048-secure-client-portal.md).

## Field record
Each `PortalField` declares:
- `key` — stable field identifier (e.g. `financial.account_number`);
- `source_domain` / `source_service` — the authoritative owner the value is read from;
- `external_visibility` — one of `visible`, `conditional`, `internal_only`, `prohibited`, `deprecated`;
- `required_permission` — the portal **grant** permission needed (grant-based, not RBAC), or `None`;
- `required_scope` — `person` | `household` | `organization` | `account`;
- `masking_rule` — `none` | `account_last4` | `omit`;
- `freshness` — whether an as-of marker is shown;
- `mutation_owner` — the authoritative service that owns any mutation (`None` = read-only);
- `deep_link`, `lifecycle`, `compliance_owner`.

## Visibility states
- `visible` — always shown to an authenticated portal principal.
- `conditional` — shown only with the required grant permission + scope.
- `internal_only` / `prohibited` — **never** externally served; declared explicitly so governance can
  assert their absence from any external surface.
- `deprecated` — retired; treated as not externally visible.

## Explicitly forbidden fields
The registry declares (and governance asserts never-visible): advisor notes, assignments, advisor work,
work queue, compliance reasoning, suitability findings, audit history, policy explanations, AI-assist
briefs, opportunity revenue, the relationship graph, and net worth. These are the "internal reasoning"
surfaces the portal must never leak.

## Masking
`mask_account_number(value)` returns `••••` + last-4 (and `••••` alone for values shorter than 4), so a full
account number is never emitted externally. `financial.account_number` carries `masking_rule =
account_last4`; governance fails if that ever changes or if the helper emits a full number.

## Usage
- Read surfaces consult the registry (directly or via the services that implement the fields) rather than
  encoding visibility inline in templates.
- The internal admin **preview** (`/admin/client-portal/accounts/{id}/preview`) builds a permissions report
  from the account's grant scope × the registry, showing exactly which fields the account is entitled to —
  without impersonation.
- `coverage()` reports totals (external / internal_only / prohibited / masked) for diagnostics.

## References
`app/portal/visibility.py`, `app/portal/governance.py`, `app/routes/portal_admin.py` (preview),
`tests/test_secure_client_portal.py` (`test_no_forbidden_field_is_externally_visible`,
`test_account_number_always_masked`, `test_visibility_coverage_declares_internal_and_prohibited`), ADR-048.
