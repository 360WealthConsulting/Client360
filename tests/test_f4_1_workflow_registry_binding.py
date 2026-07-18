"""F4.1 / Epic 4 — Workflow → platform registry binding acceptance tests (ADR-016).

Covers the platform adapter's registry-lookup abstraction, write-once instance↔
registry template binding (service + DB-trigger immutability), idempotency,
backward-compatible preservation of the existing engine (unbound legacy launches;
engine updates unaffected), and the additive/no-HTTP contract.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import engine, roles, user_roles, users, workflow_instances
from app.platform import default_registry
from app.platform.workflow_adapter import (
    RegistryBinding,
    bind_instance_template,
    get_binding,
    launch_from_registry,
    resolve_registry_template,
)
from app.platform.workflow_registry import UnknownTemplateError
from app.services.workflow_automation import launch_workflow, transition_workflow

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_TEMPLATE = "client_onboarding"  # a seeded, published DB workflow template


def _actor() -> int:
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"f41-{suffix}@example.com", normalized_email=f"f41-{suffix}@example.com",
            display_name="f41", auth_subject=f"f41-{suffix}", status="active",
        ).returning(users.c.id)).scalar_one()
        role_id = c.scalar(select(roles.c.id).where(roles.c.code == "administrator"))
        if role_id:
            c.execute(user_roles.insert().values(user_id=uid, role_id=role_id))
    return uid


def _reg_ids() -> tuple[str, str]:
    templates = default_registry().list_templates()
    return templates[0].template_id, templates[1].template_id


# --- registry lookup abstraction ---------------------------------------------

def test_resolve_registry_template_known_and_unknown():
    reg_id, _ = _reg_ids()
    template = resolve_registry_template(reg_id)
    assert template.template_id == reg_id and template.version >= 1
    with pytest.raises(UnknownTemplateError):
        resolve_registry_template("NOPE-" + uuid.uuid4().hex)


# --- launch + binding --------------------------------------------------------

def test_launch_from_registry_binds_instance():
    reg_id, _ = _reg_ids()
    version = resolve_registry_template(reg_id).version
    instance_id = launch_from_registry(reg_id, DB_TEMPLATE, actor_user_id=_actor(),
                                       idempotency_key=f"f41-{uuid.uuid4()}")
    binding = get_binding(instance_id)
    assert isinstance(binding, RegistryBinding)
    assert binding.template_id == reg_id and binding.version == version
    with engine.connect() as c:
        row = c.execute(select(workflow_instances.c.platform_template_ref,
                               workflow_instances.c.platform_template_version)
                        .where(workflow_instances.c.id == instance_id)).mappings().one()
        assert row["platform_template_ref"] == reg_id and row["platform_template_version"] == version


def test_binding_is_idempotent():
    reg_id, _ = _reg_ids()
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=_actor(), idempotency_key=f"f41-{uuid.uuid4()}")
    b1 = bind_instance_template(instance_id, reg_id)
    b2 = bind_instance_template(instance_id, reg_id)  # no-op, no error
    assert b1.template_id == b2.template_id == reg_id and b1.version == b2.version


# --- immutability (service + DB trigger) -------------------------------------

def test_binding_is_immutable_service_level():
    reg_id, other_id = _reg_ids()
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=_actor(), idempotency_key=f"f41-{uuid.uuid4()}")
    bind_instance_template(instance_id, reg_id)
    with pytest.raises(ValueError):
        bind_instance_template(instance_id, other_id)


def test_binding_is_immutable_db_trigger():
    reg_id, other_id = _reg_ids()
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=_actor(), idempotency_key=f"f41-{uuid.uuid4()}")
    bind_instance_template(instance_id, reg_id)
    with pytest.raises(Exception):  # noqa: B017 - workflow_instance_binding_immutable trigger
        with engine.begin() as c:
            c.execute(workflow_instances.update()
                      .where(workflow_instances.c.id == instance_id)
                      .values(platform_template_ref=other_id))
    assert get_binding(instance_id).template_id == reg_id  # unchanged


# --- backward compatibility (engine preserved) -------------------------------

def test_existing_launch_workflow_produces_unbound_instance():
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=_actor(), idempotency_key=f"f41-{uuid.uuid4()}")
    assert get_binding(instance_id) is None  # legacy path leaves binding NULL


def test_binding_does_not_block_engine_transitions():
    reg_id, _ = _reg_ids()
    actor = _actor()
    instance_id = launch_workflow(DB_TEMPLATE, actor_user_id=actor, idempotency_key=f"f41-{uuid.uuid4()}")
    bind_instance_template(instance_id, reg_id)
    # A normal engine update (pause) must still succeed on a bound instance.
    assert transition_workflow(instance_id, "pause", actor_user_id=actor) == "paused"
    assert get_binding(instance_id).template_id == reg_id  # binding preserved


# --- additive / no HTTP surface ----------------------------------------------

def test_adapter_has_no_http_surface_and_doc_present():
    source = (REPO_ROOT / "app" / "platform" / "workflow_adapter.py").read_text()
    assert "APIRouter" not in source and "@router" not in source
    assert "publish_event" not in source and "subscribe(" not in source  # no event-driven advancement (F4.3)
    assert (REPO_ROOT / "docs" / "WORKFLOW_EXECUTION.md").is_file()
