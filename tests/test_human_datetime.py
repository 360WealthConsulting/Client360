"""Sprint 2 (UX P-6) — the shared human_datetime Jinja filter."""
from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi.templating import Jinja2Templates

from app.templating import human_datetime, install_filters


def test_datetime_is_formatted_with_time():
    out = human_datetime(datetime(2026, 7, 20, 14, 3, tzinfo=UTC))
    assert out == "Jul 20, 2026 2:03 PM"


def test_date_is_formatted_without_time():
    assert human_datetime(date(2026, 8, 15)) == "Aug 15, 2026"


def test_none_and_empty_render_blank():
    assert human_datetime(None) == ""
    assert human_datetime("") == ""


def test_non_temporal_value_falls_back_to_str():
    assert human_datetime("already a string") == "already a string"


def test_install_filters_registers_humandt():
    templates = Jinja2Templates(directory="app/templates")
    assert "humandt" not in templates.env.filters
    install_filters(templates)
    assert templates.env.filters["humandt"] is human_datetime
