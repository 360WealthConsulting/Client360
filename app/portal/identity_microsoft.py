"""Microsoft Entra External ID identity provider for CLIENT PORTAL sign-in.

External clients authenticate against a SEPARATE Entra External ID tenant — never the staff workforce
tenant and never the staff OIDC app registration. Sharing those would put client identities in the
employee directory, and the staff sign-in path deliberately falls back to matching a user by email and
binding whatever subject presented (``app/security/service.py``), which for external clients is an
account-takeover primitive. This provider therefore never trusts email as an identity key.

Flow: ``authorization_url`` (state + nonce + PKCE challenge, all held server-side in the browser
session) → the IdP → ``exchange_code`` (authorization code + PKCE verifier) → ID token validated against
the tenant's discovery document and JWKS for signature, issuer, audience, expiry and nonce → the
immutable subject is returned. No Graph token is requested or retained: the portal needs an identity,
not an API token.

MFA EVIDENCE IS CONFIGURATION-DRIVEN AND FAILS CLOSED. Which authority proves MFA is an explicit
deployment decision, ``PORTAL_OIDC_MFA_MODE``:

* ``claims`` (the default) proves MFA from the tenant's own token claims. The exact claim Entra
  External ID emits for a given user flow depends on tenant configuration, so this module
  deliberately does NOT copy the workforce ``amr ∩ {mfa, otp, hwk}`` interpretation or guess a
  default. Until ``PORTAL_OIDC_MFA_AMR_VALUES`` and/or ``PORTAL_OIDC_MFA_ACR_VALUES`` are configured
  from the real tenant's observed tokens, MFA cannot be proven and authentication is refused.

* ``conditional_access`` names the Entra Conditional Access policy protecting the portal application
  as the enforcement authority. Some External ID deployments — including this one — emit no
  ``amr``/``acr``/``acrs`` at all, so claim evidence cannot exist and ``claims`` mode would refuse
  every otherwise-valid sign-in. In this mode a token that completed the authorization-code/PKCE flow
  and passed every check in :meth:`MicrosoftExternalIdentityProvider._verify_id_token` could only have
  been issued to a session the policy already admitted, so MFA is treated as verified.

The default stays fail-closed and Conditional Access is NEVER inferred from absent claim
configuration: an operator must name the mode. An unknown mode proves nothing and refuses sign-in.
"""
from __future__ import annotations

import os

import jwt
import requests

from app.portal.providers import (
    PORTAL_IDENTITY_PROVIDERS,
    PortalIdentityProvider,
    PortalIdentityResult,
)

MICROSOFT_PROVIDER_KEY = "microsoft"

#: Generic client-facing failure. Every validation error maps to this — the specific reason (bad issuer,
#: bad audience, expired, replayed nonce, missing MFA) is never disclosed to the browser.
GENERIC_AUTH_ERROR = "Sign-in could not be completed."

#: MFA enforcement authority for this deployment. ``claims`` proves MFA from the tenant's own
#: ``amr``/``acr`` evidence; ``conditional_access`` delegates enforcement to the Entra Conditional
#: Access policy fronting the portal application. Anything else is unknown and proves nothing.
MFA_MODE_CLAIMS = "claims"
MFA_MODE_CONDITIONAL_ACCESS = "conditional_access"
MFA_MODES = frozenset({MFA_MODE_CLAIMS, MFA_MODE_CONDITIONAL_ACCESS})
#: Absent configuration must behave exactly as before: claim evidence, or nothing.
DEFAULT_MFA_MODE = MFA_MODE_CLAIMS

_DISCOVERY_TIMEOUT = 10
_TOKEN_TIMEOUT = 15
_ALGORITHMS = ("RS256", "ES256")


def _env(name, default=""):
    return (os.getenv(name, default) or "").strip()


def _csv(name):
    return frozenset(v.strip() for v in _env(name).split(",") if v.strip())


class MicrosoftExternalIdentityProvider(PortalIdentityProvider):
    """Entra External ID, browser authorization-code flow with PKCE."""

    key = MICROSOFT_PROVIDER_KEY
    supports_redirect_flow = True
    production_capable = True

    def __init__(self, *, issuer=None, client_id=None, client_secret=None, audience=None,
                 scopes=None, mfa_mode=None, mfa_amr_values=None, mfa_acr_values=None):
        self.issuer = (issuer or _env("PORTAL_OIDC_ISSUER")).rstrip("/")
        self.client_id = client_id or _env("PORTAL_OIDC_CLIENT_ID")
        self.client_secret = client_secret or _env("PORTAL_OIDC_CLIENT_SECRET")
        self.audience = audience or _env("PORTAL_OIDC_AUDIENCE") or self.client_id
        self.scopes = scopes or (_env("PORTAL_OIDC_SCOPES") or "openid profile email")
        # Defaults to claim evidence, so an unconfigured deployment cannot prove MFA and
        # Conditional Access is never assumed from the mere absence of claim values.
        self.mfa_mode = (mfa_mode or _env("PORTAL_OIDC_MFA_MODE") or DEFAULT_MFA_MODE).lower()
        # No default: in claims mode an unconfigured deployment must not be able to prove MFA.
        self.mfa_amr_values = frozenset(mfa_amr_values or _csv("PORTAL_OIDC_MFA_AMR_VALUES"))
        self.mfa_acr_values = frozenset(mfa_acr_values or _csv("PORTAL_OIDC_MFA_ACR_VALUES"))
        if not self.issuer or not self.client_id:
            raise RuntimeError("PORTAL_OIDC_ISSUER and PORTAL_OIDC_CLIENT_ID are required")

    # --- the one-shot contract is NOT how this provider authenticates ---------

    def verify_activation(self, assertion: str) -> PortalIdentityResult:
        """Refused by design.

        A posted assertion cannot be bound to a server-held ``state``/``nonce``, so accepting one here
        would reintroduce exactly the token-substitution and replay exposure the redirect flow exists to
        prevent. Real sign-in goes through :meth:`authorization_url` / :meth:`exchange_code`."""
        raise ValueError(GENERIC_AUTH_ERROR)

    # --- configuration -------------------------------------------------------

    @classmethod
    def is_configured(cls) -> bool:
        """Whether the deployment carries enough configuration to construct the provider."""
        return bool(_env("PORTAL_OIDC_ISSUER") and _env("PORTAL_OIDC_CLIENT_ID")
                    and _env("PORTAL_OIDC_CLIENT_SECRET"))

    def mfa_evidence_configured(self) -> bool:
        """Whether this deployment has declared where MFA evidence comes from.

        ``conditional_access`` IS the declaration — an operator named the enforcement authority.
        ``claims`` additionally needs the tenant's actual claim values, without which MFA can never
        be proven. An unknown mode declares nothing."""
        if self.mfa_mode == MFA_MODE_CONDITIONAL_ACCESS:
            return True
        if self.mfa_mode != MFA_MODE_CLAIMS:
            return False
        return bool(self.mfa_amr_values or self.mfa_acr_values)

    # --- discovery -----------------------------------------------------------

    def _discovery(self) -> dict:
        response = requests.get(f"{self.issuer}/.well-known/openid-configuration",
                                timeout=_DISCOVERY_TIMEOUT)
        response.raise_for_status()
        return response.json()

    # --- browser authorization-code flow -------------------------------------

    def authorization_url(self, *, state: str, nonce: str, redirect_uri: str,
                          code_challenge: str) -> str:
        from urllib.parse import urlencode
        endpoint = self._discovery()["authorization_endpoint"]
        return endpoint + "?" + urlencode({
            "client_id": self.client_id,
            "response_type": "code",
            "response_mode": "query",
            "scope": self.scopes,
            "redirect_uri": redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        })

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str,
                      expected_nonce: str) -> PortalIdentityResult:
        discovery = self._discovery()
        response = requests.post(discovery["token_endpoint"], timeout=_TOKEN_TIMEOUT, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code_verifier": code_verifier,
        })
        if response.status_code != 200:
            # Never surface the IdP's error body — it can echo the code or client identifiers.
            raise ValueError(GENERIC_AUTH_ERROR)
        id_token = (response.json() or {}).get("id_token")
        if not id_token:
            raise ValueError(GENERIC_AUTH_ERROR)
        return self._verify_id_token(id_token, expected_nonce=expected_nonce)

    # --- token validation ----------------------------------------------------

    def _verify_id_token(self, id_token: str, *, expected_nonce: str) -> PortalIdentityResult:
        try:
            signing_key = jwt.PyJWKClient(self._discovery()["jwks_uri"]) \
                .get_signing_key_from_jwt(id_token).key
            claims = jwt.decode(
                id_token, signing_key, algorithms=list(_ALGORITHMS),
                audience=self.audience, issuer=self.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except Exception as exc:                      # signature, issuer, audience, expiry, malformed
            raise ValueError(GENERIC_AUTH_ERROR) from exc

        if not expected_nonce or claims.get("nonce") != expected_nonce:
            raise ValueError(GENERIC_AUTH_ERROR)      # replay / cross-session token substitution

        subject = self._immutable_subject(claims)
        if not subject:
            raise ValueError(GENERIC_AUTH_ERROR)

        # Last, and only after signature, issuer, audience, expiry, nonce and immutable subject have
        # all passed above: MFA is mandatory, and unproven evidence — unconfigured claim values, or
        # an unknown enforcement mode — means denied.
        if not self._mfa_verified(claims):
            raise ValueError(GENERIC_AUTH_ERROR)

        # email is returned for a SECONDARY invitation cross-check only; it is never the identity key.
        return PortalIdentityResult(subject=subject, mfa_verified=True,
                                    email=claims.get("email") or claims.get("preferred_username"))

    def _immutable_subject(self, claims) -> str:
        """The stable identifier. ``oid`` is the durable object id in Entra; ``sub`` is stable per
        application. Email/UPN are mutable and are never used."""
        oid, sub = claims.get("oid"), claims.get("sub")
        return f"{self.key}:{oid or sub}" if (oid or sub) else ""

    def _mfa_verified(self, claims) -> bool:
        """Fail-closed MFA evidence for the configured enforcement authority.

        ``conditional_access``: the Entra Conditional Access policy protecting the portal application
        is the enforcement authority. Callers reach this only from :meth:`_verify_id_token`, after the
        authorization-code/PKCE exchange succeeded and the ID token passed signature, issuer,
        audience, expiry, nonce and immutable-subject validation — so the token could only have been
        issued to a session the policy already admitted under its MFA grant control. This deployment's
        External ID tokens carry no ``amr``/``acr``/``acrs`` at all, so claim evidence cannot exist
        here; the operator must name this mode explicitly and it is never inferred.

        ``claims``: proven only by the tenant's observed and configured claim values. Deliberately NOT
        the workforce interpretation: the values Entra External ID emits for a given user flow must be
        observed in the real tenant and configured. With nothing configured this returns False, so MFA
        can never be silently assumed.

        Any other mode is unknown configuration, which proves nothing and is refused."""
        if self.mfa_mode == MFA_MODE_CONDITIONAL_ACCESS:
            return True
        if self.mfa_mode != MFA_MODE_CLAIMS:
            return False
        if not self.mfa_evidence_configured():
            return False
        amr = claims.get("amr") or []
        if isinstance(amr, str):
            amr = [amr]
        if self.mfa_amr_values and self.mfa_amr_values.intersection(amr):
            return True
        acr_values = {claims.get("acr")} | set(claims.get("acrs") or [])
        return bool(self.mfa_acr_values and self.mfa_acr_values.intersection(
            {a for a in acr_values if a}))


def register_microsoft_provider_if_configured():
    """Register the production provider ONLY when the deployment is configured for it.

    Never raises: a misconfigured deployment simply has no production provider, and the portal stays
    closed because ``production_ready()`` requires one. Failing startup instead would take the whole
    application down over a portal that is off by default."""
    if not MicrosoftExternalIdentityProvider.is_configured():
        return False
    try:
        PORTAL_IDENTITY_PROVIDERS.register(MicrosoftExternalIdentityProvider())
        return True
    except Exception:
        return False
