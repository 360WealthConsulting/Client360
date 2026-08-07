"""Storage Service — the ONLY path to the enterprise repository.

Artifact handlers NEVER touch the filesystem directly. Every read/write/stat goes through a
:class:`StorageService`, so the physical backend (local NTFS today; NAS / object storage / cloud later)
is replaceable WITHOUT changing any handler. The authoritative repository is ``D:\\Client360Data``.
"""
from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass

# OneDrive / Files-On-Demand cloud-only attribute bits (Windows only).
_FILE_ATTRIBUTE_OFFLINE = 0x1000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_PLACEHOLDER_MASK = _FILE_ATTRIBUTE_OFFLINE | _FILE_ATTRIBUTE_RECALL_ON_OPEN | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS


def _is_placeholder(st) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & _PLACEHOLDER_MASK)


@dataclass
class StatInfo:
    exists: bool
    size: int = 0
    is_placeholder: bool = False        # cloud-only (must be hydrated before an apply can read it)


class StorageService(ABC):
    """Backend-agnostic access to source reads (validation) and the destination repository (apply)."""

    @abstractmethod
    def stat(self, uri: str) -> StatInfo:
        ...

    @abstractmethod
    def exists(self, uri: str) -> bool:
        ...

    @abstractmethod
    def free_and_total(self, path: str) -> tuple[int | None, int | None]:
        ...

    # Write path is used by the Apply service (not implemented in this phase).
    def read(self, uri: str) -> bytes:
        raise NotImplementedError("read is an apply-stage operation, not built yet")

    def write(self, uri: str, data: bytes) -> None:
        raise NotImplementedError("write is an apply-stage operation, not built yet")


class LocalFilesystemStorage(StorageService):
    """Local NTFS / mounted-drive implementation. Swap for a NAS/object/cloud implementation without
    changing any handler."""

    def stat(self, uri: str) -> StatInfo:
        try:
            st = os.stat(uri)
        except OSError:
            return StatInfo(exists=False)
        return StatInfo(exists=True, size=st.st_size, is_placeholder=_is_placeholder(st))

    def exists(self, uri: str) -> bool:
        return os.path.exists(uri)

    def free_and_total(self, path: str) -> tuple[int | None, int | None]:
        try:
            usage = shutil.disk_usage(path)
            return usage.free, usage.total
        except OSError:
            return None, None
