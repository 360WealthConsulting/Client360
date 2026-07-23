# AI Assist Prompt Governance (Phase D.42)

Prompts are governed, versioned, and constraint-bearing. See [`ADVISOR_AI_ASSIST.md`](ADVISOR_AI_ASSIST.md),
[`ADR-047`](adr/ADR-047-advisor-ai-assist.md).

## Where prompts live

`app/services/ai_assist/prompts.py` — an in-code declarative `PROMPTS` dict keyed by assistant
capability. Each entry carries `version`, `capability`, `owner`, `lifecycle`, and `template`. No prompt
metadata is persisted (no migration); the registry references the version via
`registry.AssistantDef.prompt_version`.

The default provider is deterministic and does not require the prompt strings, but they are the contract a
real model provider would be given — and governance verifies each carries the required constraints.

## Required constraints (governance-verified)

Every prompt template must contain (case-insensitive substrings, `prompts.REQUIRED_CONSTRAINTS`):

- **read-only**
- use **only the supplied context**
- **cite** every factual statement
- **state when data is missing**
- **must not create, update, delete, approve, assign, file, submit, send, or complete** any record
- **no investment, tax, legal, insurance, or suitability advice**
- **no policy or compliance decision**

`governance.validate_ai_assist` flags any prompt missing a constraint (`prompt_missing_constraint`) and
any capability without a versioned prompt (`capability_without_prompt_version` /
`capability_without_prompt`).

## Lifecycle + ownership

Lifecycle ∈ `active` | `experimental` | `deprecated` | `retired`. Each registry entry records `owner`.
Bumping a prompt = a new `version` string; the registry's `prompt_version` follows it. Deprecated/retired
capabilities are excluded from `active_capabilities()`.

## Output contract enforcement

The output envelope must always carry `citations`, `limitations`, and the `human_review` label —
`contracts.validate_output` rejects any envelope lacking them, so the model can never omit required
safety/provenance fields. Governance additionally asserts these fields are in `REQUIRED_OUTPUT_FIELDS`.

## Refusals

Regulated categories are declared in `refusal.REGULATED` and governance asserts the required categories
(trade recommendation, tax conclusion, compliance approval, suitability determination, autonomous action)
are present. A matched request returns a constrained refusal (still human-review labelled) with a
suggested authoritative link.
