"""Shared, behavior-preserving helpers for the compliance services (Phase D.8A).

Small utilities with a single clear responsibility, each with two real consumers
(compliance/reviews.py and compliance/authority_admin.py). They introduce **no**
behavior change — the extracted logic is byte-for-byte equivalent to the per-service
code it replaced (proven by the D.7/D.8 behavioral suites). This is deliberately NOT a
generic workflow/state-machine engine: domain-specific transition rules stay in their
own services; only the mechanical stale-load and pagination math are shared here.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select


def now() -> datetime:
    """The application timestamp used by every compliance write (UTC)."""
    return datetime.now(UTC)


def load_for_update(conn, table, row_id, expected_status, *, noun: str,
                    not_found_error: type[Exception], stale_error: type[Exception]):
    """Load a row ``FOR UPDATE`` and enforce optimistic-concurrency (``expected_status``).

    Raises ``not_found_error(f"{noun} not found")`` when the row is absent and
    ``stale_error("{noun} is now …, not …; reload and retry")`` when the current status
    differs from ``expected_status`` — the exact messages/types each service used before,
    so a stale form submission still fails loudly rather than overwriting later changes.
    """
    row = conn.execute(
        select(table).where(table.c.id == row_id).with_for_update()
    ).mappings().first()
    if row is None:
        raise not_found_error(f"{noun} not found")
    if expected_status is not None and row["status"] != expected_status:
        raise stale_error(
            f"{noun} is now {row['status']!r}, not {expected_status!r}; reload and retry")
    return row


def clamp_page(page: int, page_size: int, *, max_size: int = 200) -> tuple[int, int]:
    """Normalize a (page, page_size) request: page >= 1, 1 <= page_size <= max_size —
    the exact bounds both list endpoints used."""
    return max(1, page), max(1, min(page_size, max_size))


def page_count(total: int, page_size: int) -> int:
    """Number of pages for ``total`` rows at ``page_size`` (0 when empty) — the exact
    ceiling division both list endpoints used."""
    return (total + page_size - 1) // page_size if total else 0
