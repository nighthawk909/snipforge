"""Whether this computer can record video at all, and if not, what to do about it.

Windows' "N" editions (sold in Europe) come without Windows' media features, Media Foundation,
which makes the recordings' H.264 video. Recording says how to add Microsoft's free Media Feature
Pack instead of failing with an encoder error.
"""
from __future__ import annotations

import ctypes
import functools
import sys

MEDIA_FEATURE_PACK = (
    "This copy of Windows has no media features (Media Foundation), which recording needs to make "
    'video. "N" editions of Windows come without them: Microsoft\'s free Media Feature Pack adds '
    'them. Search Start for "Optional features", add "Media Feature Pack", and restart.'
)
LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x800  # only Windows' own copy, never one next to SnipForge


@functools.cache
def _loads(name: str) -> bool:
    try:
        ctypes.WinDLL(name, winmode=LOAD_LIBRARY_SEARCH_SYSTEM32)
    except OSError:
        return False
    return True


def unsupported_reason() -> str | None:
    """Why this computer can't record video, or None if it can."""
    if sys.platform == "win32" and not _loads("mfplat"):
        return MEDIA_FEATURE_PACK
    return None
