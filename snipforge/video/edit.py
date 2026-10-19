"""What the video editor saves: the trim, parts cut out of the middle, a title card before the video
and a caption along its bottom, as one value (every change returns a new one, so undo is a list of
them). A plain trim is copied without re-encoding (trim.py); anything more is drawn and encoded
again (render.py), from the plans here:

- output_plan: each frame of the saved video, when it's shown and which frame of the recording it
  is (or the title card). Cuts are closed up: the frame after a cut follows the one before it.
- sound_plan: which stretches of the recording's sound go where, the same way. Under the title card
  there's silence.

Cuts are frame spans (first, stop) like the trim, inside it, sorted, never touching each other and
never taking every frame.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from ..fonts import ui_font
from .timeline import FrameTimes, TrimRange

MIN_TITLE, MAX_TITLE = 1.0, 10.0  # seconds a title card is shown
MAX_TEXT = 200  # characters in a title or caption
TITLE_BACKGROUND, TITLE_TEXT = QColor("#111318"), QColor("#ffffff")
CAPTION_BAND, CAPTION_TEXT = QColor(0, 0, 0, 170), QColor("#ffffff")
CAPTION_LINES = 3


@dataclass(frozen=True)
class Title:
    """A card shown before the video: `text`, white on near-black, for `seconds`."""

    text: str
    seconds: float = 3.0

    def __post_init__(self) -> None:
        text = " ".join(self.text.split())
        if not text:
            raise ValueError("A title needs some words")
        if len(text) > MAX_TEXT:
            raise ValueError(f"A title can be up to {MAX_TEXT} characters")
        if not MIN_TITLE <= self.seconds <= MAX_TITLE:
            raise ValueError(f"A title is shown for {MIN_TITLE:g} to {MAX_TITLE:g} seconds")
        object.__setattr__(self, "text", text)


@dataclass(frozen=True)
class Planned:
    """One frame of the saved video: shown at `at` seconds; the recording's frame `source`, or the
    title card (None)."""

    at: float
    source: int | None


@dataclass(frozen=True)
class VideoEdit:
    range: TrimRange
    cuts: tuple[tuple[int, int], ...] = ()
    title: Title | None = None
    caption: str = ""

    @classmethod
    def whole(cls, frames: FrameTimes) -> VideoEdit:
        return cls(TrimRange.whole(frames))

    @property
    def frames(self) -> FrameTimes:
        return self.range.frames

    # ------------------------------------------------------------------ what's kept
    def kept_spans(self) -> tuple[tuple[int, int], ...]:
        """The frame spans (first, stop) the saved video keeps, in order."""
        spans, at = [], self.range.first
        for first, stop in self.cuts:
            if first > at:
                spans.append((at, first))
            at = max(at, stop)
        if at < self.range.stop:
            spans.append((at, self.range.stop))
        return tuple(spans)

    def span_seconds(self, span: tuple[int, int]) -> float:
        return self.frames.boundary(span[1]) - self.frames.boundary(span[0])

    @property
    def seconds(self) -> float:
        """How long the saved video is, title card and all."""
        return sum(self.span_seconds(s) for s in self.kept_spans()) + (self.title.seconds if self.title else 0.0)

    @property
    def needs_render(self) -> bool:
        """More than a trim: the video has to be drawn and encoded again."""
        return bool(self.cuts or self.title or self.caption)

    @property
    def is_whole(self) -> bool:
        """Nothing changed: there's nothing to save."""
        return self.range.is_whole and not self.needs_render

    # ------------------------------------------------------------------ changes
    def with_range(self, trim_range: TrimRange) -> VideoEdit:
        """A new trim; cuts outside it go, and cuts that would leave nothing go too."""
        cuts = tuple(c for c in ((max(a, trim_range.first), min(b, trim_range.stop)) for a, b in self.cuts)
                     if c[0] < c[1])
        changed = replace(self, range=trim_range, cuts=cuts)
        return changed if changed.kept_spans() else replace(changed, cuts=())

    def with_cut(self, first: int, stop: int) -> VideoEdit:
        """Cut frames `first` up to `stop` out (inside the trim; joined to cuts it meets). Raises
        ValueError if nothing would be left."""
        first, stop = max(first, self.range.first), min(stop, self.range.stop)
        if first >= stop:
            return self
        merged = []
        for a, b in sorted((*self.cuts, (first, stop))):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        changed = replace(self, cuts=tuple(merged))
        if not changed.kept_spans():
            raise ValueError("That would cut out everything that's left")
        return changed

    def cut_at(self, index: int) -> tuple[int, int] | None:
        return next((c for c in self.cuts if c[0] <= index < c[1]), None)

    def without_cut(self, cut: tuple[int, int]) -> VideoEdit:
        return replace(self, cuts=tuple(c for c in self.cuts if c != cut))

    def with_title(self, title: Title | None) -> VideoEdit:
        return replace(self, title=title)

    def with_caption(self, text: str) -> VideoEdit:
        text = " ".join(text.split())
        if len(text) > MAX_TEXT:
            raise ValueError(f"A caption can be up to {MAX_TEXT} characters")
        return replace(self, caption=text)


# ---------------------------------------------------------------------- plans
def output_plan(edit: VideoEdit, fps: float) -> tuple[Planned, ...]:
    """Every frame of the saved video, in order: the title card at `fps`, then the kept frames at
    their own times, with each cut closed up."""
    plan = []
    offset = 0.0
    if edit.title is not None:
        count = max(1, round(edit.title.seconds * fps))
        plan.extend(Planned(n / fps, None) for n in range(count))
        offset = edit.title.seconds
    for first, stop in edit.kept_spans():
        start = edit.frames.boundary(first)
        plan.extend(Planned(offset + edit.frames.boundary(i) - start, i) for i in range(first, stop))
        offset += edit.span_seconds((first, stop))
    return tuple(plan)


def sound_plan(edit: VideoEdit) -> tuple[tuple[float, float, float], ...]:
    """(where it goes, from, to) in seconds, for each stretch of the recording's sound that's kept."""
    plan, offset = [], edit.title.seconds if edit.title else 0.0
    for span in edit.kept_spans():
        start, end = edit.frames.boundary(span[0]), edit.frames.boundary(span[1])
        plan.append((offset, start, end))
        offset += end - start
    return tuple(plan)


# ---------------------------------------------------------------------- drawing
def _text_font(pixels: int, bold: bool) -> QFont:
    font = ui_font(10)
    font.setPixelSize(max(8, pixels))
    font.setWeight(QFont.DemiBold if bold else QFont.Normal)
    return font


def title_card(title: Title, size: QSize) -> QImage:
    """The title card, a frame of `size`: the words centered, white on near-black."""
    card = QImage(size, QImage.Format_RGB32)
    card.fill(TITLE_BACKGROUND)
    p = QPainter(card)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    p.setFont(_text_font(round(size.height() / 11), bold=True))
    p.setPen(TITLE_TEXT)
    w, h = size.width(), size.height()
    p.drawText(QRectF(w * 0.1, h * 0.1, w * 0.8, h * 0.8), Qt.AlignCenter | Qt.TextWordWrap, title.text)
    p.end()
    return card


def with_caption(image: QImage, text: str) -> QImage:
    """`image` with `text` along its bottom on a dark band (up to three lines); `image` itself when
    there's no caption."""
    if not text:
        return image
    out = image.convertToFormat(QImage.Format_RGB32) if image.format() != QImage.Format_RGB32 else image.copy()
    w, h = out.width(), out.height()
    p = QPainter(out)
    p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
    p.setFont(_text_font(round(h / 22), bold=False))
    pad = max(4.0, h / 60)
    room = QRectF(w * 0.05, 0, w * 0.9, h)
    needed = p.boundingRect(room, Qt.AlignHCenter | Qt.TextWordWrap, text)
    line = p.fontMetrics().lineSpacing()
    band_h = min(needed.height(), line * CAPTION_LINES) + 2 * pad
    band = QRectF(0, h - band_h, w, band_h)
    p.fillRect(band, CAPTION_BAND)
    p.setPen(CAPTION_TEXT)
    p.drawText(band.adjusted(w * 0.05, pad, -w * 0.05, -pad), Qt.AlignCenter | Qt.TextWordWrap, text)
    p.end()
    return out
