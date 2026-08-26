"""The controlled-test local identity-provider gate (migration ``a7c31f9b4e02``).

``portal.production_signed_off`` de-registers the deterministic local provider — and that provider is the
only portal identity provider that exists. Recording sign-off therefore made even a SYNTHETIC production
test impossible: both auth surfaces resolve a provider and return 400 when none is registered.

``portal.local_identity_provider_enabled`` (default False) authorizes the local provider independently of
sign-off, WITHOUT weakening the production boundary: sign-off alone still removes it, the gate does not
touch ``production_ready()``, and it opens no portal surface. It is for controlled synthetic testing only
and is not a substitute for the real external IdP the compliance gate still requires.
"""
from __future__ import annotations

import pytest

from app.portal import identity_local
from app.portal.gate import GATES
from app.portal.providers import PORTAL_IDENTITY_PROVIDERS

SIGNOFF = "portal.production_signed_off"
LOCAL_IDP = "portal.local_identity_provider_enabled"


@pytest.fixture
def registry():
    """A clean provider registry for the duration of one test, restored afterwards."""
    saved = dict(PORTAL_IDENTITY_PROVIDERS._providers)
    PORTAL_IDENTITY_PROVIDERS._providers.clear()
    yield PORTAL_IDENTITY_PROVIDERS
    PORTAL_IDENTITY_PROVIDERS._providers.clear()
    PORTAL_IDENTITY_PROVIDERS._providers.update(saved)


@pytest.fixture
def gates(monkeypatch):
    """Drive the two flags the registration decision reads."""
    def _apply(**values):
        monkeypatch.setattr(identity_local, "gate", lambda name: values.get(name, GATES.get(name, False)),
                            raising=False)
        # identity_local imports gate() inside the function, so patch the definition too
        from app.portal import gate as gate_module
        real = gate_module.gate
        monkeypatch.setattr(gate_module, "gate",
                            lambda name: values.get(name, real(name)))
    return _apply


def _registered(registry):
    try:
        registry.get(identity_local.LOCAL_PROVIDER_KEY)
        return True
    except ValueError:
        return False


# --- the four-state registration matrix --------------------------------------

MATRIX = [
    (False, False, True,  "unsigned dev/CI — existing offline behaviour preserved"),
    (False, True,  True,  "unsigned + explicitly authorized"),
    (True,  False, False, "SIGNED OFF, no authorization — production protection intact"),
    (True,  True,  True,  "signed off + explicit governed synthetic-test window"),
]


@pytest.mark.parametrize("signed_off,local_idp,expect_registered,why", MATRIX)
def test_provider_registration_matrix(registry, gates, signed_off, local_idp, expect_registered, why):
    gates(**{SIGNOFF: signed_off, LOCAL_IDP: local_idp})
    returned = identity_local.register_local_provider_if_permitted()
    assert returned is expect_registered, why
    assert _registered(registry) is expect_registered, why


def test_signoff_alone_still_removes_the_only_provider(registry, gates):
    """The production boundary this gate must not weaken."""
    gates(**{SIGNOFF: True, LOCAL_IDP: False})
    assert identity_local.register_local_provider_if_permitted() is False
    assert _registered(registry) is False
    assert sorted(registry._providers) == []


def test_portal_enabled_alone_never_registers_a_provider(registry, gates):
    gates(**{SIGNOFF: True, LOCAL_IDP: False, "portal.enabled": True})
    assert identity_local.register_local_provider_if_permitted() is False
    assert _registered(registry) is False


def test_registration_is_idempotent_with_no_duplicate(registry, gates):
    gates(**{SIGNOFF: True, LOCAL_IDP: True})
    identity_local.register_local_provider_if_permitted()
    identity_local.register_local_provider_if_permitted()
    assert sorted(registry._providers) == ["local"]


def test_unresolvable_runtime_keeps_documented_defaults(registry, monkeypatch):
    """A runtime failure must not silently authorize the test provider."""
    from app.portal import gate as gate_module

    def boom(_name):
        raise RuntimeError("runtime unavailable")

    monkeypatch.setattr(gate_module, "gate", boom)
    # not signed off (documented default) -> registers; the TEST gate is treated as OFF
    assert identity_local.register_local_provider_if_permitted() is True
    assert _registered(registry) is True


# --- the gate must not widen anything else -----------------------------------

def test_new_flag_defaults_false_in_the_governed_registry():
    assert GATES[LOCAL_IDP] is False


def test_default_runtime_state_leaves_the_new_flag_false():
    from app.portal.gate import gate_status
    assert gate_status()[LOCAL_IDP] is False


def test_local_idp_gate_does_not_make_production_ready_true(gates):
    from app.portal.gate import production_ready
    gates(**{LOCAL_IDP: True})
    assert production_ready() is False, "the test IdP gate must not imply production readiness"
    gates(**{LOCAL_IDP: True, "portal.enabled": True})
    assert production_ready() is False, "still needs sign-off"


def test_production_ready_still_requires_both_master_conditions(gates, production_identity_provider):
    from app.portal.gate import production_ready
    gates(**{"portal.enabled": True, SIGNOFF: True, LOCAL_IDP: False})
    assert production_ready() is True
    gates(**{"portal.enabled": True, SIGNOFF: False, LOCAL_IDP: True})
    assert production_ready() is False


def test_local_idp_gate_opens_no_portal_surface():
    """It must never appear as a surface gate in the request-path rule table."""
    from app.services.features import portal_gate
    mapped = {g for _rx, _m, g in portal_gate._RUNTIME_GATE_RULES}
    assert LOCAL_IDP not in mapped


# --- the provider's own security properties are unchanged --------------------

def test_assertion_format_is_still_enforced():
    provider = identity_local.LocalTestIdentityProvider()
    for bad in ("", "oauth:someone", "local:", "local", "nonsense"):
        with pytest.raises(ValueError):
            provider.verify_activation(bad)


def test_mfa_is_only_verified_when_explicitly_asserted():
    provider = identity_local.LocalTestIdentityProvider()
    assert provider.verify_activation("local:tester").mfa_verified is False
    assert provider.verify_activation("local:tester:mfa").mfa_verified is True


def test_provider_never_returns_an_email_for_linkage():
    """No email fallback: linking a subject to an account stays the audited accept_invitation step."""
    result = identity_local.LocalTestIdentityProvider().verify_activation("local:tester:mfa")
    assert result.email is None
    assert result.subject == "local:tester"


def test_provider_grants_no_staff_capability():
    result = identity_local.LocalTestIdentityProvider().verify_activation("local:tester:mfa")
    for attr in ("capabilities", "roles", "user_id", "is_staff"):
        assert not hasattr(result, attr), f"identity result exposed {attr}"


def test_activation_still_requires_a_valid_invitation_and_mfa():
    """The gate changes WHO can verify identity, never the invitation/MFA requirements."""
    from app.portal.service import accept_invitation
    with pytest.raises(ValueError, match="Invitation is invalid or expired"):
        accept_invitation("not-a-real-token", "local:tester", True)


def test_accept_invitation_refuses_unverified_mfa():
    import inspect

    from app.portal import service
    source = inspect.getsource(service.accept_invitation)
    assert 'raise ValueError("MFA verification is required")' in source
    assert "if not mfa_verified" in source


# --- provider lifecycle: startup only (no hot-reload) ------------------------

def test_registration_happens_only_at_application_startup():
    """Flipping the gate on a RUNNING process changes nothing until restart.

    The registry is an in-memory dict populated once from the FastAPI lifespan. Nothing re-evaluates the
    gate per request, and no runtime refresh re-registers providers. The controlled-test activation
    procedure must therefore include a Client360 restart after enabling the flag."""
    from pathlib import Path

    call_sites = []
    for path in Path("app").rglob("*.py"):
        if path.name == "identity_local.py":
            continue
        if "register_local_provider_if_permitted" in path.read_text(encoding="utf-8"):
            call_sites.append(str(path))
    assert call_sites == ["app/main.py"], f"unexpected registration call sites: {call_sites}"

    main_src = Path("app/main.py").read_text(encoding="utf-8")
    lifespan_src = main_src.split("async def lifespan(")[1].split("\napp = FastAPI(")[0]
    assert "register_local_provider_if_permitted()" in lifespan_src, \
        "registration must run in the startup lifespan"


def test_no_runtime_refresh_path_reregisters_providers():
    """Guards against someone assuming hot-reload exists."""
    from pathlib import Path

    engine_src = Path("app/services/runtime/engine.py").read_text(encoding="utf-8")
    assert "PORTAL_IDENTITY_PROVIDERS" not in engine_src
    assert "register_local_provider_if_permitted" not in engine_src
