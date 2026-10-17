"""Scrolling capture's stitching: frames of a scrolling page become one tall image."""
import random

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter

from snipforge.stitch import Stitcher, find_shift, row_signatures

WIDTH = 120


def page(height: int, seed: int = 7) -> QImage:
    """A long page: every row a different pattern, so any slice of it has one right place."""
    rng = random.Random(seed)
    image = QImage(WIDTH, height, QImage.Format_RGB32)
    for y in range(height):
        for x in range(0, WIDTH, 8):
            color = QColor(rng.randrange(256), rng.randrange(256), rng.randrange(256))
            for dx in range(8):
                image.setPixelColor(x + dx, y, color)
    return image


def view(full: QImage, top: int, height: int, header: int = 0, footer: int = 0, scrollbar: bool = False) -> QImage:
    """What the window shows when scrolled to `top`: a slice of the page, optionally under a sticky
    header and over a sticky footer, with a scrollbar thumb that moves as it scrolls."""
    frame = full.copy(QRect(0, top, WIDTH, height))
    p = QPainter(frame)
    if header:
        p.fillRect(0, 0, WIDTH, header, QColor("#203040"))
    if footer:
        p.fillRect(0, height - footer, WIDTH, footer, QColor("#405060"))
    if scrollbar:
        p.fillRect(WIDTH - 10, 0, 10, height, QColor("#eeeeee"))
        thumb = header + round((height - header - footer - 30) * top / max(1, full.height() - height))
        p.fillRect(WIDTH - 10, thumb, 10, 30, QColor("#888888"))
    p.end()
    return frame


def same(a: QImage, b: QImage) -> bool:
    return a.size() == b.size() and a.convertToFormat(QImage.Format_RGB32) == b.convertToFormat(QImage.Format_RGB32)


# --- finding how far it scrolled ----------------------------------------------------------------

def test_finds_the_scroll_distance():
    full = page(600)
    prev, cur = (row_signatures(view(full, top, 200)) for top in (0, 57))
    assert find_shift(prev, cur) == 57


def test_no_movement_and_unrelated_frames_are_not_shifts():
    full = page(600)
    frame = row_signatures(view(full, 100, 200))
    assert find_shift(frame, frame) is None  # nothing moved: the end of the page
    assert find_shift(row_signatures(view(full, 0, 200)), row_signatures(view(page(600, seed=99), 0, 200))) is None


def test_signatures_can_skip_the_scrollbar():
    image = QImage(50, 3, QImage.Format_RGB32)
    image.fill(QColor("#ffffff"))
    other = image.copy()
    other.setPixelColor(48, 1, QColor("#000000"))  # only the scrollbar column differs
    assert row_signatures(image, right_margin=4) == row_signatures(other, right_margin=4)
    assert row_signatures(image) != row_signatures(other)


# --- stitching -------------------------------------------------------------------------------------

@pytest.mark.parametrize("step", [40, 97, 150])
def test_stitches_a_plain_page(step):
    full = page(700)
    stitcher = Stitcher()
    top = 0
    while True:
        moved = stitcher.add(view(full, top, 200))
        if top >= 500:
            break
        assert moved or top == 0
        top = min(top + step, 500)
    assert same(stitcher.result(), full)


def test_end_of_the_page_is_noticed():
    full = page(400)
    stitcher = Stitcher()
    assert stitcher.add(view(full, 0, 200)) is True
    assert stitcher.add(view(full, 120, 200)) is True
    assert stitcher.add(view(full, 200, 200)) is True  # the last bit of the page
    assert stitcher.add(view(full, 200, 200)) is False  # scrolled, but nothing moved
    assert stitcher.result().height() == 400


def test_sticky_header_footer_and_scrollbar_appear_once():
    full = page(900)
    stitcher = Stitcher()
    for top in range(0, 601, 120):
        stitcher.add(view(full, top, 300, header=30, footer=20, scrollbar=True))
    result = stitcher.result()
    # header once at the top, the whole page between (under where the header and footer were), footer once
    assert result.height() == 30 + (900 - 50) + 20
    assert result.pixelColor(5, 10).name() == "#203040"
    assert result.pixelColor(5, result.height() - 5).name() == "#405060"
    body = result.copy(QRect(0, 30, WIDTH - 10, result.height() - 50))
    expected = full.copy(QRect(0, 30, WIDTH - 10, 900 - 50))
    assert same(body, expected)


def test_a_frame_that_matches_nothing_stops_the_stitch():
    stitcher = Stitcher()
    stitcher.add(view(page(600), 0, 200))
    assert stitcher.add(view(page(600, seed=3), 0, 200)) is False
    assert stitcher.result().height() == 200


def test_frames_must_be_the_same_size():
    stitcher = Stitcher()
    stitcher.add(view(page(600), 0, 200))
    with pytest.raises(ValueError):
        stitcher.add(view(page(600), 0, 150))


def test_tall_results_stop_growing_at_the_limit():
    full = page(2000)
    stitcher = Stitcher(max_height=900)
    top = 0
    while stitcher.add(view(full, top, 300)) and top < 1700:
        top += 250
    assert stitcher.result().height() <= 900 and stitcher.full


def test_nothing_added_means_no_result():
    with pytest.raises(ValueError):
        Stitcher().result()


def text_page(height: int) -> QImage:
    """Like a real document: mostly white, with "lines of text" (varied dark bars) every 18 rows."""
    rng = random.Random(11)
    image = QImage(WIDTH, height, QImage.Format_RGB32)
    image.fill(QColor("#ffffff"))
    p = QPainter(image)
    for y in range(6, height - 12, 18):
        x = 4
        while x < WIDTH - 30:
            w = rng.randrange(4, 18)
            p.fillRect(x, y, w, 9, QColor(rng.randrange(60), rng.randrange(60), rng.randrange(60)))
            x += w + rng.randrange(2, 6)
    p.end()
    return image


def test_stitches_a_mostly_blank_document():
    full = text_page(1500)
    stitcher = Stitcher()
    for top in range(0, 1101, 133):
        stitcher.add(view(full, top, 400))
    stitcher.add(view(full, 1100, 400))
    assert same(stitcher.result(), full)


def test_large_frames_stitch_quickly():
    import time
    full = text_page(3000)
    frames = [view(full, top, 1000) for top in (0, 300, 600, 900, 1200)]
    started = time.perf_counter()
    stitcher = Stitcher()
    for frame in frames:
        stitcher.add(frame)
    assert time.perf_counter() - started < 1.5  # per scroll it has to keep up with the page
    assert same(stitcher.result(), full.copy(QRect(0, 0, WIDTH, 2200)))
