"""Model-provider seam for Advisor AI Assist (Phase D.42).

No LLM infrastructure exists in the platform, so this is the smallest possible provider abstraction plus
a DETERMINISTIC, OFFLINE ``LocalProvider`` (the default). The local provider composes a structured brief
from the already-grounded, already-authorized context facts — it makes NO network call and needs NO
credentials, so the whole suite runs offline. A real model provider can be slotted in later behind the
same ``AssistProvider`` contract; provider configuration would come from the Runtime/Configuration
services (never hard-coded secrets). The local provider also simulates timeout / failure / malformed /
refusal for tests.
"""
from __future__ import annotations

from .contracts import Citation, ContextBundle


class ProviderTimeout(Exception):
    pass


class ProviderError(Exception):
    pass


class AssistProvider:
    """Provider contract: deterministic structured generation from an authorized context bundle."""
    model = "abstract"
    available = False

    def generate(self, capability: str, bundle: ContextBundle, *, prompt: str, options=None) -> dict:
        raise NotImplementedError

    def diagnostics(self) -> dict:
        return {"model": self.model, "available": self.available, "kind": type(self).__name__}


# Section grouping per capability — deterministic, references-only.
_SECTION_ORDER = {
    "daily_brief": ("today", "priorities", "work", "meetings", "compliance"),
    "client_brief": ("identity", "financial", "work", "opportunities", "tax", "insurance", "benefits",
                     "compliance", "meetings", "relationships"),
    "household_brief": ("identity", "members", "financial", "work", "opportunities", "meetings",
                        "compliance", "relationships"),
    "meeting_prep": ("meeting", "context", "open_items", "deadlines", "questions"),
    "work_explanation": ("item", "why", "next_step"),
    "factual_question_answering": ("answer",),
}


class LocalProvider(AssistProvider):
    """Deterministic offline provider — assembles a structured brief from grounded facts."""
    model = "local-deterministic"
    available = True

    def generate(self, capability, bundle, *, prompt, options=None):
        options = options or {}
        sim = options.get("simulate")
        if sim == "timeout":
            raise ProviderTimeout("simulated timeout")
        if sim == "failure":
            raise ProviderError("simulated provider failure")
        if sim == "malformed":
            return {"sections": None}   # missing required content → validation rejects
        if sim == "refusal":
            return {"refused": True, "refusal_category": "model_refusal"}

        # Group facts into ordered sections (references-only; the values are already minimized).
        sections: dict = {name: [] for name in _SECTION_ORDER.get(capability, ("summary",))}
        for f in bundle.facts:
            group = _group_for(capability, f.fact_key)
            sections.setdefault(group, []).append(
                {"label": _label(f.fact_key), "value": f.fact_value, "class": f.fact_class,
                 "available": f.available, "source": f.source_label, "deep_link": f.deep_link})
        # Citations: one per distinct source used.
        seen, citations = set(), []
        for f in bundle.facts:
            if f.source_label in seen:
                continue
            seen.add(f.source_label)
            citations.append(Citation(source=f.source_label,
                                      fact_keys=tuple(g.fact_key for g in bundle.facts
                                                      if g.source_label == f.source_label),
                                      deep_link=f.deep_link))
        return {"sections": {k: v for k, v in sections.items() if v},
                "citations": citations,
                "narrative": _narrative(capability, bundle),
                "extra_limitations": []}


def _group_for(capability, fact_key):
    prefix = fact_key.split(".", 1)[0]
    order = _SECTION_ORDER.get(capability, ())
    return prefix if prefix in order else (order[0] if order else "summary")


def _label(fact_key):
    return fact_key.split(".", 1)[-1].replace("_", " ").capitalize()


def _narrative(capability, bundle):
    n = len([f for f in bundle.facts if f.available])
    return (f"{n} platform fact(s) from {', '.join(bundle.sources_used) or 'no sources'} "
            f"composed for {capability.replace('_', ' ')}. Review required before any action.")


# --- provider selection ------------------------------------------------------

_PROVIDER: AssistProvider = LocalProvider()


def get_provider() -> AssistProvider:
    return _PROVIDER


def set_provider(provider: AssistProvider):
    """Swap the provider (tests / future real provider wired via Runtime/Configuration)."""
    global _PROVIDER
    _PROVIDER = provider


def provider_diagnostics() -> dict:
    return get_provider().diagnostics()
