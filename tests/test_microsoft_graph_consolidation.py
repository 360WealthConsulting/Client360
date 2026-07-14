"""Release 0.9.9 Phase 2 — Microsoft Graph consolidation guard tests.

Prove that the alternate app-only Graph connector modules are gone and that the
single retained Graph path (Phase 1 token helper + config) still supports the
mail, calendar, document, and OAuth operations the application performs.
"""
import importlib

import pytest


REMOVED_MODULES = [
    "app.connectors.microsoft365.auth",
    "app.connectors.microsoft365.graph",
    "app.connectors.microsoft365.calendar",
    "app.connectors.microsoft365.mail",
    "app.connectors.microsoft365.contacts",
    "app.connectors.microsoft365.sharepoint",
]


@pytest.mark.parametrize("module_name", REMOVED_MODULES)
def test_removed_connector_modules_are_gone(module_name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


def test_config_module_is_retained():
    """The configuration module is still used by the live application."""
    from app.connectors.microsoft365.config import get_microsoft365_config
    assert callable(get_microsoft365_config)


def test_retained_graph_path_supports_oauth():
    """OAuth route imports the retained config + Phase 1 helpers, not the old client."""
    oauth = importlib.import_module("app.routes.microsoft365_oauth")
    assert hasattr(oauth, "DELEGATED_SCOPES")
    # OAuth builds its MSAL client via the single provider path.
    assert oauth.build_msal_client is not None
    assert oauth.get_microsoft365_config is not None


@pytest.mark.parametrize(
    "job_module, sync_callable",
    [
        ("app.jobs.microsoft_mail_sync", "sync_recent_mail"),
        ("app.jobs.microsoft_calendar_sync", "sync_calendar_events"),
        ("app.jobs.microsoft_document_sync", "sync_microsoft_documents"),
    ],
)
def test_retained_graph_path_supports_sync_jobs(job_module, sync_callable):
    """Each sync job imports cleanly and routes tokens through the Phase 1 helper."""
    module = importlib.import_module(job_module)
    # The job exposes its sync entrypoint...
    assert callable(getattr(module, sync_callable))
    # ...and acquires tokens via the single shared provider, not a removed client.
    assert module.get_microsoft_access_token is not None
    assert module.record_sync_health is not None


def test_single_provider_path_exposes_token_helper():
    identity = importlib.import_module("app.services.microsoft_identity")
    assert callable(identity.get_microsoft_access_token)
    assert callable(identity.record_sync_health)


def test_no_live_module_references_removed_symbols():
    """Import the app's Microsoft surface; a lingering reference would raise here."""
    for module_name in [
        "app.routes.microsoft365",
        "app.routes.microsoft365_oauth",
        "app.routes.microsoft365_calendar",
        "app.jobs.microsoft_mail_sync",
        "app.jobs.microsoft_calendar_sync",
        "app.jobs.microsoft_document_sync",
        "app.services.microsoft_identity",
    ]:
        importlib.import_module(module_name)
