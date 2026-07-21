"""Campaign service (Phase D.14) — authoritative CRUD, lifecycle, activities, documents.

Campaigns are firm marketing assets (not client records), so visibility is by capability
(``campaign.view``) rather than per-client record scope; edits require ``campaign.edit`` and the
sensitive budget/ROI fields require ``campaign.manage_budget`` / ``campaign.manage_roi`` (enforced
server-side). Lifecycle (draft → active → paused → completed → archived) appends to the
``campaign_events`` log; approved lifecycle events are recorded there (a firm-level domain has no
client anchor, so they are not written to the person/household Activity Timeline). Activities may
reference an existing Microsoft 365 ``timeline_events`` row and documents reference existing
``documents`` — no duplication.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import and_, func, select

from app.db import (
    campaign_activities,
    campaign_documents,
    campaign_events,
    campaigns,
    documents,
    engine,
    timeline_events,
    users,
)

_TERMINAL = frozenset({"completed", "archived"})
_STATUS_TRANSITIONS = {
    "active": frozenset({"draft", "paused"}),
    "paused": frozenset({"active"}),
    "completed": frozenset({"active", "paused"}),
    "archived": frozenset({"draft", "active", "paused", "completed"}),
}
_BUDGET_FIELDS = frozenset({"budget", "actual_cost"})
_ROI_FIELDS = frozenset({"expected_roi", "actual_roi"})
_PLAIN_FIELDS = frozenset({"name", "campaign_type", "start_date", "end_date", "objective",
                           "description", "target_audience", "marketing_channel", "tags", "notes"})


class CampaignError(Exception):
    """Validation or lifecycle error."""


class CampaignPermissionError(Exception):
    """A sensitive field (budget/ROI) was edited without the required capability."""


class CampaignNotFound(Exception):
    """Campaign does not exist."""


def _now():
    return datetime.now(UTC)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "campaign"


def _unique_code(c, base: str) -> str:
    code = base
    n = 1
    while c.scalar(select(campaigns.c.id).where(campaigns.c.code == code)) is not None:
        n += 1
        code = f"{base}-{n}"
    return code


# --- CRUD --------------------------------------------------------------------

def create_campaign(principal, *, name, actor_user_id, code=None, campaign_type=None,
                    status="draft", start_date=None, end_date=None, budget=None, actual_cost=None,
                    owner_user_id=None, objective=None, marketing_channel=None, target_audience=None,
                    expected_roi=None, description=None) -> dict:
    if not (name or "").strip():
        raise CampaignError("name is required")
    if status not in ("draft", "active"):
        raise CampaignError("new campaigns start draft or active")
    # Budget/ROI on create require the sensitive capabilities.
    if (budget is not None or actual_cost is not None) and not principal.can("campaign.manage_budget"):
        raise CampaignPermissionError("campaign.manage_budget required to set budget")
    if expected_roi is not None and not principal.can("campaign.manage_roi"):
        raise CampaignPermissionError("campaign.manage_roi required to set ROI")
    with engine.begin() as c:
        final_code = code or _unique_code(c, _slug(name))
        if code and c.scalar(select(campaigns.c.id).where(campaigns.c.code == code)) is not None:
            raise CampaignError("campaign code already exists")
        now = _now()
        row = c.execute(campaigns.insert().values(
            code=final_code, name=name.strip(), campaign_type=campaign_type, status=status,
            start_date=start_date, end_date=end_date, budget=budget, actual_cost=actual_cost,
            owner_user_id=owner_user_id or actor_user_id, objective=objective,
            description=description, target_audience=target_audience,
            marketing_channel=marketing_channel, expected_roi=expected_roi,
            created_by=actor_user_id, updated_by=actor_user_id, created_at=now,
            updated_at=now).returning(campaigns)).mappings().one()
        _event(c, row["id"], event_type="created", to_status=status, actor=actor_user_id)
    return dict(row)


def get_campaign(principal, campaign_id: int) -> dict | None:
    if not principal.can("campaign.view"):
        return None
    with engine.connect() as c:
        row = c.execute(select(campaigns).where(campaigns.c.id == campaign_id)).mappings().first()
        if row is None:
            return None
        campaign = dict(row)
        campaign["events"] = [dict(r) for r in c.execute(
            select(campaign_events).where(campaign_events.c.campaign_id == campaign_id)
            .order_by(campaign_events.c.occurred_at.desc())).mappings()]
        campaign["activities"] = [dict(r) for r in c.execute(
            select(campaign_activities).where(campaign_activities.c.campaign_id == campaign_id)
            .order_by(campaign_activities.c.activity_date.desc()).limit(50)).mappings()]
        campaign["documents"] = [dict(r) for r in c.execute(
            select(campaign_documents.c.id, campaign_documents.c.document_id, campaign_documents.c.label,
                   documents.c.original_name.label("document_name"))
            .select_from(campaign_documents.outerjoin(
                documents, documents.c.id == campaign_documents.c.document_id))
            .where(campaign_documents.c.campaign_id == campaign_id)).mappings()]
    return campaign


def list_campaigns(principal, *, status=None, campaign_type=None, owner_user_id=None,
                   search=None, page=1, page_size=50) -> dict:
    conds = []
    if status:
        conds.append(campaigns.c.status == status)
    if campaign_type:
        conds.append(campaigns.c.campaign_type == campaign_type)
    if owner_user_id:
        conds.append(campaigns.c.owner_user_id == owner_user_id)
    if search:
        conds.append(campaigns.c.name.ilike(f"%{search.strip()}%"))
    where = and_(*conds) if conds else None
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 50)))
    with engine.connect() as c:
        total = c.scalar(select(func.count()).select_from(campaigns).where(where)
                         if where is not None else select(func.count()).select_from(campaigns))
        stmt = select(campaigns)
        if where is not None:
            stmt = stmt.where(where)
        rows = [dict(r) for r in c.execute(
            stmt.order_by(campaigns.c.updated_at.desc()).limit(page_size)
            .offset((page - 1) * page_size)).mappings()]
    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if total else 0}


def update_campaign(principal, campaign_id: int, *, actor_user_id, fields: dict) -> dict:
    """Edit fields. Budget/cost require campaign.manage_budget; ROI requires campaign.manage_roi.
    Does not emit lifecycle events (field edits are not lifecycle events)."""
    if any(k in _BUDGET_FIELDS for k in fields) and not principal.can("campaign.manage_budget"):
        raise CampaignPermissionError("campaign.manage_budget required")
    if any(k in _ROI_FIELDS for k in fields) and not principal.can("campaign.manage_roi"):
        raise CampaignPermissionError("campaign.manage_roi required")
    allowed = _PLAIN_FIELDS | _BUDGET_FIELDS | _ROI_FIELDS
    values = {k: v for k, v in fields.items() if k in allowed}
    with engine.begin() as c:
        if c.scalar(select(campaigns.c.id).where(campaigns.c.id == campaign_id)) is None:
            raise CampaignNotFound(str(campaign_id))
        if values:
            values["updated_by"] = actor_user_id
            values["updated_at"] = _now()
            c.execute(campaigns.update().where(campaigns.c.id == campaign_id).values(**values))
        return dict(c.execute(select(campaigns).where(campaigns.c.id == campaign_id)).mappings().one())


def set_status(principal, campaign_id: int, *, new_status: str, actor_user_id, note=None) -> dict:
    if new_status not in _STATUS_TRANSITIONS:
        raise CampaignError(f"unknown status {new_status!r}")
    if new_status == "archived" and not principal.can("campaign.archive"):
        raise CampaignPermissionError("campaign.archive required")
    with engine.begin() as c:
        row = c.execute(select(campaigns).where(campaigns.c.id == campaign_id)).mappings().first()
        if row is None:
            raise CampaignNotFound(str(campaign_id))
        if row["status"] not in _STATUS_TRANSITIONS[new_status]:
            raise CampaignError(f"cannot move to {new_status} from {row['status']}")
        now = _now()
        values = {"status": new_status, "updated_by": actor_user_id, "updated_at": now}
        if new_status == "archived":
            values["archived_at"] = now
        c.execute(campaigns.update().where(campaigns.c.id == campaign_id).values(**values))
        event_type = {"active": "launched", "completed": "completed",
                      "archived": "archived", "paused": "paused"}.get(new_status, "status_changed")
        _event(c, campaign_id, event_type=event_type, from_status=row["status"],
               to_status=new_status, actor=actor_user_id, note=note)
        return dict(c.execute(select(campaigns).where(campaigns.c.id == campaign_id)).mappings().one())


def delete_campaign(principal, campaign_id: int) -> None:
    with engine.begin() as c:
        if c.scalar(select(campaigns.c.id).where(campaigns.c.id == campaign_id)) is None:
            raise CampaignNotFound(str(campaign_id))
        # opportunities.campaign_id is ON DELETE SET NULL, so attribution is detached, not lost.
        c.execute(campaigns.delete().where(campaigns.c.id == campaign_id))


def log_activity(principal, campaign_id: int, *, activity_type, actor_user_id, subject=None,
                 body=None, timeline_event_id=None) -> dict:
    with engine.begin() as c:
        if c.scalar(select(campaigns.c.id).where(campaigns.c.id == campaign_id)) is None:
            raise CampaignNotFound(str(campaign_id))
        if timeline_event_id is not None and c.scalar(
                select(timeline_events.c.id).where(timeline_events.c.id == timeline_event_id)) is None:
            raise CampaignError("referenced timeline event does not exist")
        row = c.execute(campaign_activities.insert().values(
            campaign_id=campaign_id, activity_type=activity_type, subject=subject, body=body,
            actor_user_id=actor_user_id, timeline_event_id=timeline_event_id,
            activity_date=_now(), created_at=_now()).returning(campaign_activities)).mappings().one()
    return dict(row)


def link_document(principal, campaign_id: int, document_id: int, *, actor_user_id, label=None) -> dict:
    with engine.begin() as c:
        if c.scalar(select(campaigns.c.id).where(campaigns.c.id == campaign_id)) is None:
            raise CampaignNotFound(str(campaign_id))
        if c.scalar(select(documents.c.id).where(documents.c.id == document_id)) is None:
            raise CampaignError("document does not exist")
        row = c.execute(campaign_documents.insert().values(
            campaign_id=campaign_id, document_id=document_id, label=label,
            created_by=actor_user_id, created_at=_now()).returning(campaign_documents)).mappings().one()
    return dict(row)


def owner_name(user_id) -> str | None:
    if user_id is None:
        return None
    with engine.connect() as c:
        return c.scalar(select(users.c.display_name).where(users.c.id == user_id))


def _event(c, campaign_id, *, event_type, from_status=None, to_status=None, actor=None, note=None):
    c.execute(campaign_events.insert().values(
        campaign_id=campaign_id, event_type=event_type, from_status=from_status,
        to_status=to_status, actor_user_id=actor, note=note, occurred_at=_now()))
