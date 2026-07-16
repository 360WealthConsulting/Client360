# Client360 — UI Design System

**Status:** Design specification — **awaiting approval**. No application code migrated.
**Proposed release:** 0.9.12 — Application Shell & UI Consolidation (frontend only).
**Visual companion:** [`docs/design/client360_shell_spec.html`](design/client360_shell_spec.html)
(open in a browser — renders the shell, navigation, and every component live, in both themes).

This document is the written source of truth for the Client360 interface: the design
language, the application shell, and the component vocabulary that will replace today's
several unrelated template shells and the HTML hand-built inside Python routes. It is
**additive and reversible** — templates and CSS only. It changes **no** schema, route, or
service signature, and it preserves every authorization and record-scope control unchanged.

---

## 1. Design philosophy

Client360 is an instrument, not a brochure. It is operated all day by advisors, tax
preparers, operations, compliance, and administrators, so the interface is dense where the
work is dense and calm everywhere else.

Three commitments govern every screen:

1. **Summary before detail.** Each screen leads with the numbers that drive a decision, then
   the rows. A dashboard is scanned and operated, not read top to bottom.
2. **State reads at a glance.** Severity, status, and SLA are encoded as color **and** shape —
   a stripe, a pill, a tick — never color alone and never text alone.
3. **Names, never raw IDs.** People, households, organizations, and plans appear as named,
   clickable links. Record IDs appear only as quiet monospace metadata.

**What does not change:** all authorization and record-scope rules (the UI reflects them, and
the capability-filtered navigation makes them *more* visible, granting nothing); the routes,
services, and data model; and the server-rendered **Jinja2 + CSS** stack — no SPA, no second
frontend framework, no build step.

---

## 2. Color palette

**Primary brand color: `#0E6E63`** (a deep teal). It carries Client360's identity and **every
interaction** — primary buttons, active navigation, links, focus states, and key highlights.
It is exposed as the `--accent` token (components reference `--accent`; conceptually it *is*
the brand). The neutral scale is a cool slate, biased a few degrees toward the brand so it
reads as chosen rather than defaulted.

The four **semantic** colors are an **independent** system and never borrow the brand hue.
Info in particular is its own understated steel-blue, so an "in progress" / informational
signal never reads as a brand highlight.

### Primary brand & neutrals (light theme)

| Token | Hex | Use |
|---|---|---|
| `--accent` (**primary brand**) | `#0E6E63` | Primary buttons, active nav, links, focus, key highlights |
| `--accent-2` | `#0A544B` | Brand hover / pressed |
| `--accent-soft` | `#DBEEEB` | Active-nav tint, selection, focus-ring fill |
| `--accent-ink` | `#063A34` | Text on `--accent-soft` |
| `--ground` | `#F5F7F7` | Page background |
| `--surface` | `#FFFFFF` | Cards, tables, header, sidebar |
| `--surface-2` | `#EDF1F1` | Muted panels, table headers, hovers |
| `--surface-3` | `#E4EAE9` | Deeper muted fills, skeleton shimmer |
| `--border` | `#D8E0DF` | Hairline separators |
| `--border-strong` | `#BCC8C6` | Input borders, emphasized dividers |
| `--text` | `#14201F` | Primary text / headings (teal-biased ink) |
| `--text-2` | `#33423F` | Secondary text |
| `--muted` | `#5E6E6A` | Captions, metadata, placeholders |

### Semantic (independent of the brand)

| Token | Role | Hex | Soft fill | Meaning |
|---|---|---|---|---|
| `--good` | Success | `#2F8A3E` | `--good-soft` `#E2F1E1` | Healthy / active / resolved |
| `--warn` | Warning | `#B4690E` | `--warn-soft` `#F9EAD3` | At risk / attention soon |
| `--crit` | Danger | `#B42318` | `--crit-soft` `#F9E3E0` | Breached / overdue / error |
| `--info` | Info | `#35618E` | `--info-soft` `#E5EBF3` | Informational / in progress |

> Four hues, four jobs: teal is the **brand** (interaction only); green = success; amber =
> warning; red = danger; steel-blue = info. `--good` (olive-green), `--info` (steel-blue), and
> `--accent` (teal) are deliberately spread across the wheel so status and interaction never
> read as the same signal.

### Dark theme

Every color is a token, redefined for dark. The accent brightens; contrast stays legible; the
accent still works on the darker ground.

| Token | Dark | Token | Dark |
|---|---|---|---|
| `--ground` | `#0E1514` | `--text` | `#E7ECEA` |
| `--surface` | `#16201E` | `--text-2` | `#C4CFCB` |
| `--surface-2` | `#1D2A27` | `--muted` | `#8FA09B` |
| `--surface-3` | `#25332F` | `--accent` | `#2FB3A3` |
| `--border` | `#293633` | `--accent-2` | `#54C8BA` |
| `--border-strong` | `#3A4A46` | `--accent-soft` | `#123430` |
| `--good` | `#57B15C` | `--warn` | `#D89A3C` |
| `--crit` | `#E27568` | `--info` | `#74A6DC` |

Theme selection: `prefers-color-scheme` sets the default; a `data-theme="light|dark"`
attribute on the root element overrides the media query in both directions. Components are
always styled through tokens, never with colors hard-coded inside a media query.

---

## 3. Typography

Two roles do the work.

- **UI / sans** — `ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif`.
  All interface text. Tight and confident at heading sizes (negative tracking, weight 640–680).
- **Data / mono** — `ui-mono­space, "SF Mono", "Cascadia Code", "Roboto Mono", Menlo, Consolas, monospace`
  with `font-variant-numeric: tabular-nums`. Every ID, amount, rate, and metric — so numeric
  columns align and the product reads like the ledger it is.

> Production self-hosts a licensed grotesque for headings via an inlined `@font-face`
> (data-URI, no external CDN). The spec and the fallback both use the resilient system stack
> above, so a missing webfont never degrades silently.

### Type scale

| Role | Size | Weight | Tracking | Notes |
|---|---|---|---|---|
| Display | 2.6rem (~42px) | 680 | -0.022em | Marketing/landing headings only |
| Page H1 | 1.5rem (24px) | 660 | -0.018em | One per page, in the page header |
| Section H2 | 1.0–1.15rem | 640 | -0.01em | Panel / card titles |
| Body | 0.95rem (15px) | 400 | — | Line-height 1.55; target ~65ch measure |
| Label | 0.7rem (11px) | 700 | 0.08em | Uppercase, mono, muted (eyebrows, th) |
| Data | 0.9–1.15rem | 550–600 | -0.02em | Mono, tabular figures |

Headings use `text-wrap: balance`. Uppercase labels always carry letter-spacing.

---

## 4. Spacing scale

A single base-4 scale. Layout uses flex/grid `gap`, not per-element margins.

| Step | Value | Typical use |
|---|---|---|
| `space-1` | 4px | Icon/label gaps, badge padding |
| `space-2` | 8px | Chip/button internal gaps |
| `space-3` | 12px | Card grids, stack gaps |
| `space-4` | 16px | Card padding, section gaps |
| `space-5` | 20–22px | Content padding, page-head margin |
| `space-6` | 32px | Between major content blocks |
| `space-8` | 52px | Between spec/doc sections |

Radii: `--radius-sm` 5px (inputs, chips), `--radius` 8px (cards, tables, buttons),
`--radius-lg` 12px (device frames, large surfaces). Elevation is used sparingly — hairline
borders carry most separation. `--shadow-sm` for resting cards; `--shadow` for overlays,
toasts, and popovers.

---

## 5. Grid & layout

- **App frame:** CSS grid, `grid-template-columns: var(--nav-w) 1fr` where `--nav-w` is 232px.
  The sidebar and header are fixed chrome; the content region scrolls independently.
- **Header height:** `--header-h` = 56px (brand, breadcrumb, search, alerts, user all align to it).
- **Content max width:** content is fluid; data-dense tables get their own
  `overflow-x: auto` scroll container so the page body never scrolls sideways.
- **Content padding:** 22px vertical / 24px horizontal.
- **Card/stat grids:** `repeat(auto-fit, minmax(150px, 1fr))` for metric strips;
  `1fr 1fr` two-column for panels, collapsing to single column under 720px.
- **Reading measure:** running prose is held near 65 characters.

---

## 6. Navigation hierarchy

A persistent left navigation, grouped by the job to be done, filtered by capability. Every
item declares the capability it requires; items a user cannot access are **hidden** — not
shown-then-403. This is a UI reflection of existing RBAC and grants nothing.

| Group | Items | Capability gate |
|---|---|---|
| **Overview** | Dashboard | `record.read_all` (firm-wide) |
| | Activity | authenticated staff |
| **Clients** | Households, People, Relationships | record-scoped (own/team assignments) |
| **Work** | My Work, Team & Queues, Tasks, Workflows | authenticated; Team/Queues by assignment caps |
| **Tax** | Tax Center, Intake, Returns, Documents | `tax.*` |
| **Benefits** | Organizations | `organization.read` |
| | Benefits, Reporting | `benefits.read` |
| **Oversight** | Exceptions, Exception Reporting | `exception.read` |
| | Audit, Identity, Microsoft 365 | admin/audit caps |

**Scoped landing, not a 403.** Firm-wide collection screens require `record.read_all`. A user
without it (Advisor, Operations, Tax Preparer) lands on their own **My Work** / **My
Households** rather than a raw-JSON 403 — resolving demo findings UX-01 and UX-03.

Breadcrumbs in the header carry hierarchical context (`Group / Current page`, plus record name
on detail screens). Global search sits beside the breadcrumb for jump-to-record.

---

## 7. Application shell

Every staff screen is wrapped by one shell:

- **Sidebar** (`--nav-w`): brand lockup (`C3` mark + "Client360"), then the capability-filtered
  nav groups. Active item uses `--accent-soft` fill with `--accent-ink` text and an accent icon.
- **Header** (`--header-h`): breadcrumb context · global search (max 380px) · flexible spacer ·
  alerts button (with unread dot) · user chip (avatar initials, name, **role** label).
- **Content:** a page header (eyebrow + H1 + optional description on the left, primary/secondary
  actions on the right), then the screen body. Content scrolls beneath fixed chrome.

The **portal** keeps its own separate shell (`portal/base.html`) but consumes the same design
tokens and components, so staff and client surfaces share one visual language without sharing
navigation or authorization surfaces.

Shell class names (the deliverable): `.app-shell`, `.app-sidebar`, `.app-brand`, `.app-nav`,
`.nav-group`, `.nav-item` (`.active`, `.gated`), `.app-main`, `.app-header`, `.crumbs`,
`.app-search`, `.user-chip`, `.avatar`, `.app-content`, `.page-head`.

---

## 8. Component catalog

One vocabulary replaces today's four overlapping CSS files (`main.css`, `work.css`, `tax.css`,
`workspace.css`) and the divergent `.card` / `.work-card` / `.stat-card` / `.panel` classes.

| Component | Class(es) | Notes |
|---|---|---|
| Button | `.btn` + `.secondary` `.ghost` `.danger` `.sm` | Primary is accent-filled; one primary per view |
| Card | `.card` (with `> h3` title) | Resting surface, hairline border |
| Metric | `.stat-grid` › `.stat` (`.k` `.v` `.delta`) | `.v` is mono/tabular; `.delta` uses semantic color |
| Table | `.table-wrap` › `.table-scroll` › `table.data` | See §9 |
| Badge | `.badge` + `.good` `.warn` `.crit` `.info` | Status pill; `.tick` dot optional |
| Severity | `.sev` + `.critical` `.high` `.medium` `.low` | Left color stripe glyph |
| Alert | `.alert` + `.info` `.good` `.warn` `.crit` | Inline banner with icon + title + body |
| Field | `.field` (`.valid` `.invalid`) › `.input` | See §10 |
| Filter bar | `.filterbar` › `.chip` (`.active`) | Faceted filtering; active chips show `×` |
| Tabs | `.tabs` › `.tab` (`.active`) | Detail-record sections |
| Pagination | `.pager` › `.pg` (`.active`) + `.info` | Mono page numbers, range summary |
| Empty state | `.empty` (`.em-ico` `h4` `p` + action) | See §13 |
| Skeleton | `.skel` | Shimmer loading placeholder |
| Toast | `.toast` (`.tk`) | Transient success/confirmation |
| Breadcrumb | `.crumbs` (`a` `.sep` `b`) | Header context |

Each component is token-driven; theming and both light/dark come for free.

---

## 9. Tables

Tables are the workhorse and carry the most craft.

- Structure: `.table-wrap` (border + radius + clip) → `.table-scroll` (`overflow-x: auto`) →
  `table.data` (`min-width` so it scrolls rather than crushes).
- Headers: uppercase 11px mono-ish labels, `--surface-2` background, `--muted` text; the active
  sort column is marked (`.sort` shows a ↓ in accent).
- Rows: 11px vertical padding, hairline `--border` separators, `--surface-2` hover.
- **Primary cell leads with a named link** (`.rowlink`) and carries the record ID beneath as
  quiet `.id-mono` metadata (e.g., `ORG-1047 · EIN ••-•••4210`).
- Numeric cells use `.num` — mono, `tabular-nums`, right-aligned, `white-space: nowrap`.
- State is shown with `.badge`/`.sev` in-cell, never as a bare word.
- Below the table: `.pager` with a `1–N of M` range summary.

Sensitive values (EIN, compensation, deferrals) are masked in-cell unless the viewer holds the
gating capability — the UI never widens what the service returns.

---

## 10. Forms

- Every control is a `.field` = `label` + `.input` (+ optional `.hint`, `.err`).
- **States:** default; `.field.valid` (green input border); `.field.invalid` +
  `.input.invalid` (red border) with an inline `.err` message that says what's wrong **and**
  how to fix it (`"Enter a 9-digit EIN (format ••-•••••••)."`).
- Focus: `--accent` border + `--accent-soft` outline ring; always a visible focus state.
- Selects use a CSS caret (no icon-font dependency).
- Buttons: exactly one primary action per form; destructive actions use `.btn.danger`.
- Copy: labels name things as people recognize them; a button states the outcome ("Save
  organization"), and the resulting toast confirms it in the past tense ("Organization saved").
- Dates are entered/verified, never inferred — hints say so where relevant.

---

## 11. Dashboards

Dashboards are scanned and operated:

- Lead with a `.stat-grid` metric strip (mono/tabular values, semantic `.delta` trend).
- Follow with decision panels ("Needs attention", "This week") using `.card` + `.sev`/`.badge`
  rows that link to the underlying record by name.
- Semantic color (good/warn/crit) signals state; the accent is reserved for interaction — the
  two never collide.
- Charts/sparklines (when added) get the same care as type: an area fill, a faint grid, an
  emphasized endpoint. No decorative panels — every panel supports a staff or management
  decision.
- Authorization-filtered before aggregation: a scoped user's dashboard shows only their
  in-scope records, stated plainly ("You are seeing 6 of 128 organizations").

---

## 12. Status colors

Status is a fixed, semantic mapping — the same everywhere (exceptions, obligations, plans,
returns, SLA):

| State | Token | Badge/pill | Severity stripe |
|---|---|---|---|
| Healthy / Active / Resolved | `--good` (success green) | `.badge.good` | `.sev.low` |
| Informational / In progress | `--info` (steel-blue) | `.badge.info` | `.sev.medium` |
| At risk / Attention soon | `--warn` (amber) | `.badge.warn` | `.sev.high` |
| Breached / Overdue / Error | `--crit` (danger red) | `.badge.crit` | `.sev.critical` |
| Draft / Neutral | `--muted` | `.badge` (default) | `.sev` (default) |

None of these is the brand hue — the teal `--accent` is reserved for interaction (buttons,
links, active nav, focus), never for status. Color is always paired with text and/or shape for
accessibility (see §17).

---

## 13. Empty states

`.empty` = a soft dashed surface with a glyph tile (`.em-ico`), a plain-language heading, one
sentence of guidance, and a single primary action. Empty means "nothing here yet / nothing
matched," phrased for the user — e.g., "No organizations yet — add the first employer to start
tracking benefits, plans, and obligations." Distinguish three cases in copy: *never created*,
*filtered to zero*, and *outside your scope* (the last never implies a record exists).

---

## 14. Loading states

- **Skeletons** (`.skel`) mirror the shape of the content that will replace them (text lines,
  a button block, table rows) — a shimmer that respects `prefers-reduced-motion`.
- Server-rendered pages appear complete; skeletons are for the optional progressive-enhancement
  paths (async filter/sort) only.
- Buttons that trigger a submit show a pending state and disable to prevent double-submit.

---

## 15. Error states

Browser users never see raw JSON. Every error is a styled page/panel in the shell:

- **401 Unauthenticated** → redirect to the login page (not a JSON body).
- **403 Not authorized** → a styled panel: mono `403 · NOT AUTHORIZED`, a plain heading, one
  sentence naming the missing capability, and an action to the user's permitted home ("Go to My
  Work"). Resolves demo UX-02/UX-08.
- **404 Not found** → a styled panel. **Out-of-scope records return the same 404 as truly
  missing ones** — existence is never disclosed; only the styling changes, never the
  authorization behavior.
- **500** → a calm styled page with the `request_id` (mono) for support correlation.
- Inline/form errors use the `.field.invalid` + `.err` pattern (§10). No apologies, no
  vagueness — what went wrong and how to fix it.

---

## 16. Design tokens

All tokens are CSS custom properties on `:root`, redefined under
`@media (prefers-color-scheme: dark)` and again under `:root[data-theme="dark"]` /
`:root[data-theme="light"]`. Components reference tokens only — never hard-coded colors.

```css
:root {
  /* neutrals */
  --ground:#F5F7F7; --surface:#FFFFFF; --surface-2:#EDF1F1; --surface-3:#E4EAE9;
  --border:#D8E0DF; --border-strong:#BCC8C6;
  --text:#14201F; --text-2:#33423F; --muted:#5E6E6A;
  /* primary brand (exposed as --accent) */
  --accent:#0E6E63; --accent-2:#0A544B; --accent-soft:#DBEEEB; --accent-ink:#063A34;
  /* semantic — independent of the brand */
  --good:#2F8A3E; --good-soft:#E2F1E1;   /* success */
  --warn:#B4690E; --warn-soft:#F9EAD3;   /* warning */
  --crit:#B42318; --crit-soft:#F9E3E0;   /* danger  */
  --info:#35618E; --info-soft:#E5EBF3;   /* info (steel-blue, not the brand) */
  /* radii + elevation */
  --radius-sm:5px; --radius:8px; --radius-lg:12px;
  --shadow-sm:0 1px 2px rgba(15,32,30,.06);
  --shadow:0 4px 14px rgba(15,32,30,.09);
  /* type */
  --font-sans: ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --font-mono: ui-monospace, "SF Mono", "Cascadia Code", "Roboto Mono", Menlo, Consolas, monospace;
  /* frame */
  --nav-w:232px; --header-h:56px;
}
```

Tokens ship as a single `app/static/css/app.css` (design layer) plus a small components layer;
`main.css`, `work.css`, `tax.css`, and `workspace.css` are consolidated into it and retired.

---

## 17. Accessibility standards

- **Contrast:** body and UI text meet WCAG AA (≥ 4.5:1); large headings ≥ 3:1. Both themes are
  verified, not naively inverted.
- **Never color alone:** status is always paired with text and/or a shape (badge label,
  severity stripe, icon).
- **Focus:** every interactive element has a visible `:focus-visible` ring (`--accent` +
  offset). Focus order follows DOM order.
- **Semantics:** real `<table>`/`<th scope>`, `<button>` vs `<a>` by function, `<label>` tied to
  each input, `aria-invalid` on invalid fields, landmarks (`header`/`nav`/`main`).
- **Keyboard:** all actions reachable and operable without a mouse; no hover-only affordances.
- **Motion:** `prefers-reduced-motion: reduce` disables shimmer/transitions.
- **Targets:** interactive controls ≥ 28px, primary controls 34–36px.

---

## 18. Responsive behavior

- **≥ 1024px:** full shell — persistent sidebar + header + fluid content.
- **720–1024px:** sidebar persists (or condenses to icons on the narrow end); two-column panel
  grids may collapse; tables scroll within their own container.
- **< 720px:** sidebar collapses behind a menu toggle; panel grids and the spec's two-column
  blocks become single column; the page body still never scrolls horizontally — wide content
  scrolls inside its `overflow-x:auto` wrapper.
- Metric strips use `auto-fit`/`minmax`, so they reflow without breakpoints.

---

## 19. Before / after examples

**Shell.** Before: ~16 templates extend a 4-line `base.html` (flat text-link nav, no left
nav), while dashboard, work, households, people, tasks, activities, relationships, admin,
workflows, and Microsoft 365 are **standalone** documents that each declare their own
`<!doctype>`, `<head>`, and often an inline `<style>`. After: one `.app-shell` wraps them all.

**Raw HTML in Python.** Before: ~10 route files emit markup via f-strings/`HTMLResponse` —
notably `matches.py` (~147 lines), `search.py` (~47), `people.py` (~47) — with raw-ID links
like `entity 1047 → household 22 (0.82) [view 1047] [view 22]`. After: the same data as a
`table.data` with **named** links (`Northwind Mfg.` / `Hawthorne Household`), a `.badge`
confidence, and real action buttons.

**CSS.** Before: `.card`, `.work-card`, `.stat-card`, `.metrics article`, `.panel` — five
non-interchangeable ways to draw the same box across four stylesheets. After: one `.card` /
`.stat` vocabulary in one tokenized stylesheet.

The visual companion renders these side by side (§7–§8 of the HTML spec).

---

## 20. Naming conventions

- **CSS classes:** lowercase, hyphenated, semantic (what it *is*, not how it looks) — `.card`,
  `.stat`, `.badge.warn`, `.table-wrap`. Shell parts are prefixed `app-` (`.app-shell`,
  `.app-header`, `.app-nav`). State modifiers are single words: `.active`, `.gated`, `.invalid`,
  `.valid`, `.sm`. Severity/status modifiers match the semantic tokens: `.good`, `.warn`,
  `.crit`, `.info`; `.critical`, `.high`, `.medium`, `.low`.
- **Tokens:** `--<role>[-<variant>]` — `--surface-2`, `--accent-soft`, `--good-soft`,
  `--border-strong`. Never reference a raw hex in a component.
- **Templates:** `<module>/<screen>.html`, extending `base.html`; shared fragments in
  `partials/` and Jinja macros in a `components/` include (e.g. `{{ badge('warn','At risk') }}`).
- **Icons:** one inline SVG sprite (`#ico-<name>`); no icon-font dependency.
- **Copy:** name things by what people recognize (a person manages *notifications*, not
  *webhook config*); active voice; controls state the outcome; errors explain the fix.

---

## 21. Rollout (reference — not authorized yet)

Delivered in six phases per the established cadence (each committed separately, stop-for-review,
RC gate). Additive/reversible; templates and CSS only.

| Phase | Scope | Effort |
|---|---|---|
| **P0 — Foundation** | Tokenized `app.css`, Jinja component macros, shared render helper + context processor, styled error pages; proven on 2 pilot screens | 3–4 d |
| **P1 — Shell** | New `base.html` (left nav + header + layout); migrate the ~16 screens already on `base.html` | 3–4 d |
| **P2 — Core screens** | Convert the standalone templates onto the shell | 5–7 d |
| **P3 — De-raw HTML** | Move `matches`/`search`/`people` & peers into templates; named links everywhere | 3–4 d |
| **P4 — Interaction** | Tables (sort/paginate), search & filtering, form validation, empty/loading/success/error states | 3–5 d |
| **P5 — Portal + RC** | Align portal shell to shared tokens; a11y/responsive QA; authorization & record-scope regression across personas; RC12 validation | 3–4 d |

**Total ≈ 20–28 dev-days.** Interaction states default to server-side (post-redirect-get,
inline re-render). A small progressive-enhancement layer for async filtering is an **optional,
separately-approved** decision — the default honors "no second frontend framework."

> **Approval gate.** Nothing in this document is implemented until both this written
> specification and the [visual specification](design/client360_shell_spec.html) are approved.
> On approval, work begins with **Phase 0 only**, then stops for review.
