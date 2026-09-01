"""Inspect and maintain the SharePoint Microsoft Graph change-notification subscription.

    python -m scripts.manage_sharepoint_subscription --status
    python -m scripts.manage_sharepoint_subscription --ensure

``--ensure`` is idempotent and is what the scheduled renewal runs: it CREATES the subscription when
missing, RENEWS it when at most ``RENEW_BEFORE_MINUTES`` (12h) of its ~70h lifetime remain, and leaves
a healthy subscription untouched otherwise.

Output is JSON on stdout and is SECRET-FREE. Graph echoes ``clientState`` - the shared secret the
webhook handler uses to prove a notification is genuine - back in the subscription object, and this
command's output lands in Task Scheduler logs, terminals and CI transcripts. Redaction happens in
``app.services.sharepoint_subscription`` so every caller is safe; the pass here is a deliberate second
line of defence for anything a future code path might return unsanitised.

Exit codes: 0 on success, 1 on failure, so a scheduler treats a failed renewal as a failed run. The
error path prints the exception TYPE and message but never the payload that produced it.
"""
from __future__ import annotations

import argparse
import json
import sys

from dotenv import load_dotenv

from app.services.sharepoint_subscription import (
    ensure_subscription,
    redact_secrets,
    subscription_status,
)

PROD_ENV_PATH = r"C:\Client360\app\.env"


def _emit(payload: object) -> None:
    """Print JSON with a second redaction pass - belt and braces over the service's own."""
    print(json.dumps(redact_secrets(payload), indent=2, default=str, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    load_dotenv(PROD_ENV_PATH)
    parser = argparse.ArgumentParser(
        prog="python -m scripts.manage_sharepoint_subscription",
        description="Inspect or maintain the SharePoint Graph change-notification subscription.")
    parser.add_argument("--status", action="store_true",
                        help="Report the current subscription without changing anything.")
    parser.add_argument("--ensure", action="store_true",
                        help="Create, renew, or leave the subscription unchanged (idempotent).")
    args = parser.parse_args(argv)

    if not (args.status or args.ensure):
        parser.error("Specify --status or --ensure")

    try:
        _emit(subscription_status() if args.status else ensure_subscription())
    except Exception as exc:  # noqa: BLE001 - any failure is a failed run for the scheduler
        # Type + message only: never the request/response body, which can carry clientState or a token.
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
