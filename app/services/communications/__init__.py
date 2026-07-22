"""Communications & Client Engagement platform (Phase D.18).

The authoritative domain for communication METADATA — conversations, threads, messages,
recipients, deliveries, attachment references, reusable templates, and an append-only audit
ledger. It coordinates outbound/inbound communication while preserving ownership boundaries: it
references people/households/organizations, documents, workflow, compliance, opportunities,
campaigns, referral sources, annual reviews, and business owner plans, but is **never a source of
truth for business records**. It reuses the existing notification ledger, transactional outbox,
and Microsoft 365 integrations for transport (metadata only — no proprietary transport). Approved
lifecycle events flow to the shared Activity Timeline; Analytics consumes communication statistics
(Communications never depends on Analytics).
"""
