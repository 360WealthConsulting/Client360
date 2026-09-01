"""Messages capability gates the DOOR; record scope decides the CONTENTS. And the nav matches both.

Two invariants that are easy to lose when an authorization model changes:

  1. RECORD SCOPE is a separate layer. ``communications.message.read`` authorizes a staff member to use
     the Messages FEATURE. It does not decide WHICH client conversations they may see — that is
     ``communication_hub.thread_in_staff_scope()``, per row, unchanged by this repair. A holder of the
     Messages capability with no record scope sees nothing, and the capability grants no client-data
     authority anywhere else in the application.

  2. NAVIGATION must match the real gate. The sidebar previously advertised Messages on
     ``client.read``, which eleven roles hold, so six of them were shown a link and then refused. The
     nav now tracks the capability the route actually enforces, in both directions.
"""
from __future__ import annotations

import pathlib
import re
from types import SimpleNamespace

import pytest

from app.portal import communication_hub as hub

READ_CAP = "communications.message.read"
WRITE_CAP = "communications.message.write"

_BASE_HTML = pathlib.Path("app/templates/base.html")


def _principal(*capabilities):
    caps = set(capabilities)
    return SimpleNamespace(user_id=7, email="staff@example-demo.example",
                           capabilities=sorted(caps), can=lambda code: code in caps)


def _thread(person_id=1, household_id=None, organization_id=None):
    return {"person_id": person_id, "household_id": household_id,
            "organization_id": organization_id}


# --- 1. record scope is enforced independently of the capability -------------

def test_messages_capability_does_not_place_a_thread_in_scope(monkeypatch):
    """The capability opens the surface; it must not put a single extra row in reach."""
    monkeypatch.setattr(hub, "record_in_scope", lambda *a, **kw: False)
    monkeypatch.setattr(hub, "organization_in_scope", lambda *a, **kw: False)
    holder = _principal(READ_CAP, WRITE_CAP)
    assert hub.thread_in_staff_scope(holder, _thread()) is False


def test_in_scope_thread_is_serviceable(monkeypatch):
    monkeypatch.setattr(hub, "record_in_scope", lambda *a, **kw: True)
    monkeypatch.setattr(hub, "organization_in_scope", lambda *a, **kw: False)
    assert hub.thread_in_staff_scope(_principal(READ_CAP), _thread()) is True


def test_scope_is_evaluated_without_reference_to_capabilities(monkeypatch):
    """Scope decisions must not consult the principal's capability set — the two layers stay
    orthogonal, so a capability change can never silently widen which records are reachable."""
    seen = {}

    def _record_in_scope(principal, entity_type, entity_id, write=False):
        seen["write"] = write
        return True

    monkeypatch.setattr(hub, "record_in_scope", _record_in_scope)
    monkeypatch.setattr(hub, "organization_in_scope", lambda *a, **kw: False)

    # Same scope answer for a bare holder and for an over-privileged principal.
    bare = hub.thread_in_staff_scope(_principal(READ_CAP), _thread())
    loaded = hub.thread_in_staff_scope(
        _principal(READ_CAP, WRITE_CAP, "client.read", "identity.manage", "record.read_all"),
        _thread())
    assert bare == loaded is True


def test_write_scope_is_requested_for_write_operations(monkeypatch):
    """The write flag still reaches the scope layer, so servicing a thread and merely reading it are
    scoped separately as before."""
    captured = []
    monkeypatch.setattr(hub, "record_in_scope",
                        lambda p, t, i, write=False: captured.append(write) or True)
    monkeypatch.setattr(hub, "organization_in_scope", lambda *a, **kw: False)
    hub.thread_in_staff_scope(_principal(READ_CAP, WRITE_CAP), _thread(), write=True)
    assert captured == [True]


def test_a_missing_thread_is_never_in_scope():
    assert hub.thread_in_staff_scope(_principal(READ_CAP), None) is False


def test_staff_inbox_filters_every_row_through_record_scope():
    """Pins the mechanism rather than restating it: staff_inbox must consult thread_in_staff_scope
    for each row. If that call disappears, the capability alone would expose the whole corpus."""
    import inspect
    source = inspect.getsource(hub.staff_inbox)
    assert "thread_in_staff_scope" in source


def test_messages_capability_grants_no_broader_client_data_access():
    """A Messages holder must not reach client records, documents or work through that capability.
    The capability appears in exactly one middleware rule — the /threads carve-out — and nowhere
    else in the authorization map."""
    from app.security.middleware import RULES
    gated_by_messages = [pattern.pattern for pattern, code in RULES if code == READ_CAP]
    assert gated_by_messages == [r"^/admin/client-portal/threads"], gated_by_messages


@pytest.mark.parametrize("path", ["/people", "/households", "/documents", "/work", "/portfolio",
                                  "/relationships", "/workspace"])
def test_messages_holder_cannot_reach_unrelated_client_surfaces(path):
    """Those surfaces still demand their own capabilities; the Messages pair is not among them."""
    from app.security.middleware import RULES
    required = next((code for pattern, code in RULES if pattern.search(path)), None)
    assert required not in (READ_CAP, WRITE_CAP), (path, required)


# --- 2. navigation matches the real gate -------------------------------------

def test_sidebar_gates_messages_on_the_message_capability():
    html = _BASE_HTML.read_text(encoding="utf-8")
    match = re.search(r"\{%\s*set can_messages\s*=\s*(.+?)\s*%\}", html)
    assert match, "can_messages is no longer set in base.html"
    expression = match.group(1)
    assert f"'{READ_CAP}'" in expression, expression


def test_sidebar_no_longer_gates_messages_on_client_read():
    """The exact drift this fixes: six roles held client.read without the message capability and were
    shown a link they could not open."""
    html = _BASE_HTML.read_text(encoding="utf-8")
    match = re.search(r"\{%\s*set can_messages\s*=\s*(.+?)\s*%\}", html)
    assert "client.read" not in match.group(1)


def test_the_messages_nav_entry_uses_can_messages():
    """The link itself must still be bound to that gate, not shown unconditionally."""
    html = _BASE_HTML.read_text(encoding="utf-8")
    entry = next(line for line in html.splitlines()
                 if '"/admin/client-portal/threads"' in line and '"label": "Messages"' in line)
    assert '"show": can_messages' in entry, entry


def test_navigation_and_route_agree_on_the_same_capability():
    """One capability, named identically in the template and the RULES map — so a role either sees
    the link AND can open it, or sees neither."""
    from app.security.middleware import RULES
    route_cap = next(code for pattern, code in RULES
                     if pattern.search("/admin/client-portal/threads"))
    html = _BASE_HTML.read_text(encoding="utf-8")
    assert f"'{route_cap}'" in re.search(r"\{%\s*set can_messages\s*=\s*(.+?)\s*%\}", html).group(1)
