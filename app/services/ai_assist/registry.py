"""Assistant capability registry for Advisor AI Assist (Phase D.42).

A closed registry of supported, governed assistant capabilities — no arbitrary free-form assistant modes
exist outside it. Each entry declares its required capability, source adapters, allowed deep-link types,
prompt version, contracts, and model config. Lifecycle: active | experimental | deprecated | retired.
"""
from __future__ import annotations

from dataclasses import dataclass

from .prompts import prompt_version

LIFECYCLES = ("active", "experimental", "deprecated", "retired")


@dataclass(frozen=True)
class AssistantDef:
    identifier: str
    name: str
    description: str
    owner: str
    lifecycle: str
    required_capability: str          # the DATA capability (reused; the assistant grants no data access)
    required_sources: tuple           # context source types it consumes
    allowed_deeplink_types: tuple     # surfaces it may deep-link to
    input_contract: str
    output_contract: str
    model: str = "local-deterministic"
    timeout_s: float = 8.0
    max_context: int = 4000

    @property
    def prompt_version(self) -> str | None:
        return prompt_version(self.identifier)


_NAV = ("advisor_workspace", "unified_work_queue", "client360", "household360", "meeting", "domain_workflow")

ASSISTANTS = {
    "daily_brief": AssistantDef(
        "daily_brief", "Daily Advisor Brief", "Summarize the advisor's day.", "Advisor Experience",
        "active", "client.read", ("daily_brief", "work_queue"), _NAV,
        "principal", "DailyBrief", ),
    "client_brief": AssistantDef(
        "client_brief", "Client Brief", "Summarize a client from the Client 360 snapshot.",
        "Advisor Experience", "active", "client.read", ("client360",), _NAV,
        "principal + person_id", "ClientBrief", ),
    "household_brief": AssistantDef(
        "household_brief", "Household Brief", "Summarize a household from the Household 360 snapshot.",
        "Advisor Experience", "active", "client.read", ("household360",), _NAV,
        "principal + household_id", "HouseholdBrief", ),
    "meeting_prep": AssistantDef(
        "meeting_prep", "Meeting Prep Brief", "Prepare for a client meeting (minimized brief).",
        "Advisor Experience", "active", "client.read", ("meeting_brief", "client360"), _NAV,
        "principal + person_id (+ event_id)", "MeetingPrepBrief", ),
    "work_explanation": AssistantDef(
        "work_explanation", "Work Explanation", "Explain why a work item surfaced.", "Advisor Experience",
        "active", "work.read", ("work_queue",), _NAV,
        "principal + work_item_key", "WorkExplanation", ),
    "factual_question_answering": AssistantDef(
        "factual_question_answering", "Factual Question Answering",
        "Answer bounded factual questions grounded in authorized context.", "Advisor Experience",
        "active", "client.read", ("daily_brief", "client360", "work_queue"), _NAV,
        "principal + question (+ scope)", "FactualAnswer", ),
}


def get(identifier) -> AssistantDef | None:
    return ASSISTANTS.get(identifier)


def active_capabilities() -> list[str]:
    return [k for k, a in ASSISTANTS.items() if a.lifecycle in ("active", "experimental")]
