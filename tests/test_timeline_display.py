"""Sprint 2 (D-11) — Sprint 1 note/communication timeline events have real display
styling instead of the generic default bullet.
"""
from __future__ import annotations

from app.services.timeline import _decorate_event


def _decorate(event_type):
    return _decorate_event({"event_type": event_type, "event_time": None})


def test_activity_note_added_has_dedicated_display():
    d = _decorate("activity_note_added")
    assert d["display_icon"] != "•"
    assert d["display_label"] == "Activity Note"
    assert d["display_style"] == "note"


def test_communication_logged_has_dedicated_display():
    d = _decorate("communication_logged")
    assert d["display_icon"] != "•"
    assert d["display_label"] == "Communication"
    assert d["display_style"] == "activity"


def test_unregistered_event_still_falls_back_to_default():
    d = _decorate("something_unregistered")
    assert d["display_icon"] == "•"
    assert d["display_style"] == "default"
    assert d["display_label"] == "Something Unregistered"
