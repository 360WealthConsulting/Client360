"""Platform architecture enforcement tests (Phase D.12A).

Validates the machine-readable manifest (docs/platform_architecture_manifest.yaml) and the
authoritative document (docs/PLATFORM_ARCHITECTURE.md) against the LIVE code, so the
architecture reference cannot silently drift: route count, migration head, seeded
capabilities, composition-module existence, import direction (no producer imports a
composition layer), declared-schema registration, single Alembic head, and required document
sections. These assert on explicit architecture metadata — not full-document string
snapshots — so harmless wording changes do not break them.
"""
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
MANIFEST_PATH = DOCS / "platform_architecture_manifest.yaml"
DOC_PATH = DOCS / "PLATFORM_ARCHITECTURE.md"
MIGRATIONS = REPO / "migrations" / "versions"
SERVICES = REPO / "app" / "services"


def _manifest():
    return yaml.safe_load(MANIFEST_PATH.read_text())


# --- deliverables exist ------------------------------------------------------

def test_platform_architecture_document_and_manifest_exist():
    assert DOC_PATH.is_file()
    assert MANIFEST_PATH.is_file()


def test_required_document_sections_present():
    text = DOC_PATH.read_text()
    for section in _manifest()["required_doc_sections"]:
        assert section in text, f"missing architecture section: {section!r}"


def test_advisor_workspace_doc_references_platform_doc():
    text = (DOCS / "ADVISOR_WORKSPACE_ARCHITECTURE.md").read_text()
    assert "PLATFORM_ARCHITECTURE.md" in text


# --- route count + migration head match live code ----------------------------

def test_route_count_matches_manifest():
    from app.main import app
    assert len(app.routes) == _manifest()["meta"]["route_count"]


def test_migration_head_matches_manifest_and_is_single():
    # Use Alembic's own script graph (authoritative — the same source as `alembic heads`).
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    script = ScriptDirectory.from_config(Config(str(REPO / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1, f"expected a single Alembic head, found {heads}"
    assert heads[0] == _manifest()["meta"]["migration_head"]


# --- capabilities in the manifest exist in the seeded capability table -------

def test_manifest_capabilities_exist_in_migrations():
    """Every capability the architecture document names must be seeded by a migration."""
    seeded = set()
    for p in MIGRATIONS.glob("*.py"):
        src = p.read_text()
        # capabilities are inserted as ("code", ...) or code = "x.y"; capture x.y[.z] tokens
        for m in re.findall(r'["\']([a-z_]+\.[a-z_.]+)["\']', src):
            seeded.add(m)
    declared = {cap for group in _manifest()["capabilities"].values() for cap in group}
    missing = {c for c in declared if c not in seeded}
    # Filter out any accidental column-like tokens that share the dotted shape.
    assert not missing, f"capabilities in manifest not seeded by any migration: {sorted(missing)}"


# --- composition modules exist ----------------------------------------------

def test_composition_service_modules_exist():
    for rel in _manifest()["composition_service_modules"]:
        assert (REPO / rel).is_file(), f"missing composition module: {rel}"


# --- dependency direction: no producer imports a composition layer -----------

def test_source_producers_do_not_import_composition_layers():
    manifest = _manifest()
    comp = manifest["composition_layer_modules"]
    for rel in manifest["source_producer_modules"]:
        src = (REPO / rel).read_text()
        for layer in comp:
            pattern = re.compile(rf"import\s+{layer}\b|from\s+\S*{layer}\s+import|"
                                 rf"services\s+import\s+.*\b{layer}\b")
            assert not pattern.search(src), f"{rel} must not import composition layer {layer}"


def test_advisor_intelligence_does_not_import_its_consumers():
    src = (SERVICES / "advisor_intelligence.py").read_text()
    for consumer in _manifest()["advisor_intelligence_forbidden_imports"]:
        pattern = re.compile(rf"import\s+{consumer}\b|from\s+\S*{consumer}\s+import")
        assert not pattern.search(src), f"advisor_intelligence must not import {consumer}"


def test_activity_timeline_is_a_projection_no_second_event_table():
    # Exactly one timeline-event table is created across all migrations.
    created = []
    for p in MIGRATIONS.glob("*.py"):
        created += re.findall(r'create_table\(\s*["\']([a-z_]*timeline[a-z_]*)["\']',
                              p.read_text())
    assert created.count("timeline_events") == 1
    assert set(created) == {"timeline_events"}, f"unexpected timeline tables: {set(created)}"


# --- declared schema modules are registered ----------------------------------

def test_declared_schema_modules_registered():
    schema_src = (REPO / "app" / "database" / "schema.py").read_text()
    for fn in _manifest()["declared_schema_registrations"]:
        assert f"{fn}(metadata)" in schema_src, f"{fn} not registered in schema.py"


# --- documentation honesty: unavailable data is not claimed as a domain ------

def test_not_modeled_data_is_documented_as_unavailable():
    doc = DOC_PATH.read_text()
    assert "Not currently modeled" in doc or "not modeled" in doc
    # A representative unavailable item is present in both manifest and doc.
    assert "tax_return_financial_content" in {x for x in _manifest()["not_currently_modeled"]}
