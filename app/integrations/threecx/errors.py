"""Connector failure modes, separated so the route can map each to one status code."""
from __future__ import annotations


class ThreeCxError(Exception):
    """Base class for every 3CX connector refusal."""


class ThreeCxUnavailable(ThreeCxError):
    """The connector is switched off, or has no usable secret. The route answers 404 — a probe
    must not learn whether this deployment has a telephony surface at all."""


class ThreeCxUnauthenticated(ThreeCxError):
    """No valid integration secret was presented. The route answers 401 and says how to
    authenticate, never why the attempt failed."""


# A malformed PAYLOAD is not represented here on purpose. Parsing a call outcome, a duration and a
# start instant is PBX-neutral work that belongs with the journal, so those refusals are
# ``app.services.communications.call_journal.CallJournalError`` and the route maps them to 422. A
# second, 3CX-flavoured payload error would just be the same failure with two names.
