"""Enterprise Runtime Configuration Engine (Phase D.28) — runtime evaluation over D.27 metadata.

D.28 owns runtime EVALUATION only. The Phase D.27 Enterprise Configuration domain remains the sole
owner and mutator of configuration METADATA; this engine reads that metadata (never writes it) and
computes the effective, deterministic runtime configuration + active features + edition/license,
serving them from an in-process cache and immutable snapshots.

Strict separation (ADR-033): the runtime engine never edits metadata; the metadata domain never
performs runtime evaluation. Every component consuming configuration at runtime does so through this
engine — no direct metadata-table reads elsewhere. Configuration failures never prevent safe
application startup (hydration is self-guarded and falls back to defaults / the last-known snapshot).
"""
