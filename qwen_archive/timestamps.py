"""Portable filesystem timestamp capture and restoration."""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class FileTimestamps:
    atime_ns: int
    mtime_ns: int
    birthtime_ns: int | None


def capture_timestamps(path: Path) -> FileTimestamps:
    stat = path.stat()
    birth = getattr(stat, "st_birthtime_ns", None)
    if birth is None and os.name == "nt":
        birth = int(stat.st_ctime_ns)
    return FileTimestamps(stat.st_atime_ns, stat.st_mtime_ns, birth)


def best_available_creation_datetime(path: Path) -> datetime:
    """Return true birth time when exposed; otherwise use modification time.

    POSIX ``ctime`` is metadata-change time, not creation time, so it is never
    used as a synthetic capture date.
    """
    stat = path.stat()
    birth = getattr(stat, "st_birthtime", None)
    if birth is None and os.name == "nt":
        birth = stat.st_ctime
    timestamp = float(birth) if birth is not None else float(stat.st_mtime)
    return datetime.fromtimestamp(timestamp)


def _restore_windows_creation_time(path: Path, birthtime_ns: int) -> None:
    if os.name != "nt":
        return
    generic_write = 0x40000000
    share = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    file_attribute_normal = 0x80
    invalid_handle_value = ctypes.c_void_p(-1).value

    handle = ctypes.windll.kernel32.CreateFileW(
        str(path), generic_write, share, None, open_existing, file_attribute_normal, None
    )
    if handle == invalid_handle_value:
        raise OSError(ctypes.get_last_error(), f"CreateFileW failed for {path}")
    try:
        ticks = birthtime_ns // 100 + 116444736000000000
        filetime = ctypes.c_ulonglong(ticks)
        if not ctypes.windll.kernel32.SetFileTime(handle, ctypes.byref(filetime), None, None):
            raise OSError(ctypes.get_last_error(), f"SetFileTime failed for {path}")
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def restore_timestamps(path: Path, stamps: FileTimestamps) -> None:
    if os.utime in os.supports_follow_symlinks:
        os.utime(path, ns=(stamps.atime_ns, stamps.mtime_ns), follow_symlinks=False)
    else:
        os.utime(path, ns=(stamps.atime_ns, stamps.mtime_ns))
    if stamps.birthtime_ns is not None:
        _restore_windows_creation_time(path, stamps.birthtime_ns)
