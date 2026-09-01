from app.services.drake_document_owner import (
    FROZEN_DRAKE_DOCUMENT_IDS,
    candidate_return_matches_filename,
    classify_resolved_return,
    document_year,
    is_personal_return_type,
)


def return_row(**changes):
    row = {
        "taxpayer_identifier_hash": "tp_hash",
        "spouse_identifier_hash": "sp_hash",
        "taxpayer_first_name": "Natalie",
        "taxpayer_last_name": "Porter",
        "spouse_first_name": "Louden",
        "spouse_last_name": "Porter",
    }

    row.update(changes)
    return row


def test_document_year():
    assert document_year(
        "2024 NY-MSG (PORTER NATALIE and LOUDEN).pdf"
    ) == 2024

    assert document_year(
        "Tax Return Without Year.pdf"
    ) is None


def test_filename_binds_porter_joint_return():
    assert candidate_return_matches_filename(
        "2024 NY-MSG (PORTER NATALIE and LOUDEN).pdf",
        return_row(),
    )


def test_filename_rejects_wrong_people():
    assert not candidate_return_matches_filename(
        "2024 Tax Return Documents (SMITH JOHN and JANE).pdf",
        return_row(),
    )


def test_filename_allows_safe_given_name_truncation():
    row = return_row(
        taxpayer_first_name="Rodney",
        taxpayer_last_name="Lynch",
        spouse_first_name="Teresa",
        spouse_last_name="Lynch",
    )

    assert candidate_return_matches_filename(
        (
            "2021 Tax Return Documents "
            "(LYNCH RODNEY M and TERE).pdf"
        ),
        row,
    )


def test_joint_ssn_identities_resolve_shared_household():
    identities = {
        "tp_hash": {
            "primary_person_id": 7680,
        },
        "sp_hash": {
            "primary_person_id": 7981,
        },
    }

    people = {
        7680: {
            "id": 7680,
            "full_name": "NATALIE DISALVO",
            "household_id": 306,
        },
        7981: {
            "id": 7981,
            "full_name": "Louden Porter",
            "household_id": 306,
        },
    }

    households = {
        306: {
            "id": 306,
            "name": (
                "Natalie & Louden Porter Household"
            ),
        }
    }

    result = classify_resolved_return(
        return_row(),
        identities,
        people,
        households,
    )

    assert result["status"] == "resolved"
    assert result["entity_type"] == "household"
    assert result["entity_id"] == 306
    assert result["taxpayer_person_id"] == 7680
    assert result["spouse_person_id"] == 7981

    assert (
        result["reason"]
        == "drake_ssn_hash_joint_household"
    )


def test_married_name_change_preserves_canonical_person():
    identities = {
        "tp_hash": {
            "primary_person_id": 7680,
        },
        "sp_hash": {
            "primary_person_id": 7981,
        },
    }

    people = {
        7680: {
            "id": 7680,
            "full_name": "NATALIE DISALVO",
            "household_id": 306,
        },
        7981: {
            "id": 7981,
            "full_name": "Louden Porter",
            "household_id": 306,
        },
    }

    households = {
        306: {
            "id": 306,
            "name": (
                "Natalie & Louden Porter Household"
            ),
        }
    }

    result = classify_resolved_return(
        return_row(),
        identities,
        people,
        households,
    )

    assert result["taxpayer_person_id"] == 7680
    assert result["entity_id"] == 306


def test_joint_return_holds_when_spouse_not_canonical():
    identities = {
        "tp_hash": {
            "primary_person_id": 7680,
        },
        "sp_hash": {
            "primary_person_id": None,
        },
    }

    people = {
        7680: {
            "id": 7680,
            "full_name": "NATALIE DISALVO",
            "household_id": None,
        }
    }

    result = classify_resolved_return(
        return_row(),
        identities,
        people,
        {},
    )

    assert result["status"] == "hold"

    assert (
        result["reason"]
        == "joint_identity_not_fully_canonical"
    )


def test_joint_return_holds_if_households_conflict():
    identities = {
        "tp_hash": {
            "primary_person_id": 10,
        },
        "sp_hash": {
            "primary_person_id": 20,
        },
    }

    people = {
        10: {
            "id": 10,
            "full_name": "Taxpayer",
            "household_id": 1,
        },
        20: {
            "id": 20,
            "full_name": "Spouse",
            "household_id": 2,
        },
    }

    result = classify_resolved_return(
        return_row(),
        identities,
        people,
        {},
    )

    assert result["status"] == "hold"

    assert (
        result["reason"]
        == "joint_people_household_conflict"
    )


def test_joint_return_holds_until_household_exists():
    identities = {
        "tp_hash": {
            "primary_person_id": 10,
        },
        "sp_hash": {
            "primary_person_id": 20,
        },
    }

    people = {
        10: {
            "id": 10,
            "full_name": "Taxpayer",
            "household_id": None,
        },
        20: {
            "id": 20,
            "full_name": "Spouse",
            "household_id": None,
        },
    }

    result = classify_resolved_return(
        return_row(),
        identities,
        people,
        {},
    )

    assert result["status"] == "hold"

    assert (
        result["reason"]
        == "joint_people_need_household"
    )


def test_single_taxpayer_resolves_person():
    row = return_row(
        spouse_identifier_hash=None,
        spouse_first_name=None,
        spouse_last_name=None,
    )

    identities = {
        "tp_hash": {
            "primary_person_id": 55,
        }
    }

    people = {
        55: {
            "id": 55,
            "full_name": "Single Taxpayer",
            "household_id": None,
        }
    }

    result = classify_resolved_return(
        row,
        identities,
        people,
        {},
    )

    assert result["status"] == "resolved"
    assert result["entity_type"] == "person"
    assert result["entity_id"] == 55


def test_single_taxpayer_existing_household_resolves_household():
    row = return_row(
        spouse_identifier_hash=None,
        spouse_first_name=None,
        spouse_last_name=None,
    )

    identities = {
        "tp_hash": {
            "primary_person_id": 55,
        }
    }

    people = {
        55: {
            "id": 55,
            "full_name": "Single Taxpayer",
            "household_id": 77,
        }
    }

    households = {
        77: {
            "id": 77,
            "name": "Taxpayer Household",
        }
    }

    result = classify_resolved_return(
        row,
        identities,
        people,
        households,
    )

    assert result["status"] == "resolved"
    assert result["entity_type"] == "household"
    assert result["entity_id"] == 77


def test_personal_return_type_gate():
    assert is_personal_return_type("1040")
    assert is_personal_return_type("1040X")
    assert is_personal_return_type("1040-NR")
    assert is_personal_return_type("Individual")

    assert not is_personal_return_type("1120")
    assert not is_personal_return_type("1120S")
    assert not is_personal_return_type("1065")
    assert not is_personal_return_type("1041")
    assert not is_personal_return_type("")
    assert not is_personal_return_type(None)


def test_business_return_types_are_not_personal():
    for return_type in (
        "1120",
        "1120S",
        "1065",
        "1041",
    ):
        assert not is_personal_return_type(
            return_type
        )



def test_melunis_documents_are_explicitly_frozen():
    assert 121627 in FROZEN_DRAKE_DOCUMENT_IDS
    assert 121628 in FROZEN_DRAKE_DOCUMENT_IDS


def test_unrelated_document_not_in_frozen_set():
    assert 121809 not in FROZEN_DRAKE_DOCUMENT_IDS
