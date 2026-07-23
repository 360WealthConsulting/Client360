"""Shared helpers for the Advisor Workspace service (Phase D.38)."""
from __future__ import annotations

import json


def as_json(payload):
    """Coerce rows/Decimals/datetimes into JSON-safe structures (mirrors the per-domain helper)."""
    return json.loads(json.dumps(payload if payload is not None else {}, default=str))
