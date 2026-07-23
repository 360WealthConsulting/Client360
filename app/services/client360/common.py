"""Shared helpers for the Client 360 Workspace (Phase D.40)."""
from __future__ import annotations

import json


def as_json(payload):
    """Coerce rows/Decimals/datetimes into JSON-safe structures."""
    return json.loads(json.dumps(payload if payload is not None else {}, default=str))
