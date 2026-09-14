"""Safety-contract tests for the hash-pinned 56-row naming runner."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts import apply_naming_manifest_56 as runner


def _approval(document_id=1, name="2024 - W-2 - Example Person"):
    return {
        "document_id": document_id,
        "approved_display_name": name,
        "expected_current_display_name": None,
    }


def _owner(document_id=1, **overrides):
    row = {
        "document_id": document_id,
        "expected_current_display_name": None,
        "person_id": 42,
        "household_id": None,
        "organization_id": None,
    }
    row.update(overrides)
    return row


def _locked(document_id=1, **overrides):
    row = {
        "id": document_id,
        "display_name": None,
        "person_id": 42,
        "household_id": None,
        "organization_id": None,
    }
    row.update(overrides)
    return row


def test_authority_is_exactly_hash_pinned():
    assert runner.EXPECTED_MANIFEST_SHA == (
        "7e0a3e670ffeabcd6a94d4d4e668d4f786fcbbb2023877cee9088ed6b02a7d7d"
    )
    assert runner.EXPECTED_OWNER_SHA == (
        "91ff089c79fb47afb910cc8a24a667a201b2884ca6e2fa9e09b794e6e2540475"
    )
    assert runner.EXPECTED_PINNED_PAYLOAD == (
        "2d95638d960a20fb01ecd633b75d5f7912be8bf2b6fd90d347effd813181ceed"
    )
    assert runner.AUTHORIZATION_PHRASE == (
        "APPLY-CLIENT360-DISPLAY-NAMES-56-"
        "2d95638d960a20fb01ecd633b75d5f7912be8bf2b6fd90d347effd813181ceed"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--user-id", "7", "--apply"],
        ["--user-id", "7", "--apply", "--authorize", "wrong"],
        ["--user-id", "7", "--authorize", runner.AUTHORIZATION_PHRASE],
        ["--user-id", "0"],
    ],
)
def test_invalid_apply_contract_refuses_before_reading_authority(monkeypatch, argv):
    monkeypatch.setattr(
        runner,
        "_load_authority",
        lambda _root: pytest.fail("authority must not be read"),
    )
    with pytest.raises(runner.Refused):
        runner.main(argv)


def test_locked_rows_accept_exact_null_name_and_owner():
    approvals = {1: _approval()}
    owners = {1: _owner()}
    current = runner._check_locked_rows([_locked()], approvals, owners)
    assert current[1]["display_name"] is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"display_name": ""}, "display_name is no longer NULL"),
        ({"display_name": "old"}, "display_name is no longer NULL"),
        ({"person_id": 99}, "person_id changed"),
        ({"household_id": 99}, "household_id changed"),
        ({"organization_id": 99}, "organization_id changed"),
    ],
)
def test_locked_rows_refuse_any_reviewed_state_drift(change, message):
    with pytest.raises(runner.Refused, match=message):
        runner._check_locked_rows(
            [_locked(**change)],
            {1: _approval()},
            {1: _owner()},
        )


def test_locked_rows_refuse_missing_document():
    with pytest.raises(runner.Refused, match="missing documents"):
        runner._check_locked_rows([], {1: _approval()}, {1: _owner()})


def test_internal_owner_name_collision_refuses_case_insensitively():
    approvals = {
        1: _approval(1, "2024 - W-2 - Example Person"),
        2: _approval(2, "2024 - w-2 - example person"),
    }
    owners = {1: _owner(1), 2: _owner(2)}
    with pytest.raises(runner.Refused, match="duplicate a name for one owner"):
        runner._check_collisions([], approvals, owners)


def test_existing_owner_name_collision_refuses_case_insensitively():
    collision = _locked(
        99,
        display_name="2024 - w-2 - example person",
    )
    with pytest.raises(runner.Refused, match="already used by document 99"):
        runner._check_collisions(
            [collision],
            {1: _approval()},
            {1: _owner()},
        )


@pytest.mark.parametrize(
    ("preview", "message"),
    [
        ({"bucket": "REVIEW", "collision": False, "proposed_name": "approved"}, "not SAFE"),
        ({"bucket": "SAFE", "collision": True, "proposed_name": "approved"}, "resolver collision"),
        ({"bucket": "SAFE", "collision": False, "proposed_name": "changed"}, "proposed name changed"),
    ],
)
def test_live_preview_refuses_changed_evidence(monkeypatch, preview, message):
    row = {"document_id": 1, **preview}
    monkeypatch.setattr(runner, "build_preview", lambda: {"rows": [row]})
    with pytest.raises(runner.Refused, match=message):
        runner._check_live_preview({1: _approval(1, "approved")})


def test_source_contract_is_one_transaction_and_one_commit_path():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    main = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    calls = [node for node in ast.walk(main) if isinstance(node, ast.Call)]

    begin_calls = [
        call for call in calls
        if isinstance(call.func, ast.Attribute) and call.func.attr == "begin"
    ]
    commit_calls = [
        call for call in calls
        if isinstance(call.func, ast.Attribute) and call.func.attr == "commit"
    ]
    rollback_calls = [
        call for call in calls
        if isinstance(call.func, ast.Attribute) and call.func.attr == "rollback"
    ]
    assert len(begin_calls) == 1
    assert len(commit_calls) == 1
    assert len(rollback_calls) >= 2

    audit_calls = [
        call for call in calls
        if isinstance(call.func, ast.Name) and call.func.id == "write_audit_event"
    ]
    assert len(audit_calls) == 1
    conn_kw = next(kw for kw in audit_calls[0].keywords if kw.arg == "conn")
    assert isinstance(conn_kw.value, ast.Name)
    assert conn_kw.value.id == "connection"


def test_update_statement_changes_only_display_name():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    update_assignment = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "update_name"
            for target in node.targets
        )
    )
    values_calls = [
        node for node in ast.walk(update_assignment.value)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "values"
    ]
    assert len(values_calls) == 1
    assert [keyword.arg for keyword in values_calls[0].keywords] == ["display_name"]


def test_runner_contains_no_physical_file_mutation_api():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"rename", "replace", "unlink", "move", "copy", "rmtree"}
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called.isdisjoint(forbidden)
