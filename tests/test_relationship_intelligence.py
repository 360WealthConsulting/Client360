from sqlalchemy import insert, select

from app.db import (
    engine,
    household_relationships,
    households,
    people,
    relationships,
)
from app.services.advisor_ai import build_advisor_recommendations
from app.services.relationships import (
    build_relationship_graph_from_rows,
    create_relationship,
    filter_relationship_graphs,
)


def test_graph_generation_groups_relationship_categories():
    graph = build_relationship_graph_from_rows(
        1,
        [
            {
                "id": 10,
                "code": "spouse",
                "type_name": "Spouse",
                "inverse_name": "Spouse",
                "category": "family",
                "from_person_id": 1,
                "from_name": "Michael",
                "from_id": 1,
                "to_id": 2,
                "to_person_id": 2,
                "to_household_id": None,
                "to_entity_type": "person",
                "to_name": "Alex",
                "notes": None,
                "confidence_level": 100,
                "source": "manual",
            }
        ],
    )

    assert graph["codes"] == {"spouse"}
    assert graph["categories"]["family"][0]["name"] == "Alex"


def test_search_filters_by_type_and_related_name():
    graph = {
        "person_id": 1,
        "relationships": [
            {"code": "cpa", "name": "John Smith", "label": "CPA"},
            {"code": "beneficiary", "name": "Faith", "label": "Beneficiary"},
        ],
    }

    results = filter_relationship_graphs(
        [graph], relationship_code="cpa", related_name="john"
    )

    assert results == [
        {
            "root_person_id": 1,
            "code": "cpa",
            "name": "John Smith",
            "label": "CPA",
        }
    ]


def test_relationship_crud_and_timeline_publication_integration():
    published = []

    with engine.connect() as connection:
        transaction = connection.begin()
        person_id = connection.execute(
            insert(people)
            .values(full_name="Relationship Test Client", active=True)
            .returning(people.c.id)
        ).scalar_one()
        target_id = connection.execute(
            insert(people)
            .values(full_name="Relationship Test Spouse", active=True)
            .returning(people.c.id)
        ).scalar_one()

        relationship_id = create_relationship(
            person_id=person_id,
            target_person_id=target_id,
            relationship_code="spouse",
            connection=connection,
            publisher=lambda **values: published.append(values),
        )
        row = connection.execute(
            select(relationships).where(relationships.c.id == relationship_id)
        ).mappings().one()

        assert row["active"] is True
        assert published[0]["event_type"] == "relationship_added"
        assert published[0]["person_id"] == person_id
        transaction.rollback()


def test_multiple_households_and_primary_household_integration():
    with engine.connect() as connection:
        transaction = connection.begin()
        person_id = connection.execute(
            insert(people)
            .values(full_name="Multi Household Test", active=True)
            .returning(people.c.id)
        ).scalar_one()
        household_ids = [
            connection.execute(
                insert(households)
                .values(name=name)
                .returning(households.c.id)
            ).scalar_one()
            for name in ("Primary Test Household", "Blended Test Household")
        ]
        connection.execute(
            insert(household_relationships),
            [
                {
                    "person_id": person_id,
                    "household_id": household_ids[0],
                    "relationship_type": "member",
                    "is_primary": True,
                    "is_primary_household": True,
                },
                {
                    "person_id": person_id,
                    "household_id": household_ids[1],
                    "relationship_type": "adult_child",
                    "is_primary": False,
                    "is_primary_household": False,
                },
            ],
        )
        rows = connection.execute(
            select(household_relationships).where(
                household_relationships.c.person_id == person_id
            )
        ).mappings().all()

        assert len(rows) == 2
        assert sum(bool(row["is_primary_household"]) for row in rows) == 1
        transaction.rollback()


def test_relationship_aware_advisor_recommendations():
    recommendations = build_advisor_recommendations(
        {
            "overdue_task_count": 0,
            "days_since_last_contact": 1,
            "document_count": 1,
            "activity_count": 1,
        },
        {
            "codes": {"spouse", "child", "owner"},
            "relationships": [
                {"entity_type": "trust", "name": "Family Trust"}
            ],
        },
    )

    assert "Record the client's CPA relationship." in recommendations
    assert "Review missing child beneficiary designations." in recommendations
    assert "Review the business owner's buy-sell agreement coverage." in recommendations
    assert "Record a successor trustee for the trust." in recommendations
