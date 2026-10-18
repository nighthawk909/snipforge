"""Where SnipForge's own files are, on each platform (docs/CROSS-PLATFORM.md).

Installed on Windows, the files that ship with SnipForge (license texts, an organization's theme
packs) sit next to SnipForge.exe. In SnipForge.app they're in Contents/Resources, while the
program itself is in Contents/MacOS. Caches go in %LOCALAPPDATA% on Windows and ~/Library/Caches on
a Mac.
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
    program = Path(sys.executable).resolve().parent
    if sys.platform == "darwin" and program.name == "MacOS" and program.parent.name == "Contents":
        return program.parent / "Resources"
    return program


def cache_dir() -> Path:
    """SnipForge's own cache folder: files it can always make again."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "SnipForge"
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "SnipForge" / "cache" if base else Path(tempfile.gettempdir()) / "SnipForge"
