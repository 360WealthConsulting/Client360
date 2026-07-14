from decimal import Decimal
from sqlalchemy import and_, func, or_, select
from app.db import account_beneficiaries, account_holdings, accounts, engine, households, people, securities

from app.portfolio.calculations import aggregate_portfolio

ZERO = Decimal("0")

def _portfolio(where):
    with engine.connect() as conn:
        account_rows = conn.execute(select(accounts).where(where).order_by(accounts.c.total_value.desc().nullslast())).mappings().all()
        ids = [r["id"] for r in account_rows]
        holding_rows = [] if not ids else conn.execute(select(account_holdings.c.account_id, account_holdings.c.market_value, account_holdings.c.cost_basis, account_holdings.c.unrealized_gain, securities.c.symbol, securities.c.name, securities.c.asset_class).join(securities, securities.c.id == account_holdings.c.security_id).where(account_holdings.c.account_id.in_(ids))).mappings().all()
        beneficiary_count = 0 if not ids else conn.scalar(select(func.count()).select_from(account_beneficiaries).where(and_(account_beneficiaries.c.account_id.in_(ids), account_beneficiaries.c.active.is_(True)))) or 0
    result = aggregate_portfolio(account_rows, holding_rows)
    result["beneficiary_count"] = beneficiary_count
    result["last_import_date"] = max((r.get("last_imported_at") for r in account_rows if r.get("last_imported_at")), default=None)
    return result

def get_person_portfolio(person_id):
    with engine.connect() as conn:
        household_id = conn.scalar(select(accounts.c.household_id).where(and_(accounts.c.person_id == person_id, accounts.c.household_id.is_not(None))).limit(1))
    result = _portfolio(accounts.c.person_id == person_id)
    result["household"] = _portfolio(accounts.c.household_id == household_id) if household_id else result
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

def search_portfolios(query="", min_aum=None, registration=None, high_cash=False, missing_beneficiary=False, concentration=None):
    stmt = select(people.c.id, people.c.full_name, func.sum(accounts.c.total_value).label("aum"), func.sum(accounts.c.cash_value).label("cash")).join(accounts, accounts.c.person_id == people.c.id).group_by(people.c.id)
    if query: stmt = stmt.where(or_(people.c.full_name.ilike(f"%{query}%"), accounts.c.registration_type.ilike(f"%{query}%")))
    if registration: stmt = stmt.where(accounts.c.registration_type.ilike(f"%{registration}%"))
    if missing_beneficiary: stmt = stmt.outerjoin(account_beneficiaries, account_beneficiaries.c.account_id == accounts.c.id).where(account_beneficiaries.c.id.is_(None))
    if min_aum is not None: stmt = stmt.having(func.sum(accounts.c.total_value) >= min_aum)
    if high_cash: stmt = stmt.having(func.sum(accounts.c.cash_value) / func.nullif(func.sum(accounts.c.total_value), 0) >= Decimal("0.15"))
    with engine.connect() as conn: rows = [dict(r) for r in conn.execute(stmt.order_by(func.sum(accounts.c.total_value).desc())).mappings()]
    if concentration is not None:
        rows = [r for r in rows if get_person_portfolio(r["id"])["largest_position_percent"] >= concentration]
    return rows
