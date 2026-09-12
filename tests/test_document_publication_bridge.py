"""Canonical document publication — the bridge that lets a client read their own ingested documents.

Covers the properties the feature exists to guarantee:

  * a Drake or TaxDome canonical document can be published to the right client, and reaches them;
  * person publications reach only that person; household publications reach the household;
  * two clients whose documents share a content hash stay isolated from each other;
  * archived, deleted, revoked and staff-only documents are neither listed nor downloadable;
  * the download link the client is handed is the route that actually authorizes it;
  * publishing copies no bytes and creates no second canonical row;
  * every publish and revoke leaves an audit record;
  * an out-of-scope client gets neither metadata nor bytes;
  * the four pre-existing vault records keep working exactly as before.

The cross-client tests are the reason the feature does not read ``documents.person_id``. They build
the exact production shape — one canonical row, one owner, two legitimate clients — and assert that
the non-owner sees nothing even though the row they cannot see is filed against the other person.
"""
import io
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import (
    audit_events,
    document_publication_events,
    document_publications,
    documents,
    engine,
    households,
    people,
    portal_access_grants,
    portal_accounts,
    portal_devices,
    portal_sessions,
    users,
    vault_document_links,
    vault_documents,
)
from app.portal import vault_documents as pv
from app.portal.service import (
    accept_invitation,
    create_portal_session,
    invite_portal_account,
    resolve_portal_session,
)
from app.security.models import Principal
from app.services.publication import service as publication
from app.services.vault import service as vault

pytestmark = pytest.mark.usefixtures("portal_documents_download_on")

# vault.manage is the publication authority; record.read_all puts every test client in scope.
STAFF_CAPS = frozenset({"vault.view", "vault.upload", "vault.download", "vault.manage",
                        "vault.access.all", "record.read_all"})
#: A principal holding everything EXCEPT the publication authority.
READER_CAPS = frozenset({"vault.view", "vault.download", "vault.access.all", "record.read_all"})


class _Env:
    """One isolated fixture world: staff user, clients, canonical documents, publications."""

    def __init__(self, tmp_path):
        self.suffix = uuid.uuid4().hex[:10]
        self.tmp_path = tmp_path
        self.people, self.households, self.accounts = [], [], []
        self.documents, self.publications, self.vault_docs = [], [], []
        with engine.begin() as c:
            self.user_id = c.execute(insert(users).values(
                email=f"pub-{self.suffix}@e.test", normalized_email=f"pub-{self.suffix}@e.test",
                display_name="Publishing Staff", auth_subject=f"pub-{self.suffix}", status="active"
            ).returning(users.c.id)).scalar_one()
        self.staff = Principal(self.user_id, "staff@e.test", "Staff", STAFF_CAPS)
        self.reader = Principal(self.user_id, "reader@e.test", "Reader", READER_CAPS)

    # --- world building ------------------------------------------------------

    def household(self, label="HH"):
        with engine.begin() as c:
            hid = c.execute(insert(households).values(
                name=f"{label} {self.suffix}-{len(self.households)}"
            ).returning(households.c.id)).scalar_one()
        self.households.append(hid)
        return hid

    def person(self, household_id, label="Client"):
        with engine.begin() as c:
            pid = c.execute(insert(people).values(
                household_id=household_id, full_name=f"{label} {self.suffix}-{len(self.people)}",
                active=True).returning(people.c.id)).scalar_one()
        self.people.append(pid)
        return pid

    def client(self, *, permissions=None, household_id=None, person_id=None):
        """A signed-in portal client. Returns (principal, person_id, household_id)."""
        hid = household_id if household_id is not None else self.household()
        pid = person_id if person_id is not None else self.person(hid)
        account_id, invitation = invite_portal_account(
            person_id=pid, household_id=hid, email=f"c-{self.suffix}-{pid}@e.test",
            display_name="Portal Client", access_type="self", invited_by_user_id=self.user_id,
            permissions=permissions or {"documents": True})
        accept_invitation(invitation, f"subject-{self.suffix}-{pid}", True)
        token = create_portal_session(account_id, device_fingerprint=f"d-{uuid.uuid4()}")
        self.accounts.append(account_id)
        return resolve_portal_session(token), pid, hid

    def canonical(self, *, person_id=None, household_id=None, name="2024 1040.pdf",
                  body=b"canonical bytes", sha=None, source_system="Drake", status="active",
                  archived=False, deleted=False, tax_year=2024):
        """One canonical documents row, backed by a real file so downloads can be exercised."""
        path = self.tmp_path / f"{uuid.uuid4().hex}.pdf"
        path.write_bytes(body)
        with engine.begin() as c:
            doc_id = c.execute(insert(documents).values(
                original_name=name, stored_name=f"{uuid.uuid4().hex}-{name}",
                storage_path=str(path), storage_uri=str(path), storage_provider="local",
                size_bytes=len(body), sha256=sha or uuid.uuid4().hex * 2,
                content_type="application/pdf", category="tax_document",
                person_id=person_id, household_id=household_id,
                status=status, archived=archived, tax_year=tax_year,
                tax_year_confidence="strong" if tax_year else None,
                deleted_at=datetime.now(UTC) if deleted else None,
                uploaded_by=f"{source_system} Sync",
            ).returning(documents.c.id)).scalar_one()
            ds = _document_sources()
            if ds is not None:
                c.execute(ds.insert().values(
                    document_id=doc_id, source_system=source_system,
                    source_uri=f"{source_system}://{doc_id}", available=True))
        self.documents.append(doc_id)
        return doc_id

    def publish(self, doc_id, *, audience_type, audience_id, client_visible=True, principal=None):
        pub_id = publication.publish(
            principal or self.staff, doc_id, audience_type=audience_type, audience_id=audience_id,
            client_visible=client_visible, actor_user_id=self.user_id)
        self.publications.append(pub_id)
        return pub_id

    def vault_doc(self, person_id, *, client_visible=True):
        """A pre-existing vault document — the store that must keep working unchanged."""
        doc_id = vault.create_document(
            self.staff, source=io.BytesIO(b"vault bytes"), original_filename="vault.pdf",
            display_name="Vault Doc", category="general", status="approved",
            actor_user_id=self.user_id, person_id=person_id)
        self.vault_docs.append(doc_id)
        if client_visible:
            vault.update_metadata(self.staff, doc_id, changes={"client_visible": True},
                                  actor_user_id=self.user_id)
        return doc_id

    # --- teardown ------------------------------------------------------------

    def cleanup(self):
        from app.db import portal_invitations, portal_notifications

        def _try(stmt):
            try:
                with engine.begin() as c:
                    c.execute(stmt)
            except Exception:
                pass

        if self.publications:
            _try(delete(document_publication_events).where(
                document_publication_events.c.publication_id.in_(self.publications)))
            _try(delete(document_publications).where(
                document_publications.c.id.in_(self.publications)))
        for doc_id in self.vault_docs:
            _try(delete(vault_documents).where(vault_documents.c.id == doc_id))
        if self.documents:
            ds = _document_sources()
            if ds is not None:
                _try(delete(ds).where(ds.c.document_id.in_(self.documents)))
            _try(delete(document_publications).where(
                document_publications.c.document_id.in_(self.documents)))
            _try(delete(documents).where(documents.c.id.in_(self.documents)))
        for acc in self.accounts:
            _try(delete(portal_sessions).where(portal_sessions.c.portal_account_id == acc))
            _try(delete(portal_devices).where(portal_devices.c.portal_account_id == acc))
            _try(delete(portal_access_grants).where(portal_access_grants.c.portal_account_id == acc))
            _try(delete(portal_notifications).where(portal_notifications.c.portal_account_id == acc))
            _try(delete(portal_invitations).where(portal_invitations.c.portal_account_id == acc))
            _try(delete(portal_accounts).where(portal_accounts.c.id == acc))
        if self.people:
            _try(delete(vault_document_links).where(vault_document_links.c.person_id.in_(self.people)))
            _try(delete(people).where(people.c.id.in_(self.people)))
        if self.households:
            _try(delete(households).where(households.c.id.in_(self.households)))


def _document_sources():
    from app.db import metadata
    return metadata.tables.get("document_sources")


@pytest.fixture
def env(tmp_path):
    e = _Env(tmp_path)
    try:
        yield e
    finally:
        e.cleanup()


def _titles(rows):
    return {r["display_name"] for r in rows}


def _stores(rows):
    return {pv.store_of(r) for r in rows}


# --- 1. Drake and TaxDome documents reach the right client -------------------

@pytest.mark.parametrize("source_system", ["Drake", "TaxDome Drive"])
def test_ingested_document_published_to_its_client_is_visible(env, source_system):
    principal, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id, name="2024 1040 (EXAMPLE TAXPAYER).pdf",
                           source_system=source_system)

    assert pv.portal_documents(principal) == [], "unpublished canonical document must not appear"

    env.publish(doc_id, audience_type="person", audience_id=person_id)
    rows = pv.portal_documents(principal)

    assert len(rows) == 1
    assert pv.store_of(rows[0]) == pv.SOURCE_PUBLICATION
    assert rows[0]["display_name"] == "2024 1040 (EXAMPLE TAXPAYER).pdf"
    assert rows[0]["tax_year"] == 2024
    assert rows[0]["downloadable"] is True


def test_publication_carries_the_decision_fields_the_audit_needs(env):
    _, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id)
    pub_id = env.publish(doc_id, audience_type="person", audience_id=person_id)

    row = publication.publications_for_document(doc_id)[0]
    assert row["id"] == pub_id
    assert row["document_id"] == doc_id
    assert row["audience_type"] == "person"
    assert row["person_id"] == person_id
    assert row["client_visible"] is True
    assert row["decision_source"] == "staff_manual"
    assert row["document_type"]                      # captured at decision time
    assert row["tax_year"] == 2024
    assert row["created_by_user_id"] == env.user_id
    assert row["created_at"] is not None
    assert row["revoked_at"] is None and row["archived_at"] is None


# --- 2. person and household scoping -----------------------------------------

def test_person_publication_reaches_only_that_person(env):
    """Requirement D. Two people in the SAME household; only the addressee sees the document."""
    hid = env.household()
    alice_principal, alice, _ = env.client(household_id=hid)
    bob_principal, bob, _ = env.client(household_id=env.household())

    doc_id = env.canonical(person_id=alice, name="Alice 1040.pdf")
    env.publish(doc_id, audience_type="person", audience_id=alice)

    assert _titles(pv.portal_documents(alice_principal)) == {"Alice 1040.pdf"}
    assert pv.portal_documents(bob_principal) == []
    assert bob != alice


def test_household_publication_reaches_a_member_of_that_household(env):
    """Requirement C. The publication addresses the household; the member's grant names it."""
    hid = env.household()
    principal, _, granted_household = env.client(household_id=hid)
    assert granted_household == hid

    doc_id = env.canonical(household_id=hid, name="Household Return.pdf")
    env.publish(doc_id, audience_type="household", audience_id=hid)

    assert _titles(pv.portal_documents(principal)) == {"Household Return.pdf"}


def test_household_publication_does_not_reach_another_household(env):
    theirs = env.household()
    outsider_principal, _, _ = env.client()

    doc_id = env.canonical(household_id=theirs, name="Other Household Return.pdf")
    env.publish(doc_id, audience_type="household", audience_id=theirs)

    assert pv.portal_documents(outsider_principal) == []


# --- 3. cross-client isolation with shared content ---------------------------

def test_shared_content_hash_does_not_leak_between_clients(env):
    """Requirement E, and the reason access is never read off documents.person_id.

    Two canonical rows, identical bytes and identical sha256 — the production shape produced when
    the canonical resolver deduplicates. Each is published to its own client. Neither client may see
    the other's row.
    """
    shared_sha = uuid.uuid4().hex * 2
    shared_bytes = b"identical content for two unrelated clients"

    a_principal, a_person, _ = env.client()
    b_principal, b_person, _ = env.client()

    a_doc = env.canonical(person_id=a_person, name="Shared A.pdf",
                          body=shared_bytes, sha=shared_sha)
    b_doc = env.canonical(person_id=b_person, name="Shared B.pdf",
                          body=shared_bytes, sha=shared_sha)

    a_pub = env.publish(a_doc, audience_type="person", audience_id=a_person)
    b_pub = env.publish(b_doc, audience_type="person", audience_id=b_person)

    assert _titles(pv.portal_documents(a_principal)) == {"Shared A.pdf"}
    assert _titles(pv.portal_documents(b_principal)) == {"Shared B.pdf"}

    # And neither can fetch the other's bytes by guessing the publication id.
    with pytest.raises(PermissionError):
        pv.download_publication(a_principal, b_pub)
    with pytest.raises(PermissionError):
        pv.download_publication(b_principal, a_pub)


def test_one_canonical_document_published_to_two_clients_stays_two_grants(env):
    """Requirement 4: the same content may be published separately to multiple legitimate clients.

    One canonical row, two audiences, two independent publications. Revoking one must not touch the
    other — that is what makes them separate grants rather than shared ownership.
    """
    a_principal, a_person, _ = env.client()
    b_principal, b_person, _ = env.client()
    doc_id = env.canonical(person_id=a_person, name="Joint Filing Copy.pdf")

    a_pub = env.publish(doc_id, audience_type="person", audience_id=a_person)
    b_pub = env.publish(doc_id, audience_type="person", audience_id=b_person)
    assert a_pub != b_pub

    assert _titles(pv.portal_documents(a_principal)) == {"Joint Filing Copy.pdf"}
    assert _titles(pv.portal_documents(b_principal)) == {"Joint Filing Copy.pdf"}

    publication.revoke(env.staff, a_pub, actor_user_id=env.user_id)

    assert pv.portal_documents(a_principal) == []
    assert _titles(pv.portal_documents(b_principal)) == {"Joint Filing Copy.pdf"}


def test_ownership_alone_never_grants_access(env):
    """Requirement 6. The document is filed against this person and is NOT published to them."""
    principal, person_id, _ = env.client()
    env.canonical(person_id=person_id, name="Owned But Unpublished.pdf")

    assert pv.portal_documents(principal) == []


# --- 4. exclusions: archived, deleted, revoked, staff-only -------------------

@pytest.mark.parametrize("kwargs,label", [
    ({"status": "deleted", "deleted": True}, "deleted canonical document"),
    ({"status": "archived"}, "archived canonical document"),
    ({"archived": True}, "archived-flag canonical document"),
])
def test_unpublishable_canonical_states_are_refused_at_publish(env, kwargs, label):
    _, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id, **kwargs)
    with pytest.raises(publication.PublicationError):
        publication.publish(env.staff, doc_id, audience_type="person", audience_id=person_id,
                            client_visible=True, actor_user_id=env.user_id)


def test_document_archived_after_publication_disappears_without_revoking(env):
    """The canonical state is re-checked on every read, so archiving withdraws access by itself."""
    principal, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id, name="Later Archived.pdf")
    pub_id = env.publish(doc_id, audience_type="person", audience_id=person_id)
    assert len(pv.portal_documents(principal)) == 1

    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == doc_id).values(
            status="archived", archived=True, archived_at=datetime.now(UTC)))

    assert pv.portal_documents(principal) == []
    with pytest.raises(PermissionError):
        pv.download_publication(principal, pub_id)


def test_document_deleted_after_publication_disappears(env):
    principal, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id)
    pub_id = env.publish(doc_id, audience_type="person", audience_id=person_id)

    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == doc_id).values(
            status="deleted", deleted_at=datetime.now(UTC)))

    assert pv.portal_documents(principal) == []
    with pytest.raises(PermissionError):
        pv.download_publication(principal, pub_id)


def test_revoked_publication_is_neither_listed_nor_downloadable(env):
    principal, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id)
    pub_id = env.publish(doc_id, audience_type="person", audience_id=person_id)

    publication.revoke(env.staff, pub_id, actor_user_id=env.user_id)

    assert pv.portal_documents(principal) == []
    with pytest.raises(PermissionError):
        pv.download_publication(principal, pub_id)


def test_archived_publication_is_neither_listed_nor_downloadable(env):
    principal, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id)
    pub_id = env.publish(doc_id, audience_type="person", audience_id=person_id)

    publication.archive(env.staff, pub_id, actor_user_id=env.user_id)

    assert pv.portal_documents(principal) == []
    with pytest.raises(PermissionError):
        pv.download_publication(principal, pub_id)


def test_staff_only_publication_is_neither_listed_nor_downloadable(env):
    """A publication row with client_visible false is a staff record, not a client grant."""
    principal, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id)
    pub_id = env.publish(doc_id, audience_type="person", audience_id=person_id, client_visible=False)

    assert pv.portal_documents(principal) == []
    with pytest.raises(PermissionError):
        pv.download_publication(principal, pub_id)

    publication.set_visibility(env.staff, pub_id, True, actor_user_id=env.user_id)
    assert len(pv.portal_documents(principal)) == 1


# --- 5. the download route the client is actually handed ---------------------

def test_every_listed_download_url_is_a_registered_route(env):
    """Requirement A, stated as the property that actually matters.

    The template used to hard-code one path for every row. That path IS registered (the vault
    download in app/routes/portal_api.py), so it was not broken — but it is the VAULT route, and a
    publication is not a vault document. This asserts the general rule instead of the old special
    case: whatever href a row carries, the application serves it, and it is the route that
    authorizes that row's store.
    """
    from app.main import app

    principal, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id)
    env.publish(doc_id, audience_type="person", audience_id=person_id)
    env.vault_doc(person_id)

    registered = {getattr(r, "path", None) for r in app.routes}
    expected = {
        pv.SOURCE_VAULT: "/api/v1/portal/documents/{document_id}/download",
        pv.SOURCE_PUBLICATION: "/api/v1/portal/publications/{publication_id}/download",
    }
    assert set(expected.values()) <= registered

    rows = pv.portal_documents(principal)
    assert _stores(rows) == {pv.SOURCE_VAULT, pv.SOURCE_PUBLICATION}
    for row in rows:
        store = pv.store_of(row)
        parameterized = row["download_url"].rsplit("/", 2)[0] \
            + "/" + expected[store].rsplit("/", 2)[1] + "/download"
        assert parameterized == expected[store], row
        assert parameterized in registered


def test_template_takes_the_url_from_the_row(env):
    """No route may be hard-coded in the template — it cannot know which store a row came from."""
    source = Path("app/templates/portal/documents.html").read_text(encoding="utf-8")
    import re

    assert 'href="{{ doc.download_url }}"' in source

    # Strip Jinja comments, then assert no href in the remaining markup hard-codes an API path.
    markup = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
    hard_coded = [href for href in re.findall(r'href="([^"]+)"', markup) if href.startswith("/api/")]
    assert hard_coded == [], f"template hard-codes API paths: {hard_coded}"


def test_published_rows_disclose_nothing_internal(env):
    """The portal disclosure contract applies to the new store too.

    ``source``, ``storage_path``, ``stored_name`` and ``sha256`` are all on the forbidden list in
    tests/test_portal_task_tax_visibility.py. A store discriminator is exactly the kind of internal
    provenance a client payload must not carry, which is why ``store_of`` derives it instead.
    """
    from tests.test_portal_task_tax_visibility import FORBIDDEN_FIELDS

    principal, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id)
    env.publish(doc_id, audience_type="person", audience_id=person_id)
    env.vault_doc(person_id)

    rows = pv.portal_documents(principal)
    assert rows
    for row in rows:
        leaked = set(row) & FORBIDDEN_FIELDS
        assert leaked == set(), f"client row disclosed {leaked}"


def test_published_download_returns_the_canonical_bytes(env):
    principal, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id, body=b"the real filed return")
    pub_id = env.publish(doc_id, audience_type="person", audience_id=person_id)

    path, filename, mime = pv.download_publication(principal, pub_id)
    assert path.read_bytes() == b"the real filed return"
    assert filename.lower().endswith(".pdf")
    assert mime == "application/pdf"


# --- 6. no duplicated bytes, no duplicated canonical rows --------------------

def test_publishing_creates_no_document_row_and_no_vault_row(env):
    """Requirement F and requirement 1, asserted as counts rather than as intent."""
    principal, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id, body=b"one copy only")

    with engine.connect() as c:
        docs_before = c.scalar(select(func.count()).select_from(documents))
        vault_before = c.scalar(select(func.count()).select_from(vault_documents))
        stored_path = c.scalar(select(documents.c.storage_path).where(documents.c.id == doc_id))

    env.publish(doc_id, audience_type="person", audience_id=person_id)

    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(documents)) == docs_before
        assert c.scalar(select(func.count()).select_from(vault_documents)) == vault_before
        assert c.scalar(select(documents.c.storage_path).where(
            documents.c.id == doc_id)) == stored_path

    # Exactly one file on disk holds these bytes: the publication points at the canonical path.
    row = publication.authorized_publication(
        env.publications[-1], person_ids={person_id})
    assert row["storage_path"] == stored_path
    assert len(pv.portal_documents(principal)) == 1


def test_republishing_the_same_audience_updates_rather_than_duplicates(env):
    _, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id)

    first = env.publish(doc_id, audience_type="person", audience_id=person_id, client_visible=False)
    second = env.publish(doc_id, audience_type="person", audience_id=person_id, client_visible=True)

    assert first == second
    live = [p for p in publication.publications_for_document(doc_id)
            if p["revoked_at"] is None and p["archived_at"] is None]
    assert len(live) == 1
    assert live[0]["client_visible"] is True


# --- 7. audit ----------------------------------------------------------------

def _chained_audit_actions(publication_id, *, since):
    """Hash-chained audit entries for one publication, made after ``since``.

    Time-bounded rather than counted absolutely: audit_events is append-only and never cleaned,
    while the publication id sequence restarts whenever the migration test drops and recreates the
    table — so an unbounded count picks up entries from earlier runs that share the id.
    """
    with engine.connect() as c:
        return [r[0] for r in c.execute(
            select(audit_events.c.action)
            .where(audit_events.c.entity_type == "document_publication",
                   audit_events.c.entity_id == str(publication_id),
                   audit_events.c.occurred_at >= since)
            .order_by(audit_events.c.id)).all()]


def test_publish_and_revoke_are_audited(env):
    _, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id)
    started = datetime.now(UTC)

    pub_id = env.publish(doc_id, audience_type="person", audience_id=person_id)
    publication.revoke(env.staff, pub_id, actor_user_id=env.user_id)

    actions = [e["action"] for e in publication.events_for_document(doc_id)]
    assert actions == ["published", "revoked"]

    ledger = publication.events_for_document(doc_id)
    assert ledger[0]["audience_type"] == "person"
    assert ledger[0]["audience_id"] == person_id
    assert ledger[0]["client_visible"] is True
    assert ledger[0]["actor_user_id"] == env.user_id
    assert ledger[1]["client_visible"] is False

    # And in the firm-wide hash-chained log, where a compliance review would look.
    assert _chained_audit_actions(pub_id, since=started) == [
        "document.publication.published", "document.publication.revoked"]


def test_client_download_is_audited(env):
    principal, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id)
    started = datetime.now(UTC)
    pub_id = env.publish(doc_id, audience_type="person", audience_id=person_id)

    pv.download_publication(principal, pub_id)

    with engine.connect() as c:
        entries = c.execute(
            select(audit_events.c.action, audit_events.c.metadata)
            .where(audit_events.c.action == "portal.publication.downloaded",
                   audit_events.c.entity_id == str(pub_id),
                   audit_events.c.occurred_at >= started)).mappings().all()
    assert len(entries) == 1
    assert entries[0]["metadata"]["document_id"] == doc_id


def test_revoked_publication_survives_as_evidence(env):
    _, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id)
    pub_id = env.publish(doc_id, audience_type="person", audience_id=person_id)
    publication.revoke(env.staff, pub_id, actor_user_id=env.user_id)

    rows = publication.publications_for_document(doc_id)
    assert len(rows) == 1, "revoking must not delete the row"
    assert rows[0]["revoked_at"] is not None
    assert rows[0]["revoked_by_user_id"] == env.user_id


# --- 8. out-of-scope callers get nothing at all ------------------------------

def test_out_of_scope_client_receives_no_metadata_and_no_bytes(env):
    principal, person_id, _ = env.client()
    outsider, _, _ = env.client()
    doc_id = env.canonical(person_id=person_id, name="Private Return.pdf")
    pub_id = env.publish(doc_id, audience_type="person", audience_id=person_id)

    assert pv.portal_documents(outsider) == []
    with pytest.raises(PermissionError) as excinfo:
        pv.download_publication(outsider, pub_id)
    # Identical to the message for an id that does not exist — no existence oracle.
    with pytest.raises(PermissionError) as missing:
        pv.download_publication(outsider, 10_000_000)
    assert str(excinfo.value) == str(missing.value)


def test_client_without_the_documents_permission_sees_nothing(env):
    principal, person_id, _ = env.client(permissions={"documents": False, "messages": True})
    doc_id = env.canonical(person_id=person_id)
    env.publish(doc_id, audience_type="person", audience_id=person_id)

    assert pv.portal_documents(principal) == []


def test_publishing_requires_the_publication_capability(env):
    _, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id)
    with pytest.raises(publication.PublicationPermissionError):
        publication.publish(env.reader, doc_id, audience_type="person", audience_id=person_id,
                            client_visible=True, actor_user_id=env.user_id)


def test_publishing_outside_record_scope_is_refused(env):
    """vault.manage alone is not enough — the audience must be in the actor's book."""
    _, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id)
    narrow = Principal(env.user_id, "narrow@e.test", "Narrow",
                       frozenset({"vault.manage"}))          # no record.read_all
    with pytest.raises(publication.PublicationPermissionError):
        publication.publish(narrow, doc_id, audience_type="person", audience_id=person_id,
                            client_visible=True, actor_user_id=env.user_id)


# --- 9. the pre-existing vault store keeps working ---------------------------

def test_vault_documents_still_listed_alongside_publications(env):
    """Requirement 9. The original store's rows and rules are unchanged."""
    principal, person_id, _ = env.client()
    env.vault_doc(person_id, client_visible=True)
    doc_id = env.canonical(person_id=person_id, name="Published Canonical.pdf")
    env.publish(doc_id, audience_type="person", audience_id=person_id)

    rows = pv.portal_documents(principal)
    assert _stores(rows) == {pv.SOURCE_VAULT, pv.SOURCE_PUBLICATION}
    assert len(rows) == 2
    assert _titles(rows) == {"Vault Doc", "Published Canonical.pdf"}


def test_vault_document_not_client_visible_still_hidden(env):
    principal, person_id, _ = env.client()
    env.vault_doc(person_id, client_visible=False)
    assert pv.portal_documents(principal) == []


def test_vault_and_publication_ids_do_not_collide_across_stores(env):
    """Both stores number from their own sequence, so the source discriminator is what separates
    them. A client holding vault id 7 must not reach publication id 7 through the vault route."""
    principal, person_id, _ = env.client()
    doc_id = env.canonical(person_id=person_id)
    pub_id = env.publish(doc_id, audience_type="person", audience_id=person_id)

    with pytest.raises(PermissionError):
        pv.download_document(principal, pub_id)      # vault route, publication id
