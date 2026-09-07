"""Folder-safe DISPLAY labels. A pure derivation that never touches master data.

THE RULE THIS MODULE ENFORCES
------------------------------
A client's folder is identified by ``(owner_scope_type, owner_scope_id)``. What a human SEES on that
folder is derived from the entity's display name — and derivation is the whole point. The corpus
holds an organization genuinely named::

    Shelbe LLC / Shear Maddness

That name is correct and stays correct. Rendered into a path it would silently become two folder
levels, so the visible label becomes ``Shelbe LLC - Shear Maddness`` while ``relationship_entities``
keeps the real name untouched. :func:`folder_safe_label` is the only place that transformation
happens, and it reads its input; it never writes one.

AUDITABLE RATHER THAN REVERSIBLE
---------------------------------
``?`` is removed and both ``/`` and ``|`` become ``" - "``, so the transform is not injective and
cannot be inverted. That is fine, and deliberately so: callers keep the source name alongside the
derived label (see :func:`describe`), which makes every label traceable to the entity it came from
without pretending a round trip exists.

IDENTITY IS NEVER AFFECTED
---------------------------
Two different owners may sanitize to the same visible label. They remain two owners with two
folders. :func:`visible_label_collisions` finds those cases so they can be reported as the UX
problem they are, rather than being silently merged into one folder — which is what a path-keyed
folder tree would have done.
"""
from __future__ import annotations

import re
import unicodedata

#: Characters no path segment may contain, on any filesystem or document store we target.
UNSAFE_CHARACTERS = ('/', '\\', '|', ':', '*', '?', '"', '<', '>')

#: The approved substitutions. Ordered, deterministic, and applied exactly once each.
SUBSTITUTIONS: tuple[tuple[str, str], ...] = (
    ("/", " - "),
    ("\\", " - "),
    ("|", " - "),
    (":", " -"),
    ("*", "+"),
    ("?", ""),
    ('"', "'"),
    ("<", "("),
    (">", ")"),
)

_WHITESPACE_RE = re.compile(r"\s+")


class UnsafeLabelError(ValueError):
    """Sanitation produced nothing usable. The caller must hold the document, not invent a name."""


def _strip_control_characters(text: str) -> str:
    # Category "Cc" is C0/C1 controls; "Cf" catches zero-width and bidi marks, which are invisible
    # in a folder listing and would make two labels look identical while comparing unequal.
    return "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf"))


def folder_safe_label(name) -> str:
    """Derive a folder-safe visible label. Pure; raises rather than returning something unusable.

    :raises UnsafeLabelError: when nothing survives sanitation (empty, whitespace-only, or made up
        entirely of characters that sanitize away).
    """
    text = _strip_control_characters(str(name or ""))
    for bad, good in SUBSTITUTIONS:
        text = text.replace(bad, good)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    # A trailing dot is legal in Postgres and illegal on Windows; strip repeatedly so "name..."
    # cannot leave one behind. Re-strip whitespace because "name ." leaves a space.
    while text.endswith("."):
        text = text[:-1].rstrip()
    if not text:
        raise UnsafeLabelError(f"label {name!r} sanitizes to nothing")
    return text


def is_folder_safe(name) -> bool:
    """True when ``name`` needs no sanitation at all."""
    text = str(name or "")
    try:
        return bool(text) and folder_safe_label(text) == text
    except UnsafeLabelError:
        return False


def needs_sanitation(name) -> bool:
    return not is_folder_safe(name)


def describe(name) -> dict:
    """The label pair plus what changed — what callers persist so a label stays traceable."""
    try:
        safe = folder_safe_label(name)
        error = None
    except UnsafeLabelError as exc:
        safe, error = None, str(exc)
    return {
        "source_label": None if name is None else str(name),
        "folder_safe_label": safe,
        "sanitized": safe is not None and safe != str(name or ""),
        "unsafe_characters": sorted({c for c in UNSAFE_CHARACTERS if c in str(name or "")}),
        "error": error,
    }


def visible_label_collisions(owners: dict) -> dict[str, list]:
    """Owners whose sanitized labels collide, keyed by the folded label.

    ``owners`` maps ``(scope_type, scope_id) -> source label``. Identity is untouched: a collision
    here is a reporting obligation, not a merge. Folding is case-insensitive because two folders
    differing only in case are indistinguishable to a human reading a list and are the same name on
    a case-insensitive store.
    """
    seen: dict[str, list] = {}
    for owner, label in owners.items():
        try:
            safe = folder_safe_label(label)
        except UnsafeLabelError:
            continue
        seen.setdefault(safe.casefold(), []).append(owner)
    return {label: sorted(group) for label, group in seen.items() if len(group) > 1}
