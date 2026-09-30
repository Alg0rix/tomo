"""Avoid retaining large, freed native buffers on glibc hosts.

Scrypt uses a 16 MiB temporary workspace. glibc's adaptive mmap threshold
can put subsequent workspaces on the heap and keep them resident after login.
A fixed threshold lets the allocator return large buffers to the OS on free.
Other C libraries keep their defaults; explicit operator tuning wins.
"""
from __future__ import annotations

import os
import sys


def configure_allocator() -> None:
    if not sys.platform.startswith("linux"):
        return
    if "MALLOC_MMAP_THRESHOLD_" in os.environ or "glibc.malloc.mmap_threshold" in os.environ.get("GLIBC_TUNABLES", ""):
        return
    try:
        import ctypes

        libc = ctypes.CDLL(None)
        if not hasattr(libc, "gnu_get_libc_version"):
            return
        mallopt = libc.mallopt
        mallopt.argtypes = (ctypes.c_int, ctypes.c_int)
        mallopt.restype = ctypes.c_int
        # glibc M_MMAP_THRESHOLD: same value as MALLOC_MMAP_THRESHOLD_=131072.
        mallopt(-3, 128 * 1024)
    except (AttributeError, OSError):
        pass
