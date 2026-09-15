from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def _html():
    return (
        ROOT / "app/templates/admin/client_portal.html"
    ).read_text(encoding="utf-8")


def test_invite_client_remains_primary_surface():
    html = _html()

    assert '<h2 class="section-title">Invite a client</h2>' in html
    assert 'id="invite-form"' in html
    assert 'action="/admin/client-portal/invite-form"' in html
    assert "Send portal invitation" in html


def test_portal_access_choices_are_progressively_disclosed():
    html = _html()

    assert (
        '<details class="portal-invite-full portal-access-details">'
        in html
    )
    assert "<summary>Portal access</summary>" in html
    assert 'name="access_type"' in html
    assert "access_choices" in html


def test_add_new_client_workflow_is_preserved():
    html = _html()

    assert 'id="add-client-button"' in html
    assert "Add New Client" in html
    assert 'id="add-client-form"' in html
    assert 'action="/admin/client-portal/create-client"' in html
    assert "Creating a client does not send an invitation." in html


def test_activation_security_workflow_is_preserved():
    html = _html()

    assert "activation.url" in html
    assert "sensitive, single-use invitation credential" in html
    assert "shown once, right now" in html


def test_duplicate_client_resolution_is_preserved():
    html = _html()

    assert "Possible existing clients found" in html
    assert "Use this client" in html
    assert "Create separate client anyway" in html


def test_revoke_workflow_is_preserved():
    html = _html()

    assert (
        '/admin/client-portal/accounts/{{ a.id }}/revoke'
        in html
    )
    assert "Revoke portal access" in html


def test_employee_invitation_surface_remains_separate():
    html = _html()

    assert "/admin/employees/invite" not in html
