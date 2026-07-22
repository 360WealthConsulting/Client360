"""Communication templates (Phase D.18) — reusable, deterministic message templates.

Templates are firm-level configuration (welcome, annual review, tax organizer, missing documents,
insurance/retirement review, compliance notice, workflow/appointment reminder, campaign follow-up,
referral thank-you, document request). Rendering is a pure, deterministic ``{{placeholder}}``
substitution over a supplied context — no AI, no probabilistic generation. Managing templates
requires ``communications.manage_templates`` (enforced in-route); rendering is read-only.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import select

from app.database.communication_tables import COMMUNICATION_CATEGORIES, COMMUNICATION_CHANNELS
from app.db import communication_templates as tmpl
from app.db import engine

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class TemplateError(Exception):
    """Validation error for a communication template."""


def _now():
    return datetime.now(UTC)


def list_templates(*, active_only: bool = False, category: str | None = None) -> list[dict]:
    with engine.connect() as c:
        stmt = select(tmpl).order_by(tmpl.c.code)
        if active_only:
            stmt = stmt.where(tmpl.c.active.is_(True))
        if category:
            stmt = stmt.where(tmpl.c.category == category)
        return [dict(r) for r in c.execute(stmt).mappings()]


def get_template(template_id: int | None = None, *, code: str | None = None) -> dict | None:
    with engine.connect() as c:
        if code is not None:
            row = c.execute(select(tmpl).where(tmpl.c.code == code)).mappings().first()
        else:
            row = c.execute(select(tmpl).where(tmpl.c.id == template_id)).mappings().first()
        return dict(row) if row else None


def create_template(*, code: str, name: str, body: str, category: str = "general",
                    channel: str = "email", subject: str | None = None,
                    description: str | None = None, tags=None, actor_user_id: int | None = None) -> dict:
    code = (code or "").strip()
    if not code or not (name or "").strip() or not (body or "").strip():
        raise TemplateError("code, name, and body are required")
    if category not in COMMUNICATION_CATEGORIES:
        raise TemplateError(f"invalid category {category!r}")
    if channel not in COMMUNICATION_CHANNELS:
        raise TemplateError(f"invalid channel {channel!r}")
    with engine.begin() as c:
        if c.scalar(select(tmpl.c.id).where(tmpl.c.code == code)) is not None:
            raise TemplateError(f"template code {code!r} already exists")
        row = c.execute(tmpl.insert().values(
            code=code, name=name.strip(), body=body, category=category, channel=channel,
            subject=subject, description=description, tags=tags, active=True,
            created_by_user_id=actor_user_id).returning(*tmpl.c)).mappings().one()
        return dict(row)


def update_template(template_id: int, *, name=None, body=None, subject=None, category=None,
                    channel=None, description=None, tags=None, active=None) -> dict:
    values: dict = {"updated_at": _now()}
    if name is not None:
        values["name"] = name.strip()
    if body is not None:
        values["body"] = body
    if subject is not None:
        values["subject"] = subject
    if description is not None:
        values["description"] = description
    if tags is not None:
        values["tags"] = tags
    if active is not None:
        values["active"] = bool(active)
    if category is not None:
        if category not in COMMUNICATION_CATEGORIES:
            raise TemplateError(f"invalid category {category!r}")
        values["category"] = category
    if channel is not None:
        if channel not in COMMUNICATION_CHANNELS:
            raise TemplateError(f"invalid channel {channel!r}")
        values["channel"] = channel
    with engine.begin() as c:
        if c.scalar(select(tmpl.c.id).where(tmpl.c.id == template_id)) is None:
            raise TemplateError("template not found")
        row = c.execute(tmpl.update().where(tmpl.c.id == template_id)
                        .values(**values).returning(*tmpl.c)).mappings().one()
        return dict(row)


def render(template: dict, context: dict | None = None) -> dict:
    """Deterministically render subject/body by substituting ``{{key}}`` placeholders from
    ``context``. Unknown placeholders are left blank (never invents content). Returns
    ``{"subject", "body", "channel"}``."""
    ctx = {k: ("" if v is None else str(v)) for k, v in (context or {}).items()}

    def _sub(text):
        if not text:
            return text
        return _PLACEHOLDER.sub(lambda m: ctx.get(m.group(1), ""), text)

    return {"subject": _sub(template.get("subject")), "body": _sub(template.get("body")),
            "channel": template.get("channel")}


def render_by_code(code: str, context: dict | None = None) -> dict | None:
    template = get_template(code=code)
    if template is None:
        return None
    return render(template, context)
