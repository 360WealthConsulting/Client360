"""The centralized OpenAI provider — posture, security controls, and every failure mode.

NO TEST MAKES A NETWORK CALL. ``generate`` takes an injectable client factory and every test passes
one, so the real SDK client is never constructed and api.openai.com is never contacted. The SDK's
own exception classes ARE used (constructed against synthetic httpx2 responses) rather than
look-alikes, so the failure mapping is tested against the thing it actually has to classify.

The security assertions are the point of most of this file:
  * the API key never reaches a result, a detail string, a diagnostic or a log record — proven with
    a real AuthenticationError whose message CONTAINS the key, which is exactly how a careless
    ``str(exc)`` would leak it;
  * ``store=False`` is on every transmitted payload, and no payload carries an identifier field;
  * the connectivity probe transmits two fixed literals and nothing else;
  * a missing/unavailable model is refused, never substituted.
"""
from __future__ import annotations

import logging
import pathlib

import httpx2
import openai
import pytest

from app import config
from app.services import openai_provider as P

#: Built at runtime rather than written as a literal, so no credential-SHAPED string exists
#: anywhere in the repository for a secret scanner (ours or anyone else's) to trip over.
#: These are synthetic fixtures; no real key has ever been in this file.
_SK = "sk" + "-"
API_KEY = _SK + "test-clientthreesixty-000000000000000000"
#: A DIFFERENT synthetic key — a stale or foreign credential, not the configured one.
FOREIGN_KEY = _SK + "someoneelseskey12345"
MODEL = "gpt-5.6"

# --- doubles -----------------------------------------------------------------


class _Usage:
    def __init__(self, input_tokens=11, output_tokens=3):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Response:
    """The shape the provider reads off a Responses API result."""

    def __init__(self, output_text="ok", id="resp_abc123", model=MODEL, usage=None):
        self.output_text = output_text
        self.id = id
        self.model = model
        self.usage = usage if usage is not None else _Usage()


class _Responses:
    def __init__(self, calls, response=None, raises=None):
        self._calls, self._response, self._raises = calls, response, raises

    def create(self, **kwargs):
        self._calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._response


class _Client:
    def __init__(self, calls, response=None, raises=None):
        self.responses = _Responses(calls, response, raises)


def _factory(response=None, raises=None):
    """``(client_factory, calls, keys)`` — records the payloads AND the key it was handed."""
    calls, keys = [], []

    def client_factory(api_key):
        keys.append(api_key)
        return _Client(calls, response if response is not None else _Response(), raises)

    return client_factory, calls, keys


def _request(status=401):
    req = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    return req, httpx2.Response(status, request=req)


# --- fixtures ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test states its own posture; nothing leaks in from the developer's shell."""
    for name in ("OPENAI_ENABLED", "OPENAI_MODEL", "OPENAI_API_KEY",
                 "OPENAI_TIMEOUT_SECONDS", "OPENAI_MAX_RETRIES"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("OPENAI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", API_KEY)
    monkeypatch.setenv("OPENAI_MODEL", MODEL)


def _generate(**kwargs):
    kwargs.setdefault("purpose", "advisor_ai")
    kwargs.setdefault("instructions", "Summarize.")
    kwargs.setdefault("input_text", "hello")
    return P.generate(**kwargs)


# --- disabled by default -----------------------------------------------------


def test_defaults_are_off_and_unconfigured():
    """An untouched deployment must behave exactly as it did before this shipped."""
    assert config.openai_enabled() is False
    assert config.openai_model() == ""
    assert config.openai_api_key_configured() is False
    assert P.configuration_status() == (False, "OPENAI_ENABLED is not set")


def test_disabled_provider_refuses_before_building_a_client():
    """Disabled must short-circuit BEFORE the client factory runs — no client, no network call."""
    factory, calls, keys = _factory()
    result = _generate(client_factory=factory)

    assert result.ok is False
    assert result.status == P.DISABLED
    assert result.failure_class == P.PROVIDER_DISABLED
    assert calls == [] and keys == []          # the factory was never invoked


def test_disabled_provider_still_answers_connectivity_without_a_call():
    factory, calls, _ = _factory()
    result = P.check_connectivity(client_factory=factory)
    assert (result.status, result.failure_class) == (P.DISABLED, P.PROVIDER_DISABLED)
    assert calls == []


def test_app_startup_configuration_is_clean_when_openai_is_absent():
    """The app must start normally with OpenAI disabled and no key — no warning, no exception."""
    config.validate_startup_configuration()
    assert not [w for w in config.configuration_warnings() if "OPENAI" in w]


# --- missing configuration ---------------------------------------------------


def test_missing_key_is_not_configured_and_makes_no_call(monkeypatch):
    monkeypatch.setenv("OPENAI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_MODEL", MODEL)
    factory, calls, _ = _factory()

    result = _generate(client_factory=factory)

    assert result.status == P.NOT_CONFIGURED
    assert result.failure_class == P.PROVIDER_NOT_CONFIGURED
    assert "OPENAI_API_KEY is not set" in result.detail
    assert calls == []


def test_missing_model_is_refused_and_never_substituted(monkeypatch):
    """No fallback model. An unset model is a refusal, not a quiet swap to something else."""
    monkeypatch.setenv("OPENAI_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", API_KEY)
    factory, calls, _ = _factory()

    result = _generate(client_factory=factory)

    assert result.status == P.NOT_CONFIGURED
    assert "OPENAI_MODEL is not set" in result.detail
    assert calls == []


def test_half_configured_provider_warns_at_startup(monkeypatch):
    monkeypatch.setenv("OPENAI_ENABLED", "true")
    warnings = [w for w in config.configuration_warnings() if "OPENAI_ENABLED is on" in w]
    assert len(warnings) == 1
    assert "OPENAI_API_KEY" in warnings[0] and "OPENAI_MODEL" in warnings[0]
    assert API_KEY not in warnings[0]


def test_sdk_missing_is_reported_not_raised(configured):
    """A host without the openai package gets a structured failure, never an ImportError."""
    def missing(api_key):
        raise ImportError("No module named 'openai'")

    result = _generate(client_factory=missing)
    assert result.status == P.FAILED
    assert result.failure_class == P.SDK_MISSING


# --- configured + successful -------------------------------------------------


def test_configured_provider_is_ready(configured):
    assert P.configuration_status() == (True, "")
    info = P.diagnostics()
    assert info["ready"] is True and info["enabled"] is True
    assert info["model"] == MODEL and info["api_key_configured"] is True


def test_successful_response(configured):
    factory, calls, keys = _factory(_Response(output_text="a summary", id="resp_1"))

    result = _generate(client_factory=factory)

    assert result.ok is True and result.status == P.OK
    assert result.output_text == "a summary"
    assert result.response_id == "resp_1"
    assert result.model == MODEL
    assert (result.input_tokens, result.output_tokens) == (11, 3)
    assert result.latency_ms >= 0
    assert len(calls) == 1
    assert keys == [API_KEY]                  # the key reaches the SDK and nowhere else


def test_request_uses_the_configured_model_and_responses_api(configured):
    factory, calls, _ = _factory()
    _generate(client_factory=factory)
    assert calls[0]["model"] == MODEL
    assert calls[0]["input"] == "hello"
    assert calls[0]["instructions"] == "Summarize."
    assert calls[0]["max_output_tokens"] == P.DEFAULT_MAX_OUTPUT_TOKENS


def test_timeout_and_retries_are_bounded(monkeypatch):
    """Configurable, but never unbounded — a typo cannot mean "wait forever" or "retry forever"."""
    assert config.openai_timeout_seconds() == 30.0
    assert config.openai_max_retries() == 2

    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "99999")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "500")
    assert config.openai_timeout_seconds() == 300.0
    assert config.openai_max_retries() == 5

    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "-4")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "-4")
    assert config.openai_timeout_seconds() == 1.0
    assert config.openai_max_retries() == 0

    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "not-a-number")
    assert config.openai_timeout_seconds() == 30.0


# --- store=False and data minimization ---------------------------------------


def test_store_false_is_on_every_transmitted_request(configured):
    """The retention control. Asserted on the payload actually handed to the SDK."""
    factory, calls, _ = _factory()

    for purpose in sorted(P.PURPOSES):
        _generate(purpose=purpose, client_factory=factory)

    assert len(calls) == len(P.PURPOSES)
    assert all(call["store"] is False for call in calls)


def test_store_cannot_be_turned_on_by_a_caller(configured):
    """There is no parameter through which a consumer could opt into retention."""
    import inspect
    assert "store" not in inspect.signature(P.generate).parameters
    assert "store" not in inspect.signature(P.ResponsesProvider.generate).parameters
    assert P.ResponsesProvider().diagnostics()["store"] is False


def test_request_carries_no_identifier_fields(configured):
    """metadata/user/safety_identifier/prompt_cache_key are where a client id would end up."""
    factory, calls, _ = _factory()
    _generate(client_factory=factory)
    for field in ("metadata", "user", "safety_identifier", "prompt_cache_key"):
        assert field not in calls[0]


def test_result_records_that_the_request_was_not_stored(configured):
    factory, _, _ = _factory()
    result = _generate(client_factory=factory)
    assert result.stored is False
    assert result.audit_metadata["stored"] is False


def test_audit_metadata_never_carries_model_output(configured):
    """Audit records posture and counters. The answer may be client-derived; it stays out."""
    factory, _, _ = _factory(_Response(output_text="Jane Doe's 2024 K-1"))
    result = _generate(client_factory=factory)
    assert "Jane Doe" not in str(result.audit_metadata)
    assert "output_text" not in result.audit_metadata


# --- the connectivity check --------------------------------------------------


def test_connectivity_check_sends_only_fixed_literals(configured):
    """The probe must be incapable of carrying client, document or staff data."""
    factory, calls, _ = _factory(_Response(output_text="ok"))

    result = P.check_connectivity(client_factory=factory)

    assert result.ok is True
    assert len(calls) == 1
    payload = calls[0]
    assert payload["instructions"] == P.CONNECTIVITY_INSTRUCTIONS == "Reply with the single word: ok"
    assert payload["input"] == P.CONNECTIVITY_INPUT == "ping"
    assert payload["max_output_tokens"] == P.CONNECTIVITY_MAX_OUTPUT_TOKENS
    assert payload["store"] is False
    assert payload["model"] == MODEL
    # Nothing else is transmitted at all.
    assert set(payload) == {"model", "instructions", "input", "max_output_tokens", "store"}


def test_connectivity_check_verifies_the_configured_model_answers(configured):
    """Auth alone is not enough: the check must exercise the model the operator configured."""
    factory, calls, _ = _factory()
    P.check_connectivity(client_factory=factory)
    assert calls[0]["model"] == config.openai_model()


def test_preflight_reports_posture_without_the_key(configured):
    report = P.preflight()
    assert report["api_key_configured"] is True
    assert report["model"] == MODEL
    assert report["store"] is False
    assert API_KEY not in str(report)


# --- failure modes -----------------------------------------------------------


def test_authentication_failure(configured):
    _, response = _request(401)
    factory, _, _ = _factory(raises=openai.AuthenticationError("bad key", response=response,
                                                              body=None))
    result = _generate(client_factory=factory)

    assert result.ok is False and result.status == P.FAILED
    assert result.failure_class == P.AUTH_FAILED
    assert result.detail == "OpenAI rejected the configured credential. (AuthenticationError)"


def test_timeout(configured):
    req, _ = _request()
    factory, _, _ = _factory(raises=openai.APITimeoutError(request=req))
    result = _generate(client_factory=factory)

    assert result.failure_class == P.TIMEOUT
    assert "timeout" in result.detail.lower()


def test_transport_failure(configured):
    req, _ = _request()
    factory, _, _ = _factory(raises=openai.APIConnectionError(message="down", request=req))
    result = _generate(client_factory=factory)
    assert result.failure_class == P.TRANSPORT_ERROR


def test_rate_limit(configured):
    _, response = _request(429)
    factory, _, _ = _factory(raises=openai.RateLimitError("slow down", response=response,
                                                          body=None))
    result = _generate(client_factory=factory)
    assert result.failure_class == P.RATE_LIMITED


def test_unavailable_model_fails_and_no_other_model_is_tried(configured):
    _, response = _request(404)
    factory, calls, _ = _factory(raises=openai.NotFoundError("no such model", response=response,
                                                             body=None))
    result = _generate(client_factory=factory)

    assert result.failure_class == P.MODEL_UNAVAILABLE
    assert "No other model was tried" in result.detail
    assert len(calls) == 1                        # exactly one attempt, one model


def test_api_error(configured):
    _, response = _request(500)
    factory, _, _ = _factory(raises=openai.InternalServerError("boom", response=response,
                                                               body=None))
    result = _generate(client_factory=factory)
    assert result.failure_class == P.API_ERROR


def test_unexpected_exception_is_still_a_result_not_a_raise(configured):
    factory, _, _ = _factory(raises=ValueError("something nobody predicted"))
    result = _generate(client_factory=factory)
    assert result.ok is False
    assert result.failure_class == P.UNEXPECTED_ERROR


@pytest.mark.parametrize("bad", [None, 42, object()])
def test_malformed_response(configured, bad):
    """A response that is not shaped like one fails cleanly, never as an AttributeError."""
    factory, _, _ = _factory(_Response(output_text=bad))
    result = _generate(client_factory=factory)

    assert result.ok is False
    assert result.failure_class == P.MALFORMED_RESPONSE
    assert result.output_text == ""


def test_malformed_usage_does_not_break_the_result(configured):
    response = _Response()
    response.usage = None
    factory, _, _ = _factory(response)
    result = _generate(client_factory=factory)
    assert result.ok is True and (result.input_tokens, result.output_tokens) == (0, 0)


def test_failure_map_covers_the_real_sdk_exception_classes():
    """The mapping is by class name; this keeps that table honest against the installed SDK."""
    for name in P._FAILURE_BY_EXCEPTION:
        assert hasattr(openai, name), f"{name} is no longer an openai exception class"


# --- the API key never escapes ------------------------------------------------


def test_api_key_never_appears_in_a_result_even_when_the_sdk_quotes_it(configured, caplog):
    """The failure mode this guards: SDK errors can quote the credential; str(exc) would leak it."""
    _, response = _request(401)
    leaky = openai.AuthenticationError(f"Incorrect API key provided: {API_KEY}",
                                       response=response, body=None)
    assert API_KEY in str(leaky)                  # the exception really does carry the key

    factory, _, _ = _factory(raises=leaky)
    with caplog.at_level(logging.DEBUG, logger="client360.openai_provider"):
        result = _generate(client_factory=factory)

    for value in (result.detail, str(result), str(result.audit_metadata)):
        assert API_KEY not in value
    assert API_KEY not in caplog.text
    assert all(API_KEY not in record.getMessage() for record in caplog.records)


def test_successful_call_logs_no_credential_and_no_content(configured, caplog):
    factory, _, _ = _factory(_Response(output_text="Jane Doe 2024 Schedule K-1"))
    with caplog.at_level(logging.DEBUG, logger="client360.openai_provider"):
        _generate(client_factory=factory)

    assert API_KEY not in caplog.text
    assert "Jane Doe" not in caplog.text          # model output is never logged
    assert "hello" not in caplog.text             # nor is the input


def test_diagnostics_expose_presence_not_the_key(configured):
    info = P.diagnostics()
    flat = str(info)
    assert API_KEY not in flat
    assert API_KEY[:8] not in flat                # not even a prefix
    assert str(len(API_KEY)) not in str(info.get("api_key_configured"))
    assert info["api_key_configured"] is True


def test_scrub_removes_credential_shaped_tokens(configured):
    # 1. The CONFIGURED key is redacted by exact match.
    scrubbed = P._scrub(f"boom: {API_KEY}")
    assert API_KEY not in scrubbed
    assert P._REDACTED in scrubbed

    # 2. Any credential-SHAPED token is redacted too, even when it is not the configured key —
    #    a stale key, or one belonging to another environment, still goes.
    scrubbed = P._scrub(f"leaked {FOREIGN_KEY} here")
    assert FOREIGN_KEY not in scrubbed
    assert P._REDACTED in scrubbed
    assert scrubbed.startswith("leaked ") and scrubbed.endswith(" here")   # only the token went


#: Source-ish files only. Compiled artifacts under __pycache__ embed the absolute build path, which
#: on a developer's machine can contain anything at all — including the word this test searches for.
_SOURCE_SUFFIXES = {".py", ".html", ".js", ".css", ".json", ".yaml", ".yml", ".txt", ".md", ".sql"}


def _is_source(path: pathlib.Path) -> bool:
    return "__pycache__" not in path.parts and path.suffix in _SOURCE_SUFFIXES


def _app_files_mentioning(needle: str) -> set[str]:
    """Every file under app/ containing ``needle`` — the filesystem, not git, so the answer is the
    same whether or not the new files have been staged yet."""
    root = pathlib.Path(__file__).resolve().parent.parent
    hits = set()
    for path in (root / "app").rglob("*"):
        if not path.is_file() or not _is_source(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if needle in text:
            hits.add(path.relative_to(root).as_posix())
    return hits


def test_only_the_provider_and_config_mention_the_api_key():
    """One auditable point of entry. config.py checks PRESENCE; the provider reads the VALUE."""
    assert _app_files_mentioning("OPENAI_API_KEY") == {
        "app/config.py", "app/services/openai_provider.py"}


def test_config_only_tests_the_key_for_presence():
    """config.openai_api_key_configured() must not be able to return the credential itself."""
    source = pathlib.Path(config.__file__).read_text()
    getenv_lines = [ln.strip() for ln in source.splitlines()
                    if "OPENAI_API_KEY" in ln and "getenv" in ln]
    assert getenv_lines, "expected config.py to read the variable for a presence check"
    assert all(ln.startswith("return bool(") for ln in getenv_lines), getenv_lines
    assert isinstance(config.openai_api_key_configured(), bool)


def test_the_key_can_never_reach_a_browser():
    """No route, template or static asset references the credential — or OpenAI at all.

    The OPENAI_API_KEY half is permanent. The broader "no mention of openai" half is a PHASE 1
    boundary: nothing user-facing may touch the provider yet. A later phase that ships a real
    consumer or a diagnostics panel is expected to narrow this deliberately, not by accident."""
    root = pathlib.Path(__file__).resolve().parent.parent
    for surface in ("routes", "templates", "static", "portal"):
        for path in (root / "app" / surface).rglob("*"):
            if not path.is_file() or not _is_source(path):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "OPENAI_API_KEY" not in text, path
            assert "openai" not in text.lower(), path


# --- the seam ----------------------------------------------------------------


def test_unknown_purpose_is_refused_before_anything_is_built(configured):
    factory, calls, keys = _factory()
    result = _generate(purpose="exfiltrate_everything", client_factory=factory)

    assert result.failure_class == P.UNKNOWN_PURPOSE
    assert calls == [] and keys == []


def test_declared_purposes_are_the_planned_consumers():
    """Phase 1 declares the seam's consumers; none of them are implemented yet."""
    assert P.PURPOSES == {"connectivity_check", "document_filename_normalization",
                          "document_classification", "document_understanding",
                          "advisor_ai", "staff_assistance"}


def test_no_consumer_calls_the_provider_yet():
    """Phase 1 adds the provider and nothing that uses it. config.py only DOCUMENTS it."""
    assert _app_files_mentioning("openai_provider") == {
        "app/config.py", "app/services/openai_provider.py"}


def test_document_and_ocr_services_are_untouched():
    """The named future consumers must not have grown an OpenAI call in this phase."""
    root = pathlib.Path(__file__).resolve().parent.parent
    for name in ("document_ocr.py", "document_naming.py", "document_classification.py",
                 "document_pipeline.py", "advisor_ai.py"):
        text = (root / "app" / "services" / name).read_text(encoding="utf-8")
        assert "openai" not in text.lower(), name


def test_provider_is_swappable(configured):
    class _Fake(P.OpenAIProvider):
        def generate(self, **kwargs):
            return P.ProviderResult(ok=True, status=P.OK, output_text="fake")

        def diagnostics(self):
            return {"provider": "fake"}

    original = P.get_provider()
    try:
        P.set_provider(_Fake())
        assert P.generate(purpose="advisor_ai", instructions="i", input_text="t").output_text \
            == "fake"
        assert P.diagnostics() == {"provider": "fake"}
    finally:
        P.set_provider(original)
    assert P.get_provider() is original


def test_empty_input_is_refused(configured):
    factory, calls, _ = _factory()
    result = _generate(input_text="   ", client_factory=factory)
    assert result.failure_class == P.EMPTY_REQUEST
    assert calls == []
