# Developer Demo Mode — Release Notes

**Status:** Implemented and available for local evaluation · merged to `main`
(merge commit `3bf0eec`) · **developer tooling, not a numbered product release**
(the release policy versions product releases such as `v0.9.9`; demo tooling adds
no schema change and ships no product tag).

## Purpose

A repeatable, safety-guarded way to run Client360 locally against realistic
**fictional** data so the platform can be evaluated hands-on across every major
interface and every role — without any real client data, credentials, or Microsoft
tokens, and without touching the normal Client360 database.

## Safety boundaries

- **`_demo` database only.** Every command and the demo entrypoint call
  `app/demo/safety.py::assert_demo_database()`, which refuses unless the target
  database name ends in `_demo` and the environment is not production.
- **Isolated from production code paths.** All demo code lives under `app/demo/`.
  Production runs `app.main:app` and imports none of it, so no demo login or demo
  route exists in production. The demo runs via `uvicorn app.demo.demo_app:app`,
  which fails fast at import if not pointed at a `_demo` database.
- **Real security, unchanged.** `/demo/login` requires a password and issues sessions
  through the real `authenticate_claims`/`create_session` (staff) and
  `create_portal_session` (portal) paths — real capabilities, immutable audit. No
  auth bypass; no RBAC change; no role gained `record.read_all`.
- **Fictional only.** All names, emails, accounts, documents, and Microsoft examples
  are fabricated.

## Fictional credentials

Sign in at `/demo/login`:

| Persona | Username | Password |
|---|---|---|
| Administrator | `admin` | `demo-admin-pass` |
| Advisor | `advisor` | `demo-advisor-pass` |
| Operations | `operations` | `demo-operations-pass` |
| Tax Preparer | `taxprep` | `demo-taxprep-pass` |
| Compliance | `compliance` | `demo-compliance-pass` |
| Client Portal User | `client` | `demo-client-pass` |

## Setup / reset / start / stop commands

```bash
source .venv/bin/activate
scripts/demo.sh setup   # create client360_demo (if missing), migrate to head, seed
scripts/demo.sh start   # start the server: http://127.0.0.1:8360/demo/login
scripts/demo.sh reset   # drop + recreate + migrate + reseed (idempotent)
scripts/demo.sh stop    # stop the server
scripts/demo.sh status  # is it running?
scripts/demo.sh verify  # confirm the target is a safe _demo database
scripts/demo.sh smoke   # safety, per-role login, role visibility, landings, routes
```

## Role landing pages

After login each persona is routed to a page its real RBAC permits (firm-wide screens
still require `record.read_all`, held only by Administrator and Compliance):

| Persona | Lands on |
|---|---|
| Administrator | `/` |
| Compliance | `/` |
| Advisor | `/work` |
| Operations | `/work` |
| Tax Preparer | `/tax` |
| Client Portal User | `/portal/` |

## Seeded domains

Households; people; prospects; businesses and trusts (relationship entities);
relationships; staff users (Administrator, Advisor, Operations, Tax Preparer,
Compliance) + a demo-only `tax_preparer` role; accounts, holdings, securities,
beneficiaries; activities; timeline events (incl. Microsoft-sourced examples);
documents; Microsoft mail/calendar/document examples; tasks; work assignments;
work queues (seeded by migrations); workflows and steps; tax engagements, returns,
checklist items, and missing-information items; a portal account with document
requests, a secure message thread, and notifications; audit events.

## Validation summary

- Demo DB safety guard refuses non-demo databases; refuses production.
- Clean migration to head `o5f36c4d3e2a`; exactly one Alembic head.
- Idempotent reset/reseed (identical counts across runs).
- Startup/shutdown clean (demo app).
- Login works for **every** role; role-based visibility is real (firm-wide screens
  correctly 403 for non-firm-wide roles).
- All six personas: login → correct landing → **200 text/html** (no raw-JSON 403).
- `/portfolio` renders HTML; `/portfolio/search` still returns JSON; `/readiness` 200.
- Regression tests: `tests/test_demo_login_landing.py` (10), `tests/test_portfolio_page.py` (4).
- Full automated suite: **317 passed / 5 skipped**.
- Verified running from `main` after merge; repository clean.

## Known limitations

- **Role-based visibility is real.** Firm-wide collection screens require
  `record.read_all` (Administrator, Compliance). Opening one as Advisor/Operations/Tax
  Preparer returns **403 by design** — use their scoped screens.
- **No live Microsoft 365.** Microsoft rows are fictional examples; no tenant/tokens/
  sync. The "Connect/Mail" actions redirect to real Microsoft OAuth and cannot complete
  in the demo.
- **`tax_preparer` role is demo-only** (the base RBAC grants tax capabilities only to
  `administrator`).
- Fictional documents are metadata-only; the download action 404s.

## Remaining non-blocking UX findings

Tracked in [Demo UX Review](DEMO_UX_REVIEW.md); the blocker (UX-01) is resolved. Open
fast-follow items:

- **UX-02** — 401/403 errors render as raw JSON to browser users (HTML error pages).
- **UX-03** — global staff nav is not capability-filtered (dead-end links per role).
- **UX-04** — `/matches` uses inconsistent standalone styling.
- **UX-05** — Microsoft 365 Mail/Connect dead-ends at real Microsoft OAuth (wrong port).
- **UX-06** — document download 404 ("Stored file is missing") for metadata-only docs.
- **UX-07** — `/portfolio/search` exposes raw JSON at a non-`/api` path.
- **UX-08** — logged-out `/` shows raw JSON 401 instead of a login prompt.
- **UX-09** — firm dashboard links to `/admin` (403 for Compliance).
- **UX-10** — responsive/empty states acceptable; verify visually in a later design pass.
