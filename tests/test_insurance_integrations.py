"""Insurance integration ports — DISABLED stubs (Release 0.10.0, Phase 9).

Pins that every insurance integration port is inert: disabled by default, fails safe with no
external I/O, exposes no vendor logic, reuses the shared disabled-provider idiom, audits invocation
without logging the payload/secrets, and cannot be activated by configuration. Also confirms the
module ships no network I/O, no endpoints, and no secrets.
"""
from __future__ import annotations

import inspect
import re

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.db import audit_events, engine
from app.security.dependencies import require_capability
from app.security.models import Principal
from app.services import insurance_integrations as ig

_SOURCE = inspect.getsource(ig)
_EXPECTED_KEYS = {"carrier_policy_feed", "case_status_feed", "commission_statement_feed",
                  "licensing_appointment_feed", "document_evidence_intake", "operational_export_hook"}


# --- disabled by default -----------------------------------------------------

def test_all_ports_are_disabled_and_not_connected():
    ports = ig.list_ports()
    assert {p["key"] for p in ports} == _EXPECTED_KEYS
    for p in ports:
        assert p["enabled"] is False
        assert p["status"] == "not_connected"
    # the class attribute is a hardcoded False on every registered port
    assert all(port.enabled is False for port in ig.INSURANCE_PORTS.values())


def test_connection_status_is_disabled_and_echoes_org_scope():
    r = ig.port_status("carrier_policy_feed", organization_id=42)
    assert r.outcome == "disabled" and r.status == "not_connected"
    assert r.metadata["organization_id"] == 42 and r.metadata["enabled"] is False


# --- safe failure, no external I/O -------------------------------------------

def test_invoking_a_disabled_port_fails_safe_without_raising():
    for key in _EXPECTED_KEYS:
        r = ig.invoke_port(key, organization_id=1)
        assert r.outcome == "disabled" and r.status == "not_connected"  # inert, never a fake success


def test_unknown_port_raises_valueerror():
    with pytest.raises(ValueError):
        ig.get_port("does_not_exist")
    with pytest.raises(ValueError):
        ig.port_status("does_not_exist")


def test_module_performs_no_network_io():
    # Static guarantee: the ports module imports no networking / transfer libraries.
    for lib in ("requests", "httpx", "urllib", "socket", "http.client", "ftplib", "smtplib",
                "paramiko", "aiohttp"):
        assert f"import {lib}" not in _SOURCE and f"from {lib}" not in _SOURCE, \
            f"integration ports module must not import {lib}"


# --- configuration cannot enable a port --------------------------------------

def test_configuration_values_never_enable_a_port(monkeypatch):
    # Even with a suggestive env var set, ports stay disabled (enablement is code-only).
    monkeypatch.setenv("CARRIER_POLICY_FEED_ENABLED", "true")
    monkeypatch.setenv("INSURANCE_INTEGRATIONS_ENABLED", "1")
    assert all(p["enabled"] is False for p in ig.list_ports())
    # the module reads no os.environ / config for enablement
    assert "os.environ" not in _SOURCE and "getenv" not in _SOURCE


# --- audit-safe (no payload / secrets logged) --------------------------------

def test_invoke_audits_metadata_only_never_the_payload():
    def _count():
        with engine.connect() as c:
            return c.execute(select(func.count()).select_from(audit_events).where(
                audit_events.c.action == "insurance.integration.port_invoked")).scalar_one()

    before = _count()
    ig.invoke_port("commission_statement_feed", organization_id=7, payload={"api_key": "SECRET-XYZ"},
                   actor_user_id=1)
    assert _count() == before + 1
    with engine.connect() as c:
        row = c.execute(select(audit_events).where(
            audit_events.c.action == "insurance.integration.port_invoked")
            .order_by(audit_events.c.id.desc())).mappings().first()
    meta = row["metadata"] or {}
    assert meta.get("port") == "commission_statement_feed" and meta.get("outcome") == "disabled"
    # the payload / secret must never reach the audit trail
    assert "SECRET-XYZ" not in str(row) and "api_key" not in str(meta)


# --- no secrets or production endpoints in the committed module --------------

def test_module_contains_no_endpoints_or_secrets():
    assert "http://" not in _SOURCE and "https://" not in _SOURCE   # no endpoints
    assert "BEGIN " not in _SOURCE                                  # no PEM/cert material
    for token in ("api_key =", "apikey", "secret =", "password =", "token =", "bearer ",
                  "client_secret", "private_key"):
        assert token.lower() not in _SOURCE.lower(), f"possible secret literal: {token}"
    # any 'secret'/'token'/'credential' word appears only in guardrail prose, never as a value
    assert not re.search(r"(secret|token|password|api_key)\s*=\s*['\"]", _SOURCE, re.I)


# --- authorization: read to view, write to invoke ----------------------------

def test_ports_require_read_to_view_and_write_to_invoke():
    reader = Principal(1, "r@e.com", "R", frozenset({"insurance.read"}))
    read_dep = require_capability("insurance.read")
    write_dep = require_capability("insurance.write")
    assert read_dep(principal=reader) is reader          # a reader may list/status
    with pytest.raises(HTTPException) as exc:             # ...but may not invoke
        write_dep(principal=reader)
    assert exc.value.status_code == 403


# --- vendor-neutral: no carrier-specific business logic ----------------------

def test_ports_are_vendor_neutral():
    # No concrete carrier/vendor names are hard-coded into the neutral contract.
    for vendor in ("ipipeline", "kaizen", "nipr", "docusign", "salesforce", "assetmark"):
        assert vendor not in _SOURCE.lower(), f"vendor-specific reference leaked: {vendor}"
