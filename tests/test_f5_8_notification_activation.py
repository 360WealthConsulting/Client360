"""F5.8 / Epic 5 — Notification activation layer tests (ADR-017).

Covers the stateless activation entry point: invokes an injected worker callable exactly once,
carries the worker result opaquely, reports worker success/failure at the activation level
without inspecting notifications, cooperative pre-invocation cancellation, immutable
activation-level result, and the strict scope contract (no ledger/attempt/claim/F5.7 access,
no due-time arithmetic, no specific-worker coupling, no driver).
"""
from __future__ import annotations

from pathlib import Path

from app.services.notification_activation import ActivationResult, activate

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_activate_invokes_worker_exactly_once():
    calls = {"n": 0}

    def _worker():
        calls["n"] += 1
        return {"ok": True}

    r = activate(worker=_worker)
    assert calls["n"] == 1
    assert isinstance(r, ActivationResult)
    assert r.started and r.completed and r.worker_invoked and r.worker_ok
    assert r.cancelled is False and r.error_class is None
    assert r.worker_result == {"ok": True}  # opaque pass-through
    assert r.runtime_ms >= 0


def test_worker_exception_is_activation_level_failure():
    def _worker():
        raise RuntimeError("worker blew up")

    r = activate(worker=_worker)
    assert r.worker_invoked is True and r.worker_ok is False
    assert r.error_class == "RuntimeError"  # classification only, no message/notification data
    assert r.completed is True and r.worker_result is None


def test_pre_invocation_cancellation_skips_worker():
    calls = {"n": 0}

    def _worker():
        calls["n"] += 1

    r = activate(worker=_worker, stop=lambda: True)
    assert calls["n"] == 0  # never invoked
    assert r.cancelled is True and r.worker_invoked is False and r.worker_ok is False
    assert r.completed is True


def test_stop_not_triggered_invokes_worker():
    r = activate(worker=lambda: "done", stop=lambda: False)
    assert r.worker_invoked is True and r.worker_ok is True and r.worker_result == "done"


def test_worker_result_carried_opaquely_and_delegated_in_to_dict():
    class _Metrics:
        def to_dict(self):
            return {"delivered": 3, "scanned": 3}

    r = activate(worker=lambda: _Metrics())
    d = r.to_dict()
    assert set(d) == {"started", "completed", "cancelled", "worker_invoked", "worker_ok",
                      "runtime_ms", "error_class", "worker_result"}
    assert d["worker_result"] == {"delivered": 3, "scanned": 3}  # delegated, not re-derived
    assert r.worker_result.__class__.__name__ == "_Metrics"  # object nested opaquely


def test_activation_result_is_immutable():
    import pytest
    r = activate(worker=lambda: None)
    with pytest.raises(Exception):  # frozen
        r.worker_ok = False


def test_stateless_repeated_activation_holds_no_state():
    seen = []
    r1 = activate(worker=lambda: seen.append(1) or "a")
    r2 = activate(worker=lambda: seen.append(2) or "b")
    assert r1.worker_result == "a" and r2.worker_result == "b" and seen == [1, 2]


def test_integration_with_real_worker_without_f58_importing_it():
    # F5.8 stays generic: the TEST injects the real F5.6 worker; F5.8 never imports it.
    from app.services.notification_worker import run_dispatch_cycle
    r = activate(worker=lambda: run_dispatch_cycle(claim=lambda attempted, **kw: None))
    assert r.worker_ok is True
    assert r.worker_result.idle is True  # opaque F5.6 cycle result, carried through


# --- scope contract ----------------------------------------------------------

def test_scope_contract_activation_only():
    source = (REPO_ROOT / "app" / "services" / "notification_activation.py").read_text()
    # (1) import lines: no coupling to any worker/notification/ledger/driver/clock module.
    imports = "\n".join(line for line in source.splitlines() if line.strip().startswith(("import ", "from ")))
    for forbidden_import in ("notification_worker", "notification_dispatch", "notification_retry",
                             "notifications", "app.db", "apscheduler", "datetime"):
        assert forbidden_import not in imports, f"unexpected import coupling: {forbidden_import}"
    # (2) code call-forms (won't appear in descriptive prose): no ledger/DB/clock/due-time/dispatch.
    for forbidden_call in ("app.db", "engine.", "select(", "_notifications_table", ".now(",
                           "evaluate_retry(", "delivery_attempts(", "run_dispatch_cycle(", "add_job("):
        assert forbidden_call not in source, f"unexpected usage: {forbidden_call}"
    assert (REPO_ROOT / "docs" / "NOTIFICATION_ACTIVATION.md").is_file()


def test_f56_f57_f55_unmodified_in_worktree():
    import subprocess
    out = subprocess.run(["git", "status", "--porcelain",
                          "app/services/notification_worker.py",
                          "app/services/notification_retry.py",
                          "app/services/notification_dispatch.py"],
                         cwd=REPO_ROOT, capture_output=True, text=True).stdout
    assert out.strip() == ""  # F5.6 / F5.7 / F5.5 untouched
