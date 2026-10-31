"""Where SnipForge's own files are.

Installed, the files that ship with SnipForge (license texts, an organization's theme packs) sit
next to SnipForge.exe. Caches go in %LOCALAPPDATA%.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def resources_dir() -> Path:
    """The folder with the files SnipForge ships with (LICENSE.txt, THIRD_PARTY_NOTICES.md,
    licenses/, themes/). Run from source, the repository."""
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[1]
    return Path(sys.executable).resolve().parent


def cache_dir() -> Path:
    """SnipForge's own cache folder: files it can always make again."""
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "SnipForge" / "cache" if base else Path(tempfile.gettempdir()) / "SnipForge"
