"""Source adapters for the generic migration engine.

Each adapter implements ONLY discovery (yielding SourceItems) + where its data lives. Everything after
discovery — classify, match, destination, validate, apply, reconcile — is the generic engine.
"""
from app.services.migration.adapters.taxdome import TaxDomeAdapter

__all__ = ["TaxDomeAdapter"]
