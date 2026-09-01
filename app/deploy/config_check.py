"""Production configuration preflight — fail-fast, secret-free.

Validates the deployment configuration BEFORE boot/migrate. Reuses ``app.config`` and
``app.deploy.checks``; it prints only variable NAMES and present/missing/ok — never a secret value.
It also reports WHICH environment file it resolved (path only) and warns when an ambiguous sibling of
the canonical production file exists, so "RESULT: OK" can never come from a file nobody loads.
Distinguishes fatal errors (missing production secrets, dev fallback in production) from warnings
(recommended-but-optional). Exit code 0 = ok/warnings-only, 1 = fatal.
"""
from __future__ import annotations

import os

from app.deploy import checks, service


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

    # Normalized-image derivatives (HEIC/HEIF -> JPEG). Fatal in production for the same reason the
    # Vault root is: an unusable store silently degrades every downstream image consumer. In
    # production this also fails when IMAGE_DERIVATIVE_ROOT is unset or relative, so a misconfigured
    # host cannot write generated derivatives into the deployed source tree.
    derivative_ok, derivative_detail = checks.image_derivative_storage_writable()
    if not derivative_ok:
        (fatal if prod else warnings).append(
            f"Image derivative storage is not usable: {derivative_detail}")

    checks_map = {name: ("ok" if val else ("missing" if name in checks.PRODUCTION_REQUIRED and prod
                                            else "unset"))
                  for name, val in present.items()}
    checks_map["session_secret"] = "ok" if presence["session_secret_ok"] else "insecure"
    checks_map["vault_storage"] = "ok" if vault_ok else "error"
    checks_map["image_derivative_storage"] = "ok" if derivative_ok else "error"
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


def _candidate_env_files() -> list[str]:
    """Where the CLI looks for the environment file, in order.

    The repo-relative path first, so a developer checkout, CI, Docker and the test suite keep loading
    their own file exactly as before. The canonical Windows production file second, so running
    check-config from the wrong directory on the production server validates the file the service
    actually loads instead of silently validating nothing at all."""
    return ["app/.env", service.PRODUCTION_ENV_FILE]


def _ambiguous_siblings() -> list[str]:
    """Sibling files that are NOT loaded by the service but exist on disk. Paths only, never read."""
    return [p for p in service.AMBIGUOUS_ENV_FILES if os.path.isfile(p)]


def load_environment_file() -> dict:
    """Load the environment file the app boots with and report WHICH file — never its contents.

    uvicorn loads it via --env-file; app.config reads SESSION_SECRET at import before app.db loads
    the dotenv. override=False so real process/system env always wins and no value is clobbered.

    Reporting the resolved path matters as much as loading it: an operator who edited the wrong file
    used to get a clean "RESULT: OK" from the ambient process environment, which is precisely how a
    change to a non-loaded env file looked successful."""
    for candidate in _candidate_env_files():
        if not os.path.isfile(candidate):
            continue
        try:
            from dotenv import load_dotenv
            load_dotenv(candidate, override=False)
        except Exception:  # noqa: BLE001 — best-effort; fall through to the next candidate
            continue
        return {"env_file": candidate, "ambiguous": _ambiguous_siblings()}
    return {"env_file": None, "ambiguous": _ambiguous_siblings()}


def _print_env_file(loaded: dict) -> None:
    """Report the resolved environment file by PATH only. No variable names, no values."""
    if loaded["env_file"]:
        print(f"Environment file: {loaded['env_file']}")
    else:
        print("Environment file: none found — validating the ambient process environment only.")
    for sibling in loaded["ambiguous"]:
        print(f"  WARNING: {sibling} exists and is NOT the file the service loads. "
              f"The production environment file is {service.PRODUCTION_ENV_FILE}.")


def main(argv=None) -> int:
    loaded = load_environment_file()
    result = validate_config()
    _print_env_file(loaded)
    _print(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
