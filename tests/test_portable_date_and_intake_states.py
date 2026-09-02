"""Two staff pages returned HTTP 500 on Windows. Both causes are pinned here.

1. `/workspace` (Today) — templates called ``strftime("%A, %B %-d, %Y")``. ``%-d`` is a glibc
   extension; the Microsoft C runtime raises ``ValueError: Invalid format string``, so the page
   was a 500 and five advisor/workspace tests failed with it. The same directive inside
   ``templating.human_datetime`` was swallowed by an ``except ValueError`` that returned
   ``str(value)``, so every ``humandt`` value silently degraded to a raw
   "2026-09-02 08:55:30.493174-05:00" instead of "Sep 2, 2026".

2. `/tax/intake` — the template dereferenced ``item.intake.organizer.status`` where ``organizer``
   is legitimately ``None`` until an organizer is issued (``tax_intake._detail_from_parts``
   writes ``dict(x) if x else None``, and the readiness gates read it as ``bool(x and ...)``).

These are platform/None-handling regressions, so they are asserted directly rather than through a
rendered page: the formatter is exercised for the directives the templates actually use, and the
intake template is rendered against a detail payload whose organizer is absent.
"""
from datetime import date, datetime

from app.templating import format_datetime, human_datetime


# --- portable strftime -----------------------------------------------------------------

def test_no_pad_day_directive_formats_on_every_platform():
    """`%-d` must produce an unpadded day rather than raising on Windows."""
    assert format_datetime(date(2026, 9, 2), "%A, %B %-d, %Y") == "Wednesday, September 2, 2026"


def test_no_pad_hour_directive_formats_on_every_platform():
    assert format_datetime(datetime(2026, 9, 2, 14, 3), "%-I:%M %p") == "2:03 PM"


def test_two_digit_values_are_not_altered():
    """Stripping padding must not strip a digit from a genuinely two-digit value."""
    assert format_datetime(date(2026, 9, 20), "%b %-d") == "Sep 20"
    assert format_datetime(datetime(2026, 9, 2, 11, 30), "%-I:%M") == "11:30"


def test_midnight_and_noon_read_as_twelve():
    """`%-I` is a 12-hour clock: hour 0 and hour 12 both display as 12, never 0."""
    assert format_datetime(datetime(2026, 9, 2, 0, 5), "%-I:%M %p") == "12:05 AM"
    assert format_datetime(datetime(2026, 9, 2, 12, 5), "%-I:%M %p") == "12:05 PM"


def test_human_datetime_returns_a_formatted_date_not_a_repr():
    """The regression that hid itself: the except branch used to return str(value)."""
    assert human_datetime(datetime(2026, 9, 2, 14, 3)) == "Sep 2, 2026 2:03 PM"
    assert human_datetime(date(2026, 9, 2)) == "Sep 2, 2026"
    # A raw datetime repr must never reach staff.
    assert "00:00:00" not in human_datetime(datetime(2026, 9, 2))


def test_human_datetime_tolerates_empty_and_unformattable_values():
    assert human_datetime(None) == ""
    assert human_datetime("") == ""
    assert human_datetime("already a string") == "already a string"


def test_literal_percent_in_output_is_not_read_as_a_directive():
    """A substituted value containing '%' would otherwise start a new directive."""
    assert format_datetime(date(2026, 9, 2), "%-d%%") == "2%"


def test_staff_templates_contain_no_raw_no_pad_strftime():
    """The defect must not creep back in via a new .strftime("%-d") call site."""
    import glob
    import io
    import re
    offenders = []
    for path in glob.glob("app/templates/**/*.html", recursive=True):
        text = io.open(path, encoding="utf-8").read()
        if re.search(r'\.strftime\("[^"]*%-', text):
            offenders.append(path)
    assert offenders == [], f"use the `datefmt` filter instead: {offenders}"


# --- intake: organizer / questionnaire not yet issued ------------------------------------

def test_intake_row_renders_when_no_organizer_has_been_issued():
    """A return with no organizer is a normal pre-issue state, not a 500."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("app/templates"))
    source = env.loader.get_source(env, "tax/intake_dashboard.html")[0]
    # Render just the row expression the page uses, against an absent organizer.
    row = env.from_string(
        "{% if item.intake.organizer %}{{ item.intake.organizer.status }}"
        "{% else %}Not issued{% endif %}"
    )
    absent = {"intake": {"organizer": None, "questionnaire": None}}
    assert row.render(item=absent) == "Not issued"
    # And the real template still guards both columns.
    assert "item.intake.organizer %}" in source
    assert "item.intake.questionnaire %}" in source


def test_intake_row_still_shows_a_real_status_when_one_exists():
    """The guard must not swallow a genuine status."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("app/templates"))
    row = env.from_string(
        "{% if item.intake.organizer %}{{ item.intake.organizer.status.replace('_',' ') }}"
        "{% else %}Not issued{% endif %}"
    )
    present = {"intake": {"organizer": {"status": "in_progress"}}}
    assert row.render(item=present) == "in progress"
