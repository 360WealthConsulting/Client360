from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")

def test_vault_does_not_render_second_document_request_surface():
    html = _read("app/templates/portal/documents.html")
    assert "Requested from you" not in html
    assert "/portal/upload?request_id={{ req.id }}" not in html

def test_vault_keeps_general_secure_upload():
    html = _read("app/templates/portal/documents.html")
    assert 'href="/portal/upload"' in html
    assert "Shared &amp; uploaded" in html

def test_document_requests_keep_canonical_request_upload():
    html = _read("app/templates/portal/requests.html")
    assert "Document Requests" in html
    assert '/portal/upload?request_id={{ row.id }}' in html

def test_dashboard_routes_todo_to_action_needed():
    html = _read("app/templates/portal/dashboard.html")
    assert 'href="/portal/action-needed"' in html
    assert "portal.document_requests|length" in html

def test_dashboard_vault_routes_only_to_documents():
    html = _read("app/templates/portal/dashboard.html")
    assert 'href="/portal/documents"' in html
    assert "<span>Vault</span>" in html

def test_portal_navigation_has_single_vault_surface():
    html = _read("app/templates/portal/base.html")
    assert "('/portal/documents', 'Vault')" in html
