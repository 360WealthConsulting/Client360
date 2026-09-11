"""Continuous document pipeline — a server-side service, not a script somebody runs.

WHAT IT IS
----------
A durable, resumable, parallel pipeline that discovers every new and changed document, drives it
through embedded-text extraction, OCR, classification and ownership resolution, and never stops. It
runs under the existing Client360 scheduler or as its own Windows service; it starts with the server,
picks up unfinished work after a restart, and needs no interactive session of any kind.

WHY IT EXISTS ALONGSIDE THE EXISTING SWEEPS
--------------------------------------------
``app/jobs/ocr_runner.py`` and ``document_pipeline.run_batch`` are SWEEPS — each run re-selects
candidates from the top of the corpus and works forward. That is the right shape for a one-off
migration and the wrong shape for a corpus that is continuously fed: a sweep cannot resume mid-corpus,
it re-reads work it already finished, it takes a ``limit`` that silently truncates, and it holds no
per-document claim. This package adds the durable state those sweeps do not have, and ORCHESTRATES
them rather than replacing them — extraction, OCR, classification, matching and the ownership rules
all still live in exactly one place each, and it is not here.

THE MODULES
-----------
``model``         stage/state/outcome vocabulary and tolerant table bindings
``queue``         the durable work queue: claim with lease, advance, retry, block
``discovery``     resumable, uncapped discovery of new and changed documents
``stages``        the four stage executors, each delegating to an existing service
``ownership``     Drake / TaxDome / SharePoint lanes, one review queue, never overwrite
``backpressure``  CPU, memory and database gates, plus deferral to the legacy OCR sweep
``worker``        the worker loop and thread pool
``metrics``       operational counters, stall detection, and the alert
``service``       the facade the scheduler, the CLI and the routes all call

SAFETY POSTURE
--------------
Off by default (``DOCUMENT_PIPELINE_ENABLED``), so merging this changes no runtime behaviour. It never
creates a person, household or organization; never moves or deletes a file; and writes ownership only
through ``households.resolve_document_ownership``, whose statement refuses to overwrite an existing
owner. It defers its OCR stage while one of the existing operational sweeps holds the corpus advisory
lock, so it cannot collide with a migration-scale OCR run.
"""
from app.services.document_pipeline_continuous.model import (  # noqa: F401
    LANES,
    OUTCOMES,
    STAGES,
    STATES,
    PipelineNotInstalled,
    installed,
)

__all__ = ["LANES", "OUTCOMES", "STAGES", "STATES", "PipelineNotInstalled", "installed"]
