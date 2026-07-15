# RC-1 — Release 0.9.12 (Application Shell & UI Consolidation) — UI Validation

**Scope:** Live regression + release-candidate validation of the staff UI after the
full shell migration. **Branch:** `feature/ui-design-system`. **Method:** live crawl
of the running demo (all personas), headless-browser rendering (Chromium/Edge) at
desktop/tablet/mobile in light and dark, static accessibility review, and the full
automated suite. **Frontend only** — no business logic, routes, authorization, or
record-scope changed in the migration.

> A test household ("RC1 Audit Household") was created in `client360_demo` to prove
> form submission end-to-end; it is demo data and cleared by `scripts/demo.sh reset`.

---

## Verdict: ✅ **PASS — ready for interaction polish & portal alignment**

**16 of 19 criteria PASS**, **2 PASS-with-notes** (accessibility, browser
compatibility), **1 not met** (table sorting — a planned interaction-polish item, not
yet built). No defect blocks progression; the shell is consistent, correct, gated, and
performant.

---

## Checklist

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Every page renders | ✅ PASS | 25/25 staff routes → 200 inside `app-shell`, 0 legacy markers |
| 2 | Every menu works | ✅ PASS | Nav capability-gated; **every shown item returns 200** for admin(21)/advisor(5)/operations/taxprep(6)/compliance(14) — no shown-then-403 |
| 3 | Every form submits | ✅ PASS | `POST /households` created a record (rows 4→5, 303); all POST targets registered |
| 4 | Every table sorts | ⛔ **NOT MET** | Tables are static server-rendered; no column sorting exists yet (0 sort controls). **Deferred to interaction polish.** |
| 5 | Every filter works | ✅ PASS | Search `q`, Organizations status chips, Matches status filter — active state reflected, results filter |
| 6 | Every search works | ✅ PASS | `_search` returns 100 rows on populated data; header + `/search` wired to `/search`. Demo DB has 0 `source_contacts` (seed omits them) so live demo results are empty — **data, not a defect** |
| 7 | Responsive | ✅ PASS | Verified 390 / 834 / 1280px; the one overflow defect (inline grid overrides) fixed |
| 8 | Dark mode | ✅ PASS | Rendered `/work` dark — tokens, severity chips, badges all correct |
| 9 | Light mode | ✅ PASS | Rendered `/households` light — brand teal, forms, tables correct |
| 10 | Accessibility | ◐ PASS w/ notes | `<label>` on all fields (34), `:focus-visible` ring, semantic `nav/header/main/aside`, `prefers-reduced-motion`, `color-scheme`, aria on nav/search. **Recommend** a formal AT + AA-contrast audit in polish |
| 11 | Permissions | ✅ PASS | Gated pages return **styled 403** for scoped users (`/`, `/organizations`, `/benefits`, `/admin`); nav mirrors real route capabilities |
| 12 | Record scope | ✅ PASS | advisor `/people/1` (assigned)=200, `/people/2`,`/3`=403 |
| 13 | Performance | ✅ PASS | All 25 pages **6–48 ms** server time |
| 14 | Error pages | ✅ PASS | Styled 403/404 for browsers (`Accept: text/html`), **JSON preserved** for API; missing record → styled 404; 500 handler + template in place |
| 15 | Empty states | ✅ PASS | `.empty` renders (e.g. `/organizations`) |
| 16 | Mobile (390px) | ✅ PASS | Sidebar collapses behind ☰; two-column layouts stack; no body h-scroll |
| 17 | Tablet (834px) | ✅ PASS | Sidebar collapses; workspace 3-col grid → 1 col; no overflow |
| 18 | Desktop (1280px) | ✅ PASS | Full shell; all screens |
| 19 | Browser compatibility | ◐ PASS w/ notes | Chromium/Edge verified. Stack is standard (CSS custom properties, grid, flexbox, `<details>`, no JS framework) — broadly supported. **Safari/WebKit & Firefox not tested** in this environment |

**Automated suite:** 521 passed / 5 skipped (unchanged), incl. dead-code/unused-import.

---

## Detail

### Rendering, menus, permissions, scope (1, 2, 11, 12)
All 25 staff pages render in the shell. Navigation is gated to each item's real
requirement (middleware `RULES` capability + `record.read_all` for firm-wide
collection screens + `capacity.read` for Team Work), so no user is shown a link they
cannot open. Gated pages return the **styled 403**; record scope holds (advisor sees
only assigned client records). Verified across all five staff personas.

### Forms, filters, search (3, 5, 6)
End-to-end form submission verified (household created). Every form's POST action is a
registered route. Filters reflect state and narrow results. Search runs and renders
(100 results on populated data); the demo appears empty only because its seed omits
`source_contacts`/organizations/benefits fixtures — the same reason those list pages
show their empty states.

### Responsive, themes (7, 8, 9, 16, 17, 18)
Rendered at 390 / 834 / 1280 px in both themes. Sidebar collapses behind a pure-CSS ☰
toggle < 900px; two-column layouts stack; wide tables scroll within their container so
the page body never scrolls sideways. Both themes are token-driven and legible.

### Performance, errors, empty (13, 14, 15)
Server render 6–48 ms across the board. Styled 403/404 for browsers with JSON preserved
for API/tests; empty states use the shared `.empty` component.

---

## Not met / notes

- **Table sorting (⛔):** the migrated tables (`table.data`) are static — no
  clickable column sort. This is **interaction-polish scope** (server-side `?sort=`
  links or a small progressive-enhancement layer), not built in the migration. It is
  the single unmet checklist item and is recommended for the next phase.
- **Accessibility (◐):** foundational a11y is present; a formal screen-reader pass and
  WCAG-AA contrast measurement should be done during polish.
- **Browser compatibility (◐):** verified on Chromium/Edge; Safari and Firefox were not
  available to test here. No non-standard CSS/JS is used.

## Remaining usability items (carried from the regression audit)
- Relationships search labels the **root** person as "Person {id}" (the search service
  returns no name for it) — latent (no demo data); a small query enrichment in polish.
- `/benefits` list uses its own empty copy rather than the shared `.empty` component.
- User chip is display-only (account menu/logout is interaction-polish scope).

---

## Recommendation
**Proceed to interaction polish and portal alignment.** Fold in: table sorting, the
formal accessibility pass, cross-browser (Safari/Firefox) verification, and the three
usability items above.
