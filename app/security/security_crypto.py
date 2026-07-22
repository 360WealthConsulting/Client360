"""Fernet field encryption for Enterprise Security secret references (Phase D.25).

Mirrors ``app/security/token_crypto.py`` / ``benefits_crypto.py`` / ``integration_crypto.py`` exactly:
a fail-closed symmetric cipher keyed by an environment variable (``SECURITY_SECRET_KEY``). Enterprise
Security NEVER stores a plaintext secret — a secret reference is a pointer to an existing encrypted
store (``microsoft_accounts``, an integration credential reference) or, when Security must hold its
own secret value, it stores only the ciphertext produced here. Plaintext is never logged. This does
NOT replace the existing crypto helpers; it is the Security domain's own field cipher.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet

_ENV_KEY = "SECURITY_SECRET_KEY"


class SecurityKeyMissing(RuntimeError):
    """Raised when SECURITY_SECRET_KEY is unset — the module fails closed (never plaintext)."""


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


def _fernet() -> Fernet:
    key = os.getenv(_ENV_KEY)
    if not key:
        raise SecurityKeyMissing(
            f"{_ENV_KEY} is not set; refusing to handle security secrets in plaintext.")
    return Fernet(key.encode("ascii"))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt((plaintext or "").encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt((ciphertext or "").encode("ascii")).decode("utf-8")


def mask(reference: str) -> str:
    """Non-reversible display hint (last 4), for showing that a secret exists without revealing it."""
    ref = reference or ""
    return ("•" * max(0, len(ref) - 4)) + ref[-4:] if ref else ""
