"""Data Governance platform (Phase D.23).

The authoritative governance domain for data lineage, data quality, master-data governance
(duplicates/merges/survivorship), retention, legal holds, deletion/archival review, and remediation.
It owns **governance metadata only** — findings, duplicate candidates, merge decisions, retention
assignments, legal holds, deletion requests, and cases — and is **never the source of truth for
client or business data**; canonical People/Households/Organizations/Accounts remain authoritative in
their existing domains. It **reuses** the deterministic matching/merge infrastructure
(``person_merge.merge_source_contacts``, ``promote.*``, ``person_source_links``) and the Document
Platform retention model — never replacing them, never performing an unsafe merge, and **never
issuing a hard DELETE**. It launches remediation/merge/deletion-review workflows (Workflow remains
authoritative), can be driven by Automation, feeds Analytics governance statistics, and publishes
guarded, client-anchored lifecycle events to the Timeline.
"""
