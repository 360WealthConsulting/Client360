"""Document platform (Phase D.16).

The Documents domain is the authoritative repository for all client/business/compliance/planning/
tax/insurance/retirement/benefits/operational artifacts. Every other domain REFERENCES documents
(via document_relationships or their own FK to documents.id); relationships never own documents,
and files/metadata are never duplicated. This package extends the existing minimal documents
domain (app/services/documents.py + the client-portal document_versions) into a full platform:
folders, immutable versions, multi-domain relationships, classification, retention, and a
deterministic lifecycle.
"""
