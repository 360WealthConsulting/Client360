# config/

Environment-aware configuration scaffold (E1.1).

- **`.env.example`** — the documented, **secret-free** template of environment
  variables. Copy needed values into `app/.env` (gitignored) for local runs.
- Runtime configuration and startup validation live in **`app/config.py`**
  (`validate_startup_configuration()`), called from the FastAPI lifespan. This
  scaffold documents configuration; it does not replace that mechanism (ADR-013:
  architecture evolves around the working implementation).

**Never commit** secrets, tokens, credentials, client data, or PII here.
