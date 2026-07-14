"""Tax filing provider port (reserved).

Defines the ``TaxFilingProvider`` protocol, the ``FilingResult`` value object,
and a manual-filing default. **Reserved for Epic 5 Sprint 5.6** (filing-provider
wiring) — no route or service imports this module yet. When it is wired it
should adopt the canonical ``app.portal.providers.ProviderRegistry`` in place of
the plain ``FILING_PROVIDERS`` dict, so all provider selection shares one
abstraction.
"""
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class FilingResult:
    status: str
    external_id: str = None
    submission_id: str = None
    reason_code: str = None
    message: str = None
    metadata: dict = None

class TaxFilingProvider(Protocol):
    key: str
    def submit(self, payload: dict) -> FilingResult: ...
    def status(self, external_id: str) -> FilingResult: ...

class ManualFilingProvider:
    key = "manual"
    def submit(self, payload): return FilingResult("submitted", metadata={"mode":"manual"})
    def status(self, external_id): return FilingResult("ready", external_id=external_id, metadata={"mode":"manual"})

FILING_PROVIDERS={"manual":ManualFilingProvider()}
