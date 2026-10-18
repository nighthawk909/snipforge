"""Exporting part of a video as an animated GIF that loops forever.

Frames come from frames.read (Qt's FFmpeg, exact) and are written with Pillow, one at a time, so a
long export never holds more than two frames in memory:

- each frame gets its own palette of up to 256 colours (a fast octree), and Floyd-Steinberg
  dithering mixes those colours so gradients don't band; a part with 256 colours or fewer keeps
  them exactly;
- only the rectangle that changed since the last frame is stored, drawn over what's already
  shown, and a frame that changes nothing just makes the previous one last longer. Screen
  recordings mostly stand still, so this is what keeps their GIFs small.

Frames keep an even pace: each is placed on the 1/fps step frames.read picked it for (the step at
or before it), so a 30 fps clip at 12 fps plays 8/100, 9/100, 8/100 s... rather than 10, 7, 10.
GIF frame times are in hundredths of a second, rounded on the clip's own clock so the rounding never
adds up; a frame shown for less than 2/100 s (browsers would stretch it to 1/10 s) is folded into
the next one.
"""
from __future__ import annotations

import contextlib
import logging
import math
import os
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import BinaryIO

from PIL import GifImagePlugin, Image, ImageChops
from PySide6.QtGui import QImage

from . import ffmpeg, frames

log = logging.getLogger(__name__)

MAX_FPS = 50  # GIF times are hundredths of a second, and browsers slow down anything under 2
MIN_DELAY = 2  # hundredths of a second
MAX_DELAY = 65535  # hundredths: a GIF delay is 16 bits, so a frame shows for at most 655.35 s at a time
CHANGE_THRESHOLD = 6  # out of 255: smaller differences are compression noise, lost in the dithering anyway
DISPOSAL_KEEP = 1  # each frame stays on screen and the next one draws over it
EPSILON = 1e-6
_CHANGED = [0] * (CHANGE_THRESHOLD + 1) + [255] * (255 - CHANGE_THRESHOLD)


class Cancelled(Exception):
    """The export was cancelled; nothing was written."""


def export_gif(src: str | os.PathLike, dst: str | os.PathLike, start: float = 0.0, end: float | None = None,
               fps: float = 12, width: int = 960, progress: Callable[[float], None] | None = None,
               cancel: Callable[[], bool] | None = None) -> Path:
    """Write `src` from `start` up to `end` (seconds; None for the rest) as a GIF at `fps`, at most
    `width` pixels wide, to `dst`, which is written whole or not at all. `progress` hears 0 to 1;
    when `cancel` returns True the export stops and raises Cancelled. Raises ValueError for a
    request that can't work and FFmpegError if the video can't be read."""
    src, dst = _check_names(src, dst)
    if not 0 < fps <= MAX_FPS:
        raise ValueError(f"fps must be more than 0 and at most {MAX_FPS}, not {fps}")
    if width < 1:
        raise ValueError(f"width must be at least 1, not {width}")
    if start < 0 or (end is not None and end <= start):
        raise ValueError(f"Nothing to export between {start} and {end} s")
    length = _length(src)
    if length is not None and start >= length:
        raise ValueError(f"{src.name} is only {length:.2f} s long")
    stop = end if length is None else length if end is None else min(end, length)  # when the last frame ends
    handle, partial_name = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.stem}.", suffix=".partial.gif")
    partial = Path(partial_name)
    try:
        with os.fdopen(handle, "wb") as out, \
                contextlib.closing(frames.read(src, fps=fps, width=width, start=start, end=end)) as images:
            write_gif(out, images, start, stop, fps, progress, cancel)  # closing: FFmpeg lets go of src at once
        os.replace(partial, dst)
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError as exc:  # an antivirus or sync client holding it; never hide the real error
            log.warning("Couldn't remove %s: %s", partial, exc)
    return dst


def _check_names(src, dst) -> tuple[Path, Path]:
    if any("://" in os.fspath(p) for p in (src, dst)):
        raise ValueError("Only local files can be exported")
    src, dst = Path(src), Path(dst)
    if ":" in dst.name:  # an NTFS alternate data stream
        raise ValueError(f"{dst.name} isn't a plain file name")
    if dst.exists() and src.exists() and os.path.samefile(src, dst):
        raise ValueError("A GIF can't overwrite the video it comes from")
    return src, dst


def _length(src: Path) -> float | None:
    """How long the video is, counted from its first frame as frames.read counts."""
    info = ffmpeg.probe(src)
    video = info.first("video")
    if video is None:
        raise ffmpeg.FFmpegError(f"{src.name} has no video")
    origin = max(0.0, video.start or 0.0)
    if video.duration is not None:
        return (video.start or 0.0) + video.duration - origin
    return None if info.duration is None else info.duration - origin


def write_gif(out: BinaryIO, images: Iterable[tuple[float, QImage]], start: float = 0.0, stop: float | None = None,
              fps: float = 12, progress: Callable[[float], None] | None = None,
              cancel: Callable[[], bool] | None = None) -> int:
    """Write `images`, (seconds, image) in time order, to `out` as a looping GIF that starts at
    `start` and ends at `stop` (None: the last frame lasts 1/fps). Returns the frames written."""
    writer = _Writer(out, start)
    last = start
    for seconds, image in images:
        if cancel is not None and cancel():
            raise Cancelled
        step = math.floor((seconds - start) * fps + EPSILON)  # the step frames.read picked it for
        writer.add(_pil(image), start + max(0, step) / fps)
        last = seconds
        if progress is not None and stop is not None and stop > start:
            progress(min(1.0, max(0.0, (seconds - start) / (stop - start))))
    if cancel is not None and cancel():
        raise Cancelled
    written = writer.finish(stop if stop is not None and stop > last else last + 1 / fps)
    if progress is not None:
        progress(1.0)
    return written


def _pil(image: QImage) -> Image.Image:
    """A QImage as a Pillow RGB image (frames.read gives ARGB32, which is BGRA in memory)."""
    if image.format() not in (QImage.Format.Format_ARGB32, QImage.Format.Format_RGB32):
        image = image.convertToFormat(QImage.Format.Format_RGB32)
    return Image.frombytes("RGB", (image.width(), image.height()), bytes(image.constBits()), "raw", "BGRX",
                           image.bytesPerLine())


def _changed_box(shown: Image.Image, image: Image.Image) -> tuple[int, int, int, int] | None:
    """The rectangle where `image` differs from what the GIF shows by more than noise."""
    return ImageChops.difference(shown, image).point(_CHANGED * 3).getbbox()


def _union(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _to_palette(rgb: Image.Image) -> Image.Image:
    """`rgb` with a palette of its own: its exact colours when it has 256 or fewer, otherwise an
    octree palette with Floyd-Steinberg dithering. (Pillow only dithers when given a palette, so
    the palette is made first and then applied.)"""
    colours = rgb.getcolors(256)
    if colours:
        palette = Image.new("P", (1, 1))
        palette.putpalette([channel for _count, colour in colours for channel in colour])
    else:
        palette = rgb.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
    return rgb.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG)


class _Writer:
    """Writes frames as they come. Each frame waits until the next one differs (so its delay is
    known), and only the box that changed is stored, cut from `shown`: the picture the GIF shows
    once the waiting frame is drawn."""

    def __init__(self, out: BinaryIO, start: float) -> None:
        self.out = out
        self.start = start
        self.shown: Image.Image | None = None
        self.box: tuple[int, int, int, int] | None = None  # what the waiting frame draws
        self.begins = 0  # when the waiting frame starts, in hundredths
        self.written = 0

    def add(self, image: Image.Image, seconds: float) -> None:
        at = max(0, round((seconds - self.start) * 100))
        if self.shown is None:
            self.shown, self.box = image.copy(), (0, 0, *image.size)
            return
        changed = _changed_box(self.shown, image)
        if changed is None:
            return  # nothing to see: the waiting frame just lasts longer
        if at - self.begins >= MIN_DELAY:
            self._write(at)
            self.box, self.begins = changed, at
        else:
            self.box = _union(self.box, changed)  # too brief to show on its own: fold it into the next
        self.shown.paste(image.crop(changed), changed[:2])

    def finish(self, stop: float) -> int:
        if self.shown is None:
            raise ValueError("There are no frames to write")
        self._write(max(self.begins + MIN_DELAY, round((stop - self.start) * 100)))
        self.out.write(b";")  # the GIF trailer
        return self.written

    def _write(self, until: int) -> None:
        """Write the waiting frame, shown until `until`. A wait longer than a GIF delay holds carries
        on in one-pixel frames that redraw a pixel the frame just drew, so nothing changes."""
        frame = _to_palette(self.shown.crop(self.box))
        delays = _delays(until - self.begins)
        self._emit(frame, delays[0])
        if len(delays) > 1:
            pixel = _to_palette(frame.crop((0, 0, 1, 1)).convert("RGB"))
            for delay in delays[1:]:
                self._emit(pixel, delay)

    def _emit(self, frame: Image.Image, delay: int) -> None:
        params = {"duration": delay * 10, "disposal": DISPOSAL_KEEP}
        if self.written == 0:  # the first frame's palette is the GIF's global one
            header, _ = GifImagePlugin.getheader(frame, info={"loop": 0})
            self.out.write(b"".join(header))
        else:
            params["include_color_table"] = True
        self.out.write(b"".join(GifImagePlugin.getdata(frame, offset=self.box[:2], **params)))
        self.written += 1


def _delays(total: int) -> list[int]:
    """`total` hundredths as GIF delays: none over MAX_DELAY, none under MIN_DELAY."""
    delays = []
    while total > MAX_DELAY:
        part = MAX_DELAY if total - MAX_DELAY >= MIN_DELAY else MAX_DELAY - MIN_DELAY
        delays.append(part)
        total -= part
    return [*delays, total]
