"""Single configurable data-root resolution for 360Plus persistent storage.

One OPTIONAL base env var — ``CLIENT360_DATA_ROOT`` — lets an operator relocate the entire persistent
corpus (canonical local-copy documents + SharePoint staging) to a dedicated data volume with a single
setting, instead of setting each per-source ``*_DOCUMENT_ROOT`` / staging variable individually. This is
what resolves the audit finding that the importers (default ``C:\\Client360\\Data\\Documents``) and the
relocation repository (``D:\\Client360Data``) disagreed on where canonical bytes live: point them all at one
base.

Precedence, per root (first non-empty wins):
    1. the existing PER-SOURCE env var  (unchanged — still wins, nothing renamed),
    2. ``CLIENT360_DATA_ROOT``-derived default  (only when the base is set),
    3. the LEGACY literal default  (``C:\\Client360\\…`` / ``D:\\Client360Data``) — byte-for-byte identical
       to today's behavior when the base is unset.

Nothing here renames an env var, a DB ``storage_provider`` value, or any technical identifier. The single
per-document source of truth remains ``documents.storage_uri`` in the database; this module only makes the
DEFAULT roots for NEW bytes agree on one location after cutover.
"""
from __future__ import annotations

import os

DATA_ROOT_ENV = "CLIENT360_DATA_ROOT"

_LEGACY_DOCUMENTS = r"C:\Client360\Data\Documents"
_LEGACY_SP_STAGING = r"C:\Client360\Data\Documents\SharePoint\_staging"
_LEGACY_REPOSITORY = r"D:\Client360Data"


def data_root():
    """The configured persistent-data base, or None (then the legacy ``C:\\Client360`` defaults apply)."""
    v = os.getenv(DATA_ROOT_ENV)
    return v.rstrip("\\/") if v else None


def _join(*parts):
    return "\\".join(str(p).strip("\\/") for p in parts if p)


def document_root(source, env_var):
    """Canonical local-copy destination root for a source (``TaxDome`` / ``Drake`` / ``SharePoint``).

    Per-source ``env_var`` wins; else ``<CLIENT360_DATA_ROOT>\\Documents\\<source>``; else the legacy
    ``C:\\Client360\\Data\\Documents\\<source>`` (identical to today when no base is set)."""
    specific = os.getenv(env_var)
    if specific:
        return specific
    base = data_root()
    if base:
        return _join(base, "Documents", source)
    return _join(_LEGACY_DOCUMENTS, source)


def sharepoint_staging_root():
    """SharePoint staging root. The existing (overloaded) vars still win for compatibility; else
    ``<CLIENT360_DATA_ROOT>\\Staging\\SharePoint``; else the legacy ``…\\SharePoint\\_staging`` default."""
    specific = (os.getenv("CLIENT360_SHAREPOINT_SOURCE_ROOT")
                or os.getenv("CLIENT360_SHAREPOINT_DOCUMENT_ROOT"))
    if specific:
        return specific
    base = data_root()
    if base:
        return _join(base, "Staging", "SharePoint")
    return _LEGACY_SP_STAGING


def repository_root():
    """Curated relocation-repository root. ``CLIENT360_MIGRATION_DEST_ROOT`` wins; else
    ``<CLIENT360_DATA_ROOT>\\Repository``; else the legacy ``D:\\Client360Data``. (Advisory — the migration
    framework reads its own config; this mirrors the resolution so the runbook can rely on one base.)"""
    specific = os.getenv("CLIENT360_MIGRATION_DEST_ROOT")
    if specific:
        return specific
    base = data_root()
    if base:
        return _join(base, "Repository")
    return _LEGACY_REPOSITORY
