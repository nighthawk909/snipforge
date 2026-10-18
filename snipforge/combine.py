"""Combine images: several snips in one, side by side, stacked, in a grid, or as numbered steps with
captions. The result is a new snip that opens in the editor; the steps' numbers and captions are
markup in it (a step badge and a text box each), so they stay editable there.

Pictures are never made larger than they are (that would blur them): "the same size" means each is
shrunk to the smallest one's height (side by side) or width (stacked, a grid, steps).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

LAYOUTS = {"row": "Side by side", "column": "Stacked", "grid": "Grid", "steps": "Numbered steps"}
BACKGROUNDS = {"white": "#ffffff", "light": "#f3f4f6", "dark": "#1f2937", "none": ""}  # "": transparent
GAPS = {0: "None", 12: "Small", 32: "Large"}
MAX_IMAGES = 20
MAX_SIDE = 16000  # the result's longest side, at most (bigger is shrunk to fit)
STEP_ROW = 56  # height of a step's number and caption, above its picture
STEP_BADGE_WIDTH = 4  # the step tool's style width: an 34 px badge
CAPTION_SIZE = 20


@dataclass(frozen=True)
class Options:
    layout: str = "row"
    gap: int = 12
    background: str = "white"  # a key of BACKGROUNDS
    columns: int = 2  # in a grid
    same_size: bool = True
    captions: tuple[str, ...] = field(default_factory=tuple)  # steps: one per picture, "" for none


@dataclass(frozen=True)
class Combined:
    image: QImage
    items: tuple[dict, ...]  # the editor's markup for it (step numbers and captions), as saved states

    def document(self) -> dict:
        """What the editor opens it with (snipforge.editor.canvas.Canvas.load_document)."""
        return {"version": 1, "crop": [0, 0, self.image.width(), self.image.height()], "items": list(self.items)}

    def finished(self) -> tuple[dict, QImage]:
        """Its document as the editor saves it, and the image with its markup drawn (for the
        Library's preview and search), as if it had been made in the editor."""
        from .editor.canvas import Canvas

        canvas = Canvas(self.image, {})
        try:
            canvas.load_document(self.document())
            return canvas.to_document(), canvas.render_image(commit=False, use_draft=False)
        finally:
            canvas.deleteLater()


def _scaled(image: QImage, width: int | None = None, height: int | None = None) -> QImage:
    """`image` shrunk (never enlarged) to `width` or `height`, keeping its shape."""
    if width is not None and image.width() > width:
        return image.scaledToWidth(width, Qt.SmoothTransformation)
    if height is not None and image.height() > height:
        return image.scaledToHeight(height, Qt.SmoothTransformation)
    return image


def _place(images: list[QImage], options: Options) -> tuple[int, int, list[tuple[QImage, QPointF]], list[dict]]:
    """(width, height, each picture and its top-left, markup) for the layout."""
    gap, margin = options.gap, options.gap
    placed: list[tuple[QImage, QPointF]] = []
    items: list[dict] = []
    if options.layout == "row":
        if options.same_size:
            images = [_scaled(i, height=min(j.height() for j in images)) for i in images]
        height = max(i.height() for i in images)
        x = margin
        for image in images:
            placed.append((image, QPointF(x, margin + (height - image.height()) / 2)))
            x += image.width() + gap
        return x - gap + margin, height + 2 * margin, placed, items
    if options.same_size:
        images = [_scaled(i, width=min(j.width() for j in images)) for i in images]
    if options.layout == "column":
        width = max(i.width() for i in images)
        y = margin
        for image in images:
            placed.append((image, QPointF(margin + (width - image.width()) / 2, y)))
            y += image.height() + gap
        return width + 2 * margin, y - gap + margin, placed, items
    if options.layout == "grid":
        columns = max(1, min(options.columns, len(images)))
        cell_w = max(i.width() for i in images)
        rows = [images[n:n + columns] for n in range(0, len(images), columns)]
        y = margin
        for row in rows:
            row_h = max(i.height() for i in row)
            for c, image in enumerate(row):
                x = margin + c * (cell_w + gap) + (cell_w - image.width()) / 2
                placed.append((image, QPointF(x, y + (row_h - image.height()) / 2)))
            y += row_h + gap
        return 2 * margin + columns * cell_w + (columns - 1) * gap, y - gap + margin, placed, items
    # numbered steps: a number and a caption above each picture
    from .editor.style import DEFAULT_TOOL_STYLES, Style

    width = max(i.width() for i in images)
    ink = "#ffffff" if options.background == "dark" else "#111827"
    badge = DEFAULT_TOOL_STYLES.get("step", Style(width=STEP_BADGE_WIDTH)).to_dict()
    caption = Style(color=ink, fill=False, shadow=False, font_size=CAPTION_SIZE).to_dict()
    y = margin
    for n, image in enumerate(images):
        items.append({"kind": "step", "pos": [margin + STEP_ROW / 2 - 6, y + STEP_ROW / 2], "z": 1 + 2 * n,
                      "style": badge, "number": n + 1})
        text = options.captions[n].strip() if n < len(options.captions) else ""
        if text:
            items.append({"kind": "text", "pos": [margin + STEP_ROW, y + STEP_ROW / 2 - CAPTION_SIZE], "z": 2 + 2 * n,
                          "style": caption, "text": text})
        placed.append((image, QPointF(margin, y + STEP_ROW)))
        y += STEP_ROW + image.height() + gap
    return width + 2 * margin, y - gap + margin, placed, items


def measure(images: list[QImage], options: Options = Options()) -> tuple[int, int]:
    """The combined image's size, without drawing it."""
    width, height, _placed, _items = _place(list(images), options)
    shrink = min(1.0, MAX_SIDE / max(width, height))
    return max(1, int(round(width * shrink))), max(1, int(round(height * shrink)))


def combine(images: list[QImage], options: Options = Options()) -> Combined:
    """The pictures in one image, laid out as `options` says. Raises ValueError for fewer than two
    pictures, more than MAX_IMAGES, or a layout that doesn't exist."""
    if options.layout not in LAYOUTS:
        raise ValueError(f"unknown layout {options.layout!r}")
    if not 2 <= len(images) <= MAX_IMAGES:
        raise ValueError(f"combine 2 to {MAX_IMAGES} pictures, not {len(images)}")
    if any(i.isNull() for i in images):
        raise ValueError("a picture couldn't be read")
    width, height, placed, items = _place(list(images), options)
    shrink = min(1.0, MAX_SIDE / max(width, height))
    if shrink < 1.0:  # far too big for one image: everything scaled down together
        width, height = int(width * shrink), int(height * shrink)
        placed = [(i.scaled(max(1, int(i.width() * shrink)), max(1, int(i.height() * shrink)),
                            Qt.IgnoreAspectRatio, Qt.SmoothTransformation), QPointF(p.x() * shrink, p.y() * shrink))
                  for i, p in placed]
        items = [dict(item, pos=[item["pos"][0] * shrink, item["pos"][1] * shrink]) for item in items]
    out = QImage(max(1, int(round(width))), max(1, int(round(height))), QImage.Format_ARGB32_Premultiplied)
    background = BACKGROUNDS.get(options.background, "#ffffff")
    out.fill(QColor(background) if background else Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    for image, at in placed:
        p.drawImage(QRectF(at, QRectF(image.rect()).size()), image)
    p.end()
    return Combined(out, tuple(items))
