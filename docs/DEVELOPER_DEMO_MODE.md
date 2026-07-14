# Developer Demo Mode

A repeatable, **isolated** demo of Client360 with realistic **fictional** data.
It runs against a dedicated `client360_demo` database, never touches the normal
Client360 database, uses no real client data / credentials / Microsoft tokens,
and reuses the real authentication and authorization — it does **not** weaken any
security control and adds no production login bypass.

> All names, emails, accounts, and documents here are fictional. The demo login
> lives only in the demo entrypoint (`app.demo.demo_app`); production runs
> `app.main:app` and contains no demo code.

## 1. Quick start (exact commands)

From the repository root, with the virtualenv active and PostgreSQL running:

```bash
source .venv/bin/activate

# One-time: create client360_demo, migrate to head, seed fictional data
scripts/demo.sh setup

# Start the demo server (background)
scripts/demo.sh start
```

Then open: **http://127.0.0.1:8360/demo/login**

## 2. Local URL

| What | URL |
|---|---|
| Demo login | http://127.0.0.1:8360/demo/login |
| Staff app (after login) | http://127.0.0.1:8360/ |
| Client portal (after portal login) | http://127.0.0.1:8360/portal/ |
| Liveness / readiness | http://127.0.0.1:8360/health · http://127.0.0.1:8360/readiness |

Host/port are configurable: `DEMO_HOST`, `DEMO_PORT` (default `127.0.0.1:8360`).

## 3. Demo credentials (fictional)

Sign in at `/demo/login` with a username + password.

| Persona | Username | Password | Name |
|---|---|---|---|
| Administrator | `admin` | `demo-admin-pass` | Avery Stone |
| Advisor | `advisor` | `demo-advisor-pass` | Morgan Reed |
| Operations | `operations` | `demo-operations-pass` | Riley Chen |
| Tax Preparer | `taxprep` | `demo-taxprep-pass` | Jordan Pace |
| Compliance | `compliance` | `demo-compliance-pass` | Sasha Vale |
| Client Portal User | `client` | `demo-client-pass` | Taylor Hawthorne |

**Administrator** sees firm-wide screens (the full tour). The other staff roles
are scoped by real capability-based RBAC (see limitations). The portal user lands
in the client portal.

## 4. Database safety design

Safety is enforced in `app/demo/safety.py` and applied by every command:

- **`_demo` suffix required.** Seeding, reset, start, and smoke all call
  `assert_demo_database()`, which refuses unless the target database name ends in
  `_demo`. Pointing at `client360` (or anything else) is rejected.
- **Never production.** The guard also refuses when `CLIENT360_ENVIRONMENT=production`.
- **Isolated tooling.** All demo code is under `app/demo/`. Production
  (`app.main:app`) never imports it, so no demo login or demo path exists in
  production. The demo entrypoint fails fast at import if it is not pointed at a
  `_demo` database.
- **Real security, unchanged.** Demo logins issue sessions through the real
  `authenticate_claims` / `create_session` (staff) and `create_portal_session`
  (portal) paths, with real capabilities and immutable audit. A password is
  required — there is no automatic login bypass.

Verify the guard yourself:

```bash
scripts/demo.sh verify
```

## 5. Reset / reseed

Reset drops and recreates `client360_demo`, migrates to head, and reseeds. It is
idempotent (same data every time):

```bash
scripts/demo.sh reset
```

## 6. Screens and workflows to review (start here)

Sign in as **Administrator** (`admin` / `demo-admin-pass`) for 1–8, then switch
personas for 9–10:

1. **Firm dashboard** — `/` — overview metrics and alerts.
2. **People** — `/people` — clients, plus prospects (Priya Nair, Wesley Booker).
3. **Households** — `/households` — Hawthorne, Okoro, Delgado, Kensington Trust.
4. **My Work** — `/work` — daily agenda, assignments, SLA, capacity.
5. **Tasks** — `/tasks` — open tasks across households (priorities/due dates).
6. **Tax intake** — `/tax/intake` — engagement readiness (letters, organizers,
   questionnaires, checklist, missing items).
7. **Tax returns** — `/tax/returns` — return lifecycle board.
8. **Audit log** — `/admin/audit` — immutable audit trail of demo activity.
9. **Tax Preparer** persona (`taxprep`) → `/tax/intake`, `/tax/returns` — tax work
   with tax capabilities.
10. **Client Portal** persona (`client`) → `/portal/` — client dashboard, document
    requests, and the secure message thread ("Welcome to your client portal").

Also worth a look: `/activities`, `/portfolio/search`, `/microsoft365/status`
(sync-health), and `/readiness`.

## 7. Known demo limitations

- **Role-based visibility is real.** Firm-wide collection screens (`/`, `/people`,
  `/tasks`) require the `record.read_all` capability, which only **Administrator**
  has. Signing in as Advisor/Operations/Tax Preparer/Compliance and opening a
  firm-wide screen returns **403 by design** — use their scoped screens (e.g. an
  advisor's `/work`) or use the Administrator for the full tour.
- **No live Microsoft 365.** Microsoft mail/calendar/document rows are fictional
  examples; no real tenant, tokens, or sync run. The background scheduler is
  active but has nothing real to sync.
- **`Tax Preparer` role is demo-only.** The base RBAC grants tax capabilities only
  to `administrator`; the seeder adds a `tax_preparer` role in the demo database so
  that persona is useful. This role does not exist in production data.
- Fictional documents are metadata-only (no files on disk).

## 8. Stop the server

```bash
scripts/demo.sh stop
```

Check status any time with `scripts/demo.sh status`.

## 9. Command reference

| Command | Action |
|---|---|
| `scripts/demo.sh setup` | Create demo DB (if missing), migrate to head, seed |
| `scripts/demo.sh reset` | Drop + recreate + migrate + reseed (idempotent) |
| `scripts/demo.sh start` | Start the demo server in the background |
| `scripts/demo.sh stop` | Stop the demo server |
| `scripts/demo.sh status` | Show whether the server is running |
| `scripts/demo.sh verify` | Confirm the target is a safe `_demo` database |
| `scripts/demo.sh smoke` | Run demo smoke tests (safety, logins, visibility, routes) |
