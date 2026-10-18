"""Whether this computer can record video at all, and if not, what to do about it.

Windows' "N" editions (sold in Europe) come without Windows' media features, Media Foundation,
which makes the recordings' H.264 video. Recording says how to add Microsoft's free Media Feature
Pack instead of failing with an encoder error.
"""
from __future__ import annotations

import ctypes
import sys

MEDIA_FEATURE_PACK = (
    'This edition of Windows (an "N" edition) comes without Windows\' media features, which '
    "recording needs to make video. Microsoft's free Media Feature Pack adds them: Settings › Apps › "
    "Optional features › View features › Media Feature Pack."
)


def _loads(name: str) -> bool:
    try:
        ctypes.WinDLL(name)
    except OSError:
        return False
    return True


def unsupported_reason() -> str | None:
    """Why this computer can't record video, or None if it can."""
    if sys.platform == "win32" and not _loads("mfplat"):
        return MEDIA_FEATURE_PACK
    return None
