"""Production configuration preflight — fail-fast, secret-free.

Validates the deployment configuration BEFORE boot/migrate. Reuses ``app.config`` and
``app.deploy.checks``; it prints only variable NAMES and present/missing/ok — never a secret value.
Distinguishes fatal errors (missing production secrets, dev fallback in production) from warnings
(recommended-but-optional). Exit code 0 = ok/warnings-only, 1 = fatal.
"""
from __future__ import annotations

from app.deploy import checks


def validate_config() -> dict:
    """Return a structured, secret-free validation result."""
    presence = checks.config_presence()
    prod = presence["production"]
    present = presence["present"]

    fatal: list[str] = []
    warnings: list[str] = []

    if prod:
        for name in checks.PRODUCTION_REQUIRED:
            if not present[name]:
                fatal.append(f"{name} is required in production and is not set.")
        if checks.using_dev_session_secret():
            fatal.append("SESSION_SECRET is unset or the insecure development fallback — "
                         "set a strong SESSION_SECRET before production.")
        if checks.dev_auth_enabled():
            fatal.append("CLIENT360_DEV_AUTH must not be enabled in production.")
    else:
        if checks.using_dev_session_secret():
            warnings.append("SESSION_SECRET is the development fallback (fine for dev, not production).")
        for name in checks.PRODUCTION_REQUIRED:
            if not present[name]:
                warnings.append(f"{name} is not set (required before production).")

    for name in checks.RECOMMENDED:
        if not present[name]:
            warnings.append(f"{name} is not set (recommended).")

    vault_ok, vault_detail = checks.vault_storage_writable()
    if not vault_ok:
        (fatal if prod else warnings).append(f"Vault storage is not writable: {vault_detail}")

    checks_map = {name: ("ok" if val else ("missing" if name in checks.PRODUCTION_REQUIRED and prod
                                            else "unset"))
                  for name, val in present.items()}
    checks_map["session_secret"] = "ok" if presence["session_secret_ok"] else "insecure"
    checks_map["vault_storage"] = "ok" if vault_ok else "error"
    checks_map["secure_cookies"] = "on" if presence["secure_cookies"] else "off"

    return {
        "environment": presence["environment"],
        "production": prod,
        "ok": not fatal,
        "fatal": fatal,
        "warnings": warnings,
        "checks": checks_map,          # names only — no values
    }


def _print(result: dict) -> None:
    print(f"Client360 configuration — environment={result['environment']} "
          f"production={result['production']}")
    for name, state in sorted(result["checks"].items()):
        print(f"  [{state:>8}] {name}")
    for w in result["warnings"]:
        print(f"  WARNING: {w}")
    for f in result["fatal"]:
        print(f"  FATAL:   {f}")
    print("RESULT:", "OK" if result["ok"] else "FAILED")


def _load_env_file() -> None:
    """Load app/.env so the CLI validates the SAME production config the app boots with (uvicorn loads
    it via --env-file; app.config reads SESSION_SECRET at import before app.db loads the dotenv).
    override=False so real process/system env always wins and no value is clobbered."""
    try:
        from dotenv import load_dotenv
        load_dotenv("app/.env", override=False)
    except Exception:  # noqa: BLE001 — best-effort; missing file just means rely on process env
        pass


def main(argv=None) -> int:
    _load_env_file()
    result = validate_config()
    _print(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
