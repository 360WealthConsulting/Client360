"""360 Data Consolidation & Legacy-System Retirement — migration framework (Phase 1).

Reuses the existing Client360 canonical model (source_contacts / people / households / documents /
document_sources / import_jobs) and the human-approved MDM merge engine — it adds no new architecture,
only the migration orchestration (inventory / preview / apply / reconcile / rollback) with per-run
artifacts. See ``app/services/migration/base.py``.
"""
from app.services.migration.base import (
    MigrationJob,
    MigrationResult,
    Mode,
    ModeNotSupported,
    Outcome,
)
from app.services.migration.config import MigrationConfig

__all__ = ["Mode", "MigrationJob", "MigrationResult", "ModeNotSupported", "Outcome", "MigrationConfig"]
