#!/usr/bin/env python3
"""One-time BOOTSTRAP seeder for the canonical Publication Register.

Release 0.11.0 · P3. Emits ``docs/registers/pages.yml`` — the canonical, machine-readable
Publication Register (framework deliverable 6 §2, decision D1). After this bootstrap, ``pages.yml``
is the source of truth and is edited by hand; ``DOCUMENTATION_CROSSWALK.md`` is generated FROM it by
``gen_crosswalk.py``. This seeder is a reproducible bootstrap, not a maintained generator — do not
re-run it over a hand-edited register.

Scope decisions (documented in the P3 report):
- "Required document types per area profile" = each profile's MINIMUM-VIABLE ("documented") set
  (framework 02 §D). Hybrid (node 10) areas seed the union of the Software set and the Business-Ops
  process types Policy/SOP/RACI/Checklist/Calendar (decision D3), de-duplicated.
- Real existing Confluence pages (Insurance published, Benefits drafts) and the P1 skeleton
  (nodes, templates) are added as explicit rows with their verified IDs.
- Governance skeleton files (P2) get git-canonical rows.
- 23 legacy 360OS/Atlas pages are preserved as NON-canonical manual-review rows (never canonical,
  never published-by-inference).
- D10 letter->area taxonomy migration is recorded and legacy letters preserved.
"""
from __future__ import annotations

import io
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "docs", "registers", "pages.yml")

# ---- controlled vocabularies -------------------------------------------------
STATUS = ["planned", "draft", "published", "needs_review"]
CANON = ["git", "confluence", "generated", "legacy_unresolved"]

TYPE_LABELS = {
    "EXEC": "Executive Overview", "PURPOSE": "Business Purpose", "ARCH": "Architecture",
    "DATA": "Data Model", "USERGUIDE": "User Guide", "ADMINGUIDE": "Administrator Guide",
    "SOP": "SOP", "RULES": "Business Rules", "SEC": "Security & Permissions", "WF": "Workflows",
    "EXC": "Exception Handling", "INTEG": "Integrations", "REPORT": "Reporting",
    "TROUBLE": "Troubleshooting", "FAQ": "FAQ", "TRAIN": "Training", "RELNOTES": "Release Notes",
    "CHANGELOG": "Change Log / Record", "RELATED": "Related Capabilities", "POLICY": "Policy",
    "RACI": "Roles & Responsibilities (RACI)", "CHECKLIST": "Checklist", "PROCESS": "Process Guide",
    "RUNBOOK": "Runbook", "BCDR": "Business Continuity & DR", "ASSET": "Asset & Config Inventory",
    "VENDOR": "Vendor & Contract Register", "INCIDENT": "Incident Response",
    "CONTROLS": "Controls & Compliance Register", "CALENDAR": "Operating Calendar",
    "GLOSSARY": "Glossary", "KPI": "Service Levels & KPIs",
    # structural / register / legacy
    "NODE": "Manual Node", "TEMPLATE": "Area Shell Template", "README": "Directory README",
    "META": "Meta / Guidance", "REGISTER": "Register", "LEGACY": "Legacy Capability Page",
}

# COMPLETE per-profile document-type sets (framework 01 §2 "Area profiles"). Core rows =
# Executive Overview, Business Purpose, Related Capabilities, Change Record (Ownership/Review are
# metadata; Glossary is the SHARED singleton). Each profile adds its "beyond core" list verbatim.
CORE = ["EXEC", "PURPOSE", "RELATED", "CHANGELOG"]
SOFTWARE_FULL = CORE + ["ARCH", "DATA", "USERGUIDE", "ADMINGUIDE", "SOP", "RULES", "SEC", "WF",
                        "EXC", "INTEG", "REPORT", "TROUBLE", "FAQ", "TRAIN", "RELNOTES"]  # 19
INFRA_FULL = CORE + ["ARCH", "ASSET", "RUNBOOK", "BCDR", "INCIDENT", "ADMINGUIDE", "SEC", "INTEG",
                     "VENDOR", "KPI"]  # 14
BIZOPS_FULL = CORE + ["POLICY", "RACI", "SOP", "CHECKLIST", "PROCESS", "CONTROLS", "CALENDAR",
                      "VENDOR", "TRAIN", "KPI"]  # 14
# Hybrid (node 10) = COMPLETE de-duplicated union of Software and Business-Operations requirements
# (P3 remediation Issue 2A). Superset of the narrower 01 §2 (Software + SOP/Policy/RACI/Calendar)
# and D3 (+ Checklist) definitions, so no requirement is dropped.
HYBRID_FULL = SOFTWARE_FULL + [t for t in BIZOPS_FULL if t not in SOFTWARE_FULL]  # 27
# node 80 (Libraries & Programs) carries NO profile in 01 §2 — cross-area aggregators/indexes.
# Minimal justified set (core + index/process), not a preference-based reduction.
LIBRARY_SET = ["EXEC", "PURPOSE", "PROCESS", "RELATED"]

PROFILE_TYPES = {
    "hybrid": HYBRID_FULL, "infrastructure": INFRA_FULL, "operations": BIZOPS_FULL,
    "library": LIBRARY_SET,
}

# git-canonical document types (framework 06 §1); everything else Confluence-canonical
GIT_TYPES = {"ARCH", "DATA", "RULES", "SEC", "WF", "EXC", "INTEG", "REPORT", "RELNOTES",
             "CHANGELOG", "POLICY", "RUNBOOK", "BCDR", "CONTROLS", "CALENDAR", "ASSET", "GLOSSARY"}

REVIEW_CYCLE = {"hybrid": "per_release", "infrastructure": "semiannual",
                "operations": "annual", "library": "annual"}

OWNER = "Michael Shelton (business owner)"
UNFILLED = "UNFILLED"

# ---- areas -------------------------------------------------------------------
# (code, name, node, profile)
AREAS = [
    ("CLM360", "Client360 Platform", "10", "hybrid"),
    ("TAXOPS", "Tax Operations", "10", "hybrid"),
    ("WLTH", "Wealth Management", "10", "hybrid"),
    ("INS", "Insurance", "10", "hybrid"),
    ("BEN", "Employee Benefits", "10", "hybrid"),
    ("RET", "Retirement Plans", "10", "hybrid"),
    ("CRM", "CRM", "10", "hybrid"),
    ("WORK", "Work Management", "10", "hybrid"),
    ("DOC", "Document Management", "10", "hybrid"),
    ("RPT", "Reporting", "10", "hybrid"),
    ("AIA", "AI & Automation", "10", "hybrid"),
    ("M365", "Microsoft 365", "20", "infrastructure"),
    ("AD", "Active Directory", "20", "infrastructure"),
    ("NET", "Networking", "20", "infrastructure"),
    ("SRV", "Servers", "20", "infrastructure"),
    ("SEC", "Security", "20", "infrastructure"),
    ("DR", "Disaster Recovery", "20", "infrastructure"),
    ("CMP", "Compliance", "30", "operations"),
    ("VEND", "Vendor Management", "30", "operations"),
    ("OFFICE", "Office Operations", "30", "operations"),
    ("HR", "HR", "30", "operations"),
    ("ACCT", "Accounting", "30", "operations"),
    ("MKT", "Marketing", "30", "operations"),
    ("SOPLIB", "SOP Library", "80", "library"),
    ("TRAIN", "Training", "80", "library"),
    ("RELMGMT", "Release Management", "80", "library"),
]
AREA_CODES = {a[0] for a in AREAS} | {"SHARED", "GOV"}  # 26 framework + SHARED + GOV (no MANUAL)
VALID_NODES = {"00", "01", "10", "20", "30", "40", "80", "90"}
VALID_DOCTYPES = set(TYPE_LABELS)

# D10: crosswalk section letter -> framework area (D10 validation §1)
D10_MAP = [
    ("A", "GOV", "Executive Management -> structural/GOV"),
    ("B", "MKT", "Sales and Marketing"),
    ("C", "DOC", "Client Experience"),
    ("D", "TAXOPS", "Tax Operations"),
    ("E", "WLTH", "Wealth Management"),
    ("F", "BEN", "Employee Benefits"),
    ("G", "RET", "Retirement Plans"),
    ("H", "INS", "Insurance Operations"),
    ("I", "ACCT", "Finance and Accounting"),
    ("J", "HR", "HR and People Operations"),
    ("K", "CMP", "Compliance"),
    ("L", "SEC", "Technology and Cybersecurity (L-split primary; siblings M365/AD/NET/SRV/DR)"),
    ("M", "OFFICE", "Administration -> Office Operations"),
    ("N", "TRAIN", "Training"),
]
LETTER_FOR_AREA = {area: letter for letter, area, _ in D10_MAP}

AD5 = "AD-5"


def row(**kw):
    """Build a register row with the full fixed field order and safe defaults."""
    base = dict(
        page_id=None, title=None, area=None, node=None, profile=None, doc_type=None,
        canonical_source=None, repository_path=None, confluence_page_id=None,
        confluence_parent_id=None, owner=OWNER, reviewer=UNFILLED, status="planned",
        last_reviewed="TBD", review_cycle="annual", next_review="TBD", compliance_gate="none",
        legacy_identifier=None, legacy_source=None, reconciliation_status=None, notes=None,
    )
    base.update(kw)
    return base


rows = []

# ---- framework coverage rows (minimum-viable set per profile) ----------------
for code, name, node, profile in AREAS:
    for t in PROFILE_TYPES[profile]:
        canon = "git" if t in GIT_TYPES else "confluence"
        r = row(
            page_id=f"{code}-{t}", title=f"{name} — {TYPE_LABELS[t]}", area=code, node=node,
            profile=profile, doc_type=t, canonical_source=canon,
            repository_path="TBD" if canon == "git" else None,
            confluence_page_id="TBD", review_cycle=REVIEW_CYCLE[profile], status="planned",
            notes="Framework coverage row (minimum-viable set); not yet authored.",
        )
        # preserve former crosswalk section letter on the area's EXEC (overview) row
        if t == "EXEC" and code in LETTER_FOR_AREA:
            r["legacy_identifier"] = LETTER_FOR_AREA[code]
            r["legacy_source"] = "crosswalk_section_letter"
        rows.append(r)

# AD-5 post-pass on coverage rows: regulated Insurance Business Rules, and regulated Compliance
# Policy/Controls now exist as full-profile coverage rows — gate them (never published).
for r in rows:
    if r["area"] == "INS" and r["doc_type"] == "RULES":
        r["compliance_gate"] = AD5
        r["reviewer"] = "UNFILLED (compliance reviewer — AD-5)"
        r["notes"] = "Regulated insurance business-rule set — AD-5 gated; not authored."
    if r["area"] == "CMP" and r["doc_type"] in ("POLICY", "CONTROLS"):
        r["compliance_gate"] = AD5
        r["reviewer"] = "UNFILLED (compliance reviewer — AD-5)"
        r["notes"] = "Regulated compliance content — AD-5 gated; never published while reviewer UNFILLED."

# ---- Insurance: 4 explicit regulated rule-set rows (AD-5, never published) ----
for pid, label, note in [
    ("INS-RULES-SUITABILITY", "Insurance — Suitability Rule Set", "Regulated suitability determination logic."),
    ("INS-RULES-REPLACEMENT", "Insurance — Replacement / 1035 Rule Set", "Regulated replacement/1035 recommendation logic."),
    ("INS-RULES-LICENSING", "Insurance — Licensing Validation Rule Set", "Regulated producer-licensing validation."),
    ("INS-RULES-CE", "Insurance — Continuing-Education Validation Rule Set", "Regulated CE validation."),
]:
    rows.append(row(page_id=pid, title=label, area="INS", node="10", profile="hybrid",
                    doc_type="RULES", canonical_source="git", repository_path="TBD",
                    confluence_page_id="TBD", review_cycle="per_release", status="planned",
                    compliance_gate=AD5, reviewer="UNFILLED (compliance reviewer — AD-5)",
                    notes="AD-5 BLOCKED regulated rule set — " + note + " Not authored; never publishable while reviewer UNFILLED."))

# ---- Insurance: the 12 crosswalk §3 proposed pages (6 published + 6 draft) ----
INS_PARENT = "28770305"
ins_pages = [
    # published (real Confluence pages) — non-regulated operational/boundary/descriptive
    ("INS-EXEC-01", "Insurance Operations — Release 0.10.0 (landing / section overview)", "28770305",
     "21266602", "EXEC", "published", "none",
     "Insurance area PARENT/LANDING page AND descriptive operational overview (both): it is the "
     "section landing/navigation home and carries a non-regulated scope summary. doc_type=EXEC "
     "(Executive Overview). NOT one of the 5 operational child SOPs. Verified published (28770305)."),
    ("INS-SOP-01", "Insurance Commissions — Operating Procedure", "28803073", INS_PARENT, "SOP",
     "published", "none", "Non-regulated operational SOP (Phase 5). Also serves ACCT."),
    ("INS-SOP-02", "Insurance Exceptions & Work Queues — Operating Procedure", "28835841", INS_PARENT,
     "SOP", "published", "none", "Non-regulated operational SOP (Phase 6). Also serves CMP."),
    ("INS-SOP-03", "Insurance Policyholder Portal — Operating Procedure", "28868609", INS_PARENT,
     "SOP", "published", "none", "Non-regulated operational SOP (Phase 7). Also serves DOC."),
    ("INS-REPORT-01", "Insurance Reporting & Operations Dashboard — Operating Procedure", "28901377",
     INS_PARENT, "REPORT", "published", "none", "Non-regulated staff dashboard SOP (Phase 8)."),
    ("INS-INTEG-01", "Insurance Integrations — Extension Points (Reference)", "28901397", INS_PARENT,
     "INTEG", "published", "none", "Non-regulated reference; disabled ports (Phase 9). Also serves SEC."),
    # draft proposals (no Confluence page yet)
    ("INS-USERGUIDE-01", "Insurance Policy Management", None, None, "USERGUIDE", "draft", "none",
     "Operational CRUD/lifecycle only (Phase 1). Draft proposal."),
    ("INS-SOP-04", "New Business Case Management", None, None, "SOP", "draft", "none",
     "Operational pipeline/requirements only. Suitability determination excluded (AD-5)."),
    ("INS-SOP-05", "In-Force Policy Servicing", None, None, "SOP", "draft", "none",
     "Operational servicing only. Replacement/1035 recommendation excluded (AD-5)."),
    ("INS-SOP-06", "Insurance Reviews and Obligations", None, None, "SOP", "draft", "none",
     "Review lifecycle/obligation calendar only; no suitability determination (AD-5)."),
    ("INS-SOP-07", "Producer Licensing and Continuing Education", None, None, "SOP", "draft", AD5,
     "Records + expiry reminders only; licensing/CE VALIDATION excluded (AD-5). Also serves CMP."),
    ("INS-RACI-01", "Insurance Roles and Responsibilities", None, None, "RACI", "draft", AD5,
     "Role/capability map; accountable compliance reviewer UNFILLED (AD-5). Also serves CMP."),
]
for pid, title, cid, parent, dt, st, gate, note in ins_pages:
    canon = "confluence" if cid else ("git" if dt in GIT_TYPES else "confluence")
    # draft proposals have no page yet -> visible TBD (a page is expected on publication)
    cid_val = cid if cid else ("TBD" if canon == "confluence" else None)
    rows.append(row(page_id=pid, title=title, area="INS", node="10", profile="hybrid", doc_type=dt,
                    canonical_source=canon, confluence_page_id=cid_val, confluence_parent_id=parent,
                    repository_path=None, status=st, review_cycle="per_release", compliance_gate=gate,
                    reviewer=("UNFILLED (compliance reviewer — AD-5)" if gate == AD5 else UNFILLED),
                    last_reviewed=("2026-07-17" if st == "published" else "TBD"),
                    next_review=("2026-10-17" if st == "published" else "TBD"), notes=note))

# ---- Employee Benefits: 3 real draft pages ----------------------------------
for pid, title, cid, dt, note in [
    ("BEN-REF-01", "Employee Benefits — Compliance & Renewal Obligations", "27951106", "USERGUIDE",
     "Non-regulated benefits obligations reference (v0.9.11). Confluence draft."),
    ("BEN-SOP-01", "Employee Benefits — Deadline Monitoring, Exceptions & Work Queues", "27983873",
     "SOP", "Non-regulated benefits SOP (v0.9.11). Confluence draft."),
    ("BEN-CHK-01", "Employee Benefits — Obligation Management Checklist", "27918338", "CHECKLIST",
     "Non-regulated benefits checklist (v0.9.11). Confluence draft."),
]:
    rows.append(row(page_id=pid, title=title, area="BEN", node="10", profile="hybrid", doc_type=dt,
                    canonical_source="confluence", confluence_page_id=cid, status="draft",
                    review_cycle="quarterly", last_reviewed="2026-07-15", next_review="2026-10-15",
                    notes=note))

# ---- Release 0.12 P1B authored Operations Manual pages (git-canonical, needs_review) --------
p1b_pages = [
    ("WLTH-SOP-01", "Wealth Management — Schwab Account Opening", "WLTH",
     "docs/operations-manual/wealth/schwab-account-opening.md", "quarterly",
     "Adapted from Atlas SOP-006 (24772609)."),
    ("WLTH-SOP-02", "Wealth Management — Schwab Portfolio Connect Quarterly Billing & Fee Locking",
     "WLTH", "docs/operations-manual/wealth/schwab-portfolio-connect-billing.md", "quarterly",
     "Adapted from Atlas SOP-009 (24870913) + LL-001 control."),
    ("WLTH-SOP-03", "Wealth Management — AssetMark Account Opening", "WLTH",
     "docs/operations-manual/wealth/assetmark-account-opening.md", "quarterly",
     "Adapted from Atlas SOP-013 (24838166)."),
    ("WLTH-SOP-04", "Wealth Management — AssetMark Proposal Generation", "WLTH",
     "docs/operations-manual/wealth/assetmark-proposal-generation.md", "quarterly",
     "Adapted from Atlas SOP-011 (25133057)."),
    ("TAXOPS-SOP-01", "Tax Operations — TaxDome Client Intake", "TAXOPS",
     "docs/operations-manual/tax/taxdome-intake.md", "annual",
     "Adapted from Atlas SOP-016 (23920691)."),
    ("TAXOPS-SOP-02", "Tax Operations — 1040 Individual Return Preparation (Drake)", "TAXOPS",
     "docs/operations-manual/tax/tax-1040-return-workflow.md", "annual",
     "Adapted from Atlas SOP-017 (23920712)."),
    ("WLTH-SOP-05", "Wealth Management — Schwab MoneyLink Setup", "WLTH",
     "docs/operations-manual/wealth/schwab-moneylink-setup.md", "quarterly",
     "Adapted from Atlas SOP-007 (24805377)."),
    ("WLTH-SOP-06", "Wealth Management — Schwab ACAT Transfer In", "WLTH",
     "docs/operations-manual/wealth/schwab-acat-transfer-in.md", "quarterly",
     "Adapted from Atlas SOP-008 (24838145)."),
    ("WLTH-SOP-07", "Wealth Management — AssetMark Household Setup", "WLTH",
     "docs/operations-manual/wealth/assetmark-household-setup.md", "quarterly",
     "Adapted from Atlas SOP-010 (25100289)."),
    ("WLTH-SOP-08", "Wealth Management — AssetMark Model Selection", "WLTH",
     "docs/operations-manual/wealth/assetmark-model-selection.md", "quarterly",
     "Adapted from Atlas SOP-012 (25165825)."),
    ("WLTH-SOP-09", "Wealth Management — AssetMark Funding & Transfers", "WLTH",
     "docs/operations-manual/wealth/assetmark-funding-transfers.md", "quarterly",
     "Adapted from Atlas SOP-014 (25198593)."),
    ("WLTH-SOP-10", "Wealth Management — AssetMark Billing Review", "WLTH",
     "docs/operations-manual/wealth/assetmark-billing-review.md", "quarterly",
     "Adapted from Atlas SOP-015 (25198614)."),
]
for pid, title, area, path, cyc, note in p1b_pages:
    rows.append(row(page_id=pid, title=title, area=area, node="10", profile="hybrid", doc_type="SOP",
                    canonical_source="git", repository_path=path, confluence_page_id="TBD",
                    status="needs_review", review_cycle=cyc, last_reviewed="TBD", next_review="TBD",
                    reviewer="Michael Shelton (business/operational reviewer)",
                    notes="Release 0.12 P1B authored git-canonical Operations Manual page (needs_review). "
                          + note))

# ---- SHARED singletons (node 40) --------------------------------------------
for pid, title, path, dt in [
    ("SHARED-PLATFORM-ARCH", "Platform Architecture", "docs/PRODUCTION_ARCHITECTURE.md", "ARCH"),
    ("SHARED-GLOBAL-SEC", "Global Security & Identity", "docs/IDENTITY_AUTHORIZATION_AUDIT.md", "SEC"),
    ("SHARED-EXCEPTION-ENGINE", "Global Exception Engine", "docs/ADR_EXCEPTION_ENGINE_SCOPE.md", "EXC"),
    ("SHARED-WORKFLOW-ENGINE", "Global Workflow Engine", "docs/WORKFLOW_PROCESS_AUTOMATION.md", "WF"),
    ("SHARED-DESIGN-SYSTEM", "Design System / UI", "docs/UI_DESIGN_SYSTEM.md", "ARCH"),
    ("SHARED-GLOSSARY", "Glossary & Definitions", None, "GLOSSARY"),
    ("SHARED-CALENDAR", "Operating Calendar & Key Dates", "governance/calendar/", "CALENDAR"),
    ("SHARED-ADR-INDEX", "Architecture Decisions (ADR/DEC) Index", None, "ARCH"),
]:
    has = path is not None
    rows.append(row(page_id=pid, title=title, area="SHARED", node="40", profile="operations",
                    doc_type=dt, canonical_source="git", repository_path=path or "TBD",
                    confluence_page_id="TBD", status=("draft" if has else "planned"),
                    review_cycle="semiannual",
                    notes="Shared singleton — documented once, linked from every area (never copied)."))

# ---- GOV governance skeleton rows (P2 files) --------------------------------
gov_files = [
    ("GOV-README", "Governance — README", "governance/README.md", "META", "none"),
    ("GOV-CONTRIBUTING", "Governance — CONTRIBUTING", "governance/CONTRIBUTING.md", "META", "none"),
    ("GOV-POLICIES-README", "Governance — Policies (skeleton)", "governance/policies/README.md", "POLICY", "none"),
    ("GOV-RUNBOOKS-README", "Governance — Runbooks (skeleton)", "governance/runbooks/README.md", "RUNBOOK", "none"),
    ("GOV-DR-README", "Governance — DR (skeleton)", "governance/dr/README.md", "BCDR", "none"),
    ("GOV-CONTROLS-README", "Governance — Controls (skeleton)", "governance/controls/README.md", "CONTROLS", AD5),
    ("GOV-INVENTORY-README", "Governance — Inventory (skeleton)", "governance/inventory/README.md", "ASSET", "none"),
    ("GOV-CALENDAR-README", "Governance — Calendar (skeleton)", "governance/calendar/README.md", "CALENDAR", "none"),
]
for pid, title, path, dt, gate in gov_files:
    rows.append(row(page_id=pid, title=title, area="GOV", node="90", profile="operations",
                    doc_type=dt, canonical_source="git", repository_path=path, status="draft",
                    review_cycle="semiannual", compliance_gate=gate,
                    reviewer=("UNFILLED (compliance reviewer — AD-5)" if gate == AD5 else UNFILLED),
                    notes="Phase-A governance SKELETON (guidance only; not substantive governance content)."))

# ---- GOV register / governance views (node 90) ------------------------------
rows.append(row(page_id="GOV-REGISTER-PAGESYML", title="Publication Register (canonical)",
                area="GOV", node="90", profile="operations", doc_type="REGISTER",
                canonical_source="git", repository_path="docs/registers/pages.yml",
                status="published", review_cycle="per_release",
                notes="This file — the canonical machine-readable register (D1)."))
rows.append(row(page_id="GOV-CROSSWALK", title="Documentation Crosswalk (generated view)",
                area="GOV", node="90", profile="operations", doc_type="REGISTER",
                canonical_source="generated", repository_path="docs/DOCUMENTATION_CROSSWALK.md",
                status="published", review_cycle="per_release",
                notes="Generated FROM pages.yml by scripts/registers/gen_crosswalk.py — do not edit by hand."))
for pid, title in [
    ("GOV-OWNERSHIP-DIR", "Ownership Directory"), ("GOV-REVIEW-CALENDAR", "Review Calendar"),
    ("GOV-VENDOR-REGISTER", "Vendor & Contract Register"),
    ("GOV-ASSET-REGISTER", "Asset & Configuration Inventory"),
    ("GOV-CONTROLS-REGISTER", "Controls & Compliance Register"),
    ("GOV-DOC-BACKLOG", "Documentation Backlog & Gaps"),
]:
    gate = AD5 if pid == "GOV-CONTROLS-REGISTER" else "none"
    rows.append(row(page_id=pid, title=title, area="GOV", node="90", profile="operations",
                    doc_type="REGISTER", canonical_source="generated", repository_path="TBD",
                    confluence_page_id="TBD", status="planned", review_cycle="semiannual",
                    compliance_gate=gate,
                    reviewer=("UNFILLED (compliance reviewer — AD-5)" if gate == AD5 else UNFILLED),
                    notes="Register view over pages.yml (later phase)."))

# ---- Confluence skeleton: 8 nodes + 3 templates (structural, classified under GOV) ----
# 'MANUAL' is NOT an approved taxonomy area; these structural manual pages are classified under
# the approved GOV area (Registers & Governance) while retaining their true tree node.
nodes = [
    ("GOV-NODE-00", "00 · Company Home", "28966913", "00"),
    ("GOV-NODE-01", "01 · How This Manual Works", "28835861", "01"),
    ("GOV-NODE-10", "10 · Client-Facing Operations", "28999681", "10"),
    ("GOV-NODE-20", "20 · Technology & Infrastructure", "29032449", "20"),
    ("GOV-NODE-30", "30 · Business Operations", "29032469", "30"),
    ("GOV-NODE-40", "40 · Cross-Platform & Shared", "28868631", "40"),
    ("GOV-NODE-80", "80 · Libraries & Programs", "28835881", "80"),
    ("GOV-NODE-90", "90 · Registers & Governance", "28868651", "90"),
]
for pid, title, cid, node in nodes:
    rows.append(row(page_id=pid, title=title, area="GOV", node=node, profile="operations",
                    doc_type="NODE", canonical_source="confluence", confluence_page_id=cid,
                    confluence_parent_id="21266602", status="published", review_cycle="annual",
                    notes="Operations Manual structural node (P1 skeleton; verified current). "
                          "Classified under GOV (MANUAL is not an approved area)."))
for pid, title, cid in [
    ("GOV-TEMPLATE-SW", "Area Shell Template — Software Profile", "28966933"),
    ("GOV-TEMPLATE-INFRA", "Area Shell Template — Infrastructure Profile", "28999701"),
    ("GOV-TEMPLATE-BIZ", "Area Shell Template — Business Operations Profile", "28835901"),
]:
    rows.append(row(page_id=pid, title=title, area="GOV", node="01", profile="operations",
                    doc_type="TEMPLATE", canonical_source="confluence", confluence_page_id=cid,
                    confluence_parent_id="28835861", status="published", review_cycle="annual",
                    notes="Area Shell template PAGE (not a native Confluence template). "
                          "Classified under GOV (MANUAL is not an approved area)."))

# ---- 23 legacy 360OS/Atlas pages (non-canonical, manual_review) -------------
HOME = "21266602"
legacy = [
    ("LEGACY-HOME-360OS", "360OS Operations Home", "24117290", HOME, "GOV", "00", "HOME-001",
     "Overlaps 00 · Company Home; eventually merge (one canonical home)."),
    ("LEGACY-360-STANDARDS", "\U0001F4D0 360 Standards", "23199768", HOME, "GOV", "01", "360-STANDARDS",
     "Overlaps 01 · How This Manual Works; merge/link the documentation standard. Likely area GOV (non-canonical)."),
    ("LEGACY-ATLAS-ARCHIVE", "\U0001F5C4️ Atlas Archive", "25755689", HOME, "GOV", "90", "ATLAS-ARCHIVE",
     "Legacy/duplicate store; recommend retain in place as archive target."),
    ("LEGACY-CAP004-TAX", "\U0001F9FE Tax Operations", "23494657", HOME, "TAXOPS", "10", "CAP-004",
     "Eventually move/link under TAXOPS; process = Confluence-canonical."),
    ("LEGACY-CAP005-CMP", "⚖️ Compliance", "23560193", HOME, "CMP", "30", "CAP-005",
     "Eventually move; AD-5 relevant; controls are git-canonical."),
    ("LEGACY-CAP007-HR", "\U0001F465 HR / People Operations", "23560201", HOME, "HR", "30", "CAP-007",
     "Eventually move/link under HR."),
    ("LEGACY-CAP009-FIN", "\U0001F4B0 Finance Operations", "24510485", HOME, "ACCT", "30", "CAP-009",
     "Eventually move under ACCT."),
    ("LEGACY-CAP010-MKT", "\U0001F4E3 Marketing Operations", "25034803", HOME, "MKT", "30", "CAP-010",
     "Eventually move under MKT."),
    ("LEGACY-CAP011-VEND", "\U0001F91D Vendor Management", "24510566", HOME, "VEND", "30", "CAP-011",
     "Published legacy; eventually move/link under VEND."),
    ("LEGACY-CAP012-BCP", "\U0001F6E1️ Business Continuity", "24510526", HOME, "DR", "20", "CAP-012",
     "Eventually move -> link (DR is git-canonical in governance/dr)."),
    ("LEGACY-CAP006-TECH", "\U0001F4BB Technology Operations", "24051794", HOME, "SEC", "20", "CAP-006",
     "Published legacy; 1->many split across node 20; requires manual review."),
    ("LEGACY-CAP013-RISK", "⚠️ Risk Management", "25886741", HOME, "CMP", "30", "CAP-013",
     "No 1:1 framework area (risk cross-cutting); requires manual review."),
    ("LEGACY-CAP008-EXEC", "\U0001F3DB️ Executive Management", "25493545", HOME, "GOV", "00", "CAP-008",
     "Structural, not a capability area; requires manual review."),
    ("LEGACY-CAP014-CX", "\U0001F48E Client Experience", "25657365", HOME, "DOC", "10", "CAP-014",
     "Eventually move/link under DOC/CLM360."),
    ("LEGACY-CAP001-LIFECYCLE", "\U0001F464 Client Lifecycle", "23330817", HOME, "CRM", "10", "CAP-001",
     "Many child pages; eventually move under CRM; high dependency; manual review."),
    ("LEGACY-CAP002-SCHWAB", "\U0001F4C8 Schwab Operations", "23330825", HOME, "WLTH", "10", "CAP-002",
     "Eventually move/link under WLTH."),
    ("LEGACY-CAP003-ASSETMARK", "\U0001F4CA AssetMark Operations", "23265282", HOME, "WLTH", "10", "CAP-003",
     "Eventually move/link under WLTH."),
    ("LEGACY-OFFICE", "\U0001F4BC Office Operations", "23625729", HOME, "OFFICE", "30", "OFFICE-OPS",
     "Eventually move under OFFICE."),
    ("LEGACY-KNOWLEDGE-LIB", "\U0001F4DA Knowledge Library", "23199760", HOME, "SOPLIB", "80", "KNOWLEDGE-LIB",
     "Aggregator; link/merge under node 80; manual review."),
    ("LEGACY-BIZ-INTEL", "\U0001F4CA Business Intelligence", "23035917", HOME, "RPT", "10", "BIZ-INTEL",
     "Eventually move/link under RPT; manual review."),
    ("LEGACY-ATLAS-V02", "\U0001F4D8 Atlas v0.2 Repository", "23822337", HOME, "GOV", "90", "ATLAS-V02",
     "Meta/experimental; eventually archive or manual review."),
    ("LEGACY-BUILDER-PILOT", "Builder Pilot", "23920682", HOME, "GOV", "90", "BUILDER-PILOT",
     "Unclear purpose; requires manual review."),
    ("LEGACY-HOME-LEGACY", "\U0001F3E0 Home", "23166977", HOME, "GOV", "00", "HOME-LEGACY",
     "Third home-like page; overlaps 00; requires manual review. Likely area GOV (non-canonical)."),
]
for pid, title, cid, parent, area, node, legid, note in legacy:
    rows.append(row(page_id=pid, title=title, area=area, node=node, profile="operations",
                    doc_type="LEGACY", canonical_source="legacy_unresolved",
                    confluence_page_id=cid, confluence_parent_id=parent, status="needs_review",
                    review_cycle="quarterly", legacy_identifier=legid, legacy_source="360os_atlas",
                    reconciliation_status="manual_review",
                    notes="NON-CANONICAL legacy 360OS/Atlas page. Destination is a LIKELY mapping only; "
                          "disposition pending the separate reconciliation decision. " + note))

# ---- ensure every D10 letter is preserved on some row of its target area -----
for letter, area, _ in D10_MAP:
    already = any(r.get("legacy_identifier") == letter
                  and r.get("legacy_source") == "crosswalk_section_letter" for r in rows)
    if already:
        continue
    # prefer the area's EXEC coverage row; else the first row of that area
    target = next((r for r in rows if r["area"] == area and r["doc_type"] == "EXEC"), None)
    if target is None:
        target = next((r for r in rows if r["area"] == area), None)
    if target is not None:
        target["legacy_identifier"] = letter
        target["legacy_source"] = "crosswalk_section_letter"

# ---- assemble document -------------------------------------------------------
doc = {
    "meta": {
        "schema_version": "1.0",
        "canonical": True,
        "generated": False,
        "description": ("Canonical machine-readable Publication Register for the 360 Wealth "
                        "Consulting Operations Manual (Release 0.11.0 · P3). Edit THIS file; "
                        "docs/DOCUMENTATION_CROSSWALK.md is generated from it."),
        "row_count": len(rows),
    },
    "enums": {"status": STATUS, "canonical_source": CANON,
              "valid_areas": sorted(AREA_CODES), "valid_nodes": sorted(VALID_NODES)},
    "schema": {
        "fields": [
            "page_id", "title", "area", "node", "profile", "doc_type", "canonical_source",
            "repository_path", "confluence_page_id", "confluence_parent_id", "owner", "reviewer",
            "status", "last_reviewed", "review_cycle", "next_review", "compliance_gate",
            "legacy_identifier", "legacy_source", "reconciliation_status", "notes",
        ],
        "null_allowed": ["repository_path", "confluence_page_id", "confluence_parent_id",
                         "legacy_identifier", "legacy_source", "reconciliation_status", "notes"],
        "invariants": ["unique page_id", "unique non-null confluence_page_id",
                       "unique non-null repository_path (canonical)",
                       "compliance_gate set (AD-5) => status != published",
                       "legacy_unresolved rows are never canonical and never published"],
    },
    "taxonomy_migration_d10": [
        {"letter": lt, "area": ar, "note": nt} for lt, ar, nt in D10_MAP
    ],
    "pages": sorted(rows, key=lambda r: (r["node"] or "zz", r["area"], r["doc_type"], r["page_id"])),
}

os.makedirs(os.path.dirname(OUT), exist_ok=True)
buf = io.StringIO()
buf.write("# CANONICAL Publication Register — 360 Wealth Consulting Operations Manual\n")
buf.write("# Release 0.11.0 · Phase A (P3). THIS FILE IS THE SOURCE OF TRUTH (decision D1).\n")
buf.write("# Edit here; regenerate the human view with scripts/registers/gen_crosswalk.py.\n")
buf.write("# Validate with scripts/registers/validate_register.py.\n")
yaml.safe_dump(doc, buf, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(buf.getvalue())

print(f"wrote {OUT} with {len(rows)} rows")
