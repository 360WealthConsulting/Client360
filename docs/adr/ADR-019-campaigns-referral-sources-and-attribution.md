# ADR-019 — Campaigns, Referral Sources, and Business Development Attribution

## Status
Accepted

## Date
2026-07-21

## Decision owners
Platform Architecture; Domain Owner (Campaigns / Referral Sources / Business Development);
Business Operations Owner (Michael Shelton — business-development requirements).

## Context
Client360 managed existing clients (D.1–D.12) and a sales pipeline (D.13) but could not measure
**where business originates**: which referral partners and marketing campaigns generate revenue,
acquisition cost, conversion, and lifetime value. The audit confirmed no Campaign or Referral
Source domain existed. This attribution data needed authoritative homes that Opportunities
reference but that never own Opportunities, without breaking the D.5 golden or any accepted ADR.

## Decision
Introduce **two new authoritative source domains** — **Campaigns** (`campaigns`,
`campaign_events`, `campaign_activities`, `campaign_documents`) and **Referral Sources**
(`referral_sources`, `referral_source_advisors`, `referral_source_events`) — plus an
**Opportunity-owned attribution linkage** (`opportunity_attributions` + additive nullable columns
on `opportunities`: `campaign_id`, `referral_source_id`, `origin`, `lead_method`,
`marketing_medium`, `referral_type`, `attribution_locked`).
- Campaigns and Referral Sources **are authoritative source domains**; they are **not**
  composition layers, Advisor Work, Opportunities, People, Organizations, Annual Review, or
  Business Owner Planning.
- **Opportunity references** Campaigns and Referral Sources; **Campaigns/Referral Sources never
  own Opportunities** (FK `ON DELETE SET NULL` — deleting a campaign/source detaches attribution,
  never deletes opportunities).
- **Referral metrics** (referrals, conversion, revenue, LTV, average close time) are **computed**
  from attributed opportunities — **never stored** (no drift).
- **Attribution is immutable after an opportunity closes** unless explicitly overridden
  (`attribution_locked` set on close; `override=True` required to change it).
- **Business Development Intelligence** is deterministic (not AI) and a **dedicated service**
  (`app/services/bizdev/intelligence.py`) — **NOT** registered into the D.5 Advisor Intelligence
  `_PRODUCERS` seam, so the D.5 golden and `advisor_intelligence.py` remain untouched.
- **Sensitive financial fields** (campaign budget/actual cost require `campaign.manage_budget`;
  expected/actual ROI require `campaign.manage_roi`) are enforced **server-side**.
- **Timeline:** approved lifecycle events are recorded in each domain's own event log
  (`campaign_events`, `referral_source_events`) — a firm-level domain has no client anchor, so
  campaign lifecycle is not written to the person/household Activity Timeline; when a referral
  source **is** an existing client, an approved add/deactivate event is additionally published to
  that client's timeline via the shared writer (ADR-009). **Never** a field-change event.
- **Advisor Work** may reference campaigns/referral sources via existing linkage patterns; it
  never owns them. **Microsoft 365** is referenced via existing `timeline_events` (no
  calendar/mail duplication). **Annual Review** and **Business Owner Planning** get **read-only**
  attribution visibility through their existing opportunity sections; they never own attribution.

## Alternatives considered
1. **Store campaign/referral attribution as text on opportunities (D.13's `originating_campaign`
   / `referral_source_text`).** Rejected: not authoritative, not reportable, cannot compute ROI/
   LTV, and violates single ownership (ADR-002).
2. **Store computed referral metrics on the referral_sources row.** Rejected: guarantees drift as
   opportunities change; computed-on-read is always correct.
3. **Register BD intelligence into the D.5 seam.** Rejected: `test_registry_matches_golden` pins
   the global registry, breaking the byte-for-byte D.5 golden (same reasoning as ADR-018); and BD
   signals are firm/book-level, not per-client.

## Reasons for the decision
Dedicated source domains give attribution authoritative homes with their own capabilities, scope,
and reporting; computed metrics stay correct; immutable-after-close attribution preserves
historical integrity; and the D.5 golden and every ADR are preserved.

## Consequences
### Positive consequences
- Authoritative, reportable business-development attribution (ROI, CAC, conversion, LTV).
- Referral metrics never drift (computed on read).
- D.5 Advisor Intelligence untouched; Opportunities remain the pipeline owner.

### Negative consequences and tradeoffs
- Attribution reports recompute over opportunities each call (bounded by book scope).
- Campaign lifecycle events are not in the client Activity Timeline (they have no client anchor);
  they live in `campaign_events` — an intentional, documented boundary.
- BD intelligence is on the BD dashboard, not the per-client Advisor Intelligence panel.

## Enforcement
- Domains: `app/services/campaign/{service,reporting}.py`,
  `app/services/referral/{service,reporting}.py`, `app/services/bizdev/intelligence.py`;
  routes `app/routes/{campaign,referral,business_development}.py`; attribution in
  `app/services/opportunity/service.py` (`set_attribution`, lock on close). Migration
  `l2c3d4e5f6a7`; declared schema `app/database/campaign_referral_tables.py` (registered).
- 11 capabilities (`campaign.view/edit/delete/report/archive/manage_budget*/manage_roi*`,
  `referral.view/edit/delete/report`; `*` sensitive).
- D.5 golden untouched (`advisor_intelligence.py` never imports the new domains).
- Tests: `tests/test_campaign_referral.py`; manifest/platform-architecture/route guards updated.

## Exceptions
None currently approved.

## Revisit conditions
Multi-touch attribution weighting models beyond the current primary/secondary split; a
campaign/marketing-automation integration; or a firm-level (non-client) timeline surface — each
would warrant a new or superseding ADR.

## References
- `app/services/{campaign,referral,bizdev}/`, `app/routes/{campaign,referral,business_development}.py`
- migration `migrations/versions/l2c3d4e5f6a7_campaigns_referrals_attribution.py`
- `docs/PLATFORM_ARCHITECTURE.md`, `docs/platform_architecture_manifest.yaml`
- `tests/test_campaign_referral.py`; relates to ADR-002, ADR-005, ADR-009, ADR-013, ADR-018
