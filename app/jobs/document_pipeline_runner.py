"""Service host and CLI for the continuous document pipeline.

Two ways to run the pipeline, both this file:

* ``python -m app.jobs.document_pipeline_runner run`` — the SERVICE. Runs until stopped, with a
  discovery thread and a pool of workers. This is what a Windows service (NSSM, or a scheduled task
  with "run whether user is logged on or not") executes, and it needs no interactive session,
  browser, RDP or AI assistant of any kind: it is a plain console process that reads its configuration
  from the environment and its work from the database.
* the other subcommands — one-shot operations for install, verification and incident response.

Shutdown is cooperative. SIGINT/SIGTERM (and, on Windows, the console CTRL events the service manager
sends) set the stop event; workers finish the document they are on, hand back every lease they hold,
and exit. A hard kill is also safe — the leases lapse and the work returns to the queue — but a clean
stop makes that instant rather than a lease-length wait.

::

    python -m app.jobs.document_pipeline_runner run                 # the service
    python -m app.jobs.document_pipeline_runner run --workers 4
    python -m app.jobs.document_pipeline_runner discover            # enqueue new/changed, then exit
    python -m app.jobs.document_pipeline_runner drain               # process the queue, then exit
    python -m app.jobs.document_pipeline_runner status              # metrics snapshot
    python -m app.jobs.document_pipeline_runner health              # healthy / idle / stopped / stalled
    python -m app.jobs.document_pipeline_runner blockers            # the visible blocker queue
    python -m app.jobs.document_pipeline_runner reviews             # the ownership review queue
    python -m app.jobs.document_pipeline_runner requeue --document-id 1234
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading

log = logging.getLogger("client360.document_pipeline")


def _install_signal_handlers(stop_event: threading.Event) -> None:
    """Translate every stop signal this platform offers into the same cooperative stop.

    SIGBREAK is the one a Windows service manager sends on `Stop`; without it the service would only
    ever be killed, and a killed worker's documents wait out their leases before anyone else can take
    them."""
    def _stop(signum, _frame):
        log.info("document pipeline received signal %s — stopping", signum)
        stop_event.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        handler = getattr(signal, name, None)
        if handler is not None:
            try:
                signal.signal(handler, _stop)
            except (ValueError, OSError):
                # Not the main thread, or unsupported on this platform. The service still stops on a
                # hard kill; it just does not get to release its leases first.
                log.debug("could not install a %s handler", name)


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )


def _print(payload) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def cmd_run(args) -> int:
    """The service: discover and process continuously until stopped."""
    from app.services.document_pipeline_continuous import service as pipeline

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    log.info("document pipeline starting (workers=%s)", args.workers or "configured default")
    result = pipeline.run_service(stop_event=stop_event, workers=args.workers,
                                  discovery_interval_seconds=args.discovery_interval)
    log.info("document pipeline stopped")
    _print(result)
    return 0


def cmd_discover(args) -> int:
    from app.services.document_pipeline_continuous import service as pipeline
    _print(pipeline.discover(max_pages=args.max_pages))
    return 0


def cmd_drain(args) -> int:
    from app.services.document_pipeline_continuous import service as pipeline
    _print(pipeline.drain(max_passes=args.max_passes))
    return 0


def cmd_status(_args) -> int:
    from app.services.document_pipeline_continuous import service as pipeline
    _print(pipeline.status())
    return 0


def cmd_health(_args) -> int:
    """Exit code doubles as a monitoring probe: 0 healthy/idle, 1 stalled/stopped/not installed."""
    from app.services.document_pipeline_continuous import service as pipeline
    status = pipeline.health()
    _print(status)
    return 0 if status.get("healthy") else 1


def cmd_blockers(args) -> int:
    from app.services.document_pipeline_continuous import service as pipeline
    _print(pipeline.blockers(limit=args.limit))
    return 0


def cmd_reviews(args) -> int:
    from app.services.document_pipeline_continuous import service as pipeline
    _print(pipeline.reviews(lane=args.lane, limit=args.limit))
    return 0


def cmd_requeue(args) -> int:
    from app.services.document_pipeline_continuous import service as pipeline
    moved = pipeline.requeue_document(args.document_id)
    _print({"document_id": args.document_id, "requeued": moved,
            "note": None if moved else "no task at rest for that document — nothing was changed"})
    return 0 if moved else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.jobs.document_pipeline_runner",
        description="Continuous document pipeline: service host and operations CLI.")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the pipeline continuously until stopped (the service)")
    run.add_argument("--workers", type=int, default=None,
                     help="parallel workers (default: DOCUMENT_PIPELINE_WORKERS)")
    run.add_argument("--discovery-interval", type=int, default=60,
                     help="seconds between discovery passes (default: 60)")
    run.set_defaults(func=cmd_run)

    discover = sub.add_parser("discover", help="enqueue new and changed documents, then exit")
    discover.add_argument("--max-pages", type=int, default=None,
                          help="page budget; omit to drain discovery completely")
    discover.set_defaults(func=cmd_discover)

    drain = sub.add_parser("drain", help="process the queue until it is empty, then exit")
    drain.add_argument("--max-passes", type=int, default=None,
                       help="optional pass budget; omit to drain the queue completely")
    drain.set_defaults(func=cmd_drain)

    sub.add_parser("status", help="metrics snapshot").set_defaults(func=cmd_status)
    sub.add_parser("health", help="healthy / idle / stopped / stalled (exit 1 if unhealthy)"
                   ).set_defaults(func=cmd_health)

    blockers = sub.add_parser("blockers", help="the visible blocker queue")
    blockers.add_argument("--limit", type=int, default=50)
    blockers.set_defaults(func=cmd_blockers)

    reviews = sub.add_parser("reviews", help="the ownership review queue")
    reviews.add_argument("--lane", default=None, choices=("drake", "taxdome", "sharepoint"))
    reviews.add_argument("--limit", type=int, default=50)
    reviews.set_defaults(func=cmd_reviews)

    requeue = sub.add_parser("requeue", help="put one document back through the pipeline")
    requeue.add_argument("--document-id", type=int, required=True)
    requeue.set_defaults(func=cmd_requeue)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    from app.services.document_pipeline_continuous.model import PipelineNotInstalled
    try:
        return args.func(args)
    except PipelineNotInstalled as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
