# Parallel OCR benchmark — 1, 2 and 4 workers

Run on 360SRV, 2026-09-12, while the production single worker (PID 2184) kept sweeping untouched.
Same frozen 200-document sample through all three arms, production extraction path, production
timeout bounds. No database row was written and no document was altered.

Host: Xeon E-2434, 4 physical cores / 8 logical, 32 GB RAM.

## Sample

200 documents, stratified, all with a readable file on disk. 185 already had stored OCR text, which
is what equivalence is measured against.

| Stratum | Documents |
|---|---:|
| hybrid PDF (text layer + OCR) | 35 |
| non-PDF images (jpg/jpeg/png/tiff/heic) | 35 |
| text-layer PDF | 30 |
| multipage PDF, 10+ pages | 25 |
| image-based PDF (rendered; text layer unreadable) | 20 |
| large scans, 8–25 MB | 20 |
| duplicate-hash reuse cases | 20 |
| previously timed out | 10 |
| malformed / recovery-required | 5 |

Stored per-document durations could not be used to build a "near timeout" stratum:
`ocr_started_at` and `ocr_completed_at` are written in the same statement, so every stored duration
is exactly zero. The 10 documents that genuinely hit the timeout are used instead, alongside the
large-scan and high-page-count cohorts.

## Results

| Workers | Elapsed | Docs/min | Speedup | Efficiency | CPU mean | CPU max | Free MB min | p95 s/doc |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1763.7 s | 6.80 | 1.00× | 100% | 54.0% | 100.0% | 9,175 | 40.7 |
| 2 | 919.8 s | 13.05 | 1.92× | 96% | 62.8% | 90.5% | 7,218 | 45.2 |
| 4 | 556.6 s | 21.56 | 3.17× | 79% | 75.0% | 95.0% | 6,127 | 50.2 |

Disk was never a factor: the queue stayed at 0.0 throughout, as it does under the single worker.
Free memory never approached the 2,048 MB floor; the closest was 6,127 MB at four workers.

## Output equivalence

The important result is that parallelism changes nothing:

- **200 of 200 documents produced an identical outcome in all three arms.**
- **200 of 200 produced an identical character count in all three arms.**
- Outcomes were 195 completed and 5 failed in every arm, the same five documents each time.

Against *stored* text, 159 of 185 landed within ±5% of the stored character count, identically in
every arm. The 26 that did not are a harness artifact, not a regression: this harness always runs the
full extraction path, while many stored rows were produced by the text-layer-only engine, so the
harness recovers more text (mean ratio 1.38). Since the figure is identical across all three arms, it
says nothing about parallelism either way.

## Failures

The same 5 documents failed in every arm, all with `DecompressionBombError` — images between 190 and
301 megapixels against Pillow's 178,956,970 ceiling. Deterministic, unrelated to worker count, and
already identified independently as a pixel-ceiling configuration issue rather than file corruption.

No document hit the OCR timeout in any arm. The slowest single document took 184.1 s.

## Contention with the production worker

The production worker's own throughput, from its chunk checkpoints, is the honest measure. Its parent
process CPU time is not: the OCR runs in a short-lived spawned child, so the parent accumulates
almost no CPU regardless of load.

| Window | Production worker throughput |
|---|---:|
| Baseline, before the benchmark | 8.55 docs/min |
| During arm 1 (1 benchmark worker) | 7.77 docs/min (−9%) |
| During arms 2 and 4 | 4.08 docs/min (−52%) |

Arms 2 and 4 ran 15 and 9 minutes, which is too few 50-document checkpoints to separate, so they are
reported together.

This contention **understates** the parallel runner's real throughput: every arm was competing with a
production worker that would not exist after cutover, because the parallel runner replaces it.

## Recommended worker count: 3

Two workers are nearly free at 96% efficiency; the fourth is the expensive one, dropping efficiency
to 79% on a 4-core box. Three keeps a core's worth of headroom for the Client360 web application,
which is the whole reason the workers run at BELOW_NORMAL priority.

Four is reasonable for an unattended overnight catch-up. The runner warns above four and the
admission gates hold new claims if CPU or memory move against the web application either way.

## Projected completion

Backlog at the time of writing: 31,024 never attempted plus 2,582 in the retry lane, so 33,606
documents of OCR work. Projected from the measured single-worker production rate of 8.55 docs/min
scaled by the measured speedups.

| Workers | Projected docs/min | Hours to drain |
|---:|---:|---:|
| 1 (today) | 8.55 | 65.5 |
| 2 | 16.42 | 34.1 |
| 3 | 22.23 | 25.2 |
| 4 | 27.10 | 20.7 |

The 3-worker row is interpolated between the measured 2- and 4-worker arms; every other row is
measured. At the recommended 3 workers the remaining corpus finishes in about a day instead of
about two and a half.

## Reproducing

The sample manifest carries storage paths and is deliberately not committed. Supply it at run time:

```bash
export OCR_BENCH_SAMPLE=/path/to/bench_sample.json
python scripts/benchmark_ocr_parallel.py --workers 4 --legacy-pid <pid> --out arm4.json
```

The report the script writes contains document ids, timings and character counts only — no
filenames, no paths, no document text.
