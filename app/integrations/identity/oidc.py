import os
from urllib.parse import urlencode
import jwt
import requests
from app.integrations.identity.base import IdentityClaims

class OidcIdentityProvider:
    """Standards-based adapter; authorization services never depend on a vendor."""
    def __init__(self, issuer=None, client_id=None, client_secret=None):
        self.issuer = (issuer or os.getenv("OIDC_ISSUER", "")).rstrip("/")
        self.client_id = client_id or os.getenv("OIDC_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("OIDC_CLIENT_SECRET", "")
        if not self.issuer or not self.client_id:
            raise RuntimeError("OIDC_ISSUER and OIDC_CLIENT_ID are required")

    def _discovery(self):
        response = requests.get(f"{self.issuer}/.well-known/openid-configuration", timeout=10)
        response.raise_for_status()
        return response.json()

    def authorization_url(self, *, state, redirect_uri):
        endpoint = self._discovery()["authorization_endpoint"]
        return endpoint + "?" + urlencode({"client_id": self.client_id, "response_type": "code", "scope": "openid email profile", "redirect_uri": redirect_uri, "state": state})

    def exchange_code(self, *, code, redirect_uri):
        discovery = self._discovery()
        response = requests.post(discovery["token_endpoint"], data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "client_id": self.client_id, "client_secret": self.client_secret}, timeout=15)
        response.raise_for_status()
        token = response.json()["id_token"]
        key = jwt.PyJWKClient(discovery["jwks_uri"]).get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256", "ES256"], audience=self.client_id, issuer=self.issuer)
        methods = claims.get("amr", [])
        return IdentityClaims(str(claims["sub"]), claims.get("email", ""), claims.get("name") or claims.get("email", ""), bool({"mfa", "otp", "hwk"}.intersection(methods)))
