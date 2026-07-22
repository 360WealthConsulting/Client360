from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, or_, select

from app.db import (
    account_beneficiaries,
    account_holdings,
    accounts,
    engine,
    household_relationships,
    households,
    people,
    securities,
)
from app.portfolio.calculations import aggregate_portfolio

ZERO = Decimal("0")
# Cash/total ratio at/above which a portfolio is flagged "high cash" (advisor
# attention). One threshold shared by the portfolio search filter and the
# wealth dashboard count so they never diverge.
HIGH_CASH_RATIO = Decimal("0.15")

# The canonical Wealth contract — the single vocabulary every portfolio-shaped
# result exposes. get_person_portfolio() and get_household_portfolio() both build
# on `_portfolio()`, so both surface exactly these concepts under exactly these
# names. Shared Wealth components (Phase C.1 PR-3) consume only this vocabulary.
_CANONICAL_KEYS = (
    "aum", "cash", "cash_percent", "allocation", "largest_positions",
    "concentration", "holdings", "accounts", "beneficiary_count", "last_import_date",
)


def _largest_position_percents(person_ids):
    """Bulk `largest_position_percent` for many people in a bounded number of
    queries, using the same aggregate_portfolio math as get_person_portfolio so
    the numeric result is identical (removes the concentration-filter N+1)."""
    person_ids = list(dict.fromkeys(person_ids))
    if not person_ids: return {}
    with engine.connect() as conn:
        account_rows = conn.execute(select(accounts).where(accounts.c.person_id.in_(person_ids))).mappings().all()
        acct_ids = [r["id"] for r in account_rows]
        holding_rows = [] if not acct_ids else conn.execute(select(account_holdings.c.account_id, account_holdings.c.market_value, account_holdings.c.cost_basis, account_holdings.c.unrealized_gain, securities.c.symbol, securities.c.name, securities.c.asset_class).join(securities, securities.c.id == account_holdings.c.security_id).where(account_holdings.c.account_id.in_(acct_ids))).mappings().all()
    acct_to_person = {r["id"]: r["person_id"] for r in account_rows}
    accounts_by_person = defaultdict(list)
    for r in account_rows: accounts_by_person[r["person_id"]].append(r)
    holdings_by_person = defaultdict(list)
    for h in holding_rows: holdings_by_person[acct_to_person[h["account_id"]]].append(h)
    return {pid: aggregate_portfolio(accounts_by_person.get(pid, []), holdings_by_person.get(pid, []))["largest_position_percent"] for pid in person_ids}

def _portfolio(where):
    with engine.connect() as conn:
        account_rows = conn.execute(select(accounts).where(where).order_by(accounts.c.total_value.desc().nullslast())).mappings().all()
        ids = [r["id"] for r in account_rows]
        holding_rows = [] if not ids else conn.execute(select(account_holdings.c.account_id, account_holdings.c.market_value, account_holdings.c.cost_basis, account_holdings.c.unrealized_gain, securities.c.symbol, securities.c.name, securities.c.asset_class).join(securities, securities.c.id == account_holdings.c.security_id).where(account_holdings.c.account_id.in_(ids))).mappings().all()
        beneficiary_count = 0 if not ids else conn.scalar(select(func.count()).select_from(account_beneficiaries).where(and_(account_beneficiaries.c.account_id.in_(ids), account_beneficiaries.c.active.is_(True)))) or 0
    agg = aggregate_portfolio(account_rows, holding_rows)
    largest = agg["largest_holdings"]
    last_import_date = max((r.get("last_imported_at") for r in account_rows if r.get("last_imported_at")), default=None)
    # Build the canonical contract first — this is the single source of truth for
    # both get_person_portfolio() and get_household_portfolio().
    result = {
        "aum": agg["total_aum"],
        "cash": agg["cash"],
        "cash_percent": agg["cash_percent"],
        "allocation": agg["asset_allocation"],
        "largest_positions": largest,
        "concentration": {
            "largest_position_percent": agg["largest_position_percent"],
            "top_position": largest[0] if largest else None,
        },
        "holdings": agg["holdings"],
        "accounts": agg["accounts"],
        "beneficiary_count": beneficiary_count,
        "last_import_date": last_import_date,
    }
    # Compatibility aliases (TEMPORARY). Legacy key names that some templates
    # still read; they mirror the canonical values exactly and exist only to keep
    # existing UI rendering byte-identical during the migration. Remove once every
    # consumer reads the canonical vocabulary above (after Phase C.1 PR-3).
    result["total_aum"] = result["aum"]
    result["asset_allocation"] = result["allocation"]
    result["largest_holdings"] = result["largest_positions"]
    result["largest_position_percent"] = result["concentration"]["largest_position_percent"]
    return result

def book_aum(person_ids):
    """Total AUM (sum of ``accounts.total_value``) across a set of person ids (Phase D.15
    analytics read). Follows the ``person_ids`` scope convention used elsewhere in this module:
    ``None`` -> firm-wide (all accounts); an empty set -> 0; a set -> only those clients. Bounded
    single aggregate query; no per-account fetch."""
    with engine.connect() as conn:
        if person_ids is None:
            return conn.scalar(select(func.coalesce(func.sum(accounts.c.total_value), 0))) or ZERO
        if not person_ids:
            return ZERO
        return conn.scalar(select(func.coalesce(func.sum(accounts.c.total_value), 0))
                           .where(accounts.c.person_id.in_(tuple(person_ids)))) or ZERO


def get_person_portfolio(person_id):
    with engine.connect() as conn:
        household_id = conn.scalar(select(accounts.c.household_id).where(and_(accounts.c.person_id == person_id, accounts.c.household_id.is_not(None))).limit(1))
    result = _portfolio(accounts.c.person_id == person_id)
    result["household"] = _portfolio(accounts.c.household_id == household_id) if household_id else result
    return result

def _household_members(household_id):
    """Roster of people in a household, reusing the household_relationships→people
    join (same source the household profile page uses). Primary contacts first."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                people.c.id, people.c.full_name, people.c.primary_email,
                household_relationships.c.relationship_type,
                household_relationships.c.is_primary,
            )
            .select_from(household_relationships.join(people, people.c.id == household_relationships.c.person_id))
            .where(household_relationships.c.household_id == household_id)
            .order_by(household_relationships.c.is_primary.desc(), people.c.full_name)
        ).mappings().all()
    return [dict(r) for r in rows]

def get_household_portfolio(household_id):
    """Aggregate a household's portfolio across all member accounts.

    Reuses `_portfolio()` — which reuses `aggregate_portfolio()` /
    `calculate_allocation()` — so household and person portfolios share a single
    aggregation implementation (no duplicated logic). Adds the member roster.
    An empty household (or one with no accounts) yields safe zeros/empties.
    """
    p = _portfolio(accounts.c.household_id == household_id)
    # Pass the canonical contract straight through (no per-service renaming) and
    # add the household-only extras. Legacy aliases are intentionally not
    # re-exposed here — the household surface already reads canonical keys.
    result = {k: p[k] for k in _CANONICAL_KEYS}
    result["household_id"] = household_id
    result["members"] = _household_members(household_id)
    return result

def get_firm_portfolio_metrics():
    with engine.connect() as conn:
        firm_aum = conn.scalar(select(func.coalesce(func.sum(accounts.c.total_value), 0))) or ZERO
        cash = conn.scalar(select(func.coalesce(func.sum(accounts.c.cash_value), 0))) or ZERO
        largest_household = conn.execute(select(households.c.name, func.sum(accounts.c.total_value).label("aum")).join(accounts, accounts.c.household_id == households.c.id).group_by(households.c.id).order_by(func.sum(accounts.c.total_value).desc()).limit(1)).mappings().first()
        largest_position = conn.execute(select(securities.c.symbol, func.sum(account_holdings.c.market_value).label("value")).join(account_holdings, account_holdings.c.security_id == securities.c.id).group_by(securities.c.id).order_by(func.sum(account_holdings.c.market_value).desc()).limit(1)).mappings().first()
        missing_beneficiaries = conn.scalar(select(func.count()).select_from(accounts.outerjoin(account_beneficiaries, and_(account_beneficiaries.c.account_id == accounts.c.id, account_beneficiaries.c.active.is_(True)))).where(and_(accounts.c.registration_type.ilike("%IRA%"), account_beneficiaries.c.id.is_(None)))) or 0
        without_reviews = conn.scalar(select(func.count()).select_from(accounts).where(accounts.c.last_review_date.is_(None))) or 0
    return {"firm_aum": firm_aum, "cash_waiting": cash, "largest_household": largest_household, "largest_position": largest_position, "missing_beneficiaries": missing_beneficiaries, "accounts_without_reviews": without_reviews}

def accounts_due_for_review(person_ids, *, stale_days=365, limit=20, today=None):
    """Accounts whose review is due — no `last_review_date`, or older than
    `stale_days`. Authoritative wealth read for the advisor dashboard's
    "reviews due" panel. `person_ids` scopes the read: `None` = unrestricted
    (record.read_all), an empty collection = no accessible people (returns `[]`).
    Read-only; sources the existing `accounts.last_review_date` field (no
    review-workflow instances in this slice — see Phase D.1 note)."""
    if person_ids is not None and len(person_ids) == 0:
        return []
    today = today or date.today()
    cutoff = today - timedelta(days=stale_days)
    stmt = (
        select(
            accounts.c.id, accounts.c.person_id, accounts.c.household_id,
            accounts.c.account_name, accounts.c.account_number, accounts.c.custodian,
            accounts.c.last_review_date,
        )
        .where(
            accounts.c.person_id.is_not(None),
            or_(accounts.c.last_review_date.is_(None), accounts.c.last_review_date < cutoff),
        )
    )
    if person_ids is not None:
        stmt = stmt.where(accounts.c.person_id.in_(tuple(person_ids)))
    stmt = stmt.order_by(accounts.c.last_review_date.asc().nullsfirst()).limit(limit)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def accounts_review_approaching(person_ids, *, cycle_days=365, within_days=30, limit=200, today=None):
    """Accounts whose annual review is *approaching* — reviewed on a `cycle_days`
    cadence and now within `within_days` of the next review, but NOT yet overdue.
    Authoritative wealth read behind the Advisor Intelligence "portfolio review
    opportunity" (Phase D.5C); it reuses the SAME `accounts.last_review_date`
    cadence as `accounts_due_for_review` (overdue) and is deliberately DISJOINT
    from it (overdue accounts are excluded — those are the D.5B operational
    signal). `person_ids` scopes the read (`None` = record.read_all, empty = `[]`).
    Read-only; no review-workflow instances, no new cadence policy."""
    if person_ids is not None and len(person_ids) == 0:
        return []
    today = today or date.today()
    overdue_cutoff = today - timedelta(days=cycle_days)                 # older -> overdue (excluded)
    approaching_cutoff = today - timedelta(days=cycle_days - within_days)  # newer -> not yet approaching
    stmt = (
        select(
            accounts.c.id, accounts.c.person_id, accounts.c.household_id,
            accounts.c.account_name, accounts.c.account_number, accounts.c.custodian,
            accounts.c.last_review_date,
        )
        .where(
            accounts.c.person_id.is_not(None),
            accounts.c.last_review_date.is_not(None),
            accounts.c.last_review_date >= overdue_cutoff,       # not overdue
            accounts.c.last_review_date < approaching_cutoff,    # within the approaching window
        )
    )
    if person_ids is not None:
        stmt = stmt.where(accounts.c.person_id.in_(tuple(person_ids)))
    stmt = stmt.order_by(accounts.c.last_review_date.asc()).limit(limit)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


def accounts_missing_required_beneficiary(person_ids, *, limit=200):
    """IRA accounts with NO active beneficiary — a missing *required* designation.
    Authoritative wealth read behind the Advisor Intelligence "beneficiary review
    opportunity" (Phase D.5C). It reuses the EXACT predicate the firm-portfolio
    metric already uses (`registration_type ILIKE '%IRA%'` AND no active
    `account_beneficiaries` row); it does NOT infer a missing beneficiary for
    non-IRA registrations. `person_ids` scopes the read (`None` = record.read_all,
    empty = `[]`). Read-only."""
    if person_ids is not None and len(person_ids) == 0:
        return []
    stmt = (
        select(
            accounts.c.id, accounts.c.person_id, accounts.c.household_id,
            accounts.c.account_name, accounts.c.account_number, accounts.c.registration_type,
        )
        .select_from(accounts.outerjoin(account_beneficiaries, and_(
            account_beneficiaries.c.account_id == accounts.c.id,
            account_beneficiaries.c.active.is_(True))))
        .where(
            accounts.c.person_id.is_not(None),
            accounts.c.registration_type.ilike("%IRA%"),
            account_beneficiaries.c.id.is_(None),
        )
    )
    if person_ids is not None:
        stmt = stmt.where(accounts.c.person_id.in_(tuple(person_ids)))
    stmt = stmt.order_by(accounts.c.id.asc()).limit(limit)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]

def get_wealth_dashboard():
    """Book-triage figures for the advisor Wealth dashboard.

    Reuses get_firm_portfolio_metrics() (firm AUM, cash, missing beneficiaries,
    accounts needing review) and adds the high-cash count. Pure reads over
    existing tables — no new schema or business policy.
    """
    metrics = get_firm_portfolio_metrics()
    with engine.connect() as conn:
        high_cash_count = conn.scalar(
            select(func.count()).select_from(accounts).where(
                and_(accounts.c.total_value > 0,
                     func.coalesce(accounts.c.cash_value, 0) / accounts.c.total_value >= HIGH_CASH_RATIO))
        ) or 0
    return {**metrics, "high_cash_count": high_cash_count}

def search_portfolios(query="", min_aum=None, registration=None, high_cash=False, missing_beneficiary=False, concentration=None, limit=None):
    stmt = select(people.c.id, people.c.full_name, func.sum(accounts.c.total_value).label("aum"), func.sum(accounts.c.cash_value).label("cash")).join(accounts, accounts.c.person_id == people.c.id).group_by(people.c.id)
    if query: stmt = stmt.where(or_(people.c.full_name.ilike(f"%{query}%"), accounts.c.registration_type.ilike(f"%{query}%")))
    if registration: stmt = stmt.where(accounts.c.registration_type.ilike(f"%{registration}%"))
    if missing_beneficiary: stmt = stmt.outerjoin(account_beneficiaries, account_beneficiaries.c.account_id == accounts.c.id).where(account_beneficiaries.c.id.is_(None))
    if min_aum is not None: stmt = stmt.having(func.sum(accounts.c.total_value) >= min_aum)
    if high_cash: stmt = stmt.having(func.sum(accounts.c.cash_value) / func.nullif(func.sum(accounts.c.total_value), 0) >= HIGH_CASH_RATIO)
    with engine.connect() as conn: rows = [dict(r) for r in conn.execute(stmt.order_by(func.sum(accounts.c.total_value).desc())).mappings()]
    if concentration is not None:
        percents = _largest_position_percents([r["id"] for r in rows])
        rows = [r for r in rows if percents.get(r["id"], ZERO) >= concentration]
    return rows[:limit] if limit else rows
