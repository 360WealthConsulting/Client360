"""Credential-bearing query parameters must never reach an access log.

Production evidence: uvicorn's access logger writes the request line verbatim, query string included,
so every invitation link and OIDC callback wrote a LIVE credential into a rotated on-disk log —

    GET /portal/activate?invitation=<single-use activation credential> HTTP/1.1  200

A log is rotated, backed up, and read by people diagnosing unrelated problems. It is not a secret
store. The filter rewrites the VALUE and keeps everything an operator actually needs.
"""
from __future__ import annotations

import logging

import pytest

from app.observability.log_redaction import (REDACTED, SENSITIVE_PARAMS,
                                             QueryStringRedactionFilter, install_log_redaction,
                                             redact)

SECRET = "s3cr3t-LIVE-CREDENTIAL"


@pytest.mark.parametrize("param", ["invitation", "code", "state", "session_state", "token",
                                   "id_token", "access_token", "refresh_token", "reset_token",
                                   "verification_code", "otp", "client_secret", "password",
                                   "nonce", "code_verifier", "code_challenge", "assertion"])
def test_every_sensitive_parameter_value_is_removed(param):
    line = f'GET /x?{param}={SECRET} HTTP/1.1'
    out = redact(line)
    assert SECRET not in out, f"{param} leaked"
    assert f"{param}={REDACTED}" in out


def test_the_production_request_lines_that_leaked_are_now_safe():
    for line, secret in (
            (f'GET /portal/activate?invitation={SECRET} HTTP/1.1', SECRET),
            (f'GET /portal/auth/callback?code={SECRET}&state=abc HTTP/1.1', SECRET),
            (f'GET /portal/login?invitation={SECRET}&error=failed HTTP/1.1', SECRET)):
        out = redact(line)
        assert secret not in out and "abc" not in out


def test_useful_information_is_preserved():
    out = redact('127.0.0.1 - "GET /portal/documents?page=2&sort=name HTTP/1.1" 200')
    assert out == '127.0.0.1 - "GET /portal/documents?page=2&sort=name HTTP/1.1" 200'


def test_the_method_path_and_status_survive_redaction():
    out = redact(f'127.0.0.1 - "GET /portal/activate?invitation={SECRET} HTTP/1.1" 303')
    assert "GET" in out and "/portal/activate" in out and "303" in out and "127.0.0.1" in out
    assert SECRET not in out


def test_a_path_with_no_query_string_is_untouched():
    for line in ('GET /health HTTP/1.1', 'POST /portal/verify HTTP/1.1', 'GET / HTTP/1.1'):
        assert redact(line) == line


def test_multiple_parameters_are_all_redacted():
    out = redact(f"/cb?code={SECRET}&state={SECRET}&session_state={SECRET}&keep=yes")
    assert SECRET not in out
    assert "keep=yes" in out
    assert out.count(REDACTED) == 3


def test_an_empty_value_is_left_alone():
    """``?code=`` discloses nothing; rewriting it would only make logs harder to read."""
    assert redact("/cb?code=&state=") == "/cb?code=&state="


def test_matching_is_case_insensitive():
    assert SECRET not in redact(f"/x?INVITATION={SECRET}")
    assert SECRET not in redact(f"/x?Code={SECRET}")


def test_prefixed_parameter_names_are_covered():
    """``id_token``/``access_token``/``portal_invitation`` all end in a sensitive name."""
    for name in ("id_token", "access_token", "refresh_token", "portal_invitation", "x_api_key"):
        assert SECRET not in redact(f"/x?{name}={SECRET}"), name


def test_a_similarly_named_but_harmless_parameter_is_not_over_redacted():
    """Redaction must not eat ordinary parameters that merely contain a substring."""
    for harmless in ("postcode=SW1A", "encoded=abc", "statement=open", "barcode=123"):
        assert harmless in redact(f"/x?{harmless}")


def test_the_filter_rewrites_a_real_uvicorn_access_record():
    """uvicorn formats the request line through record.args, not the message."""
    logger = logging.getLogger("test.uvicorn.access.sample")
    record = logger.makeRecord(
        logger.name, logging.INFO, "x", 1, '%s - "%s %s %s" %d',
        ("127.0.0.1", "GET", f"/portal/activate?invitation={SECRET}", "HTTP/1.1", 200), None)
    assert QueryStringRedactionFilter().filter(record) is True
    assert SECRET not in record.getMessage()
    assert "/portal/activate" in record.getMessage() and "200" in record.getMessage()


def test_the_filter_handles_dict_args_and_non_string_args():
    logger = logging.getLogger("test.uvicorn.access.sample")
    record = logger.makeRecord(logger.name, logging.INFO, "x", 1, "%(url)s %(status)d",
                               {"url": f"/x?token={SECRET}", "status": 200}, None)
    assert QueryStringRedactionFilter().filter(record) is True
    assert SECRET not in record.getMessage()


def test_the_filter_never_raises_on_an_odd_record():
    logger = logging.getLogger("test.uvicorn.access.sample")
    record = logger.makeRecord(logger.name, logging.INFO, "x", 1, object(), None, None)
    assert QueryStringRedactionFilter().filter(record) in (True, False)   # never an exception


def test_install_is_idempotent_and_covers_the_uvicorn_access_logger():
    install_log_redaction()
    install_log_redaction()
    filters = [f for f in logging.getLogger("uvicorn.access").filters
               if isinstance(f, QueryStringRedactionFilter)]
    assert len(filters) == 1, "installing twice stacked duplicate filters"


def test_the_uvicorn_access_logger_actually_redacts_after_install():
    install_log_redaction()
    logger = logging.getLogger("uvicorn.access")
    record = logger.makeRecord(
        "uvicorn.access", logging.INFO, "x", 1, '%s - "%s %s %s" %d',
        ("127.0.0.1", "GET", f"/portal/auth/callback?code={SECRET}", "HTTP/1.1", 303), None)
    for f in logger.filters:
        f.filter(record)
    assert SECRET not in record.getMessage()


def test_redaction_is_installed_at_application_startup():
    import inspect

    from app import main
    src = inspect.getsource(main)
    assert "install_log_redaction()" in src, "startup does not install the redaction filter"


def test_the_sensitive_list_covers_every_credential_this_system_issues():
    for required in ("invitation", "code", "state", "session_state", "token", "verification_code",
                     "otp", "secret", "password", "nonce", "verifier"):
        assert required in SENSITIVE_PARAMS, f"{required} is not redacted"


def test_a_non_string_input_is_returned_unchanged():
    for value in (None, 200, ["a"], {"b": 1}):
        assert redact(value) is value
