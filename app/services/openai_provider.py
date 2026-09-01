"""The single server-side seam between Client360 and OpenAI (Phase 1).

Nothing in the platform calls OpenAI today. This module exists so that when something does, there
is exactly ONE place where the credential is read, the model is chosen, the timeout and retry
budget are set, the retention flag is pinned, and failures are turned into safe, structured
outcomes. Scattering ``OpenAI()`` calls through routes and document services would put a live API
key, an unbounded network call, and a client-data retention decision into every one of them.

WHAT THIS PHASE DOES NOT DO. It ships the provider and a harmless connectivity check, and nothing
else. Document filename normalization, document classification, OCR/document understanding, Advisor
AI and staff assistance are the intended CONSUMERS (see :data:`PURPOSES`) and are deliberately not
implemented here; no document is read, renamed, classified or altered by anything in this file.

CREDENTIAL BOUNDARY.
  * ``OPENAI_API_KEY``'s value is read in exactly one function, :func:`_api_key`, at call time.
    It is never stored on an object, never passed to a template, never returned by any function
    here, and never crosses into a response body — every public return value is a
    :class:`ProviderResult`, whose fields are enumerated and contain no credential.
  * Nothing here logs the key, and no exception TEXT from the SDK is ever surfaced or logged
    (see :func:`_safe_detail`). SDK errors can quote request headers and echo request content;
    only the exception's CLASS is used, mapped to a fixed failure class and a fixed sentence.
  * :func:`_scrub` is the belt-and-braces backstop: any string this module emits is run through it,
    so a credential-shaped token cannot escape even by a route nobody anticipated.

FAIL CLOSED, NEVER SILENTLY.
  * Disabled by default. With ``OPENAI_ENABLED`` off, :func:`generate` returns before a client is
    built and makes no network call.
  * NO fallback model. If ``OPENAI_MODEL`` is unset the request is refused; if the configured model
    is rejected by the API the failure is reported as ``model_unavailable``. The provider never
    retries against a different model — spending money on a model the operator did not choose, and
    returning output attributed to one, are both worse than an honest failure.
  * ``store=False`` is pinned on EVERY request (:func:`_request_kwargs`) and is not a parameter.
    Client360 handles client financial, tax and identity records; Phase 1 takes the position that
    none of it is retained by the API, and removes the possibility of getting that wrong per-call.
    A future phase that wants retention must add an explicitly reviewed opt-in — it cannot happen
    by accident here.
  * No request carries ``metadata``, ``user``, ``safety_identifier`` or ``prompt_cache_key``. Those
    are the fields into which a caller would naturally drop a client or household id.
  * :func:`generate` never raises. The outcome is the return value, so a caller cannot turn a
    provider failure into a 500 by forgetting a ``try``.

The ``openai`` SDK is imported LAZILY, so the app — and the whole test suite — imports cleanly on a
host that does not have it; a missing SDK is reported as ``sdk_missing``, not an ImportError at
startup.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import re
import time
from dataclasses import dataclass

from app import config

log = logging.getLogger("client360.openai_provider")

PROVIDER = "openai"

# --- outcome vocabulary ------------------------------------------------------
# Mirrors app/services/email_delivery.py: an honest status the caller can show a human, never a
# bare bool. "disabled" and "not_configured" are normal states, not errors.
OK = "ok"
DISABLED = "disabled"
NOT_CONFIGURED = "not_configured"
FAILED = "failed"

# --- failure classes ---------------------------------------------------------
PROVIDER_DISABLED = "provider_disabled"
PROVIDER_NOT_CONFIGURED = "provider_not_configured"
SDK_MISSING = "sdk_missing"
UNKNOWN_PURPOSE = "unknown_purpose"
EMPTY_REQUEST = "empty_request"
AUTH_FAILED = "auth_failed"
RATE_LIMITED = "rate_limited"
MODEL_UNAVAILABLE = "model_unavailable"
REQUEST_REJECTED = "request_rejected"
TIMEOUT = "timeout"
TRANSPORT_ERROR = "transport_error"
API_ERROR = "api_error"
MALFORMED_RESPONSE = "malformed_response"
UNEXPECTED_ERROR = "unexpected_error"

#: The consumers this seam is being built for. A purpose must be declared here before any code can
#: call through — an undeclared purpose is refused rather than quietly allowed, so the set of things
#: that may send data to OpenAI stays reviewable in one place. NONE of these are implemented yet.
PURPOSES = frozenset({
    "connectivity_check",              # this module's own harmless health check (no client data)
    "document_filename_normalization",
    "document_classification",
    "document_understanding",          # OCR / document understanding
    "advisor_ai",
    "staff_assistance",
})

#: Conservative ceiling so a caller cannot accidentally commission an essay.
DEFAULT_MAX_OUTPUT_TOKENS = 1024

_REDACTED = "[REDACTED]"
#: Credential-shaped tokens, scrubbed even when they are not the configured key (a stale key in an
#: error body, a key belonging to another environment). Deliberately broad.
_KEY_SHAPED = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")


# --- configuration -----------------------------------------------------------

def _api_key() -> str:
    """The raw ``OPENAI_API_KEY``, read at call time.

    THE ONLY read of this value in the codebase. Callers get the key into a local frame, hand it
    straight to the SDK client, and let it go; it is never returned upward. Use
    :func:`app.config.openai_api_key_configured` when you only need to know whether one exists."""
    return (os.getenv("OPENAI_API_KEY", "") or "").strip()


def is_enabled() -> bool:
    return config.openai_enabled()


def configured_model() -> str:
    return config.openai_model()


def configuration_status() -> tuple[bool, str]:
    """``(ready, reason)``. Never raises, never echoes a secret value.

    ``reason`` names the missing variable — a NAME is operationally necessary and carries nothing
    sensitive; a VALUE never appears."""
    if not is_enabled():
        return False, "OPENAI_ENABLED is not set"
    if not config.openai_api_key_configured():
        return False, "OPENAI_API_KEY is not set"
    if not configured_model():
        return False, "OPENAI_MODEL is not set"
    return True, ""


def _scrub(text) -> str:
    """Remove credential material from any string this module is about to emit.

    The primary controls are upstream (no exception text is surfaced at all); this is the backstop
    that makes "the key never appears in a log or an error" true by construction rather than by
    review of every call site."""
    out = str(text)
    key = _api_key()
    if key and key in out:
        out = out.replace(key, _REDACTED)
    return _KEY_SHAPED.sub(_REDACTED, out)


# --- results -----------------------------------------------------------------

@dataclass(frozen=True)
class ProviderResult:
    """The honest outcome of one provider call.

    ``detail`` is staff-facing and safe: it never contains an API response body, a stack trace, an
    SDK exception message, or a credential. ``output_text`` is the model's answer and is therefore
    the ONE field that may contain content derived from the caller's input — it is excluded from
    :attr:`audit_metadata` for exactly that reason."""

    ok: bool
    status: str
    purpose: str = ""
    model: str = ""
    failure_class: str | None = None
    detail: str = ""
    output_text: str = ""
    response_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    #: Always False. Pinned by _request_kwargs; recorded so an audit trail can PROVE the request
    #: asked the API not to retain it, rather than asserting it in a comment.
    stored: bool = False

    @property
    def audit_metadata(self) -> dict:
        """Safe metadata for an audit event — posture and counters only, never content."""
        data = {"provider": PROVIDER, "status": self.status, "purpose": self.purpose,
                "model": self.model, "stored": self.stored, "latency_ms": self.latency_ms,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens}
        if self.failure_class:
            data["failure_class"] = self.failure_class
        return data


def _failure(status, failure_class, detail, *, purpose="", model="", latency_ms=0):
    return ProviderResult(ok=False, status=status, purpose=purpose, model=model,
                          failure_class=failure_class, detail=_scrub(detail),
                          latency_ms=latency_ms)


# --- error classification ----------------------------------------------------
# Mapped by exception CLASS NAME walked up the MRO, not by isinstance. Two reasons: this module must
# classify without importing the SDK at module scope, and the mapping stays readable as a table.
_FAILURE_BY_EXCEPTION = {
    "APITimeoutError": TIMEOUT,
    "APIConnectionError": TRANSPORT_ERROR,
    "AuthenticationError": AUTH_FAILED,
    "PermissionDeniedError": AUTH_FAILED,
    "RateLimitError": RATE_LIMITED,
    "NotFoundError": MODEL_UNAVAILABLE,
    "BadRequestError": REQUEST_REJECTED,
    "UnprocessableEntityError": REQUEST_REJECTED,
    "InternalServerError": API_ERROR,
    "ConflictError": API_ERROR,
    "APIStatusError": API_ERROR,
    "APIError": API_ERROR,
    "OpenAIError": API_ERROR,
}

#: One fixed sentence per failure class. The SDK's own message is never used: it can quote request
#: headers (credential) and echo request content (client data) back into our logs and UI.
_DETAIL_BY_FAILURE = {
    AUTH_FAILED: "OpenAI rejected the configured credential.",
    RATE_LIMITED: "OpenAI rate-limited the request; it was not completed.",
    MODEL_UNAVAILABLE: ("The configured OPENAI_MODEL is not available to this account. No other "
                        "model was tried."),
    REQUEST_REJECTED: "OpenAI rejected the request as invalid.",
    TIMEOUT: "The OpenAI request exceeded its timeout budget.",
    TRANSPORT_ERROR: "Client360 could not reach OpenAI.",
    API_ERROR: "OpenAI returned an error.",
    UNEXPECTED_ERROR: "The OpenAI request failed unexpectedly.",
}


def _classify(exc) -> str:
    for cls in type(exc).__mro__:
        hit = _FAILURE_BY_EXCEPTION.get(cls.__name__)
        if hit:
            return hit
    return UNEXPECTED_ERROR


def _safe_detail(failure_class: str, exc) -> str:
    """A fixed sentence plus the exception's CLASS name — never ``str(exc)``."""
    sentence = _DETAIL_BY_FAILURE.get(failure_class, _DETAIL_BY_FAILURE[UNEXPECTED_ERROR])
    return _scrub(f"{sentence} ({type(exc).__name__})")


# --- request construction ----------------------------------------------------

def _request_kwargs(*, model, instructions, input_text, max_output_tokens, temperature) -> dict:
    """Build the Responses API payload.

    ``store=False`` is set HERE, unconditionally, and is not reachable from any caller. Note what is
    absent as much as what is present: no ``metadata``, ``user``, ``safety_identifier`` or
    ``prompt_cache_key`` — the fields a caller would naturally use to attach a client or household
    id, which would put an identifier in front of the API independently of the prompt."""
    kwargs = {
        "model": model,
        "instructions": instructions,
        "input": input_text,
        "max_output_tokens": max_output_tokens,
        "store": False,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    return kwargs


def _default_client_factory(api_key: str):
    """The real SDK client. Injected in tests — no network call is ever made there.

    Timeout and bounded retries are configured on the CLIENT, so they apply to every request made
    through it and cannot be forgotten at a call site."""
    from openai import OpenAI

    return OpenAI(api_key=api_key, timeout=config.openai_timeout_seconds(),
                  max_retries=config.openai_max_retries())


def _extract_output_text(response) -> str | None:
    """The response's text, or ``None`` when the response is not shaped like one.

    A provider that returns a malformed object must fail as ``malformed_response``, not raise an
    AttributeError three layers up in a caller that has no idea what an SDK object looks like."""
    text = getattr(response, "output_text", None)
    if text is None or not isinstance(text, str):
        return None
    return text


def _usage_counts(response) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    def _count(name):
        value = getattr(usage, name, 0)
        return value if isinstance(value, int) else 0
    return _count("input_tokens"), _count("output_tokens")


# --- the provider seam -------------------------------------------------------

class OpenAIProvider:
    """The provider contract future consumers depend on.

    Deliberately narrow: one text-in / text-out call plus diagnostics. Consumers (filename
    normalization, classification, document understanding, Advisor AI, staff assistance) will each
    own their own prompt and their own validation of what comes back; what they must NOT own is the
    credential, the timeout, the retry budget or the retention flag. Those live here.

    The seam mirrors app/services/ai_assist/provider.py, which established get_provider() /
    set_provider() for exactly this reason — a test or a future implementation can swap the whole
    provider without any consumer knowing."""

    name = PROVIDER

    def generate(self, *, purpose, instructions, input_text, model=None,
                 max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS, temperature=None,
                 client_factory=None) -> ProviderResult:
        raise NotImplementedError

    def diagnostics(self) -> dict:
        raise NotImplementedError


class ResponsesProvider(OpenAIProvider):
    """The real provider — OpenAI Responses API, server-side, disabled by default."""

    def generate(self, *, purpose, instructions, input_text, model=None,
                 max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS, temperature=None,
                 client_factory=None) -> ProviderResult:
        """One Responses API call. NEVER raises — the outcome is the return value.

        ``model`` overrides the configured model for a caller that legitimately needs a different
        one; passing None (the normal case) uses ``OPENAI_MODEL``. An empty configured model is a
        refusal, never a substitution."""
        if purpose not in PURPOSES:
            return _failure(FAILED, UNKNOWN_PURPOSE,
                            "This purpose is not declared in openai_provider.PURPOSES; it was "
                            "refused before any request was built.", purpose=str(purpose))

        # Posture first: refuse before a client exists, so a disabled or half-configured
        # deployment cannot make a network call under any code path below.
        if not is_enabled():
            return _failure(DISABLED, PROVIDER_DISABLED,
                            "The OpenAI provider is disabled (OPENAI_ENABLED is not set).",
                            purpose=purpose)
        ready, reason = configuration_status()
        if not ready:
            return _failure(NOT_CONFIGURED, PROVIDER_NOT_CONFIGURED,
                            f"The OpenAI provider is not configured ({reason}).", purpose=purpose)

        model = (model or configured_model()).strip()
        if not model:
            return _failure(NOT_CONFIGURED, PROVIDER_NOT_CONFIGURED,
                            "The OpenAI provider is not configured (OPENAI_MODEL is not set).",
                            purpose=purpose)
        if not (input_text or "").strip():
            return _failure(FAILED, EMPTY_REQUEST, "There was nothing to send.",
                            purpose=purpose, model=model)

        try:
            client = (client_factory or _default_client_factory)(_api_key())
        except ImportError:
            return _failure(FAILED, SDK_MISSING,
                            "The openai package is not installed on this host.",
                            purpose=purpose, model=model)
        except Exception as exc:                  # noqa: BLE001 — never leak SDK construction detail
            failure_class = _classify(exc)
            return _failure(FAILED, failure_class, _safe_detail(failure_class, exc),
                            purpose=purpose, model=model)

        kwargs = _request_kwargs(model=model, instructions=instructions, input_text=input_text,
                                 max_output_tokens=max_output_tokens, temperature=temperature)
        started = time.monotonic()
        try:
            response = client.responses.create(**kwargs)
        except Exception as exc:                  # noqa: BLE001 — every failure is a result
            failure_class = _classify(exc)
            elapsed = int((time.monotonic() - started) * 1000)
            # Names and classes only; no exception text, no payload, no credential.
            log.warning("openai request failed: purpose=%s model=%s failure=%s exception=%s",
                        purpose, model, failure_class, type(exc).__name__)
            return _failure(FAILED, failure_class, _safe_detail(failure_class, exc),
                            purpose=purpose, model=model, latency_ms=elapsed)

        elapsed = int((time.monotonic() - started) * 1000)
        text = _extract_output_text(response)
        if text is None:
            log.warning("openai returned a malformed response: purpose=%s model=%s", purpose, model)
            return _failure(FAILED, MALFORMED_RESPONSE,
                            "OpenAI returned a response Client360 could not read.",
                            purpose=purpose, model=model, latency_ms=elapsed)

        input_tokens, output_tokens = _usage_counts(response)
        response_id = getattr(response, "id", "") or ""
        # The model that actually answered. Reported, never silently accepted as a substitute: a
        # caller that cares can compare it against `model`.
        answered_with = getattr(response, "model", "") or model
        log.info("openai request ok: purpose=%s model=%s stored=False latency_ms=%s",
                 purpose, answered_with, elapsed)
        return ProviderResult(ok=True, status=OK, purpose=purpose, model=str(answered_with),
                              output_text=text, response_id=_scrub(response_id),
                              input_tokens=input_tokens, output_tokens=output_tokens,
                              latency_ms=elapsed, stored=False)

    def diagnostics(self) -> dict:
        """Posture without a network call, and without the credential.

        ``api_key_configured`` is a BOOLEAN. There is no diagnostic anywhere that reveals the key,
        a prefix of it, or its length."""
        ready, reason = configuration_status()
        return {"provider": PROVIDER, "kind": type(self).__name__,
                "enabled": is_enabled(),
                "api_key_configured": config.openai_api_key_configured(),
                "model": configured_model(),
                "timeout_seconds": config.openai_timeout_seconds(),
                "max_retries": config.openai_max_retries(),
                "store": False,
                "ready": ready, "reason": reason,
                "purposes": sorted(PURPOSES)}


_PROVIDER: OpenAIProvider = ResponsesProvider()


def get_provider() -> OpenAIProvider:
    return _PROVIDER


def set_provider(provider: OpenAIProvider) -> None:
    """Swap the provider (tests / a future implementation behind the same contract)."""
    global _PROVIDER
    _PROVIDER = provider


def generate(*, purpose, instructions, input_text, model=None,
             max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS, temperature=None,
             client_factory=None) -> ProviderResult:
    """Module-level entry point. The one call every future consumer should make."""
    return get_provider().generate(purpose=purpose, instructions=instructions,
                                   input_text=input_text, model=model,
                                   max_output_tokens=max_output_tokens, temperature=temperature,
                                   client_factory=client_factory)


def diagnostics() -> dict:
    return get_provider().diagnostics()


# --- connectivity check ------------------------------------------------------
# Verifies four things in one round trip: the provider is configured, it is enabled, the credential
# authenticates, and the CONFIGURED model actually answers. A credential check alone would pass on
# an account that cannot use the model, which is the failure operators actually hit.

CONNECTIVITY_PURPOSE = "connectivity_check"
#: Fixed literals. NO client, document, household or staff data — nothing from a database, a
#: request, or a file reaches this payload, and there is no parameter through which it could.
#: tests/test_openai_provider.py asserts the transmitted payload is exactly these.
CONNECTIVITY_INSTRUCTIONS = "Reply with the single word: ok"
CONNECTIVITY_INPUT = "ping"
#: Just enough for the one word, so a health check cannot become an expensive call.
CONNECTIVITY_MAX_OUTPUT_TOKENS = 16


def check_connectivity(*, client_factory=None) -> ProviderResult:
    """Harmless end-to-end check of the OpenAI provider. Never raises.

    Sends a fixed two-word probe and nothing else. Safe to run in any environment: with the provider
    disabled or unconfigured it returns that posture without touching the network."""
    return generate(purpose=CONNECTIVITY_PURPOSE,
                    instructions=CONNECTIVITY_INSTRUCTIONS,
                    input_text=CONNECTIVITY_INPUT,
                    max_output_tokens=CONNECTIVITY_MAX_OUTPUT_TOKENS,
                    client_factory=client_factory)


def preflight() -> dict:
    """Operational health check: posture + a live connectivity probe. Used by the deploy preflight.

    Returns ``{ok, enabled, api_key_configured, model, sdk_installed, status, failure_class,
    detail, latency_ms}``. No credential, and no client data, appears in the result."""
    info = diagnostics()
    # find_spec, not an import: a health check should not have the side effect of loading the SDK,
    # and absence is an answer here, never a crash.
    try:
        sdk_installed = importlib.util.find_spec("openai") is not None
    except (ImportError, ValueError):
        sdk_installed = False

    result = check_connectivity()
    return {"ok": result.ok,
            "enabled": info["enabled"],
            "api_key_configured": info["api_key_configured"],
            "model": info["model"],
            "sdk_installed": sdk_installed,
            "timeout_seconds": info["timeout_seconds"],
            "max_retries": info["max_retries"],
            "store": False,
            "status": result.status,
            "failure_class": result.failure_class,
            "detail": result.detail,
            "latency_ms": result.latency_ms}


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(
        prog="python -m app.services.openai_provider",
        description="OpenAI provider preflight / connectivity check. Sends no client data.")
    p.add_argument("--check", action="store_true", help="Check configuration + connectivity.")
    p.parse_args(argv)
    r = preflight()
    print(f"OpenAI provider preflight: {'OK' if r['ok'] else 'NOT READY'}")
    print(f"  enabled: {r['enabled']}")
    print(f"  api key configured: {r['api_key_configured']}")   # boolean; never the value
    print(f"  model: {r['model'] or '(unset)'}")
    print(f"  sdk installed: {r['sdk_installed']}")
    print(f"  timeout: {r['timeout_seconds']}s  max retries: {r['max_retries']}  store: False")
    print(f"  status: {r['status']}")
    if r["failure_class"]:
        print(f"  failure: {r['failure_class']}")
    if r["detail"]:
        print(f"  detail: {r['detail']}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
