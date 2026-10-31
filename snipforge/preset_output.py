"""What happens to a snip taken with a capture preset (snipforge/presets.py): its edges (a border,
torn sides) and where it goes: the editor, the clipboard or a folder.

The edges go into the snip's document in the Library, exactly as if they'd been added in the
editor, so the Library shows them and opening the snip later keeps them editable.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtGui import QImage

from . import prefs
from .editor.edges import Edges
from .presets import Preset


def with_edges(image: QImage, preset: Preset) -> tuple[dict | None, QImage]:
    """The snip's document with the preset's edges, and the snip drawn with them; (None, `image`)
    for a preset without edges."""
    if not preset.has_edges:
        return None, image
    from .editor.canvas import Canvas

    canvas = Canvas(image, {})
    try:
        canvas.set_edges(Edges(border=preset.border, torn=preset.torn))
        return canvas.to_document(), canvas.render_image(commit=False, use_draft=False)
    finally:
        canvas.deleteLater()


def folder(preset: Preset) -> Path:
    """Where a Save to a folder preset saves: its own folder, or the Save folder in Settings."""
    own = Path(preset.folder) if preset.folder else None
    return own if own is not None and own.is_absolute() else prefs.export_dir()


def new_path(preset: Preset, now: dt.datetime | None = None) -> Path:
    """A new file in the preset's folder, named like the editor's saves, in the format Settings says."""
    target = folder(preset)
    target.mkdir(parents=True, exist_ok=True)
    stem = (now or dt.datetime.now()).strftime("snip-%Y%m%d-%H%M%S")
    path = target / f"{stem}.{prefs.export_format()}"
    n = 2
    while path.exists():
        path = target / f"{stem}-{n}.{prefs.export_format()}"
        n += 1
    return path
