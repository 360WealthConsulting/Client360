# AI Assist Provider Guide (Phase D.42)

How the model-provider seam works and how to add a real provider. See [`ADVISOR_AI_ASSIST.md`](ADVISOR_AI_ASSIST.md),
[`ADR-047`](adr/ADR-047-advisor-ai-assist.md).

## The seam

`app/services/ai_assist/provider.py` defines the smallest possible provider contract:

```python
class AssistProvider:
    model = "..."
    available = bool
    def generate(self, capability, bundle, *, prompt, options=None) -> dict: ...
    def diagnostics(self) -> dict: ...
```

`generate` receives the **already-grounded, already-authorized, minimized** `ContextBundle` (the provider
never assembles context itself) and returns `{sections, citations, narrative, extra_limitations}` (or
`{"refused": True, "refusal_category": ...}`). The assistant validates the result and wraps it in the
required output envelope.

## The default: `LocalProvider` (deterministic, offline)

No LLM infrastructure exists in the platform, so the default provider is deterministic and **makes no
network call and needs no credentials** — the whole test suite runs offline. It groups the bundle's
grounded facts into ordered sections per capability and derives citations from the facts' sources. It
also **simulates** provider behaviors for tests via `options["simulate"]`:

- `"timeout"` → raises `ProviderTimeout`;
- `"failure"` → raises `ProviderError`;
- `"malformed"` → returns missing content (validation rejects → fail-closed fallback);
- `"refusal"` → returns a model refusal.

## Selection + configuration

`get_provider()` returns the configured provider (default `LocalProvider`); `set_provider(...)` swaps it
(tests, or a future real provider). Generation is gated by
`runtime.consumption.feature_enabled("advisor.ai_assist", default=True)` — a governed runtime gate, **no
raw env-var fallback**. When disabled, the assistant fails closed to deterministic source facts.

## Adding a real provider (future)

1. Implement `AssistProvider` (e.g. an HTTP client to a model API) behind the same `generate` contract —
   consume the supplied `prompt` + `bundle.facts`, request **deterministic structured output**, and map
   the response into `{sections, citations}`.
2. **Configuration comes from the Runtime / Configuration services** (model id, timeout, allowed model,
   context-size limit, prompt version) — **never hard-code secrets**; read credentials from the
   platform's secure configuration, not the source.
3. Enforce a **timeout** and **bounded retries**; on exhaustion raise `ProviderTimeout`/`ProviderError`
   so the assistant fails closed.
4. Register it via `set_provider(...)` at startup (guarded by `feature_enabled`), or keep `LocalProvider`
   as the default and select the real provider by runtime config.
5. Keep a **local test double** so CI never calls an external service — the suite must remain network-free.

## Diagnostics

`provider_diagnostics()` reports `{model, available, kind}`; the assist diagnostics route surfaces it
alongside prompt versions and counters — no secrets, no prompt contents.
