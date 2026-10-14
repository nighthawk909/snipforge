"""Snip library: every capture is kept as an original image plus editable annotations.

Layout on disk (one folder per snip):
    Pictures/SnipForge/Library/<id>/original.png   untouched capture
    Pictures/SnipForge/Library/<id>/document.json  annotations + crop (re-editable)
    Pictures/SnipForge/Library/<id>/rendered.png   full-size flattened result (copy/export)
    Pictures/SnipForge/Library/<id>/preview.png    small thumbnail
All writes go to a temp file first and are renamed into place, so a crash never
leaves a half-written file behind.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QFile, Qt
from PySide6.QtGui import QImage

log = logging.getLogger(__name__)

DEFAULT_ROOT = Path.home() / "Pictures" / "SnipForge" / "Library"
ORIGINAL, DOCUMENT, RENDERED, PREVIEW = "original.png", "document.json", "rendered.png", "preview.png"
THUMB_MAX = 480


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


def _save_image_atomic(image: QImage, target: Path) -> None:
    tmp = target.with_name(target.name + ".tmp")
    if not image.save(str(tmp), "PNG"):
        tmp.unlink(missing_ok=True)
        raise OSError(f"Could not write {target}")
    os.replace(tmp, target)


class Library:
    def __init__(self, root: Path = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    def create(self, image: QImage) -> Entry:
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:19]
        path = self.root / stamp
        suffix = 1
        while path.exists():
            path = self.root / f"{stamp}-{suffix}"
            suffix += 1
        path.mkdir()
        _save_image_atomic(image, path / ORIGINAL)
        return Entry(path.name, path)

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
        tmp = entry.path / (DOCUMENT + ".tmp")
        tmp.write_text(json.dumps(document), encoding="utf-8")
        os.replace(tmp, entry.path / DOCUMENT)
        _save_image_atomic(rendered, entry.path / RENDERED)
        preview = rendered
        if max(rendered.width(), rendered.height()) > THUMB_MAX:
            preview = rendered.scaled(THUMB_MAX, THUMB_MAX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        _save_image_atomic(preview, entry.path / PREVIEW)

    def move_to_trash(self, entry: Entry) -> bool:
        """Sends the snip folder to the Recycle Bin (recoverable)."""
        result = QFile.moveToTrash(str(entry.path))
        return bool(result[0] if isinstance(result, tuple) else result)
