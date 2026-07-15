"""Application-level encryption for sensitive benefits fields (Release 0.9.11).

Benefits carries PHI/financial PII — employer EIN and (later) SSN/compensation must
never be stored in plaintext (ADR-18 §2.3 / §13). This wraps Fernet symmetric
encryption keyed by a secrets-managed ``BENEFITS_FIELD_KEY``. It fails **closed**
when the key is absent and never logs plaintext. Same idiom as
``app.security.token_crypto`` (Microsoft token cache).

Key management: ``BENEFITS_FIELD_KEY`` is a urlsafe-base64 32-byte Fernet key from
the environment / secrets manager (never committed), backed up separately from the
database.
"""
import os

from cryptography.fernet import Fernet

KEY_ENV_VAR = "BENEFITS_FIELD_KEY"


class BenefitsKeyMissing(RuntimeError):
    """Raised when benefits field encryption is required but the key is unset."""


def generate_key() -> str:
    """Generate a new Fernet key (operators / tests). Not used at runtime."""
    return Fernet.generate_key().decode("ascii")


def _cipher() -> Fernet:
    key = os.getenv(KEY_ENV_VAR)
    if not key:
        raise BenefitsKeyMissing(f"{KEY_ENV_VAR} is required to encrypt/decrypt sensitive benefits fields")
    return Fernet(key.encode("ascii") if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    """Encrypt a sensitive field value to ciphertext text."""
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Decrypt ciphertext produced by :func:`encrypt`."""
    return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")


def mask(plaintext: str) -> str:
    """Non-reversible display mask (e.g. an EIN shown without the sensitive capability)."""
    if not plaintext:
        return ""
    tail = plaintext[-4:]
    return "•" * max(0, len(plaintext) - 4) + tail
