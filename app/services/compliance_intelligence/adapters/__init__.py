"""Compliance Intelligence adapters (Phase D.47) — read-only, fail-closed composers that turn the
authoritative compliance/review/exception/licensing services into supervisory items + compliance
exceptions. They never mutate, never submit/assign/decide/resolve, and each is independently testable.
"""
from .exceptions import compliance_exceptions
from .licensing import licensing_items
from .reviews import review_items

__all__ = ["review_items", "compliance_exceptions", "licensing_items"]
