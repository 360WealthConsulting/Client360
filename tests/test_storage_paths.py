"""storage_paths — single CLIENT360_DATA_ROOT base with legacy-default and per-source override precedence."""
import importlib

import pytest

from app.services import storage_paths as sp

_PER_SOURCE = {"TaxDome": "CLIENT360_TAXDOME_DOCUMENT_ROOT", "Drake": "CLIENT360_DRAKE_DOCUMENT_ROOT",
               "SharePoint": "CLIENT360_SHAREPOINT_DOCUMENT_ROOT"}
_ALL_VARS = ("CLIENT360_DATA_ROOT", *_PER_SOURCE.values(),
             "CLIENT360_SHAREPOINT_SOURCE_ROOT", "CLIENT360_MIGRATION_DEST_ROOT")


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for v in _ALL_VARS:
        monkeypatch.delenv(v, raising=False)
    yield


def test_document_root_legacy_default_unchanged_when_nothing_set():
    # This is the critical backward-compat guarantee: identical to today's literals.
    assert sp.document_root("TaxDome", _PER_SOURCE["TaxDome"]) == r"C:\Client360\Data\Documents\TaxDome"
    assert sp.document_root("Drake", _PER_SOURCE["Drake"]) == r"C:\Client360\Data\Documents\Drake"
    assert sp.document_root("SharePoint", _PER_SOURCE["SharePoint"]) == r"C:\Client360\Data\Documents\SharePoint"


def test_data_root_base_derives_all_document_roots(monkeypatch):
    monkeypatch.setenv("CLIENT360_DATA_ROOT", r"D:\360PlusData")
    assert sp.document_root("TaxDome", _PER_SOURCE["TaxDome"]) == r"D:\360PlusData\Documents\TaxDome"
    assert sp.document_root("Drake", _PER_SOURCE["Drake"]) == r"D:\360PlusData\Documents\Drake"
    assert sp.document_root("SharePoint", _PER_SOURCE["SharePoint"]) == r"D:\360PlusData\Documents\SharePoint"


def test_data_root_trailing_slash_is_normalized(monkeypatch):
    monkeypatch.setenv("CLIENT360_DATA_ROOT", "D:\\360PlusData\\")
    assert sp.document_root("Drake", _PER_SOURCE["Drake"]) == r"D:\360PlusData\Documents\Drake"


def test_per_source_var_wins_over_base(monkeypatch):
    monkeypatch.setenv("CLIENT360_DATA_ROOT", r"D:\360PlusData")
    monkeypatch.setenv("CLIENT360_TAXDOME_DOCUMENT_ROOT", r"E:\Custom\TaxDome")
    assert sp.document_root("TaxDome", _PER_SOURCE["TaxDome"]) == r"E:\Custom\TaxDome"   # override wins
    assert sp.document_root("Drake", _PER_SOURCE["Drake"]) == r"D:\360PlusData\Documents\Drake"  # base still


def test_sharepoint_staging_root_precedence(monkeypatch):
    assert sp.sharepoint_staging_root() == r"C:\Client360\Data\Documents\SharePoint\_staging"   # legacy
    monkeypatch.setenv("CLIENT360_DATA_ROOT", r"D:\360PlusData")
    assert sp.sharepoint_staging_root() == r"D:\360PlusData\Staging\SharePoint"                # base -> Staging
    monkeypatch.setenv("CLIENT360_SHAREPOINT_SOURCE_ROOT", r"E:\stage")
    assert sp.sharepoint_staging_root() == r"E:\stage"                                          # override wins


def test_repository_root_precedence(monkeypatch):
    assert sp.repository_root() == r"D:\Client360Data"                                          # legacy default
    monkeypatch.setenv("CLIENT360_DATA_ROOT", r"D:\360PlusData")
    assert sp.repository_root() == r"D:\360PlusData\Repository"
    monkeypatch.setenv("CLIENT360_MIGRATION_DEST_ROOT", r"D:\Explicit\Repo")
    assert sp.repository_root() == r"D:\Explicit\Repo"                                          # explicit wins


def test_importer_defaults_match_storage_paths_when_unset(monkeypatch):
    # Importer module constants (import-time) must equal the legacy literals when nothing is configured.
    for v in _ALL_VARS:
        monkeypatch.delenv(v, raising=False)
    for mod_name, source in (("app.importers.taxdome_drive", "TaxDome"),
                             ("app.importers.drake", "Drake"),
                             ("app.importers.sharepoint", "SharePoint")):
        mod = importlib.reload(importlib.import_module(mod_name))
        assert mod.DEFAULT_DESTINATION_ROOT == rf"C:\Client360\Data\Documents\{source}"


def test_ingestion_staging_root_uses_storage_paths(monkeypatch):
    from app.services import microsoft_ingestion as mi
    for v in _ALL_VARS:
        monkeypatch.delenv(v, raising=False)
    assert mi._staging_root() == r"C:\Client360\Data\Documents\SharePoint\_staging"             # legacy unchanged
    monkeypatch.setenv("CLIENT360_DATA_ROOT", r"D:\360PlusData")
    assert mi._staging_root() == r"D:\360PlusData\Staging\SharePoint"
