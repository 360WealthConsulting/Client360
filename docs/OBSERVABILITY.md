# Client360 — Observability Foundation (E1.5)

Baseline observability for operating Client360: central application logging plus
the existing health/readiness endpoints. This is a foundation, added in-place
(ADR-013) without changing application behavior — it standardizes how the app's
own log lines are rendered and documents the operational signals that already
exist.

## Logging

Before E1.5, the app used ad-hoc `logging.getLogger("client360.*")` with no
central configuration (Python defaults: WARNING, unformatted). E1.5 adds one
idempotent configurator:

- **`app/observability/logging.py` → `configure_logging()`** — configures the
  **`client360`** logger namespace only (not the root logger, not uvicorn), so it
  changes log *formatting/level*, never request handling or responses.
- Wired into application startup (the FastAPI lifespan), so it takes effect when
  the server runs. Importing the app (e.g., in tests) does not trigger it.

### Configuration (environment-aware)
| Variable | Values | Default | Effect |
|---|---|---|---|
| `LOG_LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` | `INFO` | Level for the `client360` logger |
| `LOG_FORMAT` | `plain`/`json` | `plain` | Human-readable vs single-line JSON |

- **plain**: `2026-07-18 12:00:00,000 INFO client360.ops: <message>`
- **json**: `{"time": "...", "level": "INFO", "logger": "client360.ops", "message": "...", ...}`
  — plus any structured fields passed via `logger.info(..., extra={...})`.

Set these in `app/.env` (see `config/.env.example`). For production log
aggregation, prefer `LOG_FORMAT=json`.

### Using it in code
Existing call sites are unchanged. Continue to use the `client360` namespace:
```python
import logging
logger = logging.getLogger("client360.<area>")
logger.info("something happened", extra={"account_id": account_id})  # never log PII
```
> Never log secrets, PII, SSNs, or tax-return data (Engineering Constitution §9).

## Health & readiness endpoints (existing)
| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | public | DB-independent **liveness** — `{"status": "ok", ...}` |
| `GET /readiness` | public | **Readiness**: database connectivity, Alembic head in-sync, Microsoft-sync health, scheduler status. Returns HTTP 503 when not ready. |

`scripts/dev.sh doctor` also surfaces environment/schema/config signals locally
(see [LOCAL_DEVELOPMENT.md](LOCAL_DEVELOPMENT.md)).

## Known gaps / future improvements (non-blocking)
- **Request correlation IDs** — not yet added (would touch the request path);
  deferred to keep E1.5 behavior-neutral. A future item can add a correlation-id
  contextvar + logging filter.
- **`/version` endpoint** — not present today; a future item may add build/version
  reporting.
- **Metrics / tracing** — no metrics or distributed tracing yet; future work.
- **The single `getLogger(__name__)` call site** is outside the `client360`
  namespace and uses root defaults; migrate it to `client360.*` when touched.

## Troubleshooting
| Symptom | Fix |
|---|---|
| No app logs appear | Ensure the server started (lifespan runs `configure_logging`); lower `LOG_LEVEL` |
| Logs not JSON in prod | Set `LOG_FORMAT=json` in the environment |
| Duplicate log lines | The `client360` logger sets `propagate=False`; check for a second manual handler |
