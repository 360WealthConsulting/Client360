from datetime import date, datetime, timezone

PRIORITY_WEIGHTS = {"low": 5, "normal": 20, "high": 45, "urgent": 70}
CLOSED_STATUSES = {"complete", "completed", "cancelled", "closed"}


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def sla_risk(item, now=None):
    now = now or datetime.now(timezone.utc)
    due = _aware(item.get("sla_due_at"))
    if not due or item.get("status") in CLOSED_STATUSES:
        return {"level": "none", "hours_remaining": None, "score": 0}
    hours = (due - now).total_seconds() / 3600
    level = "breached" if hours < 0 else "critical" if hours <= 8 else "warning" if hours <= 24 else "healthy"
    score = 100 if hours < 0 else 75 if hours <= 8 else 40 if hours <= 24 else 0
    return {"level": level, "hours_remaining": round(hours, 1), "score": score}


def priority_score(item, now=None):
    now = now or datetime.now(timezone.utc)
    score = PRIORITY_WEIGHTS.get(item.get("priority", "normal"), 20)
    due = item.get("due_date")
    if due:
        due_date = due.date() if isinstance(due, datetime) else due
        days = (due_date - now.date()).days
        score += 50 if days < 0 else 35 if days == 0 else max(0, 20 - days * 2)
    score += sla_risk(item, now)["score"]
    if item.get("status") == "blocked": score += 15
    return min(score, 200)


def capacity_metrics(items, available_minutes=480):
    open_items = [item for item in items if item.get("status") not in CLOSED_STATUSES]
    committed = sum(max(int(item.get("estimated_minutes") or 0), 0) for item in open_items)
    utilization = committed / available_minutes if available_minutes > 0 else 1.0
    return {
        "available_minutes": available_minutes, "committed_minutes": committed,
        "remaining_minutes": max(available_minutes - committed, 0),
        "utilization_percent": round(utilization * 100, 1),
        "capacity_score": max(0, round(100 - utilization * 100, 1)),
        "over_capacity": committed > available_minutes, "open_items": len(open_items),
    }


def queue_matches(item, criteria, today=None):
    today = today or date.today()
    if criteria.get("unassigned") and item.get("assigned"):
        return False
    if criteria.get("overdue") and not (
        item.get("due_date") and item["due_date"] < today and item.get("status") not in CLOSED_STATUSES
    ):
        return False
    for key in ("waiting_on", "status", "work_type", "entity_type", "category"):
        if key in criteria and item.get(key) != criteria[key]: return False
    severity = criteria.get("severity")
    if severity is not None:
        allowed = severity if isinstance(severity, (list, tuple)) else [severity]
        if item.get("severity") not in allowed: return False
    minimum = criteria.get("minimum_priority")
    if minimum and PRIORITY_WEIGHTS.get(item.get("priority"), 0) < PRIORITY_WEIGHTS.get(minimum, 0): return False
    return True


def queue_items(items, criteria):
    return [item for item in items if queue_matches(item, criteria)]


def daily_agenda(items, now=None):
    enriched = []
    for item in items:
        value = dict(item); value["priority_score"] = priority_score(value, now); value["sla_risk"] = sla_risk(value, now)
        enriched.append(value)
    return sorted(enriched, key=lambda item: (-item["priority_score"], item.get("due_date") or date.max, item.get("title") or item.get("name") or ""))


def bottlenecks(items):
    groups = {}
    for item in items:
        key = item.get("waiting_on") or ("blocked" if item.get("status") == "blocked" else None)
        if key: groups.setdefault(key, []).append(item)
    return sorted(
        ({"reason": key, "count": len(values), "oldest_due": min((v.get("due_date") for v in values if v.get("due_date")), default=None)} for key, values in groups.items()),
        key=lambda row: (-row["count"], row["reason"]),
    )
