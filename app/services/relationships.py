from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import (
    engine,
    households,
    household_relationships,
    people,
    relationship_entities,
    relationship_types,
    relationships,
)
from app.services.timeline import add_timeline_event


def ensure_person_entity(connection, person_id: int) -> int:
    person = connection.execute(
        select(people.c.id, people.c.full_name).where(people.c.id == person_id)
    ).mappings().one_or_none()
    if person is None:
        raise ValueError("Person not found.")
    return connection.execute(
        pg_insert(relationship_entities)
        .values(
            entity_type="person",
            person_id=person_id,
            name=person["full_name"] or f"Person {person_id}",
        )
        .on_conflict_do_update(
            index_elements=[relationship_entities.c.person_id],
            set_={"name": person["full_name"] or f"Person {person_id}"},
        )
        .returning(relationship_entities.c.id)
    ).scalar_one()


def ensure_household_entity(connection, household_id: int) -> int:
    household = connection.execute(
        select(households.c.id, households.c.name).where(
            households.c.id == household_id
        )
    ).mappings().one_or_none()
    if household is None:
        raise ValueError("Household not found.")
    return connection.execute(
        pg_insert(relationship_entities)
        .values(
            entity_type="household",
            household_id=household_id,
            name=household["name"],
        )
        .on_conflict_do_update(
            index_elements=[relationship_entities.c.household_id],
            set_={"name": household["name"]},
        )
        .returning(relationship_entities.c.id)
    ).scalar_one()


def create_named_entity(connection, entity_type: str, name: str, details=None) -> int:
    if entity_type not in {"business", "trust", "estate", "professional"}:
        raise ValueError("Unsupported relationship entity type.")
    if not name.strip():
        raise ValueError("Entity name is required.")
    return connection.execute(
        insert(relationship_entities)
        .values(
            entity_type=entity_type,
            name=name.strip(),
            details=details or {},
        )
        .returning(relationship_entities.c.id)
    ).scalar_one()


def create_relationship(
    *,
    person_id: int,
    relationship_code: str,
    target_person_id: Optional[int] = None,
    target_entity_type: Optional[str] = None,
    target_name: Optional[str] = None,
    effective_date: Optional[date] = None,
    inactive_date: Optional[date] = None,
    notes: Optional[str] = None,
    confidence_level: float = 100,
    source: str = "manual",
    created_by: Optional[str] = None,
    connection=None,
    publisher: Callable[..., Any] = add_timeline_event,
) -> int:
    owns_connection = connection is None
    context = engine.begin() if owns_connection else None
    db = context.__enter__() if context else connection
    try:
        from_entity_id = ensure_person_entity(db, person_id)
        if target_person_id is not None:
            to_entity_id = ensure_person_entity(db, target_person_id)
        else:
            to_entity_id = create_named_entity(
                db, target_entity_type or "professional", target_name or ""
            )
        relationship_type = db.execute(
            select(relationship_types).where(
                relationship_types.c.code == relationship_code,
                relationship_types.c.active.is_(True),
            )
        ).mappings().one_or_none()
        if relationship_type is None:
            raise ValueError("Relationship type not found.")
        relationship_id = db.execute(
            pg_insert(relationships)
            .values(
                from_entity_id=from_entity_id,
                to_entity_id=to_entity_id,
                relationship_type_id=relationship_type["id"],
                effective_date=effective_date,
                inactive_date=inactive_date,
                notes=notes or None,
                confidence_level=confidence_level,
                source=source,
                created_by=created_by or None,
                active=inactive_date is None,
            )
            .on_conflict_do_update(
                constraint="uq_relationship_edge",
                set_={
                    "effective_date": effective_date,
                    "inactive_date": inactive_date,
                    "notes": notes or None,
                    "confidence_level": confidence_level,
                    "source": source,
                    "created_by": created_by or None,
                    "active": inactive_date is None,
                    "updated_at": datetime.now(timezone.utc),
                },
            )
            .returning(relationships.c.id)
        ).scalar_one()
        target = db.execute(
            select(relationship_entities).where(
                relationship_entities.c.id == to_entity_id
            )
        ).mappings().one()
        if context:
            context.__exit__(None, None, None)
            context = None
    except Exception as exc:
        if context:
            context.__exit__(type(exc), exc, exc.__traceback__)
        raise

    publisher(
        person_id=person_id,
        source="client360",
        event_type="relationship_added",
        title=f"Added {relationship_type['name'].lower()}",
        summary=target["name"],
        external_id=f"relationship-added-{relationship_id}-person-{person_id}",
        event_metadata={
            "relationship_id": relationship_id,
            "relationship_code": relationship_code,
            "target_entity_id": to_entity_id,
            "target_name": target["name"],
            "source": source,
            "confidence_level": confidence_level,
        },
    )
    return relationship_id


def deactivate_relationship(
    relationship_id: int,
    *,
    inactive_date: Optional[date] = None,
) -> bool:
    with engine.begin() as connection:
        row = connection.execute(
            select(
                relationships,
                relationship_entities.c.person_id,
                relationship_types.c.name.label("type_name"),
            )
            .select_from(
                relationships
                .join(
                    relationship_entities,
                    relationship_entities.c.id == relationships.c.from_entity_id,
                )
                .join(
                    relationship_types,
                    relationship_types.c.id == relationships.c.relationship_type_id,
                )
            )
            .where(relationships.c.id == relationship_id)
        ).mappings().one_or_none()
        if row is None:
            return False
        connection.execute(
            update(relationships)
            .where(relationships.c.id == relationship_id)
            .values(
                active=False,
                inactive_date=inactive_date or date.today(),
                updated_at=datetime.now(timezone.utc),
            )
        )
    if row["person_id"]:
        add_timeline_event(
            person_id=row["person_id"],
            source="client360",
            event_type="relationship_updated",
            title=f"{row['type_name']} relationship ended",
            external_id=(
                f"relationship-ended-{relationship_id}-person-{row['person_id']}"
            ),
            event_metadata={"relationship_id": relationship_id},
        )
    return True


def _relationship_rows(person_id: int, include_inactive: bool = False):
    from_entity = relationship_entities.alias("from_entity")
    to_entity = relationship_entities.alias("to_entity")
    statement = (
        select(
            relationships,
            relationship_types.c.code,
            relationship_types.c.name.label("type_name"),
            relationship_types.c.inverse_name,
            relationship_types.c.category,
            from_entity.c.id.label("from_id"),
            from_entity.c.person_id.label("from_person_id"),
            from_entity.c.name.label("from_name"),
            to_entity.c.id.label("to_id"),
            to_entity.c.person_id.label("to_person_id"),
            to_entity.c.household_id.label("to_household_id"),
            to_entity.c.entity_type.label("to_entity_type"),
            to_entity.c.name.label("to_name"),
        )
        .select_from(
            relationships
            .join(from_entity, from_entity.c.id == relationships.c.from_entity_id)
            .join(to_entity, to_entity.c.id == relationships.c.to_entity_id)
            .join(
                relationship_types,
                relationship_types.c.id == relationships.c.relationship_type_id,
            )
        )
        .where(
            or_(
                from_entity.c.person_id == person_id,
                to_entity.c.person_id == person_id,
            )
        )
    )
    if not include_inactive:
        statement = statement.where(relationships.c.active.is_(True))
    with engine.connect() as connection:
        return connection.execute(statement).mappings().all()


def build_relationship_graph(person_id: int) -> dict[str, Any]:
    graph = build_relationship_graph_from_rows(
        person_id,
        _relationship_rows(person_id),
    )
    memberships = get_person_households(person_id)
    for household in memberships:
        graph["categories"].setdefault("household", []).append(
            {
                "relationship_id": None,
                "code": "household_member",
                "label": household["relationship_type"].replace("_", " ").title(),
                "name": household["name"],
                "person_id": None,
                "household_id": household["id"],
                "entity_id": None,
                "entity_type": "household",
                "notes": None,
                "confidence_level": 100,
                "source": "household_membership",
            }
        )
    if memberships:
        graph["codes"].add("household_member")
    graph["relationships"] = [
        item
        for items in graph["categories"].values()
        for item in items
    ]
    return graph


def build_relationship_graph_from_rows(
    person_id: int,
    rows,
) -> dict[str, Any]:
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_codes: set[str] = set()
    for row in rows:
        outgoing = row["from_person_id"] == person_id
        item = {
            "relationship_id": row["id"],
            "code": row["code"],
            "label": row["type_name"] if outgoing else (row["inverse_name"] or row["type_name"]),
            "name": row["to_name"] if outgoing else row["from_name"],
            "person_id": row["to_person_id"] if outgoing else row["from_person_id"],
            "household_id": row["to_household_id"] if outgoing else None,
            "entity_id": row["to_id"] if outgoing else row["from_id"],
            "entity_type": row["to_entity_type"] if outgoing else "person",
            "notes": row["notes"],
            "confidence_level": row["confidence_level"],
            "source": row["source"],
        }
        categories[row["category"]].append(item)
        all_codes.add(row["code"])
    return {
        "person_id": person_id,
        "categories": dict(categories),
        "codes": all_codes,
        "relationships": [item for items in categories.values() for item in items],
    }


def filter_relationship_graphs(
    graphs,
    *,
    relationship_code: Optional[str] = None,
    related_name: Optional[str] = None,
):
    rows = []
    for graph in graphs:
        for item in graph["relationships"]:
            if relationship_code and item["code"] != relationship_code:
                continue
            if related_name and related_name.lower() not in item["name"].lower():
                continue
            rows.append({"root_person_id": graph["person_id"], **item})
    return rows


def get_person_households(person_id: int):
    with engine.connect() as connection:
        return connection.execute(
            select(
                households.c.id,
                households.c.name,
                household_relationships.c.relationship_type,
                household_relationships.c.is_primary,
                household_relationships.c.is_primary_household,
            )
            .select_from(
                household_relationships.join(
                    households,
                    households.c.id == household_relationships.c.household_id,
                )
            )
            .where(household_relationships.c.person_id == person_id)
            .order_by(
                household_relationships.c.is_primary_household.desc(),
                households.c.name,
            )
        ).mappings().all()


def search_relationships(
    *, relationship_code: Optional[str] = None, related_name: Optional[str] = None
):
    from_entity = relationship_entities.alias("search_from")
    to_entity = relationship_entities.alias("search_to")
    statement = (
        select(
            relationships.c.id,
            relationship_types.c.code,
            relationship_types.c.name.label("type_name"),
            relationship_types.c.inverse_name,
            from_entity.c.id.label("from_id"),
            from_entity.c.person_id.label("from_person_id"),
            from_entity.c.name.label("from_name"),
            to_entity.c.id.label("to_id"),
            to_entity.c.person_id.label("to_person_id"),
            to_entity.c.name.label("to_name"),
            relationships.c.source,
        )
        .select_from(
            relationships
            .join(from_entity, from_entity.c.id == relationships.c.from_entity_id)
            .join(to_entity, to_entity.c.id == relationships.c.to_entity_id)
            .join(
                relationship_types,
                relationship_types.c.id == relationships.c.relationship_type_id,
            )
        )
        .where(relationships.c.active.is_(True))
    )
    if relationship_code:
        statement = statement.where(
            relationship_types.c.code == relationship_code
        )
    if related_name:
        pattern = f"%{related_name.strip()}%"
        statement = statement.where(
            or_(
                from_entity.c.name.ilike(pattern),
                to_entity.c.name.ilike(pattern),
            )
        )
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()

    results = []
    for row in rows:
        if row["from_person_id"]:
            results.append(
                {
                    "root_person_id": row["from_person_id"],
                    "relationship_id": row["id"],
                    "code": row["code"],
                    "label": row["type_name"],
                    "name": row["to_name"],
                    "person_id": row["to_person_id"],
                    "entity_id": row["to_id"],
                    "source": row["source"],
                }
            )
        if row["to_person_id"]:
            results.append(
                {
                    "root_person_id": row["to_person_id"],
                    "relationship_id": row["id"],
                    "code": row["code"],
                    "label": row["inverse_name"] or row["type_name"],
                    "name": row["from_name"],
                    "person_id": row["from_person_id"],
                    "entity_id": row["from_id"],
                    "source": row["source"],
                }
            )
    return results
