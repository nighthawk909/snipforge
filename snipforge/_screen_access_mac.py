"""The macOS half of screen_access.py: Quartz's Screen Recording calls (macOS 10.15 and later).

Imported only on a Mac (PyObjC's Quartz is a Mac-only dependency, requirements.txt).
"""
from __future__ import annotations

from Quartz import CGPreflightScreenCaptureAccess, CGRequestScreenCaptureAccess


def preflight() -> bool:
    """Whether SnipForge may capture other apps' windows. Never asks the person."""
    return bool(CGPreflightScreenCaptureAccess())


def request() -> bool:
    """Puts SnipForge in System Settings' Screen Recording list (switched off) and, the first time,
    shows macOS's own prompt. Returns at once; a switch turned on applies after a restart."""
    return bool(CGRequestScreenCaptureAccess())
