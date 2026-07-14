import hashlib
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.db import account_holdings, accounts, custodians, engine, people, portfolio_import_runs, portfolio_transactions, securities
from app.portfolio.adapters.base import PortfolioSourceAdapter
from app.portfolio.matching import normalize_email
from app.services.timeline import add_timeline_event

def match_person_id(connection, email):
    normalized = normalize_email(email)
    if not normalized: return None
    return connection.scalar(select(people.c.id).where(func.lower(func.trim(people.c.primary_email)) == normalized))

def _upsert(connection, table, values, constraint, updates=None):
    stmt = pg_insert(table).values(**values)
    if updates is None: updates = {k: getattr(stmt.excluded, k) for k in values if k not in {"id"}}
    return connection.execute(stmt.on_conflict_do_update(constraint=constraint, set_=updates).returning(table.c.id)).scalar_one()

def import_portfolio_file(path: Path, adapter: PortfolioSourceAdapter):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    batch = adapter.read(path)
    stats = {"accounts": 0, "positions": 0, "transactions": 0, "matched": 0, "unmatched": 0, "deduplicated": False}
    with engine.begin() as conn:
        custodian_id = _upsert(conn, custodians, {"code": adapter.custodian_code, "name": "Charles Schwab"}, "custodians_code_key")
        existing_run = conn.scalar(select(portfolio_import_runs.c.id).where(portfolio_import_runs.c.custodian_id == custodian_id, portfolio_import_runs.c.source_type == batch.source_type, portfolio_import_runs.c.file_hash == digest))
        if existing_run:
            stats["deduplicated"] = True
            return stats
        run_id = conn.execute(portfolio_import_runs.insert().values(custodian_id=custodian_id, source_type=batch.source_type, source_file=str(path), file_hash=digest, status="running", stats={} ).returning(portfolio_import_runs.c.id)).scalar_one()
        account_ids = {}
        for record in batch.accounts:
            before = conn.execute(select(accounts).where(accounts.c.custodian == "Schwab", accounts.c.account_number == record.account_number)).mappings().first()
            person_id = match_person_id(conn, record.owner_email) or (before and before.get("person_id"))
            stats["matched" if person_id else "unmatched"] += 1
            account_id = _upsert(conn, accounts, {"person_id": person_id, "custodian_id": custodian_id, "custodian": "Schwab", "account_number": record.account_number, "account_name": record.name, "registration_type": record.registration, "status": record.status, "total_value": record.total_value, "cash_value": record.cash_value, "closed_date": record.closed_date, "source_file": str(path), "last_imported_at": datetime.now(timezone.utc)}, "uq_account_custodian_number")
            account_ids[record.account_number] = account_id
            stats["accounts"] += 1
            event = None
            if person_id and not before: event = ("portfolio_account_opened", "New account opened")
            elif person_id and before and str(before.get("status", "")).lower() != str(record.status).lower() and str(record.status).lower() == "closed": event = ("portfolio_account_closed", "Account closed")
            elif person_id and before and abs(record.cash_value - (before.get("cash_value") or 0)) >= 10000: event = ("portfolio_cash_movement", "Large cash movement")
            if event: add_timeline_event(source="schwab", event_type=event[0], title=event[1], person_id=person_id, external_id=f"account:{record.account_number}:{event[0]}:{record.total_value}:{record.cash_value}", event_metadata={"account_number_last4": record.account_number[-4:], "value": str(record.total_value)})
        for record in batch.positions:
            account_id = account_ids.get(record.account_number) or conn.scalar(select(accounts.c.id).where(accounts.c.custodian == "Schwab", accounts.c.account_number == record.account_number))
            if not account_id: continue
            security_id = _upsert(conn, securities, {"symbol": record.symbol, "cusip": None, "name": record.name, "asset_class": record.asset_class}, "uq_security_symbol")
            _upsert(conn, account_holdings, {"account_id": account_id, "security_id": security_id, "quantity": record.quantity, "price": record.price, "market_value": record.market_value, "cost_basis": record.cost_basis, "unrealized_gain": record.market_value - record.cost_basis if record.cost_basis is not None else None, "as_of_date": record.as_of_date}, "uq_current_account_security")
            stats["positions"] += 1
        for record in batch.transactions:
            account_id = account_ids.get(record.account_number) or conn.scalar(select(accounts.c.id).where(accounts.c.custodian == "Schwab", accounts.c.account_number == record.account_number))
            if account_id:
                _upsert(conn, portfolio_transactions, {"account_id": account_id, "external_transaction_id": record.external_id, "transaction_date": record.transaction_date, "transaction_type": record.transaction_type, "amount": record.amount, "description": record.description}, "uq_account_transaction")
                stats["transactions"] += 1
        conn.execute(portfolio_import_runs.update().where(portfolio_import_runs.c.id == run_id).values(status="complete", stats=stats, completed_at=datetime.now(timezone.utc)))
    return stats
