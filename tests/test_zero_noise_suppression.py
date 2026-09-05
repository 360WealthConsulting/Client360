"""Zero-value presentation rules for Today, Team Work and Intake.

The approved rule from the Client Profile pass: a counter reading 0 reports the ABSENCE of
work, not a finding, and must not occupy a tile, a badge or a card. These screens each had a
band of them — six "Today" tiles, three "High 0 / Medium 0 / Low 0" badges, six exception
counters and five intake metric cards — which pushed the actual work below the fold.

Both directions matter, so both are pinned: the zero case must disappear, and the populated
case must render EXACTLY as before. A suppression rule that also hides real data would be a
worse defect than the noise it removes.
"""

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATES = "app/templates"


@pytest.fixture
def env():
    e = Environment(loader=FileSystemLoader(TEMPLATES))
    e.filters["humandt"] = lambda v: str(v or "")
    e.filters["datefmt"] = lambda v, f: str(v or "")
    e.globals["asset_version"] = "test"
    return e


def _summary(**over):
    base = {"open": 0, "blocker": 0, "at_risk": 0, "breached": 0,
            "unassigned": 0, "compliance": 0}
    base.update(over)
    return {"audience": "operations", "summary": base}


# --- shared exception strip (Dashboard, Tax, Team Work all include it) -------------------

def test_exception_strip_is_omitted_when_every_counter_is_zero(env):
    out = env.get_template("partials/exception_summary.html").render(
        exception_summary=_summary())
    assert "Exception summary" not in out
    assert "Open exceptions" not in out


def test_exception_strip_renders_only_the_counters_that_carry_a_finding(env):
    out = env.get_template("partials/exception_summary.html").render(
        exception_summary=_summary(open=4, breached=2))
    assert "Open exceptions" in out and "Breached" in out
    # The counters still at zero must not dilute the two that matter.
    assert "Blocker" not in out
    assert "At risk" not in out
    assert "Compliance" not in out


def test_open_exceptions_anchors_the_row_even_at_zero(env):
    """It is the denominator the other counters are read against."""
    out = env.get_template("partials/exception_summary.html").render(
        exception_summary=_summary(breached=1))
    assert "Open exceptions" in out
    assert "Breached" in out


def test_exception_strip_absent_when_the_viewer_cannot_read_exceptions(env):
    """dashboard_summary returns None without `exception.read` — unchanged behaviour."""
    out = env.get_template("partials/exception_summary.html").render(exception_summary=None)
    assert out.strip() == ""


# --- Today: tiles and priority badges ---------------------------------------------------

def _today_tiles(env, **counts):
    """Exercise the tile-selection expression exactly as the template spells it."""
    ws = {"today": {"appointments": 0, "compliance": 0, "tax": 0,
                    "insurance": 0, "benefits": 0, "exceptions": 0}}
    ws["today"].update(counts)
    tpl = env.from_string(
        "{% set t = [('Appointments', ws.today.appointments), ('Compliance', ws.today.compliance),"
        "('Tax returns', ws.today.tax), ('Insurance', ws.today.insurance),"
        "('Benefits', ws.today.benefits), ('Exceptions', ws.today.exceptions)]"
        " | selectattr(1) | list %}"
        "{% for label, value in t %}{{ label }}={{ value }};{% endfor %}"
    )
    return tpl.render(ws=ws)


def test_today_band_is_empty_when_no_domain_has_work(env):
    assert _today_tiles(env) == ""


def test_today_renders_only_the_domains_with_work(env):
    out = _today_tiles(env, appointments=3, tax=1)
    assert out == "Appointments=3;Tax returns=1;"


def test_today_preserves_every_non_zero_tile(env):
    out = _today_tiles(env, appointments=1, compliance=2, tax=3,
                       insurance=4, benefits=5, exceptions=6)
    for label in ("Appointments", "Compliance", "Tax returns",
                  "Insurance", "Benefits", "Exceptions"):
        assert label in out


def _priorities(env, high=0, medium=0, low=0):
    tpl = env.from_string(
        "{% set p = [('High', ws.priorities.high, 'crit'), ('Medium', ws.priorities.medium, 'warn'),"
        "('Low', ws.priorities.low, 'good')] | selectattr(1) | list %}"
        "{% for label, value, kind in p %}{{ label }}={{ value }};{% endfor %}"
    )
    return tpl.render(ws={"priorities": {"high": high, "medium": medium, "low": low}})


def test_priority_badges_absent_when_all_three_are_zero(env):
    assert _priorities(env) == ""


def test_priority_badges_render_only_the_populated_bands(env):
    assert _priorities(env, high=2) == "High=2;"
    assert _priorities(env, medium=1, low=5) == "Medium=1;Low=5;"


# --- Intake: metric cards and empty state ------------------------------------------------

def test_intake_hides_metric_cards_and_shows_one_empty_state_when_nothing_is_in_scope(env):
    out = env.get_template("tax/intake_dashboard.html").render(
        data={"items": [], "metrics": {"returns": 0, "letters": 0, "organizers": 0,
                                       "questionnaires": 0, "ready": 0}})
    assert "stats-grid" not in out
    assert "<strong>0</strong>" not in out
    assert "No authorized intake work" in out


def test_intake_renders_metrics_and_the_readiness_table_when_returns_exist(env):
    item = {"return_type": "1040", "overdue_items": 0,
            "intake": {"context": {"year": 2026}, "missing": [], "client_readiness": 50,
                       "organizer": {"status": "in_progress"}, "questionnaire": None}}
    out = env.get_template("tax/intake_dashboard.html").render(
        data={"items": [item], "metrics": {"returns": 1, "ready": 0}})
    assert "stats-grid" in out
    assert "1040" in out
    assert "in progress" in out          # a real organizer status survives
    assert "Not issued" in out           # questionnaire=None stays honest
    assert "No authorized intake work" not in out
