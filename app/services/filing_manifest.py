"""The manifest envelope, shared by both filing phases and owned by neither.

These primitives are lifted from the batch-1 filing work (PR #253) and from the strict-safe
ownership batches before it, because that envelope has already survived several production applies:
byte-pin the reviewed artifact, re-prove every structural claim it makes rather than trusting it,
hash the PARSED content so a CRLF re-save is not a false alarm, and put the row count into the
confirmation phrase so a stale phrase cannot approve a resized batch.

WHAT IS NEW HERE: THE PHASE TAG
--------------------------------
Batch 1 created folders and set ``documents.folder_id`` under one confirmation phrase. The canonical
architecture forbids that — folder materialization and document filing are separately authorized —
so every manifest now carries a :data:`PHASE_A` / :data:`PHASE_B` tag, the tag is inside the hashed
payload, and it is inside the confirmation phrase. :func:`require_phase` is called by each apply
script before it reads anything else.

The consequence is the one that matters: a Phase A manifest handed to the Phase B script is rejected
on its phase tag, and its digest would not match either. Neither operator error nor a copied command
line can make one authorization perform both.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

PHASE_A = "PHASE_A_FOLDERS"
PHASE_B = "PHASE_B_DOCUMENTS"
PHASES = (PHASE_A, PHASE_B)

#: Batch family. Distinct from the retired ``DOCUMENT-FILING-BATCH1``, so no phrase can be reused.
BATCH_FAMILY = "CANONICAL-FILING"


class ManifestError(ValueError):
    """A gate refused. Always raised before any write."""


def require(condition, message) -> None:
    if not condition:
        raise ManifestError(f"ABORT: {message}")


def sha256_of(path) -> str:
    """SHA256 of a file's bytes — the artifact's identity, streamed so size does not matter."""
    digest = hashlib.sha256()
    with open(Path(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(payload) -> bytes:
    """UTF-8 canonical JSON: sorted keys, no whitespace, no ASCII escaping.

    Hashing this rather than a file makes the digest a statement about CONTENT, so the same manifest
    written on Windows and on Linux digests identically while the file SHA stays an exact pin.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def digest_of(payload) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def require_phase(manifest, expected_phase) -> None:
    """Refuse a manifest built for the other phase. The first gate every apply script runs."""
    require(expected_phase in PHASES, f"unknown phase {expected_phase!r}")
    actual = (manifest or {}).get("phase")
    require(actual in PHASES, f"manifest carries no valid phase tag (got {actual!r})")
    require(actual == expected_phase,
            f"manifest is {actual}, this operation is {expected_phase} — "
            "one authorization may never execute both phases")


def confirm_phrase(phase, row_count) -> str:
    """``APPLY-CANONICAL-FILING-PHASE_A_FOLDERS-1621``. Phase and size are both inside it."""
    require(phase in PHASES, f"unknown phase {phase!r}")
    return f"APPLY-{BATCH_FAMILY}-{phase}-{int(row_count)}"


def rollback_phrase(phase, row_count) -> str:
    require(phase in PHASES, f"unknown phase {phase!r}")
    return f"ROLLBACK-{BATCH_FAMILY}-{phase}-{int(row_count)}"


def claim(registry: dict, key, node: dict, *, compare_fields) -> None:
    """Register a node, refusing a key that two different node bodies both claim.

    The collision must be caught as the node is first seen. ``setdefault`` would keep the first body
    and silently drop the second, so "one key, two names" — two owners whose display labels differ
    under the same scope id — would never be visible again. A unique index would not catch it
    either: it is one key, inserted once, wearing somebody else's name.
    """
    existing = registry.get(key)
    if existing is None:
        registry[key] = node
        return
    for field in compare_fields:
        require(existing.get(field) == node.get(field),
                f"key {key!r} is claimed twice with different {field}: "
                f"{existing.get(field)!r} and {node.get(field)!r}")


def batch_id(phase, digest) -> str:
    """Short, stable batch id: phase plus the first 12 hex of the manifest digest."""
    require(phase in PHASES, f"unknown phase {phase!r}")
    return f"{BATCH_FAMILY}-{phase}-{str(digest)[:12]}"
