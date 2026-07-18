# Client360 — Local Development Guide (E1.2)

Make Client360 reproducible on a clean workstation. One documented workflow:
**`scripts/dev.sh`**. (Demo mode uses `scripts/demo.sh`; the test suite uses
`scripts/test.sh` against a disposable `*_test` database — see below.)

## Prerequisites
- **Python 3.12** (pinned in `.python-version`).
- **PostgreSQL** — either a local install, or the Docker Compose service below.
- **Docker + Docker Compose** — *optional*, only if you don't have local PostgreSQL.
- Git.

## One-time setup
```bash
python3.12 -m venv .venv && source .venv/bin/activate
scripts/dev.sh setup     # install pinned deps, create the dev DB if missing, migrate to head
scripts/dev.sh doctor    # validate Python, DB connectivity, schema-at-head, config
```

Copy any values you need from [`config/.env.example`](../config/.env.example)
into **`app/.env`** (gitignored). **Never commit secrets.** Required/known vars:

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | e.g. `postgresql://localhost/client360` |
| `CLIENT360_ENVIRONMENT` | no | `development` (default) or `production` |
| `SESSION_SECRET` | prod only | dev uses a marked insecure fallback if unset |
| `MICROSOFT_TOKEN_KEY` | for M365 | leave unset to disable Microsoft 365 sync |

## Daily workflow
```bash
source .venv/bin/activate
scripts/dev.sh migrate   # apply any new migrations (non-destructive)
scripts/dev.sh run       # http://127.0.0.1:8000  (uvicorn app.main:app --reload)
# Ctrl-C to stop.
```
Health check (public, no auth): `curl http://127.0.0.1:8000/health`.

## Using Docker for PostgreSQL (optional)
If you don't have local PostgreSQL:
```bash
scripts/dev.sh db-up     # docker compose up -d db  (postgres:16, port 5432)
# set app/.env: DATABASE_URL=postgresql://postgres:postgres@localhost:5432/client360
scripts/dev.sh migrate
scripts/dev.sh run
scripts/dev.sh db-down   # stop the database (data persists in a named volume)
```
To run the **whole stack** (app + db) in containers:
```bash
docker compose -f infrastructure/docker-compose.yml --profile app up --build
# the app container runs `alembic upgrade head` then uvicorn on :8000
```
`docker compose ... down -v` also drops the database volume.

## Running the tests
The suite runs against a **disposable** `client360_test` database (never your dev
or production data — a structural safety guard enforces this):
```bash
scripts/test.sh run      # reset the test DB + run the full suite
scripts/test.sh fast     # run without resetting (quick iteration)
```

## Build reproducibility
- Python is pinned (`.python-version` = 3.12); runtime deps are fully pinned in
  `requirements.txt`; dev tooling in `requirements-dev.txt`.
- The container image (`infrastructure/Dockerfile`) uses a pinned base
  (`python:3.12-slim`) and installs the pinned `requirements.txt` — reproducible
  builds. `pip check` verifies the resolved set is internally consistent.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `DATABASE_URL is missing` at startup | Set it in `app/.env` (see table above) |
| `no Python 3 interpreter found` | `python3.12 -m venv .venv && source .venv/bin/activate` |
| PostgreSQL not reachable | `scripts/dev.sh db-up` (Docker) or start your local PostgreSQL |
| Schema not at head | `scripts/dev.sh migrate` |
| Microsoft 365 sync disabled warning | Expected without `MICROSOFT_TOKEN_KEY`; safe to ignore in dev |
| Port 8000 in use | `DEV_PORT=8010 scripts/dev.sh run` |
| Suite refuses to run | Target must be a disposable `*_test` DB — use `scripts/test.sh` |

## Boundaries (ADR-013)
The application root is `app/`; architecture evolves around the working
implementation. See [`docs/architecture/MODULE_MAP.md`](architecture/MODULE_MAP.md)
and [ADR-013](architecture/adr/ADR-013-repository-reconciliation.md).
