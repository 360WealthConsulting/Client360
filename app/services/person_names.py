"""Canonical person display name — ONE implementation, used by every read model.

``people.full_name`` is a convenience column that is NOT populated for every person: many canonical
rows carry only ``first_name`` / ``last_name``. A read model that renders ``full_name`` bare therefore
renders ``None`` (or falls through to a ``"<entity_type> <id>"`` placeholder) for exactly those people,
even though the name is present in the canonical columns.

This module is the single place that resolution lives: ``full_name`` when it has content, otherwise
``first_name + last_name``, otherwise the caller's fallback. It is pure — no database access, no
imports from any service — so any layer may use it without creating a cycle.
"""
from __future__ import annotations

UNNAMED = "(unnamed)"


def person_display_name(full_name=None, first_name=None, last_name=None, *, fallback=None) -> str:
    """The name to show for a person. Never returns ``None`` and never invents an identifier."""
    if full_name and full_name.strip():
        return full_name.strip()
    combined = f"{(first_name or '').strip()} {(last_name or '').strip()}".strip()
    if combined:
        return combined
    if fallback and str(fallback).strip():
        return str(fallback).strip()
    return UNNAMED


def person_row_display_name(row, *, fallback=None) -> str:
    """``person_display_name`` for a mapping/row carrying any of full_name/first_name/last_name.

    Tolerates rows that select only a subset of those columns (``.get`` on each), so a caller that
    has not yet widened its SELECT degrades to the previous behaviour instead of raising.
    """
    if row is None:
        return person_display_name(fallback=fallback)
    return person_display_name(row.get("full_name"), row.get("first_name"), row.get("last_name"),
                               fallback=fallback)
