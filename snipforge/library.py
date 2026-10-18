"""Snip library: every capture is kept as an original image plus editable annotations.

Layout on disk (one folder per snip):
    Pictures/SnipForge/Library/<id>/original.png   untouched capture
    Pictures/SnipForge/Library/<id>/document.json  annotations + crop (re-editable)
    Pictures/SnipForge/Library/<id>/rendered.png   full-size flattened result (copy/export)
    Pictures/SnipForge/Library/<id>/preview.png    small thumbnail
    Pictures/SnipForge/Library/<id>/meta.json      source app and window title, favorite, tags, and the
                                                   words recognized in the snip (for search)

A screen recording has a folder of its own too (snipforge/video/recordings.py):
    Pictures/SnipForge/Library/<id>/recording.mp4   the video
    Pictures/SnipForge/Library/<id>/recording.json  its length and size, written once it's finished
    Pictures/SnipForge/Library/<id>/preview.png     a frame from it, made the first time it's shown
    Pictures/SnipForge/Library/<id>/meta.json       favorite and tags, as for a snip
It's listed only once recording.json is there, so never while it's still being recorded. It has no
original.png, so older SnipForge versions sharing the folder (OneDrive, say) simply don't list it.
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

from .privacy import is_private_window

log = logging.getLogger(__name__)

DEFAULT_ROOT = Path.home() / "Pictures" / "SnipForge" / "Library"
ORIGINAL, DOCUMENT, RENDERED, PREVIEW = "original.png", "document.json", "rendered.png", "preview.png"
META = "meta.json"
RECORDING, RECORDING_DETAILS = "recording.mp4", "recording.json"
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


def scrub(text: str) -> str:
    """Replace lone surrogates (from truncated window titles or hand-edited files) so the text can
    always be written as UTF-8."""
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def clean_tags(tags) -> tuple[str, ...]:
    """Lowercase, trimmed, unique, at most MAX_TAGS of at most MAX_TAG characters."""
    if not isinstance(tags, (list, tuple)):
        return ()
    found: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        tag = scrub(" ".join(tag.split()).lower()[:MAX_TAG])
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
            out[key] = scrub(value[:_LIMITS[key]]) if key in _LIMITS else value
    return out


@dataclass(frozen=True)
class Snapshot:
    """One snip as found by Library.scan(): what's on disk, without opening any file."""
    entry: Entry
    meta_stamp: int | None      # meta.json modification time (ns), None if there's none
    edited_at: float | None     # document.json modification time, None if never edited
    has_preview: bool


class DocumentUnreadable(OSError):
    """document.json exists but couldn't be read; callers must not overwrite it."""


@dataclass(frozen=True)
class RecordingDetails:
    duration: float | None = None  # seconds
    size: tuple[int, int] | None = None  # pixels


@dataclass(frozen=True)
class Entry:
    id: str
    path: Path
    kind: str = "snip"  # or "recording"

    @property
    def is_recording(self) -> bool:
        return self.kind == "recording"

    @property
    def recording_path(self) -> Path:
        return self.path / RECORDING

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
        """The card's picture. A recording's is made from the video the first time it's shown, so
        until then this names a file that isn't there yet."""
        if self.is_recording:
            return self.path / PREVIEW
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


def _mtime(item, ns: bool):
    """A scandir entry's modification time (cached by Windows in the listing), or None."""
    if item is None:
        return None
    try:
        info = item.stat()
    except OSError:
        return None
    return info.st_mtime_ns if ns else info.st_mtime


def _preview(image: QImage) -> QImage:
    if max(image.width(), image.height()) <= THUMB_MAX:
        return image
    return image.scaled(THUMB_MAX, THUMB_MAX, Qt.KeepAspectRatio, Qt.SmoothTransformation)


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


def write_recording_details(path: Path, duration: float, size: tuple[int, int], *,
                            only_if_missing: bool = False) -> bool:
    """Mark a Library recording finished, with its length and size: the Library lists it from then
    on. Written whole or not at all; nothing is written beside a file not named as a recording.
    Returns whether recording.json is there now."""
    path = Path(path)
    if path.name != RECORDING:
        return False
    target = path.with_name(RECORDING_DETAILS)
    if only_if_missing and target.exists():
        return True
    try:
        data = {"duration": round(float(duration), 3), "width": int(size[0]), "height": int(size[1]), "version": 1}
        _write_text_atomic(json.dumps(data, allow_nan=False), target)  # ValueError for an endless length
    except (OSError, TypeError, ValueError) as exc:  # the video is safe; it just isn't listed yet
        log.warning("Couldn't mark %s finished for the Library: %s", path, exc)
        return False
    return True


def _kind(files: dict) -> str | None:
    """What a folder holds, from its file names: a snip, a finished recording, or neither."""
    if ORIGINAL in files:
        return "snip"
    if RECORDING in files and RECORDING_DETAILS in files:
        return "recording"
    return None


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
        try:  # a small preview now keeps the Library from decoding full screenshots later
            _save_image_atomic(_preview(image), path / PREVIEW)
        except OSError as exc:
            log.info("Couldn't write a preview for snip %s: %s", entry.id, exc)
        if source is not None and (source.title or source.app):
            try:
                self.update_meta(entry, title=source.title, app=source.app)
            except OSError as exc:  # the snip itself is safe; only its label is missing
                log.warning("Couldn't record where snip %s came from: %s", entry.id, exc)
        return entry

    def meta(self, entry: Entry) -> EntryMeta:
        """Never fails: a missing, damaged or momentarily locked meta.json reads as defaults."""
        try:
            return self.read_meta(entry)
        except OSError:
            return EntryMeta()

    def read_meta(self, entry: Entry) -> EntryMeta:
        """Like meta(), but a file that exists and can't be read right now (antivirus, the search
        indexer) raises OSError instead of pretending to be empty, so nothing overwrites it with
        defaults. A damaged file is moved aside to meta.json.bad."""
        path = entry.path / META
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return EntryMeta()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            log.warning("Snip %s had an unreadable meta.json; it was kept as meta.json.bad", entry.id)
            try:
                os.replace(path, path.with_name(META + ".bad"))
            except OSError:
                pass
            return EntryMeta()
        if not isinstance(data, dict):
            return EntryMeta()
        meta = EntryMeta(**_normalized(data))
        if meta.title and is_private_window(meta.title, meta.app):
            # Saved by an older version that didn't know this browser or language: never shown or
            # searched, and gone from the file the next time the snip's details are saved.
            meta = replace(meta, title="")
        return meta

    def update_meta(self, entry: Entry, **changes) -> EntryMeta:
        known = {f.name for f in fields(EntryMeta)}
        unknown = set(changes) - known
        if unknown:
            raise ValueError(f"unknown snip details: {', '.join(sorted(unknown))}")
        with self._meta_lock:
            meta = replace(self.read_meta(entry), **_normalized(changes))
            data = {f.name: getattr(meta, f.name) for f in fields(EntryMeta)}
            data["tags"] = list(meta.tags)
            _write_text_atomic(json.dumps(data, ensure_ascii=False), entry.path / META)
        return meta

    def scan(self) -> list[Snapshot]:
        """Every snip, newest first, from one directory listing per snip (no file is opened)."""
        found: list[Snapshot] = []
        try:
            folders = [d for d in os.scandir(self.root) if d.is_dir()]
        except OSError:
            return []
        for folder in folders:
            try:
                files = {f.name: f for f in os.scandir(folder.path)}
            except OSError:
                continue
            kind = _kind(files)
            if kind is None:
                continue
            found.append(Snapshot(Entry(folder.name, Path(folder.path), kind), _mtime(files.get(META), ns=True),
                                  _mtime(files.get(DOCUMENT), ns=False), PREVIEW in files))
        found.sort(key=lambda s: s.entry.id, reverse=True)
        return found

    def entries(self, limit: int | None = None) -> list[Entry]:
        """Snips only (not recordings), newest first: what callers that open images expect."""
        if not self.root.exists():
            return []
        found = [Entry(p.name, p) for p in self.root.iterdir() if (p / ORIGINAL).exists()]
        found.sort(key=lambda e: e.id, reverse=True)
        return found[:limit] if limit else found

    def get(self, entry_id: str) -> Entry | None:
        """A snip or a finished recording; None if there's neither."""
        path = self.root / entry_id
        if (path / ORIGINAL).exists():
            return Entry(entry_id, path)
        if (path / RECORDING).exists() and (path / RECORDING_DETAILS).exists():
            return Entry(entry_id, path, "recording")
        return None

    def recording_details(self, entry: Entry) -> RecordingDetails:
        """A recording's length and size; unknown (None) if recording.json can't be read."""
        try:
            data = json.loads((entry.path / RECORDING_DETAILS).read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            return RecordingDetails()
        if not isinstance(data, dict):
            return RecordingDetails()
        duration, width, height = data.get("duration"), data.get("width"), data.get("height")
        duration = float(duration) if isinstance(duration, (int, float)) and 0 <= duration < 10**7 else None
        size = (width, height) if all(isinstance(v, int) and 0 < v <= 100_000 for v in (width, height)) else None
        return RecordingDetails(duration, size)

    def exports(self, entry: Entry) -> list[str]:
        """Files the video editor saved beside a recording (trimmed copies, GIFs): they go with it to
        the Recycle Bin, so the person is asked first. Empty for a snip."""
        if not entry.is_recording:
            return []
        own = {RECORDING, RECORDING_DETAILS, PREVIEW, META}
        try:
            names = [f.name for f in os.scandir(entry.path) if f.is_file()]
        except OSError:
            return []
        return sorted(n for n in names if n not in own and not n.startswith(".") and not n.endswith(".tmp"))

    def load_image(self, entry: Entry) -> QImage:
        if entry.is_recording:
            raise OSError(f"{entry.id} is a recording, not an image")
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
        _save_image_atomic(_preview(rendered), entry.path / PREVIEW)
        try:  # words that were blurred or cropped away must stop matching: read the result again later
            self.update_meta(entry, text="", indexed=False)
        except (OSError, ValueError) as exc:
            log.info("Couldn't reset the search text of snip %s: %s", entry.id, exc)

    def move_to_trash(self, entry: Entry) -> bool:
        """Sends the snip folder to the Recycle Bin (recoverable)."""
        result = QFile.moveToTrash(str(entry.path))
        return bool(result[0] if isinstance(result, tuple) else result)
