"""Client360 platform infrastructure (E1.6+).

Low-level, domain-agnostic primitives shared across the application. Currently
the transactional outbox & dispatcher (Backlog F1.3).
"""

from app.platform.outbox import (
    clear_subscribers,
    dispatch_pending,
    publish,
    subscribe,
)

__all__ = ["publish", "subscribe", "clear_subscribers", "dispatch_pending"]
