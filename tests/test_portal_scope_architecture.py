"""Portal scope callers must consciously choose base-identity or permission-enforced feature scope.

``portal_scope(account_id, permission=None)`` let six callers silently omit the permission, so the
resolver was never asked to enforce the grant — a client whose grant set ``messages: False`` still listed
threads. The resolver itself was always correct; the omissions were caller-level.

``portal_scope`` now REQUIRES a permission and ``portal_base_scope`` names identity-only resolution
explicitly. This module is the structural guard: every call site is classified exactly once, by AST, so a
new caller cannot appear unclassified and a feature caller cannot quietly use the base helper.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

APP = pathlib.Path("app")

#: Callers allowed to use identity-only scope, each because an INDEPENDENT authorization layer decides
#: access afterwards. Adding to this set is a deliberate governance decision, not a convenience.
BASE_SCOPE_CALLERS = {
    ("app/portal/service.py", "dashboard"),
    #   identity bootstrap; every panel re-resolves under its own grant permission
    ("app/routes/portal_admin.py", "portal_admin_preview"),
    #   staff entitlement preview must report the WHOLE grant scope
    ("app/services/features/service.py", "client_can"),
    #   relationship resolution INSIDE the feature decision — a permission here would be circular
    ("app/routes/payroll.py", "portal_payroll"),
    #   followed by client_can(payroll FEATURE_KEY, organization_id=...)
    ("app/services/billing/service.py", "client_billing_subjects"),
    #   followed by client_can("billing" / "invoice_view") in each billing service
    ("app/services/communications/engagement/service.py", "portal_engagement"),
    #   followed by client_can("client_timeline")
}


def _calls(tree, name):
    """Yield the enclosing function name for every call to ``name`` in ``tree``."""
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def enclosing(node):
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur.name
            cur = parents.get(cur)
        return "<module>"

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name:
            yield node, enclosing(node)


def _scan(helper):
    out = []
    for path in APP.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                                    # pragma: no cover - defensive
            continue
        for node, fn in _calls(tree, helper):
            if fn in ("portal_scope", "portal_base_scope", "_resolve_scope"):
                continue                                       # the definitions themselves
            out.append((str(path).replace("\\", "/"), fn, node))
    return out


def test_every_base_scope_caller_is_explicitly_classified():
    found = {(p, fn) for p, fn, _n in _scan("portal_base_scope")}
    unclassified = found - BASE_SCOPE_CALLERS
    assert unclassified == set(), (
        f"unclassified portal_base_scope caller(s): {sorted(unclassified)}. Identity-only scope is only "
        f"acceptable where an independent authorization layer decides access — classify it here with the "
        f"reason, or use portal_scope(..., permission=...).")


def test_no_classified_base_caller_disappeared():
    """If a caller is removed, update the inventory deliberately rather than leaving it stale."""
    found = {(p, fn) for p, fn, _n in _scan("portal_base_scope")}
    missing = BASE_SCOPE_CALLERS - found
    assert missing == set(), f"classified base-scope caller(s) no longer present: {sorted(missing)}"


def test_every_portal_scope_call_passes_an_explicit_permission():
    offenders = [(p, fn) for p, fn, node in _scan("portal_scope")
                 if not any(kw.arg == "permission" for kw in node.keywords)]
    assert offenders == [], (
        f"portal_scope called without an explicit permission: {offenders}. This is the omission class "
        f"the split exists to prevent.")


def test_no_caller_is_in_both_classes():
    base = {(p, fn) for p, fn, _n in _scan("portal_base_scope")}
    feature = {(p, fn) for p, fn, _n in _scan("portal_scope")}
    both = base & feature
    assert both == set(), f"caller(s) use BOTH scope helpers: {sorted(both)}"


def test_portal_scope_signature_requires_permission():
    import inspect

    from app.portal.service import portal_base_scope, portal_scope
    sig = inspect.signature(portal_scope)
    perm = sig.parameters["permission"]
    assert perm.kind is inspect.Parameter.KEYWORD_ONLY
    assert perm.default is inspect.Parameter.empty, "permission must have NO default"
    assert "permission" not in inspect.signature(portal_base_scope).parameters


def test_portal_scope_rejects_an_empty_permission():
    from app.portal.service import portal_scope
    with pytest.raises(TypeError):
        portal_scope(1)                                        # missing keyword entirely
    with pytest.raises(ValueError):
        portal_scope(1, permission="")                         # empty string is not a permission


def test_feature_scope_callers_are_all_grant_backed():
    """Every permission passed must be a real grant permission, not an invented one."""
    known = {"documents", "messages", "tasks", "financial", "benefits", "census", "insurance"}
    used = set()
    for _p, _fn, node in _scan("portal_scope"):
        for kw in node.keywords:
            if kw.arg == "permission" and isinstance(kw.value, ast.Constant):
                used.add(kw.value.value)
    assert used, "no portal_scope permissions found — the scan is broken"
    assert used <= known, f"unknown grant permission(s) in use: {sorted(used - known)}"
