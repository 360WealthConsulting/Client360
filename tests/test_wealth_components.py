"""Unit tests for the shared Wealth presentation macros (Phase C.1 PR-3).

These render each macro in isolation and assert the exact DOM the Household and
Client workspaces relied on inline before extraction — the pixel-identity guard
for the refactor. The macros are presentation-only: given canonical inputs they
emit fixed markup, with no business logic.
"""
from decimal import Decimal


def _macros():
    from app.templating import templates
    return templates.env.get_template("wealth/components.html").module


def test_summary_card_with_and_without_caption():
    m = _macros()
    with_caption = str(m.summary_card("Cash", "$30,000.00", "10.0%"))
    assert '<article class="card">' in with_caption
    assert '<span class="detail-label">Cash</span>' in with_caption
    assert "<h2>$30,000.00</h2>" in with_caption
    assert '<p class="subtle">10.0%</p>' in with_caption
    # No caption -> no <p> element (needed for cards like Household AUM).
    no_caption = str(m.summary_card("Household AUM", "$400,000.00"))
    assert "<h2>$400,000.00</h2>" in no_caption
    assert '<p class="subtle">' not in no_caption


def test_allocation_card_rows_and_empty_state():
    m = _macros()
    alloc = {"Equity": {"percent": Decimal("80.0"), "value": Decimal("480000")}}
    html = str(m.allocation_card(alloc))
    assert "<h3>Asset allocation</h3>" in html
    assert '<div class="detail-row"><span>Equity</span>' in html
    assert "80.0% · $480,000.00" in html
    assert "No positions imported." in str(m.allocation_card({}))


def test_positions_card_rows_and_empty_state():
    m = _macros()
    positions = [{"symbol": "VTSAX", "name": "Vanguard Total Stock", "market_value": Decimal("480000")}]
    html = str(m.positions_card(positions))
    assert "<h3>Largest positions</h3>" in html
    assert "VTSAX · Vanguard Total Stock" in html
    assert "$480,000.00" in html
    assert "No positions imported." in str(m.positions_card([]))


def test_accounts_table_and_scoped_empty_state():
    m = _macros()
    accounts = [{"account_name": "DEMO-1", "account_number": "1",
                 "registration_type": "Roth IRA", "custodian": "Schwab",
                 "total_value": Decimal("600000")}]
    html = str(m.accounts_table(accounts, "household"))
    assert '<table class="data">' in html
    assert "<td>Roth IRA</td>" in html and "<td>Schwab</td>" in html
    assert '<td class="num">$600,000.00</td>' in html
    # Empty state carries the caller-provided scope word.
    assert "No portfolio accounts found for this household." in str(m.accounts_table([], "household"))
    assert "No portfolio accounts found for this client." in str(m.accounts_table([], "client"))
