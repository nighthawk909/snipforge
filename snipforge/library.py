"""Snip library: every capture is kept as an original image plus editable annotations.

Layout on disk (one folder per snip):
    Pictures/SnipForge/Library/<id>/original.png   untouched capture
    Pictures/SnipForge/Library/<id>/document.json  annotations + crop (re-editable)
    Pictures/SnipForge/Library/<id>/rendered.png   full-size flattened result (copy/export)
    Pictures/SnipForge/Library/<id>/preview.png    small thumbnail
    Pictures/SnipForge/Library/<id>/meta.json      source app and window title, favorite, tags, and the
                                                   words recognized in the snip (for search)
All writes go to a temp file first and are renamed into place, so a crash never
leaves a half-written file behind.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, fields, replace
from pathlib import Path

from PySide6.QtCore import QFile, Qt
from PySide6.QtGui import QImage

log = logging.getLogger(__name__)

DEFAULT_ROOT = Path.home() / "Pictures" / "SnipForge" / "Library"
ORIGINAL, DOCUMENT, RENDERED, PREVIEW = "original.png", "document.json", "rendered.png", "preview.png"
META = "meta.json"
THUMB_MAX = 480
MAX_TITLE, MAX_APP, MAX_TEXT = 200, 40, 20_000
MAX_TAGS, MAX_TAG = 10, 32


@dataclass(frozen=True)
class EntryMeta:
    title: str = ""        # window title the snip came from
    app: str = ""          # e.g. "Excel"
    favorite: bool = False
    tags: tuple[str, ...] = ()
    text: str = ""         # words recognized in the snip, for search
    indexed: bool = False  # text recognition has run (even if it found nothing)


def clean_tags(tags) -> tuple[str, ...]:
    """Lowercase, trimmed, unique, at most MAX_TAGS of at most MAX_TAG characters."""
    if not isinstance(tags, (list, tuple)):
        return ()
    found: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        tag = " ".join(tag.split()).lower()[:MAX_TAG]
        if tag and tag not in found:
            found.append(tag)
    return tuple(found[:MAX_TAGS])


_TYPES = {"title": str, "app": str, "favorite": bool, "text": str, "indexed": bool}
_LIMITS = {"title": MAX_TITLE, "app": MAX_APP, "text": MAX_TEXT}


def _normalized(changes: dict) -> dict:
    """Valid fields only, coerced to their limits; anything of the wrong type is dropped."""
    out = {}
    for key, value in changes.items():
        if key == "tags":
            out[key] = clean_tags(value)
        elif key in _TYPES and isinstance(value, _TYPES[key]):
            out[key] = value[:_LIMITS[key]] if key in _LIMITS else value
    return out


class DocumentUnreadable(OSError):
    """document.json exists but couldn't be read; callers must not overwrite it."""


@dataclass(frozen=True)
class Entry:
    id: str
    path: Path

    @property
    def created(self) -> dt.datetime:
        try:
            return dt.datetime.strptime(self.id[:15], "%Y%m%d-%H%M%S")
        except ValueError:
            return dt.datetime.fromtimestamp(self.path.stat().st_mtime)

    @property
    def original_path(self) -> Path:
        return self.path / ORIGINAL

    def _first_existing(self, *names: str) -> Path:
        for name in names:
            if (self.path / name).exists():
                return self.path / name
        return self.path / ORIGINAL

    @property
    def rendered_path(self) -> Path:
        """Full-size result with annotations (falls back to the original)."""
        return self._first_existing(RENDERED, ORIGINAL)

    @property
    def preview_path(self) -> Path:
        return self._first_existing(PREVIEW, RENDERED, ORIGINAL)


def _replace(tmp: Path, target: Path, attempts: int = 5) -> None:
    """os.replace, retried briefly: antivirus and the Windows search indexer sometimes hold a
    just-written file for a moment."""
    for attempt in range(attempts):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.03 * (attempt + 1))


def _write_text_atomic(text: str, target: Path) -> None:
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    _replace(tmp, target)


def _save_image_atomic(image: QImage, target: Path) -> None:
    tmp = target.with_name(target.name + ".tmp")
    if not image.save(str(tmp), "PNG"):
        tmp.unlink(missing_ok=True)
        raise OSError(f"Could not write {target}")
    _replace(tmp, target)


class Library:
    def __init__(self, root: Path = DEFAULT_ROOT) -> None:
        self.root = Path(root)
        self._meta_lock = threading.Lock()  # the text indexer writes from its own thread

    def create(self, image: QImage, source=None) -> Entry:
        """`source` is the window the snip came from (a winrects.WindowInfo), if known."""
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:19]
        path = self.root / stamp
        suffix = 1
        while path.exists():
            path = self.root / f"{stamp}-{suffix}"
            suffix += 1
        path.mkdir()
        _save_image_atomic(image, path / ORIGINAL)
        entry = Entry(path.name, path)
        if source is not None and (source.title or source.app):
            try:
                self.update_meta(entry, title=source.title, app=source.app)
            except OSError as exc:  # the snip itself is safe; only its label is missing
                log.warning("Couldn't record where snip %s came from: %s", entry.id, exc)
        return entry

    def meta(self, entry: Entry) -> EntryMeta:
        """Never fails: a missing or damaged meta.json reads as defaults."""
        try:
            data = json.loads((entry.path / META).read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            return EntryMeta()
        return EntryMeta(**_normalized(data)) if isinstance(data, dict) else EntryMeta()

    def update_meta(self, entry: Entry, **changes) -> EntryMeta:
        known = {f.name for f in fields(EntryMeta)}
        unknown = set(changes) - known
        if unknown:
            raise ValueError(f"unknown snip details: {', '.join(sorted(unknown))}")
        with self._meta_lock:
            meta = replace(self.meta(entry), **_normalized(changes))
            data = {f.name: getattr(meta, f.name) for f in fields(EntryMeta)}
            data["tags"] = list(meta.tags)
            _write_text_atomic(json.dumps(data, ensure_ascii=False), entry.path / META)
        return meta

    def entries(self, limit: int | None = None) -> list[Entry]:
        if not self.root.exists():
            return []
        found = [Entry(p.name, p) for p in self.root.iterdir() if (p / ORIGINAL).exists()]
        found.sort(key=lambda e: e.id, reverse=True)
        return found[:limit] if limit else found

    def get(self, entry_id: str) -> Entry | None:
        path = self.root / entry_id
        return Entry(entry_id, path) if (path / ORIGINAL).exists() else None

    def load_image(self, entry: Entry) -> QImage:
        image = QImage(str(entry.original_path))
        if image.isNull():
            raise OSError(f"Could not read {entry.original_path}")
        return image

    def load_document(self, entry: Entry) -> dict:
        """Returns {} for a snip never edited. Raises DocumentUnreadable if the file is there but bad."""
        doc_path = entry.path / DOCUMENT
        if not doc_path.exists():
            return {}
        try:
            data = json.loads(doc_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DocumentUnreadable(f"Couldn't read {doc_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise DocumentUnreadable(f"{doc_path} isn't a SnipForge document")
        return data

    def save(self, entry: Entry, document: dict, rendered: QImage) -> None:
        _write_text_atomic(json.dumps(document), entry.path / DOCUMENT)
        _save_image_atomic(rendered, entry.path / RENDERED)
        preview = rendered
        if max(rendered.width(), rendered.height()) > THUMB_MAX:
            preview = rendered.scaled(THUMB_MAX, THUMB_MAX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        _save_image_atomic(preview, entry.path / PREVIEW)

    def move_to_trash(self, entry: Entry) -> bool:
        """Sends the snip folder to the Recycle Bin (recoverable)."""
        result = QFile.moveToTrash(str(entry.path))
        return bool(result[0] if isinstance(result, tuple) else result)
