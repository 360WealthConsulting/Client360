"""Redact credential-bearing query parameters from access logs.

THE PRODUCTION PROBLEM. Uvicorn's access logger writes the request line verbatim, query string
included. Every invitation link, OIDC callback and sign-in redirect therefore wrote a live credential
into an on-disk log that is rotated, backed up and read by people diagnosing unrelated issues:

    GET /portal/activate?invitation=<a single-use activation credential> HTTP/1.1  200
    GET /portal/auth/callback?code=<an authorization code>&state=<...>   HTTP/1.1  303

A log is not a secret store. This filter rewrites the VALUE of a known-sensitive parameter to
``REDACTED`` before the record is formatted, everywhere it appears in the record's message or
arguments.

WHAT IS KEPT. Method, path, protocol, status, client address and every non-sensitive parameter — the
things an operator actually needs. Only the values of listed keys change, and the key names remain
visible, so a log still shows THAT a callback carried a code without showing the code.

WHY A FILTER AND NOT A LOGGING CONFIG. ``configure_logging()`` deliberately owns only the
``client360`` namespace and does not reconfigure uvicorn's loggers, levels or handlers. Attaching a
filter adds no handler and changes no level, so that scope discipline is preserved: this is the one
mechanism that can sanitise uvicorn's own records without taking ownership of them.

FAIL-OPEN BY DESIGN, IN THE SAFE DIRECTION: any unexpected error while redacting drops the record
rather than emitting an unredacted one.
"""
from __future__ import annotations

import logging
import re

REDACTED = "REDACTED"

#: Parameter names whose VALUES must never reach a log. Matched case-insensitively, and as whole
#: names or as a suffix after ``_`` so ``id_token``/``access_token``/``refresh_token`` are covered by
#: ``token``. Add to this list; never remove from it.
SENSITIVE_PARAMS = (
    "invitation", "invite", "code", "state", "session_state", "token", "access_token", "id_token",
    "refresh_token", "reset", "reset_token", "verification", "verification_code", "otp",
    "secret", "client_secret", "password", "passwd", "pwd", "key", "api_key", "apikey",
    "auth", "authorization", "assertion", "nonce", "verifier", "code_verifier", "code_challenge",
    "signature", "sig", "session", "sid", "ticket", "challenge",
)

#: ``key=value`` up to the next separator. The key may be a bare sensitive name or ``*_<name>``.
_PARAM_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])((?:[A-Za-z0-9_.\-]*_)?(?:" + "|".join(
        re.escape(p) for p in SENSITIVE_PARAMS) + r"))(=)([^&\s\"'>;]*)")


def redact(text):
    """Return ``text`` with every sensitive ``key=value`` value replaced.

    Applied to the whole string rather than only after ``?`` on purpose: uvicorn formats the request
    line into one field, and other loggers embed URLs mid-sentence. An empty value is left alone —
    ``?code=`` discloses nothing and rewriting it would only make logs harder to read."""
    if not isinstance(text, str) or "=" not in text:
        return text
    return _PARAM_RE.sub(lambda m: f"{m.group(1)}={REDACTED}" if m.group(3) else m.group(0), text)


class QueryStringRedactionFilter(logging.Filter):
    """Rewrites a record's message and string arguments in place. Never blocks a record."""

    def filter(self, record):
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: (redact(v) if isinstance(v, str) else v)
                               for k, v in record.args.items()}
        except Exception:                       # noqa: BLE001 — see module docstring
            return False
        return True


#: Loggers that carry request lines. uvicorn's access log is the one that leaked in production;
#: the others are included so the same values cannot reappear through a different channel.
REDACTED_LOGGERS = ("uvicorn.access", "uvicorn.error", "uvicorn", "gunicorn.access",
                    "hypercorn.access", "client360")


def install_log_redaction(logger_names=REDACTED_LOGGERS):
    """Attach the filter to each logger, at most once. Idempotent and safe to call repeatedly.

    A filter on the LOGGER only sees records logged through that logger, so it is also attached to
    each logger's existing handlers — records propagating up from child loggers reach the handler
    without passing the parent logger's own filters."""
    installed = []
    for name in logger_names:
        logger = logging.getLogger(name)
        if not any(isinstance(f, QueryStringRedactionFilter) for f in logger.filters):
            logger.addFilter(QueryStringRedactionFilter())
            installed.append(name)
        for handler in logger.handlers:
            if not any(isinstance(f, QueryStringRedactionFilter) for f in handler.filters):
                handler.addFilter(QueryStringRedactionFilter())
    return installed
