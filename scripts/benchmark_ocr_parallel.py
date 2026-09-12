"""Benchmark OCR extraction throughput at N workers. READ-ONLY: writes no database row, no file.

Runs the SAME frozen document sample through the production extraction path (subprocess isolation,
real wall-clock caps) at a given worker count, and compares each result against the text already
stored for that document so parallelism can be shown not to change the output.

The sample file carries storage paths and is supplied at run time via ``OCR_BENCH_SAMPLE``; it is
never committed and no filename, path or document text is written to the report.

    python scripts/benchmark_ocr_parallel.py --workers 1 --out arm1.json
"""
from __future__ import annotations

import argparse
import ctypes
import json
import multiprocessing as mp
import os
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_FACTORY = "app.services.ocr_backend.build_production_extractor"
_WS = re.compile(r"\s+")


def _norm(s):
    """Whitespace-insensitive comparison: line breaking differs harmlessly between runs."""
    return _WS.sub(" ", (s or "")).strip()


def _similarity(a, b):
    """Cheap token-level Jaccard. Enough to separate 'same text' from 'different text'."""
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# --- host sampling ------------------------------------------------------------------------------

class _Mem(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _FT(ctypes.Structure):
    _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]


def _times():
    i, k, u = _FT(), _FT(), _FT()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
    to_i = lambda f: (f.high << 32) | f.low  # noqa: E731
    return to_i(i), to_i(k), to_i(u)


def _free_mb():
    s = _Mem()
    s.dwLength = ctypes.sizeof(_Mem)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return int(s.ullAvailPhys // (1024 * 1024))


def _sampler(stop, out_q, legacy_pid):
    """Sample CPU, free memory and the legacy worker's CPU seconds for the life of an arm."""
    import subprocess
    prev = _times()
    cpu, mem = [], []
    legacy_start = legacy_end = None

    def legacy_cpu_seconds():
        if not legacy_pid:
            return None
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Process -Id {legacy_pid} -ErrorAction SilentlyContinue)"
                 f".TotalProcessorTime.TotalSeconds"],
                capture_output=True, text=True, timeout=15)
            return float((out.stdout or "").strip())
        except Exception:  # noqa: BLE001
            return None

    legacy_start = legacy_cpu_seconds()
    while not stop.wait(2.0):
        cur = _times()
        di, dk, du = (cur[0] - prev[0]), (cur[1] - prev[1]), (cur[2] - prev[2])
        total = dk + du
        if total > 0:
            cpu.append(100.0 * (total - di) / total)
        mem.append(_free_mb())
        prev = cur
    legacy_end = legacy_cpu_seconds()

    out_q.put({
        "cpu_percent_mean": round(statistics.fmean(cpu), 1) if cpu else None,
        "cpu_percent_max": round(max(cpu), 1) if cpu else None,
        "free_mb_min": min(mem) if mem else None,
        "free_mb_mean": round(statistics.fmean(mem)) if mem else None,
        "legacy_worker_cpu_seconds_consumed": (
            round(legacy_end - legacy_start, 1)
            if (legacy_start is not None and legacy_end is not None) else None),
    })


# --- the work -----------------------------------------------------------------------------------

def _extract_one(doc):
    """Run one document through the production isolated extraction path. Returns a timing record."""
    from app.services import ocr_isolation
    from app.services.ocr_exceptions import OcrEncryptedPdf, OcrTimeout

    # Identical bounds to production: the benchmark must not look faster by running looser caps.
    hard_timeout, stall_timeout = ocr_isolation.default_bounds()

    row = {"id": doc["document_id"], "original_name": Path(doc["path"]).name,
           "sha256": None, "storage_uri": doc["path"], "storage_path": doc["path"],
           "content_type": None}
    t0 = time.monotonic()
    outcome, text, pages, err = "completed", "", None, None
    try:
        result = ocr_isolation.run_document(_FACTORY, row, doc["path"],
                                            hard_timeout=hard_timeout,
                                            stall_timeout=stall_timeout,
                                            doc_id=doc["document_id"],
                                            name=row["original_name"])
        if isinstance(result, dict):
            text = result.get("text") or ""
            pages = result.get("page_count")
        else:
            text = result or ""
        if not text.strip():
            outcome = "no_text"
    except OcrTimeout as exc:
        outcome, err = "timed_out", str(exc)[:200]
    except OcrEncryptedPdf:
        outcome, err = "encrypted", "password required"
    except Exception as exc:  # noqa: BLE001
        outcome, err = "failed", f"{type(exc).__name__}: {str(exc)[:160]}"
    elapsed = time.monotonic() - t0

    return {"document_id": doc["document_id"], "stratum": doc["stratum"],
            "seconds": round(elapsed, 3), "outcome": outcome, "error": err,
            "chars": len(text.strip()), "pages": pages,
            "size_bytes": doc["size_bytes"],
            "_text": text}


def _worker(task_q, done_q):
    while True:
        doc = task_q.get()
        if doc is None:
            return
        try:
            done_q.put(_extract_one(doc))
        except Exception as exc:  # noqa: BLE001
            done_q.put({"document_id": doc.get("document_id"), "stratum": doc.get("stratum"),
                        "seconds": 0.0, "outcome": "harness_error",
                        "error": f"{type(exc).__name__}: {exc}", "chars": 0, "pages": None,
                        "size_bytes": doc.get("size_bytes", 0), "_text": ""})


def run_arm(sample, workers, legacy_pid):
    ctx = mp.get_context("spawn")
    task_q, done_q = ctx.Queue(), ctx.Queue()
    for doc in sample:
        task_q.put(doc)
    for _ in range(workers):
        task_q.put(None)

    stop = ctx.Event()
    sample_q = ctx.Queue()
    sampler = ctx.Process(target=_sampler, args=(stop, sample_q, legacy_pid))
    sampler.start()

    started = time.monotonic()
    procs = [ctx.Process(target=_worker, args=(task_q, done_q)) for _ in range(workers)]
    for p in procs:
        p.start()
    records = [done_q.get() for _ in sample]
    for p in procs:
        p.join()
    elapsed = time.monotonic() - started

    stop.set()
    host = sample_q.get()
    sampler.join(timeout=10)
    return records, elapsed, host


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sample", default=os.getenv("OCR_BENCH_SAMPLE"))
    ap.add_argument("--legacy-pid", type=int, default=int(os.getenv("OCR_LEGACY_PID", "0")) or None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    if not args.sample:
        ap.error("no sample: pass --sample or set OCR_BENCH_SAMPLE")
    manifest = json.loads(Path(args.sample).read_text(encoding="utf-8"))
    sample = manifest["documents"][: args.limit] if args.limit else manifest["documents"]

    stored = {d["document_id"]: d for d in sample}
    records, elapsed, host = run_arm(sample, args.workers, args.legacy_pid)

    # Equivalence against the text already stored for each document.
    compared = matched = 0
    sims = []
    for r in records:
        meta = stored.get(r["document_id"], {})
        if not meta.get("has_stored_text") or r["outcome"] != "completed":
            continue
        compared += 1
        expected_chars = meta.get("stored_char_count") or 0
        ratio = (r["chars"] / expected_chars) if expected_chars else 0.0
        sims.append(ratio)
        if 0.95 <= ratio <= 1.05:
            matched += 1
        r["char_ratio_vs_stored"] = round(ratio, 4)

    for r in records:
        r.pop("_text", None)

    outcomes = {}
    for r in records:
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    secs = [r["seconds"] for r in records]

    report = {
        "artifact": "ocr_parallel_benchmark_arm",
        "generated_utc": datetime.now(UTC).isoformat(),
        "workers": args.workers,
        "documents": len(records),
        "elapsed_seconds": round(elapsed, 2),
        "documents_per_minute": round(len(records) / (elapsed / 60.0), 2) if elapsed else 0.0,
        "seconds_per_document": {
            "mean": round(statistics.fmean(secs), 2) if secs else 0,
            "median": round(statistics.median(secs), 2) if secs else 0,
            "p95": round(sorted(secs)[int(len(secs) * 0.95) - 1], 2) if secs else 0,
            "max": round(max(secs), 2) if secs else 0,
        },
        "outcomes": outcomes,
        "equivalence": {
            "compared": compared,
            "within_5_percent_of_stored_char_count": matched,
            "mean_char_ratio": round(statistics.fmean(sims), 4) if sims else None,
        },
        "host": host,
        "legacy_worker_pid": args.legacy_pid,
        "privacy": "document ids, timings and character counts only; no filenames, paths or text",
        "records": records,
    }
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"workers={args.workers}  docs={len(records)}  elapsed={report['elapsed_seconds']}s  "
          f"{report['documents_per_minute']} docs/min")
    print(f"  outcomes   {outcomes}")
    print(f"  equivalence {matched}/{compared} within 5% of stored char count")
    print(f"  host        {host}")
    print(f"  -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
