"""E1.8 / F1.5 — Workflow Template Registry acceptance tests.

Covers registration, lookup, version selection, serialization/deserialization,
compatibility validation, immutable published versions, and the 18-SOP mapping
(cross-checked against the frozen register).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.platform import SCHEMA_VERSION
from app.platform.workflow_registry import (
    DRAFT,
    PUBLISHED,
    ImmutableTemplateError,
    IncompatibleTemplateError,
    UnknownTemplateError,
    WorkflowTemplate,
    WorkflowTemplateRegistry,
    build_default_registry,
    default_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _template(template_id="T1", version=1, status=DRAFT, **kw):
    return WorkflowTemplate(
        template_id=template_id, name=f"Name {template_id}", category="ops",
        version=version, status=status, **kw,
    )


# --- registration / lookup ---------------------------------------------------

def test_register_and_lookup():
    reg = WorkflowTemplateRegistry()
    reg.register(_template("T1"))
    assert "T1" in reg
    assert reg.get("T1").template_id == "T1"
    assert reg.latest("T1").version == 1
    with pytest.raises(UnknownTemplateError):
        reg.get("nope")


def test_validation_rejects_bad_template():
    reg = WorkflowTemplateRegistry()
    with pytest.raises(Exception):
        reg.register(WorkflowTemplate(template_id="", name="x", category="c"))
    with pytest.raises(Exception):
        reg.register(WorkflowTemplate(template_id="x", name="x", category="c", version=0))
    with pytest.raises(Exception):
        reg.register(WorkflowTemplate(template_id="x", name="x", category="c", status="bogus"))


# --- version selection -------------------------------------------------------

def test_version_selection_is_additive():
    reg = WorkflowTemplateRegistry()
    reg.register(_template("T1", version=1))
    reg.register(_template("T1", version=2, description="v2"))
    assert reg.versions("T1") == [1, 2]
    assert reg.latest("T1").version == 2                 # latest
    assert reg.get("T1", 1).version == 1                 # specific version
    assert reg.get("T1").description == "v2"


# --- serialization / deserialization -----------------------------------------

def test_dict_and_json_roundtrip():
    t = _template("T9", required_event_types=["X"], supported_schema_versions=[SCHEMA_VERSION])
    assert WorkflowTemplate.from_dict(t.to_dict()) == t
    assert WorkflowTemplate.from_json(t.to_json()) == t


def test_from_dict_ignores_unknown_fields():
    data = _template("T3").to_dict()
    data["not_a_field"] = "ignore me"
    assert WorkflowTemplate.from_dict(data).template_id == "T3"


# --- compatibility -----------------------------------------------------------

def test_compatibility_validation():
    reg = WorkflowTemplateRegistry()
    ok = _template("T1", supported_schema_versions=[SCHEMA_VERSION])
    reg.validate_compatibility(ok)  # no raise
    assert ok.supports_schema_version(SCHEMA_VERSION)

    bad = _template("T2", supported_schema_versions=[SCHEMA_VERSION + 99])
    with pytest.raises(IncompatibleTemplateError):
        reg.validate_compatibility(bad)


# --- immutability ------------------------------------------------------------

def test_published_versions_are_immutable():
    reg = WorkflowTemplateRegistry()
    reg.register(_template("T1", version=1))
    published = reg.publish("T1", 1)
    assert published.status == PUBLISHED

    # Re-registering the same published version with different content is rejected.
    with pytest.raises(ImmutableTemplateError):
        reg.register(_template("T1", version=1, status=PUBLISHED, description="changed"))

    # A new version is additive and allowed.
    reg.register(_template("T1", version=2, description="v2"))
    assert reg.versions("T1") == [1, 2]
    assert reg.latest_published("T1").version == 1


def test_draft_is_mutable_until_published():
    reg = WorkflowTemplateRegistry()
    reg.register(_template("T1", version=1, description="a"))
    reg.register(_template("T1", version=1, description="b"))  # draft replace allowed
    assert reg.get("T1", 1).description == "b"


# --- 18-SOP mapping ----------------------------------------------------------

def test_default_registry_has_18_sop_templates():
    reg = build_default_registry()
    assert len(reg) == 18
    ids = {t.template_id for t in reg.list_templates()}
    assert {f"TAXOPS-SOP-0{i}" for i in range(1, 9)} <= ids
    assert {f"WLTH-SOP-{i:02d}" for i in range(1, 11)} <= ids
    for t in reg.list_templates():
        assert t.status == DRAFT              # SOPs are needs_review, not published
        assert t.version == 1
        assert t.supported_schema_versions == [SCHEMA_VERSION]
        assert t.category in {"tax-operations", "wealth-operations"}


def test_default_registry_is_singleton():
    assert default_registry() is default_registry()


def test_seed_matches_register():
    """Drift guard: the runtime seed must match the frozen SOP catalog (pages.yml)."""
    import yaml

    data = yaml.safe_load((REPO_ROOT / "docs" / "registers" / "pages.yml").read_text())
    rows = data if isinstance(data, list) else data.get("pages", data)
    sop = {
        r["page_id"]: r
        for r in rows
        if "operations-manual" in str(r.get("repository_path", ""))
        and r.get("doc_type") == "SOP"
        and r.get("canonical_source") == "git"
    }
    reg = build_default_registry()
    assert set(sop) == {t.template_id for t in reg.list_templates()}
    for t in reg.list_templates():
        row = sop[t.template_id]
        assert t.name == row["title"]
        assert t.metadata["repository_path"] == row["repository_path"]
        assert t.metadata["area"] == row["area"]
