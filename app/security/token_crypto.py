"""Application-level encryption for Microsoft 365 OAuth token material.

Release 0.9.9 (Platform Consolidation), Phase 1. Microsoft OAuth token/cache
material must never be stored in plaintext (RC8/RC9 H10; PRODUCTION_ARCHITECTURE
§9/§19). This module wraps Fernet symmetric encryption keyed by a
secrets-managed ``MICROSOFT_TOKEN_KEY``. It fails closed when the key is absent
and never logs plaintext.

Key management: ``MICROSOFT_TOKEN_KEY`` is a urlsafe-base64 32-byte Fernet key
sourced from the environment / secrets manager (never committed). It must be
backed up separately from the database — restored ciphertext is undecryptable
without it. Rotation is a background re-encrypt of each account's cache blob.
"""
import os

from cryptography.fernet import Fernet

KEY_ENV_VAR = "MICROSOFT_TOKEN_KEY"


class TokenKeyMissing(RuntimeError):
    """Raised when token encryption is required but MICROSOFT_TOKEN_KEY is unset."""


def generate_key() -> str:
    """Generate a new Fernet key (for operators / tests). Not used at runtime."""
    return Fernet.generate_key().decode("ascii")


def _cipher() -> Fernet:
    # Read the key on every call so the module fails closed the moment the key is
    # removed, and so tests can set/rotate the key without a cached instance.
    key = os.getenv(KEY_ENV_VAR)
    if not key:
        raise TokenKeyMissing(
            f"{KEY_ENV_VAR} is required to encrypt/decrypt Microsoft 365 tokens"
        )
    return Fernet(key.encode("ascii") if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    """Encrypt a string (e.g. a serialized MSAL token cache) to ciphertext text."""
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Decrypt ciphertext produced by :func:`encrypt` back to the original string."""
    return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
