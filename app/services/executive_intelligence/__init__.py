"""Enterprise Reporting & Executive Intelligence layer (Phase D.48).

A governed, READ-ONLY composition that provides firm-wide operational visibility WITHOUT introducing another
analytics engine, data warehouse, BI platform, reporting database, or metrics system. It composes named
executive dashboards from a declarative dashboard + widget registry over the platform's authoritative
operational services and the SINGLE Analytics Registry (``analytics.metrics`` — every KPI value flows through
``compute_metric``, inheriting record scope + the ``analytics.executive`` gate). It defines no new metrics,
owns no persistence, and never mutates; every widget is explainable and deep-links to its authoritative
surface.
"""
from .service import compose_dashboard, executive_summary, get_widget, list_dashboards

__all__ = ["compose_dashboard", "list_dashboards", "executive_summary", "get_widget"]
