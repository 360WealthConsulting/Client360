from __future__ import annotations

import argparse
import json

from dotenv import load_dotenv

load_dotenv(r"C:\Client360\app\.env")

from app.services.sharepoint_subscription import (
    ensure_subscription,
    subscription_status,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--ensure", action="store_true")
    args = parser.parse_args()

    if args.status:
        print(json.dumps(subscription_status(), indent=2, default=str))
        return

    if args.ensure:
        print(json.dumps(ensure_subscription(), indent=2, default=str))
        return

    parser.error("Specify --status or --ensure")


if __name__ == "__main__":
    main()
