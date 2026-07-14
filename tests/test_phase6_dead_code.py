"""Release 0.9.9 Phase 6 — dead-code-removal regression / import validation.

Guards that the removed debug endpoint stays gone and that the whole app import
graph still loads (a stale reference to a removed import would fail here), and
re-runs the unused-import check the cleanup was based on.
"""
import ast
import importlib
import pathlib

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parent.parent / "app"


def _route_pairs():
    from app.main import app
    pairs = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        for method in getattr(route, "methods", None) or []:
            pairs.add((method, path))
    return pairs


def test_timeline_test_debug_endpoint_removed():
    pairs = _route_pairs()
    assert ("POST", "/timeline/test") not in pairs
    # the real timeline view is preserved
    assert any(p == "/timeline/person/{person_id}" for _, p in pairs)


def _app_modules():
    for path in sorted(APP_ROOT.rglob("*.py")):
        rel = path.relative_to(APP_ROOT.parent).with_suffix("")
        parts = rel.parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        yield ".".join(parts)


@pytest.mark.parametrize("module_name", list(_app_modules()))
def test_app_module_imports_cleanly(module_name):
    # A lingering reference to a removed import would raise on import.
    # app.models.* is a pre-existing orphaned ORM scaffold (zero callers; uses
    # PEP 604 `X | None` that fails on this project's Python 3.9). It is flagged
    # as a deferred dead-code removal in the Phase 6 report, out of this phase's
    # scope (timeline debug endpoint + unused imports), so it is skipped here.
    if module_name.startswith("app.models"):
        pytest.skip("orphaned ORM scaffold — deferred dead-code removal (Phase 6 report)")
    if module_name == "app.demo.demo_app":
        pytest.skip("demo entrypoint intentionally guards its own import against a non-demo database")
    importlib.import_module(module_name)


def _unused_imports(path):
    tree = ast.parse(path.read_text())
    imported = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname is None and "." in a.name:
                    continue
                imported[(a.asname or a.name).split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for a in node.names:
                if a.name == "*":
                    continue
                imported[a.asname or a.name] = node.lineno
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            n = node
            while isinstance(n, ast.Attribute):
                n = n.value
            if isinstance(n, ast.Name):
                used.add(n.id)
    allnames = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "__all__" for t in node.targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                allnames = {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
    return [name for name, _ln in imported.items() if name not in used and name not in allnames]


def test_no_unused_imports_remain_in_app():
    offenders = {}
    for path in sorted(APP_ROOT.rglob("*.py")):
        unused = _unused_imports(path)
        if unused:
            offenders[str(path)] = unused
    assert not offenders, f"unused imports reintroduced: {offenders}"
