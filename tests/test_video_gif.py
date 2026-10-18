"""GIF export (snipforge/video/gif.py): frames read with Qt's FFmpeg, written with Pillow, a palette
per frame, dithered, and only the part of each frame that changed stored. Test clips carry each
frame's number as blocks, so "the right frame" is checked exactly; the GIF's own blocks are read
back with a small parser, so palettes, places and delays are checked as written."""
import io
import math
import random
import struct
import sys
from dataclasses import dataclass

import pytest
from PIL import Image
from PySide6.QtGui import QColor, QImage, QPainter

from snipforge.video import clip, ffmpeg, gif

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not ffmpeg.available(),
                                reason="Qt's FFmpeg isn't usable here (ffmpeg.unavailable_reason() says why)")
FPS = 30


@dataclass
class Frame:
    x: int
    y: int
    width: int
    height: int
    own_palette: bool
    delay: int  # hundredths of a second
    disposal: int


@dataclass
class Gif:
    width: int
    height: int
    loop: int | None
    frames: list[Frame]


def _sub_blocks(data: bytes, pos: int) -> tuple[list[bytes], int]:
    blocks = []
    while data[pos]:
        blocks.append(data[pos + 1:pos + 1 + data[pos]])
        pos += 1 + data[pos]
    return blocks, pos + 1


def parse(data: bytes) -> Gif:
    """The parts of a GIF these tests check, read straight from its blocks."""
    assert data[:6] == b"GIF89a"
    width, height, packed = struct.unpack("<HHB", data[6:11])
    pos = 13 + (3 * (2 << (packed & 7)) if packed & 0x80 else 0)
    loop, frames, control = None, [], {}
    while data[pos] != 0x3B:
        if data[pos] == 0x21:
            label = data[pos + 1]
            blocks, pos = _sub_blocks(data, pos + 2)
            if label == 0xF9:
                control = {"delay": struct.unpack("<H", blocks[0][1:3])[0], "disposal": blocks[0][0] >> 2 & 7}
            elif label == 0xFF and blocks[0] == b"NETSCAPE2.0":
                loop = struct.unpack("<H", blocks[1][1:3])[0]
        else:
            assert data[pos] == 0x2C, f"unexpected block {data[pos]:#x} at {pos}"
            x, y, w, h, flags = struct.unpack("<HHHHB", data[pos + 1:pos + 10])
            pos += 10 + (3 * (2 << (flags & 7)) if flags & 0x80 else 0)
            _, pos = _sub_blocks(data, pos + 1)  # after the LZW code size
            frames.append(Frame(x, y, w, h, bool(flags & 0x80), control.get("delay", 0), control.get("disposal", 0)))
            control = {}
    return Gif(width, height, loop, frames)


def shown_images(path) -> list[QImage]:
    """Every frame as a viewer shows it (Pillow puts the partial frames together)."""
    images = []
    with Image.open(path) as im:
        for n in range(im.n_frames):
            im.seek(n)
            rgb = im.convert("RGB")
            images.append(QImage(rgb.tobytes(), rgb.width, rgb.height, 3 * rgb.width,
                                 QImage.Format.Format_RGB888).copy())
    return images


@pytest.fixture(scope="module")
def two_seconds(tmp_path_factory, qapp):
    return clip.make_test_clip(tmp_path_factory.mktemp("src") / "two.mp4", seconds=2.0, size=(320, 240), fps=FPS)


def first_frame_at_or_after(seconds: float) -> int:
    return math.ceil(seconds * FPS - 1e-6)


def test_frames_come_at_the_chosen_rate_and_size(two_seconds, tmp_path):
    out = gif.export_gif(two_seconds, tmp_path / "out.gif", fps=12, width=160)

    written = parse(out.read_bytes())
    assert (written.width, written.height) == (160, 120)
    assert len(written.frames) == 24
    assert sum(f.delay for f in written.frames) == 200  # exactly the clip's 2 s, in hundredths
    assert {f.delay for f in written.frames} <= {8, 9}  # 1/12 s each, rounding spread out
    numbers = [clip.frame_number(image) for image in shown_images(out)]
    assert numbers == [first_frame_at_or_after(n / 12) for n in range(24)]


def test_the_gif_loops_forever(two_seconds, tmp_path):
    out = gif.export_gif(two_seconds, tmp_path / "out.gif", end=0.5, width=80)

    assert parse(out.read_bytes()).loop == 0


def test_only_the_chosen_part_is_exported(two_seconds, tmp_path):
    out = gif.export_gif(two_seconds, tmp_path / "part.gif", start=0.5, end=1.5, fps=10, width=160)

    written = parse(out.read_bytes())
    assert len(written.frames) == 10
    assert sum(f.delay for f in written.frames) == 100
    assert [clip.frame_number(image) for image in shown_images(out)] == list(range(15, 45, 3))


def test_every_frame_gets_its_own_palette(two_seconds, tmp_path):
    """The clip's colour changes every frame, so one palette for all would band; each frame after
    the first carries its own (the first uses the GIF's global one)."""
    written = parse(gif.export_gif(two_seconds, tmp_path / "out.gif", end=1.0, width=160).read_bytes())

    assert not written.frames[0].own_palette
    assert all(f.own_palette for f in written.frames[1:])
    assert all(f.disposal == 1 for f in written.frames)  # each frame stays, the next draws over it


def test_progress_runs_from_zero_to_one(two_seconds, tmp_path):
    seen = []

    gif.export_gif(two_seconds, tmp_path / "out.gif", width=80, progress=seen.append)

    assert len(seen) >= 24
    assert all(0.0 <= value <= 1.0 for value in seen)
    assert seen == sorted(seen)
    assert seen[-1] == 1.0


def test_cancelling_leaves_no_file(two_seconds, tmp_path):
    calls = []

    def cancel():
        calls.append(1)
        return len(calls) > 3

    with pytest.raises(gif.Cancelled):
        gif.export_gif(two_seconds, tmp_path / "out.gif", width=80, cancel=cancel)

    assert list(tmp_path.iterdir()) == []


def test_cancelling_keeps_a_gif_that_was_already_there(two_seconds, tmp_path):
    out = tmp_path / "out.gif"
    out.write_bytes(b"the gif before")

    with pytest.raises(gif.Cancelled):
        gif.export_gif(two_seconds, out, width=80, cancel=lambda: True)

    assert out.read_bytes() == b"the gif before"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out.gif"]


def test_a_failure_half_way_leaves_no_leftovers(two_seconds, tmp_path, monkeypatch):
    real = gif.frames.read

    def broken(*args, **kwargs):
        for n, item in enumerate(real(*args, **kwargs)):
            if n == 5:
                raise ffmpeg.FFmpegError("disk full")
            yield item

    monkeypatch.setattr(gif.frames, "read", broken)
    with pytest.raises(ffmpeg.FFmpegError, match="disk full"):
        gif.export_gif(two_seconds, tmp_path / "out.gif", width=80)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kwargs", [{"fps": 0}, {"fps": 51}, {"width": 0}, {"start": -1.0},
                                    {"start": 1.0, "end": 1.0}, {"start": 3.0}])
def test_impossible_requests_are_refused(two_seconds, tmp_path, kwargs):
    with pytest.raises(ValueError):
        gif.export_gif(two_seconds, tmp_path / "out.gif", **kwargs)
    assert list(tmp_path.iterdir()) == []


def test_only_plain_local_files_are_written(two_seconds, tmp_path):
    with pytest.raises(ValueError):
        gif.export_gif(two_seconds, "http://127.0.0.1:9/out.gif")
    with pytest.raises(ValueError):
        gif.export_gif(two_seconds, tmp_path / "out.gif:hidden")
    with pytest.raises(ValueError):
        gif.export_gif(two_seconds, two_seconds)  # never over the video itself


# ---------------------------------------------------------------- writing frames directly

def noise(seed: int, size=(320, 240)) -> QImage:
    data = random.Random(seed).randbytes(size[0] * size[1] * 4)
    return QImage(data, size[0], size[1], size[0] * 4, QImage.Format.Format_RGB32).copy()


def with_square(background: QImage, x: int) -> QImage:
    image = background.copy()
    painter = QPainter(image)
    painter.fillRect(x, 100, 16, 16, QColor("red"))
    painter.end()
    return image


def write(images: list[QImage], fps: float = 12) -> bytes:
    out = io.BytesIO()
    gif.write_gif(out, [(n / fps, image) for n, image in enumerate(images)], stop=len(images) / fps, fps=fps)
    return out.getvalue()


def test_only_the_part_that_changed_is_stored(qapp):
    background = noise(1)
    moving_square = write([with_square(background, 10 + 4 * n) for n in range(24)])
    all_changing = write([noise(n) for n in range(24)])

    assert len(moving_square) < len(all_changing) / 10
    later = parse(moving_square).frames[1:]
    assert all(f.width <= 20 and f.height <= 16 for f in later)  # just the square, old place and new
    assert [f.x for f in later] == [10 + 4 * (n - 1) for n in range(1, 24)]


def test_a_still_video_becomes_one_long_frame(qapp):
    written = parse(write([noise(7)] * 24))

    assert len(written.frames) == 1
    assert written.frames[0].delay == 200


def test_frames_shown_only_briefly_are_merged_not_rushed(qapp):
    """A recording that dropped frames can have two frames a few milliseconds apart; browsers
    stretch any delay under 2/100 s to 1/10 s, so such a frame is folded into the next one."""
    out = io.BytesIO()
    images = [with_square(noise(3), x) for x in (10, 40, 70)]
    gif.write_gif(out, [(0.0, images[0]), (0.5, images[1]), (0.505, images[2])], stop=1.0)

    written = parse(out.getvalue())
    assert [f.delay for f in written.frames] == [50, 50]
    assert all(f.delay >= 2 for f in written.frames)


def test_a_still_stretch_longer_than_a_gif_delay_holds_is_carried_on(qapp):
    """A GIF delay holds at most 655.35 s; a frame shown longer carries on in a one-pixel frame
    that redraws what's already there."""
    out = io.BytesIO()
    gif.write_gif(out, [(0.0, noise(1, (32, 24))), (700.0, noise(2, (32, 24)))], stop=701.0)

    written = parse(out.getvalue())
    assert [f.delay for f in written.frames] == [65535, 4465, 100]
    assert (written.frames[1].width, written.frames[1].height) == (1, 1)
    shown = shown_images(io.BytesIO(out.getvalue()))
    assert shown[0] == shown[1]


def test_the_last_frame_is_never_too_brief(qapp):
    out = io.BytesIO()
    gif.write_gif(out, [(0.0, noise(1)), (1 / 12, noise(2))], stop=1 / 12 + 0.005, fps=12)

    assert [f.delay for f in parse(out.getvalue()).frames] == [8, 2]


def test_dithering_keeps_the_colours_of_a_gradient(qapp):
    """More colours than a GIF holds: Floyd-Steinberg dithering mixes palette colours so that, seen
    from a little way off (averaged over 4x4 pixels), the picture keeps its colours."""
    image = QImage(256, 128, QImage.Format.Format_RGB32)
    for y in range(128):
        for x in range(256):
            image.setPixelColor(x, y, QColor(x, (x + 2 * y) // 3, 255 - x // 2))
    with Image.open(io.BytesIO(write([image]))) as im:
        shown = im.convert("RGB").reduce(4).tobytes()
    source = Image.frombytes("RGB", (256, 128), bytes(image.convertToFormat(QImage.Format.Format_RGB888).constBits()))
    expected = source.reduce(4).tobytes()

    error = sum(abs(a - b) for a, b in zip(shown, expected, strict=True)) / len(shown)
    assert error < 1.5  # about 1 when dithered; 3 with the same palette undithered (bands)


def test_a_frame_with_few_colours_keeps_them_exactly(qapp):
    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(QColor(12, 34, 56))
    painter = QPainter(image)
    painter.fillRect(0, 0, 32, 32, QColor(200, 100, 50))
    painter.end()

    with Image.open(io.BytesIO(write([image]))) as im:
        colours = sorted(c for _n, c in im.convert("RGB").getcolors())
    assert colours == [(12, 34, 56), (200, 100, 50)]


def test_nothing_to_write_is_an_error(qapp):
    with pytest.raises(ValueError):
        gif.write_gif(io.BytesIO(), [], stop=1.0)
