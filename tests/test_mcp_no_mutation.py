"""Structural guarantees: the MCP surface is read-only, and cannot quietly stop being read-only.

These are not behaviour tests — they are constraints on the CODE. A future contributor adding a
``create_client`` tool, importing a mutating service into the adapter, or exposing raw SQL should
fail here, in a test whose name says why, rather than shipping a write path into a firm's client
records behind an assistant.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.mcp import protocol, tools
from app.mcp.scopes import ALL_SCOPES
from app.mcp.tools import TOOLS

_MCP_PACKAGE = Path(inspect.getfile(tools)).parent

#: The complete, frozen Phase 1 surface. Adding a tool means changing this list deliberately.
EXPECTED_TOOLS = {
    "search_clients", "get_client", "list_client_documents",
    "get_document", "search_documents", "get_document_text",
}

#: Verbs that name a state change. A tool called any of these is a write tool whatever it does.
_MUTATION_VERBS = ("create", "update", "delete", "insert", "write", "set_", "add_", "remove",
                   "archive", "approve", "upload", "send", "merge", "assign", "revoke", "restore")


def test_the_surface_is_exactly_the_six_phase_one_tools():
    assert set(TOOLS) == EXPECTED_TOOLS


def test_no_tool_name_suggests_a_mutation():
    offenders = [name for name in TOOLS if any(v in name.lower() for v in _MUTATION_VERBS)]
    assert offenders == [], f"mutating-sounding tool(s) on a read-only surface: {offenders}"


def test_every_tool_declares_a_known_scope_and_a_handler():
    for name, spec in TOOLS.items():
        assert spec["scope"] in ALL_SCOPES, f"{name} declares an unknown scope"
        assert callable(spec["handler"]), f"{name} has no handler"
        assert spec["description"].strip(), f"{name} has no description"


def test_every_tool_schema_is_closed():
    """``additionalProperties: false`` everywhere — an unknown argument is refused, not ignored."""
    for name, spec in TOOLS.items():
        schema = spec["schema"]
        assert schema["type"] == "object", name
        assert schema.get("additionalProperties") is False, f"{name} accepts unknown arguments"


def test_dispatch_reaches_nothing_outside_the_registry():
    """``tools/call`` has no path to a callable that is not a registered tool."""
    source = inspect.getsource(protocol.call_tool)
    assert "TOOLS.get(name)" in source
    assert "spec[\"handler\"]" in source


@pytest.mark.parametrize("forbidden", [
    "run_sql", "query", "execute_sql", "sql", "read_file", "write_file",
    "shell", "exec", "fetch_url", "http_get",
])
def test_no_generic_escape_hatch_tool_exists(forbidden):
    """No generic SQL, filesystem, shell or URL-fetch tool, by any of its usual names."""
    assert forbidden not in TOOLS


def _mcp_sources():
    return sorted(p for p in _MCP_PACKAGE.glob("*.py"))


def test_the_adapter_never_imports_a_mutating_primitive():
    """No subprocess, no filesystem writing, no outbound HTTP anywhere in app/mcp."""
    banned = {"subprocess", "shutil", "os.system", "requests", "httpx", "urllib.request",
              "socket", "pickle"}
    offenders = []
    for path in _mcp_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name in banned or name.split(".")[0] in banned:
                    offenders.append(f"{path.name}: {name}")
    assert offenders == [], f"forbidden import(s) in the MCP adapter: {offenders}"


def test_the_adapter_issues_no_write_statements():
    """No INSERT/UPDATE/DELETE construction in the tool, projection or protocol layers.

    ``tokens.py`` is exempt and explicitly listed: issuing, revoking and stamping ``last_used_at`` on
    a CREDENTIAL are writes to the MCP token store, never to client data. Nothing else may write.
    """
    exempt = {"tokens.py"}
    offenders = []
    for path in _mcp_sources():
        if path.name in exempt:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in (".insert()", ".update()", ".delete()", "engine.begin("):
            if marker in text:
                offenders.append(f"{path.name}: {marker}")
    assert offenders == [], f"write construct(s) on a read-only surface: {offenders}"


def test_token_writes_touch_only_the_token_table():
    """The one module allowed to write may write to ``mcp_access_tokens`` and nothing else."""
    from app.mcp import tokens as tokens_module
    text = Path(inspect.getfile(tokens_module)).read_text(encoding="utf-8")
    for marker in (".insert()", ".update()"):
        for line in text.splitlines():
            if marker in line:
                assert "mcp_access_tokens" in line, f"write to a non-token table: {line.strip()}"
    assert ".delete()" not in text, "tokens are revoked, never deleted — revocation is evidence"


def test_no_tool_imports_a_document_or_client_mutation_service():
    """The adapter must reach the read side of each service, never its write side."""
    text = Path(inspect.getfile(tools)).read_text(encoding="utf-8")
    for banned in ("create_document", "update_document", "soft_delete", "set_status",
                   "archive(", "approve(", "person_creation", "person_merge",
                   "document_merge", "save_person_document"):
        assert banned not in text, f"the MCP adapter references a mutating primitive: {banned}"
