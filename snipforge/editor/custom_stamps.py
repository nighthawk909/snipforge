"""Your own stamps: pictures you add (a logo, an icon, a photo) and drop onto snips like the built-in
stamps.

They live as files in a Stamps folder next to the Library (PNG, or SVG as it is), named by their
file's stem; a style refers to one as "custom:<name>". Every open editor follows additions and
removals (`notifier`). A snip that uses one needs document version 3 (an older SnipForge would draw
the check stamp instead), and a stamp whose file is gone draws as an empty frame, never an error.
"""
from __future__ import annotations

import logging
import re
import shutil
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QImage, QImageReader

log = logging.getLogger(__name__)

DEFAULT_ROOT = Path.home() / "Pictures" / "SnipForge" / "Stamps"
PREFIX = "custom:"
MAX_SIDE = 1024  # bigger pictures are scaled down when added: stamps are small
MAX_STAMPS = 48
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".svg")
_root: Path | None = None


def root() -> Path:
    return _root or DEFAULT_ROOT


def set_root(path: Path | None) -> None:
    """Where the stamps are kept (tests use a throwaway folder)."""
    global _root
    _root = path
    image.cache_clear()


def is_custom(name: str) -> bool:
    return isinstance(name, str) and name.startswith(PREFIX)


def _safe_stem(stem: str) -> str:
    """A file stem that's safe on any disk: letters, digits, spaces, dashes and underscores."""
    cleaned = re.sub(r"[^\w \-]+", "", stem, flags=re.UNICODE).strip()
    return cleaned[:40] or "stamp"


def _path_of(name: str) -> Path | None:
    stem = name[len(PREFIX):]
    for suffix in (".png", ".svg"):
        path = root() / f"{stem}{suffix}"
        if path.is_file():
            return path
    return None


def names() -> list[str]:
    """Every stamp you've added, as style names, in alphabetical order."""
    folder = root()
    if not folder.is_dir():
        return []
    found = sorted({p.stem for p in folder.iterdir() if p.suffix.lower() in (".png", ".svg") and p.is_file()},
                   key=str.lower)
    return [PREFIX + stem for stem in found[:MAX_STAMPS]]


def label_of(name: str) -> str:
    return name[len(PREFIX):] if is_custom(name) else name


@lru_cache(maxsize=64)
def image(name: str) -> QImage | None:
    """The stamp's picture, or None when its file is gone (or unreadable)."""
    path = _path_of(name)
    if path is None:
        return None
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    if path.suffix.lower() == ".svg":  # rastered crisp at stamp size; a lens or a big stamp scales it
        size = reader.size()
        reader.setScaledSize(size.scaled(256, 256, Qt.KeepAspectRatio) if size.isValid() else QSize(256, 256))
    picture = reader.read()
    return None if picture.isNull() else picture


def add(source: Path) -> str | None:
    """Copy a picture in as a stamp. Returns its style name, or None if it couldn't be read or there's
    no room. A picture bigger than MAX_SIDE is scaled down; an SVG is kept as it is."""
    source = Path(source)
    if source.suffix.lower() not in IMAGE_SUFFIXES or len(names()) >= MAX_STAMPS:
        return None
    folder = root()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("Couldn't make the stamps folder: %s", exc)
        return None
    stem = _safe_stem(source.stem)
    name = PREFIX + stem
    n = 2
    while _path_of(name) is not None:  # keep both: "logo", "logo 2"...
        name = f"{PREFIX}{stem} {n}"
        n += 1
    stem = name[len(PREFIX):]
    try:
        if source.suffix.lower() == ".svg":
            shutil.copyfile(source, folder / f"{stem}.svg")
        else:
            reader = QImageReader(str(source))
            reader.setAutoTransform(True)
            picture = reader.read()
            if picture.isNull():
                return None
            if max(picture.width(), picture.height()) > MAX_SIDE:
                picture = picture.scaled(MAX_SIDE, MAX_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            if not picture.save(str(folder / f"{stem}.png"), "PNG"):
                return None
    except OSError as exc:
        log.warning("Couldn't add the stamp: %s", exc)
        return None
    image.cache_clear()
    notifier().changed.emit()
    return name


def remove(name: str) -> bool:
    path = _path_of(name) if is_custom(name) else None
    if path is None:
        return False
    try:
        path.unlink()
    except OSError as exc:
        log.warning("Couldn't remove the stamp: %s", exc)
        return False
    image.cache_clear()
    notifier().changed.emit()
    return True


class _Notifier(QObject):
    changed = Signal()


_notifier: _Notifier | None = None


def notifier() -> _Notifier:
    global _notifier
    if _notifier is None:
        _notifier = _Notifier()
    return _notifier
