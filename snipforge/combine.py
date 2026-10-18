"""Combine images: several snips in one, side by side, stacked, in a grid, or as numbered steps with
captions. The result is a new snip that opens in the editor; the steps' numbers and captions are
markup in it (a step badge and a text box each), so they stay editable there.

The layout is planned from the pictures' sizes alone (plan()), so measuring it and previewing it
never touch the full-size pictures; the pictures are scaled once, as they're drawn. They're never
made larger than they are (that would blur them): "the same size" means each is shrunk to the
smallest one's height (side by side) or width (stacked, a grid, steps). A result too big to save and
open again (more than MAX_SIDE on a side, or MAX_PIXELS in all) has its pictures shrunk to fit,
while the step numbers and captions keep a readable size.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter

LAYOUTS = {"row": "Side by side", "column": "Stacked", "grid": "Grid", "steps": "Numbered steps"}
BACKGROUNDS = {"white": "#ffffff", "light": "#f3f4f6", "dark": "#1f2937", "none": ""}  # "": transparent
GAPS = {0: "None", 12: "Small", 32: "Large"}
MAX_IMAGES = 20
MAX_SIDE = 16000  # the result's longest side, at most
MAX_PIXELS = 48_000_000  # and its area: Qt won't read back an image of more than 256 MB (4 bytes a pixel)
STEP_ROW = 56  # height of a step's number and caption, above its picture (on a picture 960 px wide)
STEP_BADGE_WIDTH = 4.0  # the step tool's style width: a 34 px badge
CAPTION_SIZE = 20
MARKUP_WIDTH = 960  # pictures wider than this get bigger numbers and captions, up to MARKUP_MOST times
MARKUP_MOST = 3.0
CAPTION_MIN_WIDTH = 480  # a step's content is at least this wide when it has a caption


@dataclass(frozen=True)
class Options:
    layout: str = "row"
    gap: int = 12
    background: str = "white"  # a key of BACKGROUNDS
    columns: int = 2  # in a grid
    same_size: bool = True
    captions: tuple[str, ...] = field(default_factory=tuple)  # steps: one per picture, "" for none


@dataclass(frozen=True)
class Plan:
    width: int
    height: int
    rects: tuple[QRectF, ...]  # where each picture goes, in the result's pixels
    items: tuple[dict, ...]  # the editor's markup (step numbers and captions), as saved states


@dataclass(frozen=True)
class Combined:
    image: QImage
    items: tuple[dict, ...]

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


def _size(item) -> tuple[float, float]:
    if isinstance(item, QImage | QSize):
        return float(item.width()), float(item.height())
    return float(item[0]), float(item[1])


def _same_size(sizes: list[tuple[float, float]], options: Options) -> list[tuple[float, float]]:
    if not options.same_size:
        return sizes
    if options.layout == "row":
        height = min(h for _w, h in sizes)
        return [(max(1, round(w * height / h)), height) if h > height else (w, h) for w, h in sizes]
    width = min(w for w, _h in sizes)
    return [(width, max(1, round(h * width / w))) if w > width else (w, h) for w, h in sizes]


def _caption_width(text: str, size: int) -> float:
    """How wide a caption's text box is (as the editor's text tool draws it)."""
    from PySide6.QtGui import QFont, QFontMetricsF

    from .editor.text_items import _font

    return QFontMetricsF(_font(size, QFont.Bold)).horizontalAdvance(text) + 2 * 8 + 4  # its margins


def _layout(sizes: list[tuple[float, float]], options: Options, scale: float) -> Plan:
    """The layout with every picture scaled by `scale` (markup, gaps and margins as they are), on
    whole pixels so pictures stay sharp."""
    sizes = [(max(1, round(w * scale)), max(1, round(h * scale))) for w, h in sizes]
    gap = margin = float(options.gap)
    rects: list[QRectF] = []
    if options.layout == "row":
        height = max(h for _w, h in sizes)
        x = margin
        for w, h in sizes:
            rects.append(QRectF(x, margin + (height - h) // 2, w, h))
            x += w + gap
        return Plan(math.ceil(x - gap + margin), math.ceil(height + 2 * margin), tuple(rects), ())
    if options.layout == "column":
        width = max(w for w, _h in sizes)
        y = margin
        for w, h in sizes:
            rects.append(QRectF(margin + (width - w) // 2, y, w, h))
            y += h + gap
        return Plan(math.ceil(width + 2 * margin), math.ceil(y - gap + margin), tuple(rects), ())
    if options.layout == "grid":
        columns = max(1, min(options.columns, len(sizes)))
        cell_w = max(w for w, _h in sizes)
        y = margin
        for start in range(0, len(sizes), columns):
            row = sizes[start:start + columns]
            row_h = max(h for _w, h in row)
            for c, (w, h) in enumerate(row):
                rects.append(QRectF(margin + c * (cell_w + gap) + (cell_w - w) // 2, y + (row_h - h) // 2, w, h))
            y += row_h + gap
        return Plan(math.ceil(2 * margin + columns * cell_w + (columns - 1) * gap), math.ceil(y - gap + margin),
                    tuple(rects), ())
    return _steps(sizes, options, margin, gap)


def _steps(sizes: list[tuple[float, float]], options: Options, margin: float, gap: float) -> Plan:
    """Numbered steps: a number and a caption above each picture, sized to be read on it."""
    from .editor.style import Style

    content = max(w for w, _h in sizes)
    k = min(MARKUP_MOST, max(1.0, content / MARKUP_WIDTH))  # bigger markup on bigger pictures
    row, font_size = round(STEP_ROW * k), round(CAPTION_SIZE * k)
    captions = [(options.captions[n].strip() if n < len(options.captions) else "") for n in range(len(sizes))]
    for text in filter(None, captions):  # a caption is never cut off by a narrow picture
        content = max(content, CAPTION_MIN_WIDTH, row + _caption_width(text, font_size))
    ink = "#ffffff" if options.background == "dark" else "#111827"
    badge = Style(width=STEP_BADGE_WIDTH * k).to_dict()
    caption = Style(color=ink, fill=False, shadow=False, font_size=font_size).to_dict()
    rects: list[QRectF] = []
    items: list[dict] = []
    y = margin
    for n, ((w, h), text) in enumerate(zip(sizes, captions, strict=True)):
        items.append({"kind": "step", "pos": [margin + row / 2 - 6 * k, y + row / 2], "z": 1 + 2 * n,
                      "style": badge, "number": n + 1})
        if text:
            items.append({"kind": "text", "pos": [margin + row, y + row / 2 - font_size], "z": 2 + 2 * n,
                          "style": caption, "text": text})
        rects.append(QRectF(margin, y + row, w, h))
        y += row + h + gap
    return Plan(math.ceil(content + 2 * margin), math.ceil(y - gap + margin), tuple(rects), tuple(items))


def plan(sizes: list, options: Options = Options()) -> Plan:
    """Where everything goes, from the pictures' sizes (QImage, QSize or (width, height)). Raises
    ValueError for fewer than two pictures, more than MAX_IMAGES, an empty one or an unknown layout."""
    if options.layout not in LAYOUTS:
        raise ValueError(f"unknown layout {options.layout!r}")
    if not 2 <= len(sizes) <= MAX_IMAGES:
        raise ValueError(f"combine 2 to {MAX_IMAGES} pictures, not {len(sizes)}")
    measured = [_size(s) for s in sizes]
    if any(w <= 0 or h <= 0 for w, h in measured):
        raise ValueError("a picture couldn't be read")
    measured = _same_size(measured, options)
    scale = 1.0
    for _ in range(8):  # markup and gaps don't shrink, so a few rounds settle it
        laid = _layout(measured, options, scale)
        fits = min(MAX_SIDE / max(laid.width, laid.height), math.sqrt(MAX_PIXELS / (laid.width * laid.height)))
        if fits >= 1.0:
            return laid
        scale *= fits * 0.995
    return laid


def measure(sizes: list, options: Options = Options()) -> tuple[int, int]:
    """The combined image's size."""
    laid = plan(sizes, options)
    return laid.width, laid.height


def _draw(images: list[QImage], laid: Plan, options: Options, scale: float = 1.0) -> QImage:
    out = QImage(max(1, math.ceil(laid.width * scale)), max(1, math.ceil(laid.height * scale)),
                 QImage.Format_ARGB32_Premultiplied)
    background = BACKGROUNDS.get(options.background, "#ffffff")
    out.fill(QColor(background) if background else Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.scale(scale, scale)
    for image, rect in zip(images, laid.rects, strict=True):
        p.drawImage(rect, image)  # scaled once, here
    p.end()
    return out


def combine(images: list[QImage], options: Options = Options()) -> Combined:
    """The pictures in one image, laid out as `options` says (ValueError as plan() says)."""
    if any(i.isNull() for i in images):
        raise ValueError("a picture couldn't be read")
    laid = plan(images, options)
    return Combined(_draw(images, laid, options), laid.items)


def preview(images: list[QImage], sizes: list, options: Options, longest: int) -> QImage:
    """What combine() makes, at most `longest` pixels on a side, from small copies (`images`) of
    pictures whose real sizes are `sizes`: the same layout, numbers and captions, only smaller."""
    laid = plan(sizes, options)
    scale = min(1.0, longest / max(laid.width, laid.height))
    out = _draw(images, laid, options, scale)
    if laid.items:
        from PySide6.QtWidgets import QGraphicsScene

        from .editor.factory import create_item

        scene = QGraphicsScene()
        for state in laid.items:
            scene.addItem(create_item(state, QImage(), QRectF(0, 0, laid.width, laid.height)))
        p = QPainter(out)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        scene.render(p, QRectF(out.rect()), QRectF(0, 0, laid.width, laid.height))
        p.end()
    return out
