# Client360 — Wealth Experience Architecture

Reference for developers working on the advisor-facing **Wealth** experience:
the Wealth Dashboard, Portfolio search, Household Wealth Workspace, and Client
Wealth Workspace. It records the architecture established through Phase A
(feature build), Phase B (meeting-prep UX), and Phase C.1 (design-system
cleanup), so future changes stay consistent with the design intent.

_Last updated for `release/0.13.0` (Phase C.1 complete)._

---

## 1. Guiding thesis

The Wealth experience serves **one persona — a financial advisor** — doing **two
jobs**:

1. **Triage** (between meetings): "Who across my book needs me?" → the **Wealth
   Dashboard** and **Portfolio** search.
2. **Meeting preparation & conduct** (before/during a meeting): "What does this
   client own, what needs attention, what should I discuss?" → the **Household**
   and **Client Wealth Workspaces**.

Every surface is scoped to one of these jobs. Do not blur them: the dashboard is
not a metrics wall, and the workspaces are not admin screens.

The three meeting-prep questions are the acceptance test for anything shown **by
default** in a workspace:

> • What does this client own?  • What needs attention?  • What should I discuss?

If a default-visible element answers none of these, it should be collapsed or
removed.

---

## 2. Service architecture

All Wealth read logic lives in **`app/services/portfolio.py`** and the pure math
in **`app/portfolio/calculations.py`**. Ingestion is separate (see §10).

### Layers (bottom-up)
| Layer | Location | Responsibility |
|---|---|---|
| **Pure calculation** | `app/portfolio/calculations.py` — `aggregate_portfolio()`, `calculate_allocation()` | Stateless math over account/holding rows. No DB, no I/O. |
| **Shared query helper** | `app/services/portfolio.py` — `_portfolio(where)` | Runs the bounded account/holding/beneficiary queries for a filter, calls `aggregate_portfolio()`, and **assembles the canonical contract** (§3). The single source of portfolio shape. |
| **Entity services** | `get_person_portfolio()`, `get_household_portfolio()` | Thin wrappers over `_portfolio()` that add entity-specific extras (person → `household`; household → `household_id`, `members`). |
| **Firm/aggregate services** | `get_firm_portfolio_metrics()`, `get_wealth_dashboard()`, `search_portfolios()` | Firm-wide scans for the dashboard and Portfolio search. |
| **Routes** | `app/routes/wealth.py`, `app/routes/portfolio.py`, `app/routes/households.py`, `app/routes/people.py` | Call services, pass results to templates. No aggregation in routes. |

### Key invariants
- **All portfolio aggregation flows through `_portfolio()` → `aggregate_portfolio()`.** There is exactly one place that sums AUM, computes cash %, allocation, and concentration. Never re-implement these in a route or template.
- `HIGH_CASH_RATIO = Decimal("0.15")` is the single "high cash" threshold shared by `search_portfolios()` (the filter) and `get_wealth_dashboard()` (the count). They must never diverge.
- Services are **read-only**. Wealth mutations (imports) go through the ingestion pipeline (§10), not these functions.

---

## 3. Canonical portfolio contract

Established in **Phase C.1 PR-2**. Both `get_person_portfolio()` and
`get_household_portfolio()` build on `_portfolio()`, which produces **one
canonical vocabulary** (constant `_CANONICAL_KEYS`):

| Key | Type | Meaning |
|---|---|---|
| `aum` | Decimal | Total value across the scoped accounts |
| `cash` | Decimal | Total cash value |
| `cash_percent` | Decimal | `cash / aum * 100` |
| `allocation` | dict `{asset_class: {value, percent}}` | Asset-class breakdown (`calculate_allocation`) |
| `largest_positions` | list | Holdings sorted by market value desc (top 10) |
| `concentration` | dict `{largest_position_percent, top_position}` | Single-position risk |
| `holdings` | list | **Full** sorted holdings |
| `accounts` | list | Account rows (name, number, registration, custodian, value) |
| `beneficiary_count` | int | Active beneficiaries on the scoped accounts |
| `last_import_date` | datetime\|None | Most recent import timestamp |

**Context-specific extras:** `get_person_portfolio()` adds `household` (a nested
`_portfolio` result for the person's household); `get_household_portfolio()` adds
`household_id` and `members`.

### Compatibility aliases (temporary)
`_portfolio()` appends legacy aliases immediately before returning — `total_aum`,
`asset_allocation`, `largest_holdings`, `largest_position_percent` — mirroring
canonical values exactly. They exist **only** so any not-yet-migrated consumer
keeps rendering. As of Phase C.1, **no template references them** (only the
unrelated `dashboard.total_aum`, a different object, remains). The build order is
deliberate and must be preserved:

```
canonical contract  →  compatibility aliases  →  return
```

**Aliases are slated for removal.** New code must consume canonical keys only.

---

## 4. Dashboard architecture (`/wealth`)

- **Route:** `app/routes/wealth.py` (`GET /wealth`). **Template:** `app/templates/wealth/dashboard.html`. **Service:** `get_wealth_dashboard()`.
- **Job:** book triage. Layout: a compact **Firm AUM / Firm Cash** context strip, then three **Advisor attention** worklists — *Missing beneficiaries*, *High cash*, *Accounts needing review*.
- The first two attention tiles are **links into filtered Portfolio worklists** (`/portfolio?missing_beneficiary=true`, `/portfolio?high_cash=true`). The dashboard is a **launcher**, not a report.
- Money is whole-dollar (`{:,.0f}`) — a **scan surface** (see §11).
- Deliberately excludes: recent-activity changelogs, firm counts beyond AUM/Cash, and quick-action cards that duplicate the sidebar nav (all removed in Phase B/C.1).

---

## 5. Portfolio architecture (`/portfolio`)

- **Route:** `app/routes/portfolio.py` — `GET /portfolio` (HTML), `GET /portfolio/search` (JSON), `POST /portfolio/import/schwab` (manual local-file import). **Template:** `app/templates/portfolio/search.html`. **Service:** `search_portfolios()`.
- **Job:** find-a-client directory / worklist. Search by name + two worklist toggles (`high_cash`, `missing_beneficiary`) that mirror the dashboard tiles. Results (Client · AUM · Cash · Detail) link to the **Client** workspace (`/people/{id}?tab=portfolio`).
- Uses the **shared design system** (Phase C.1 PR-1): `ui.page_head`, `section-title`, `table.data` with `.num` numeric columns. Money whole-dollar (`{:,.0f}`, scan surface).
- Do not reintroduce the removed `min_aum` / `registration` / `concentration` filters or the legacy `.filters`/`.panel` styling.

---

## 6. Household Wealth Workspace architecture (`/households/{id}`)

- **Route:** `app/routes/households.py` (`household_profile`). **Template:** `app/templates/households/profile.html`. **Service:** `get_household_portfolio()`.
- **This is the reference meeting-prep screen.** One cohesive top-to-bottom view, read in order:
  1. **Summary** (visible): AUM · Cash % · Concentration · Beneficiaries · **Meeting agenda** (open-task count).
  2. Allocation + Largest positions.
  3. Accounts table.
  4. **All holdings** — collapsed `<details>` (on-page drill-down, no navigation).
  5. Household members table.
  6. **Manage members** — collapsed `<details>` (admin, out of the way).
- Roll-up AUM is sourced from `get_household_portfolio()` (single source of truth); the route no longer runs a second AUM query.
- Money is `{:,.2f}` — a **detail surface**.

---

## 7. Client Wealth Workspace architecture (`/people/{id}?tab=portfolio`)

- **Location:** the `active_tab == "portfolio"` block of `app/templates/people/workspace.html` (a tab within the person profile — **not** a standalone file). **Service:** `get_person_portfolio()`.
- **Deliberately aligned** with the Household workspace (Phase B PR-3): "Client wealth" section title, the same card styling/hierarchy, allocation/positions before an Accounts table, matched empty-state copy.
- **Summary (intentionally different set):** Client AUM · Household AUM · Cash % · Beneficiaries. It does **not** carry Concentration or Meeting agenda — the person profile has its own Tasks tab, and the household is the concentration unit. This asymmetry is a design decision, not an oversight.
- Consumes **canonical keys only** (`aum`, `household.aum`, `allocation`, `largest_positions`).

---

## 8. Shared component library

**`app/templates/wealth/components.html`** — imported as
`{% import "wealth/components.html" as wc %}`. Established in Phase C.1 PR-3.

| Macro | Signature | Renders |
|---|---|---|
| `summary_card` | `(label, value, caption='')` | One metric card. **Caller pre-formats `value` and decides `caption`.** |
| `allocation_card` | `(allocation)` | The Asset-allocation card from a canonical `allocation` dict. |
| `positions_card` | `(positions)` | The Largest-positions card from a canonical `largest_positions` list. |
| `accounts_table` | `(accounts, scope)` | The Accounts card/table; `scope` ("household"/"client") only fills the empty-state word. |

### Contract for these macros (do not violate)
- **Presentation-only.** They know *how to render*, nothing else. No business rules, no data-derived branching beyond empty-state rendering, no caller-varying formatting decisions.
- **Canonical inputs only.** Never reference legacy aliases inside a shared component.
- **The calling page owns** which cards appear, their order, captions, value formatting, and visibility. A whole "summary grid" is intentionally **not** a component — the Household (5 cards) and Client (4 cards) grids differ, and each page assembles its own from `summary_card()` calls. Do not over-abstract this into one component.

---

## 9. Data flow

```
Custodian export (Schwab CSV)
        │  app/services/portfolio_import.py + app/portfolio/adapters/
        ▼
Postgres  (accounts, account_holdings, securities, account_beneficiaries,
           households, household_relationships)   [reflection-only tables]
        │  app/services/portfolio.py
        ▼
_portfolio(where)  →  aggregate_portfolio()  →  CANONICAL CONTRACT (+ aliases)
        │
        ├─ get_person_portfolio()      (+ household)
        ├─ get_household_portfolio()   (+ household_id, members)
        ├─ get_firm_portfolio_metrics() / get_wealth_dashboard()
        └─ search_portfolios()
        │
        ▼
Routes (wealth / portfolio / households / people)
        ▼
Templates  →  wealth/components.html macros  →  HTML
```

---

## 10. Ingestion (context; separate from the read path)

- **Adapter boundary:** `app/portfolio/adapters/base.py` (`PortfolioSourceAdapter` Protocol) + `SchwabCsvAdapter`. Custodian-neutral records in `app/portfolio/models.py` (`PortfolioBatch`).
- **Importer:** `app/services/portfolio_import.py` (`import_portfolio_file`) — dedupes on `portfolio_import_runs.file_hash`, upserts accounts/holdings/transactions, emits timeline events.
- Ingestion is currently **manual/local-path** (`POST /portfolio/import/schwab` under `01 Raw Imports/Schwab`). A production scheduled-ingestion path is out of scope and a known dependency.
- The adapter parses cash/performance/billing/beneficiary snapshots into `PortfolioBatch`, but the importer only persists accounts/holdings/transactions today. `performance_snapshots`, `billing_snapshots`, `household_portfolio_snapshots`, `position_snapshots`, `tax_lots`, and `cash_snapshots` tables exist but are **unused** (see the wealth-module assessment).

---

## 11. Rendering flow & conventions

- **Templates extend `base.html`**; workspaces load `workspace.css` via `{% block head %}` for `.workspace-grid` / `.detail-row`. Dashboard and Portfolio need only base-loaded `app.css`.
- **Design-system classes** (all in `app.css`): `.card`, `.detail-label`, `.detail-row`, `.section-title`, `.stat-grid`, `.workspace-grid`, `table.data` + `.num`, `.rowlink`, `.subtle`, `.btn`, `.input`, and the `ui.page_head` macro. Reuse these; do not borrow `.filters`/`.panel`/`.metrics` from the Work/Tax modules.
- **Money-format convention (deliberate):**
  - **Scan surfaces** (Dashboard, Portfolio list) → whole dollars `{:,.0f}`.
  - **Detail surfaces** (Household, Client workspaces) → cents `{:,.2f}`.
  This scan-vs-detail split is intentional; keep it.

---

## 12. Design principles

1. **Two jobs, four surfaces.** Triage (dashboard, portfolio) vs. prep (workspaces). Keep them distinct.
2. **The three questions gate default visibility.** Owns-it / needs-attention / to-discuss. Everything else collapses or leaves.
3. **One aggregation path.** `_portfolio()` → `aggregate_portfolio()`. No parallel math.
4. **Canonical vocabulary is the contract.** New consumers read canonical keys; aliases are temporary.
5. **Components render; pages compose.** Presentation macros hold no logic; pages decide content.
6. **On-page drill-down over navigation.** Detail (full holdings) and admin (manage members) collapse in place via `<details>`; an advisor never leaves the screen mid-meeting.
7. **Reuse the design system.** No new CSS or borrowed module styling for cohesion work.

---

## 13. Extension guidelines

- **Adding a metric to a workspace summary:** add a `summary_card()` call in that page with a pre-formatted value + caption. If the value derives from portfolio data, expose it as a **canonical key** in `_portfolio()` first — never format new aggregates in the template.
- **Adding a new card type shared by both workspaces:** add a presentation-only macro to `wealth/components.html` taking canonical inputs; migrate both pages to it. Do not inline-duplicate.
- **New worklist on the dashboard:** add a count to `get_wealth_dashboard()` and a tile linking into a Portfolio filter. If the filter doesn't exist, that filter is **new functionality** — get product approval; do not fake a dead-end tile.
- **New custodian:** implement a `PortfolioSourceAdapter`; do not touch the read path.
- **New surface:** decide its job (triage vs. prep) first; reuse `ui.page_head`, `section-title`, `table.data`, and the `wealth/components.html` macros; pick the money convention by scan-vs-detail.
- **Cross-linking (open follow-up):** the Client workspace's "Household AUM" and Portfolio results could link to the household; `person.household_id` is already in context. UX-only, safe.

---

## 14. Do NOT change without architectural review

These encode decisions with cross-surface or correctness impact:

1. **The canonical contract / `_CANONICAL_KEYS`** — renaming or reshaping keys breaks every consumer. Additive canonical keys are fine; renames/removals need review.
2. **The canonical→aliases→return order in `_portfolio()`**, and **removing the compatibility aliases** — only after confirming zero consumers remain (search all templates *and* any Python/JSON caller).
3. **`aggregate_portfolio()` return keys** — consumed directly by tests and `_largest_position_percents`; treat as a stable contract.
4. **`HIGH_CASH_RATIO`** — a shared threshold; changing it silently shifts both the dashboard count and the Portfolio filter. It is a **business rule**, not a styling constant.
5. **The scan-vs-detail money convention** (`{:,.0f}` vs `{:,.2f}`).
6. **The triage-vs-prep separation** — do not turn the dashboard into a metrics wall or a workspace into an admin screen; do not add default-visible elements that fail the three questions.
7. **The Household vs Client summary asymmetry** (Client intentionally omits Concentration / Meeting agenda) — it reflects the person-profile Tasks tab and the household-as-concentration-unit decision.
8. **The shared-component presentation-only contract** — introducing logic, business rules, or legacy-alias references into `wealth/components.html` re-creates the divergence Phase C.1 removed.
9. **The single aggregation path** — no portfolio math in routes/templates.
10. **Regulated/advisory scope** — performance, billing, fee reconciliation, IPS, drift, rebalancing, and model portfolios are **explicitly out** until business/compliance owners exist (tied to the V1 risk register `GOV-2`/`PD-4`). Do not introduce them opportunistically.

---

## 15. Related documents
- `docs/UI_DESIGN_SYSTEM.md` — the app-wide component/design-system reference.
- `docs/PRODUCT_DECISIONS.md` — business-rule decisions (household grouping, match auto-merge, etc.).
- `docs/V1_RISK_REGISTER.md` — program risks incl. compliance ownership gating regulated wealth features.
