"""Authenticated browser flows via the development-only sign-in provider.

Signs in as the Administrator persona (full RBAC) through /dev-auth and exercises the
core staff surface end to end in a real browser: dashboard, people, households, search,
profile, notes, tasks, and the communication quick actions.
"""
from __future__ import annotations


def test_dev_login_establishes_a_session(app_page, live_server):
    # app_page is already signed in; landing must not be the IdP login page.
    assert "/auth/login" not in app_page.url


def test_dashboard_renders(app_page, live_server):
    response = app_page.goto(f"{live_server}/")
    assert response is not None and response.ok
    assert "/auth/login" not in app_page.url


def test_people_directory_renders(app_page, live_server):
    response = app_page.goto(f"{live_server}/people")
    assert response is not None and response.ok


def test_households_page_renders(app_page, live_server):
    response = app_page.goto(f"{live_server}/households")
    assert response is not None and response.ok


def test_search_finds_seeded_client(app_page, live_server, seeded_client):
    app_page.goto(f"{live_server}/search?q={seeded_client['tag']}")
    assert seeded_client["name"] in app_page.content()


def test_client_profile_renders(app_page, live_server, seeded_client):
    response = app_page.goto(f"{live_server}/people/{seeded_client['person_id']}")
    assert response is not None and response.ok
    assert seeded_client["name"] in app_page.content()


def test_notes_page_renders(app_page, live_server, seeded_client):
    response = app_page.goto(f"{live_server}/people/{seeded_client['person_id']}/notes")
    assert response is not None and response.ok
    assert "Permanent client note" in app_page.content()


def test_tasks_page_renders(app_page, live_server, seeded_client):
    response = app_page.goto(f"{live_server}/people/{seeded_client['person_id']}/tasks")
    assert response is not None and response.ok


def test_communication_quick_actions_present(app_page, live_server, seeded_client):
    app_page.goto(f"{live_server}/people/{seeded_client['person_id']}")
    content = app_page.content()
    assert "Log Call" in content and "Log Email" in content and "Log Meeting" in content
