"""Advisor AI Assist orchestrator (Phase D.42).

For each capability: gate on the governed runtime feature, assemble the authorized minimized context,
invoke the provider (deterministic offline default), validate the structured output (required
citations/limitations/human-review can never be omitted), record safe in-process operational stats (no
DB write, no prompts, no client facts), and return the labelled envelope. Regulated requests are
refused. On provider timeout/failure/malformed output, or when the feature is disabled, it FAILS CLOSED
to deterministic source facts (never fabricated) with a "generation unavailable" label — never breaking
the caller. It NEVER mutates, NEVER writes to any database, NEVER publishes to the outbox, NEVER reads
rm_* tables.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime

from . import context as ctx
from .common import GENERATION_UNAVAILABLE, note, note_latency, note_refusal
from .contracts import (
    Citation,
    default_limitations,
    envelope,
    refusal_output,
    validate_output,
)
from .prompts import PROMPTS
from .provider import ProviderTimeout, get_provider
from .refusal import check_regulated


def _enabled() -> bool:
    """Governed runtime gate — no raw env fallback (Runtime authority + a hard default)."""
    try:
        from app.services.runtime import consumption
        return consumption.feature_enabled("advisor.ai_assist", default=True, shim=True)
    except Exception:
        return True


def _provider_status(available: bool, model: str) -> dict:
    return {"available": available, "model": model, "label": None if available else GENERATION_UNAVAILABLE}


def _sections_from_facts(bundle) -> dict:
    """Deterministic fallback grouping (no provider) — used when generation is unavailable."""
    sections: dict = {}
    for f in bundle.facts:
        group = f.fact_key.split(".", 1)[0]
        sections.setdefault(group, []).append(
            {"label": f.fact_key.split(".", 1)[-1].replace("_", " ").capitalize(),
             "value": f.fact_value, "class": f.fact_class, "available": f.available,
             "source": f.source_label, "deep_link": f.deep_link})
    return sections


def _citations_from_bundle(bundle) -> list:
    seen, out = set(), []
    for f in bundle.facts:
        if f.source_label in seen:
            continue
        seen.add(f.source_label)
        out.append(Citation(source=f.source_label, deep_link=f.deep_link,
                            fact_keys=tuple(g.fact_key for g in bundle.facts
                                            if g.source_label == f.source_label)))
    return out


def _run(capability, principal, *, entity_type=None, entity_id=None, **kwargs) -> dict:
    simulate = kwargs.pop("_simulate", None)   # test/provider hook — not part of context assembly
    note("requests", capability=capability)
    t0 = time.perf_counter()
    bundle = ctx.assemble(principal, capability, **kwargs)
    provider = get_provider()
    generation_ok = _enabled() and provider.available
    result = None
    if generation_ok:
        prompt = (PROMPTS.get(capability) or {}).get("template", "")
        try:
            gen = provider.generate(capability, bundle, prompt=prompt, options={"simulate": simulate})
            if gen.get("refused"):
                note_refusal(gen.get("refusal_category", "model_refusal"))
                out = refusal_output(gen.get("refusal_category", "model_refusal"),
                                     "The assistant declined to answer this request.")
                _finish_stats(t0, out)
                return out
            if gen.get("sections"):
                result = gen
        except Exception as exc:   # ANY provider fault fails closed — never breaks the caller
            note("timeouts" if isinstance(exc, ProviderTimeout) else "provider_failures")
            result = None

    if result is not None:
        problems = validate_output(_wrap(capability, bundle, result, available=True))
        if problems:
            note("malformed")
            result = None

    if result is not None:
        out = _wrap(capability, bundle, result, available=True)
        note("success", capability=capability)
    else:
        # fail closed to deterministic source facts (never fabricate; preserve navigation).
        out = envelope(
            capability, provider_status=_provider_status(False, provider.model),
            sections=_sections_from_facts(bundle), facts=bundle.facts,
            citations=_citations_from_bundle(bundle),
            limitations=default_limitations([GENERATION_UNAVAILABLE]),
            navigation=bundle.navigation, now=datetime.now(UTC))
        note("success" if bundle.facts else "failures", capability=capability)
    out["unavailable"] = list(out.get("unavailable") or []) + list(bundle.unavailable)
    note("citations", amount=len(out.get("citations") or []))
    _finish_stats(t0, out)
    return out


def _wrap(capability, bundle, gen, *, available):
    return envelope(
        capability, provider_status=_provider_status(available, get_provider().model),
        sections=gen.get("sections") or {}, facts=bundle.facts,
        citations=gen.get("citations") or _citations_from_bundle(bundle),
        limitations=default_limitations(gen.get("extra_limitations")),
        navigation=bundle.navigation, now=datetime.now(UTC))


def _finish_stats(t0, out):
    note_latency(round((time.perf_counter() - t0) * 1000, 2))


# --- public capabilities -----------------------------------------------------

def daily_brief(principal, *, simulate=None) -> dict:
    return _run("daily_brief", principal, entity_type="advisor", entity_id=principal.user_id,
                _simulate=simulate)


def client_brief(principal, person_id, *, simulate=None) -> dict:
    return _run("client_brief", principal, entity_type="person", entity_id=person_id,
                person_id=person_id, _simulate=simulate)


def household_brief(principal, household_id, *, simulate=None) -> dict:
    return _run("household_brief", principal, entity_type="household", entity_id=household_id,
                household_id=household_id, _simulate=simulate)


def meeting_prep(principal, person_id, *, event_id=None, simulate=None) -> dict:
    return _run("meeting_prep", principal, entity_type="person", entity_id=person_id,
                person_id=person_id, event_id=event_id, _simulate=simulate)


def work_explanation(principal, item_type, item_id, *, simulate=None) -> dict:
    return _run("work_explanation", principal, entity_type="work_item", entity_id=item_id,
                item_type=item_type, item_id=item_id, _simulate=simulate)


def answer(principal, question, *, person_id=None, household_id=None, simulate=None) -> dict:
    """Bounded factual question answering — refuses regulated requests, grounded in authorized context."""
    note("requests", capability="factual_question_answering")
    regulated = check_regulated(question)
    if regulated:
        category, message, link = regulated
        note_refusal(category)
        out = refusal_output(category, message, suggested_link=link)
        return out
    t0 = time.perf_counter()
    bundle = ctx.assemble(principal, "factual_question_answering", question=question,
                          person_id=person_id, household_id=household_id)
    hit = _match_answer(question, bundle)
    if hit is None:
        note("unsupported_questions")
        out = {"kind": "factual_answer", "human_review": _hr(), "generated_at": _now(),
               "answer": "Insufficient data — this question is not answerable from the available "
                         "platform context.", "unsupported": True, "citations": [],
               "limitations": default_limitations(), "navigation": bundle.navigation}
    else:
        fact, value = hit
        out = {"kind": "factual_answer", "human_review": _hr(), "generated_at": _now(),
               "answer": f"{fact.source_label}: {fact.fact_key.split('.', 1)[-1].replace('_', ' ')} = {value}.",
               "unsupported": False,
               "citations": [Citation(fact.source_label, (fact.fact_key,), fact.deep_link).to_dict()],
               "limitations": default_limitations(),
               "navigation": [{"label": f"Open {fact.source_label}", "href": fact.deep_link}]
               if fact.deep_link else bundle.navigation}
        note("success", capability="factual_question_answering")
        note("citations", amount=1)
    _finish_stats(t0, out)
    return out


def _match_answer(question, bundle):
    """Deterministic grounding: map question keywords to a supplied fact. No inference beyond the facts."""
    q = (question or "").lower()
    keymap = [
        (("overdue",), "work.my_overdue"), (("due today", "today"), "work.due_today"),
        (("high priority", "priority"), "priorities.high"),
        (("sla", "breach"), "work.sla_breaches"), (("unassigned",), "work.unassigned_team"),
        (("meeting", "next"), "meetings.next_activity"), (("meetings today",), "meetings.today"),
        (("open work", "how much work", "work"), "work.open_work"),
        (("aum", "assets", "portfolio"), "financial.aum"),
        (("tax",), "tax.active"), (("insurance", "policies"), "insurance.policy_count"),
        (("compliance", "reviews"), "compliance.open_reviews"),
        (("members", "household"), "members.count"),
    ]
    for keywords, fact_key in keymap:
        if any(k in q for k in keywords):
            f = next((g for g in bundle.facts if g.fact_key.endswith(fact_key) and g.available), None)
            if f is not None:
                return f, f.fact_value
    return None


def _hr():
    from .common import HUMAN_REVIEW_LABEL
    return HUMAN_REVIEW_LABEL


def _now():
    return datetime.now(UTC).isoformat()
