"""360Plus Client Feature & Access Control framework.

Public surface:
    catalog       — the code-defined feature/product catalog (add features here; no migration).
    service       — effective_access engine + audited CRUD + client_can.
    enforcement   — require_client_feature dependency for server-side portal enforcement.
"""
from app.services.features.enforcement import require_client_feature
from app.services.features.service import client_can, effective_access

__all__ = ["require_client_feature", "client_can", "effective_access"]
