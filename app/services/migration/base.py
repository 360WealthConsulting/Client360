"""MigrationJob — the common abstraction every legacy-system migration implements.

One class per source domain (Wealthbox contacts, TaxDome documents, …). Each supports the five modes
the migration program mandates:

    inventory  — read-only: what exists at the source (counts / storage / duplicates / readiness)
    preview    — read-only: exactly what an apply WOULD create/link, plus exceptions (no writes)
    apply      — transactional import into Client360 (records an ``import_jobs`` row)
    reconcile  — read-only: source vs. staged vs. imported counts + exception list
    rollback   — undo one ``import_jobs`` batch (never deletes source data)

Guardrails baked in:
  * Only ``apply`` and ``rollback`` ever write to Client360; ``inventory`` / ``preview`` / ``reconcile``
    open a plain (uncommitted) connection and must only read. ``import_jobs`` rows are written ONLY for
    the write modes — so an inventory/preview run makes no database change of any kind.
  * Every run writes four artifacts to its own timestamped directory: ``manifest.json``,
    ``reconciliation.csv``, ``exceptions.csv``, ``summary.txt``.
  * Provenance (source system / file / hashes / source ids) is recorded in the manifest and carried into
    ``source_contacts`` / ``document_sources`` by the concrete jobs.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from app.services.migration.config import MigrationConfig


class Mode(StrEnum):
    INVENTORY = "inventory"
    PREVIEW = "preview"
    APPLY = "apply"
    RECONCILE = "reconcile"
    ROLLBACK = "rollback"


#: Modes permitted to write to Client360. All others must be strictly read-only.
WRITE_MODES: frozenset[Mode] = frozenset({Mode.APPLY, Mode.ROLLBACK})


class ModeNotSupported(RuntimeError):
    """A job was asked to run a mode it does not support / has disabled for this phase. Raised by
    ``run()`` BEFORE any database access — no ``import_jobs`` row is opened and nothing is written."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Outcome:
    """What a mode hook returns; the base wraps it into a :class:`MigrationResult`."""

    counts: dict = field(default_factory=dict)
    exceptions: list[dict] = field(default_factory=list)
    reconciliation: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class MigrationResult:
    source_system: str
    mode: str
    status: str
    started_at: str
    completed_at: str
    counts: dict
    exceptions: list[dict]
    reconciliation: list[dict]
    notes: list[str]
    run_dir: str
    import_job_id: int | None


# --- import_jobs integration (write modes only) ------------------------------

def open_import_job(source_system: str, source_file: str, source_hash: str | None = None) -> int:
    """Insert a running ``import_jobs`` row and return its id. APPLY/ROLLBACK only."""
    from sqlalchemy import insert

    from app.db import engine, metadata
    import_jobs = metadata.tables["import_jobs"]
    with engine.begin() as c:
        return c.execute(insert(import_jobs).values(
            source_system=source_system, source_file=source_file, file_hash=source_hash,
            status="running", started_at=datetime.now(UTC)).returning(import_jobs.c.id)).scalar_one()


def complete_import_job(job_id: int, counts: dict, status: str = "completed") -> None:
    from sqlalchemy import update

    from app.db import engine, metadata
    import_jobs = metadata.tables["import_jobs"]
    with engine.begin() as c:
        c.execute(update(import_jobs).where(import_jobs.c.id == job_id).values(
            status=status, completed_at=datetime.now(UTC),
            rows_read=int(counts.get("rows_read", 0)), rows_inserted=int(counts.get("rows_inserted", 0)),
            rows_updated=int(counts.get("rows_updated", 0)), rows_skipped=int(counts.get("rows_skipped", 0))))


def fail_import_job(job_id: int, error_message: str) -> None:
    from sqlalchemy import update

    from app.db import engine, metadata
    import_jobs = metadata.tables["import_jobs"]
    with engine.begin() as c:
        c.execute(update(import_jobs).where(import_jobs.c.id == job_id).values(
            status="failed", completed_at=datetime.now(UTC), error_message=error_message[:2000]))


# --- artifact writing --------------------------------------------------------

def _write_csv(path: Path, rows: list[dict]) -> None:
    """Write ``rows`` as CSV using the union of keys (stable order). Always writes a file (header only
    when empty) so the artifact set is complete for every run."""
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields or ["(none)"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


class MigrationJob:
    """Base for every source migration. Subclasses set :attr:`source_system` and override the mode
    hooks they support (``_inventory`` / ``_preview`` / ``_apply`` / ``_reconcile`` / ``_rollback``);
    unimplemented hooks raise ``NotImplementedError`` so an unsupported mode fails loudly."""

    source_system: str = "unknown"
    #: Modes this job supports THIS phase. Fail-closed: anything not listed is refused up front
    #: (before any database access). Subclasses must declare what they actually implement/enable.
    supported_modes: frozenset[Mode] = frozenset()

    def __init__(self, config: MigrationConfig | None = None):
        self.config = config or MigrationConfig.from_env()

    # -- mode hooks (subclasses override the ones they support) ---------------
    def _inventory(self, **opts) -> Outcome:
        raise NotImplementedError(f"{self.source_system}: inventory not implemented")

    def _preview(self, **opts) -> Outcome:
        raise NotImplementedError(f"{self.source_system}: preview not implemented")

    def _apply(self, **opts) -> Outcome:
        raise NotImplementedError(f"{self.source_system}: apply not implemented")

    def _reconcile(self, **opts) -> Outcome:
        raise NotImplementedError(f"{self.source_system}: reconcile not implemented")

    def _rollback(self, **opts) -> Outcome:
        raise NotImplementedError(f"{self.source_system}: rollback not implemented")

    # -- run directory --------------------------------------------------------
    def run_dir(self, mode: Mode, stamp: str) -> Path:
        d = self.config.migration_root / "reports" / self.source_system.lower().replace(" ", "_") / mode.value / stamp
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- orchestration --------------------------------------------------------
    def run(self, mode: Mode, **opts) -> MigrationResult:
        """Dispatch to the mode hook, manage the ``import_jobs`` row (write modes only), and always
        emit the four artifacts. Read-only modes never touch the database beyond SELECTs in the hook."""
        mode = Mode(mode)
        started = _now_iso()
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        run_dir = self.run_dir(mode, stamp)
        # Fail-closed: an unsupported / disabled mode is refused BEFORE any database access — no
        # import_jobs row is opened and no table is touched. A refusal artifact set is still written.
        if mode not in self.supported_modes:
            outcome = Outcome(notes=[
                f"{self.source_system}: mode '{mode.value}' is not enabled in this phase — refused "
                "before any database access (no import_jobs row, no data written)."])
            result = self._finalize(mode, "refused", started, _now_iso(), run_dir, None, outcome, opts)
            self._write_artifacts(result, run_dir)
            raise ModeNotSupported(
                f"{self.source_system}: '{mode.value}' mode is disabled in this phase — "
                "no import_jobs row was created and no database changes were made.")
        job_id: int | None = None
        status = "completed"
        try:
            if mode in WRITE_MODES and mode is Mode.APPLY:
                job_id = open_import_job(self.source_system, str(opts.get("source_file", "")),
                                         opts.get("source_hash"))
            hook = {Mode.INVENTORY: self._inventory, Mode.PREVIEW: self._preview,
                    Mode.APPLY: self._apply, Mode.RECONCILE: self._reconcile,
                    Mode.ROLLBACK: self._rollback}[mode]
            outcome = hook(job_id=job_id, **opts) if mode in WRITE_MODES else hook(**opts)
        except Exception as exc:
            status = "failed"
            outcome = Outcome(notes=[f"ERROR: {exc}"])
            completed = _now_iso()
            result = self._finalize(mode, status, started, completed, run_dir, job_id, outcome, opts)
            if job_id is not None:
                fail_import_job(job_id, str(exc))
            self._write_artifacts(result, run_dir)
            raise
        completed = _now_iso()
        result = self._finalize(mode, status, started, completed, run_dir, job_id, outcome, opts)
        if job_id is not None:
            complete_import_job(job_id, outcome.counts)
        self._write_artifacts(result, run_dir)
        return result

    def _finalize(self, mode, status, started, completed, run_dir, job_id, outcome, opts) -> MigrationResult:
        return MigrationResult(
            source_system=self.source_system, mode=mode.value, status=status,
            started_at=started, completed_at=completed, counts=outcome.counts,
            exceptions=outcome.exceptions, reconciliation=outcome.reconciliation,
            notes=outcome.notes, run_dir=str(run_dir), import_job_id=job_id)

    def _write_artifacts(self, result: MigrationResult, run_dir: Path) -> None:
        manifest = {
            "source_system": result.source_system, "mode": result.mode, "status": result.status,
            "started_at": result.started_at, "completed_at": result.completed_at,
            "counts": result.counts, "exception_count": len(result.exceptions),
            "import_job_id": result.import_job_id, "notes": result.notes,
            "provenance": {"source_system": result.source_system,
                           "config": {"migration_root": str(self.config.migration_root)}},
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        _write_csv(run_dir / "reconciliation.csv", result.reconciliation)
        _write_csv(run_dir / "exceptions.csv", result.exceptions)
        lines = [
            f"{result.source_system} — {result.mode} — {result.status}",
            f"started {result.started_at}  completed {result.completed_at}",
            f"run dir: {run_dir}",
            f"import_job_id: {result.import_job_id}",
            "",
            "counts:",
            *[f"  {k}: {v}" for k, v in result.counts.items()],
            f"exceptions: {len(result.exceptions)}",
        ]
        if result.notes:
            lines += ["", "notes:", *[f"  - {n}" for n in result.notes]]
        (run_dir / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
