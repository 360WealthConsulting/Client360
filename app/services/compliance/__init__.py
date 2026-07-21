"""Compliance Enablement Framework (Phase D.6).

The governance layer *above* Advisor Intelligence. It READS Advisor Intelligence
registry metadata and never the reverse: nothing here executes rules, generates
recommendations, enforces policy, or modifies Advisor Intelligence. Metadata only
— no persistence, no database tables, no workflow, no automation.
"""
