"""snipforge/video/screen_frames.py: numbered frames at a steady rate from a (fake) screen.

What's checked is what the frame thread guarantees on any machine, however slow: each frame's
number is the one due when it was made, give or take the LATE_FRAMES it may run behind before
skipping ahead. How many frames a busy machine (a CI runner) manages in a given time isn't, so the
tests run until what they need has happened (LIMIT is only a stuck test's way out)."""
import threading
import time

import pytest
from PySide6.QtGui import QColor, QImage

from snipforge.video import screen_frames
from snipforge.video.clock import Clock
from snipforge.video.screen_frames import LATE_FRAMES, ScreenFrames

AREA = (0, 0, 64, 48)
FPS = 50
LIMIT = 10.0  # seconds a test may run before giving up on what it waits for


class FakeScreen:
    """Changes to a new shade every `every` seconds; grab() waits like DXGI does."""

    def __init__(self, every: float | None = None) -> None:
        self.every, self.shade, self.closed, self.reopens = every, 0, False, 0
        self.last = time.perf_counter()

    def grab(self, timeout_ms: int):
        time.sleep(timeout_ms / 1000)
        if self.every and time.perf_counter() - self.last >= self.every:
            self.last = time.perf_counter()
            self.shade += 1
            image = QImage(64, 48, QImage.Format.Format_RGB32)
            image.fill(QColor(self.shade, 0, 0))
            return bytearray(image.constBits()), image.bytesPerLine()
        return None

    def close(self) -> None:
        self.closed = True


class NoPointer:
    def __init__(self) -> None:
        self.drawn = 0

    def draw(self, frame, now) -> None:
        self.drawn += 1


class Timing(NoPointer):
    """Writes into each frame (its text, not its pixels) the recording's time when it was made."""

    def __init__(self, clock) -> None:
        super().__init__()
        self.clock = clock

    def draw(self, frame, now) -> None:
        super().draw(frame, now)
        media = self.clock.media_time(now)
        if media is not None:  # None only if a pause came in between the frame thread's look and this
            frame.setText("media", repr(media))


def first_image(_area) -> QImage:
    image = QImage(64, 48, QImage.Format.Format_RGB32)
    image.fill(QColor(0, 200, 0))
    return image


def assert_numbered_in_order(frames, numbers):
    """Numbered from 0, in order, none twice. A number can only be missing because its frame was
    late (skipped ahead, which a busy machine does) or the queue was full, and both are counted."""
    assert numbers[0] == 0 and numbers == sorted(set(numbers))
    missing = numbers[-1] + 1 - len(numbers)
    assert missing <= frames.late_skipped + frames.queue_dropped


def assert_numbered_by_the_recordings_time(taken):
    """Each frame's number is the one due when it was made: not before its time, and no more than
    LATE_FRAMES whole frames after it (beyond that the frame thread skips ahead). Not frame 0, made
    at the thread's first wake however late, and never skipped past."""
    timed = [(index, float(image.text("media"))) for index, image in taken if index and image.text("media")]
    assert timed
    for index, media in timed:
        assert index <= media * FPS + 1e-6 < index + LATE_FRAMES + 1, (index, media * FPS)


def run(until, limit=LIMIT, clock=None, screen=None, overlay=None, during=None, **kwargs):
    """Make frames until until(taken) says enough (or `limit` seconds pass); `during(clock, now)`
    is called as it goes."""
    clock = clock or Clock()
    clock.begin(time.perf_counter())
    screen = screen or FakeScreen()
    frames = ScreenFrames(AREA, FPS, clock, overlay or NoPointer(), open_screen=lambda area: screen,
                          first_image=first_image, **kwargs)
    frames.start()
    taken = []
    deadline = time.perf_counter() + limit
    while not until(taken) and time.perf_counter() < deadline:
        if during:
            during(clock, time.perf_counter())
        taken += frames.take()
        time.sleep(0.01)
    frames.stop()
    return frames, taken + frames.take(), screen


def at_least(count):
    return lambda taken: len(taken) >= count


def test_frames_come_numbered_at_the_rate_asked_for():
    clock = Clock()
    frames, taken, screen = run(lambda taken: taken and taken[-1][0] >= 40, clock=clock, overlay=Timing(clock))

    numbers = [index for index, _image in taken]
    assert_numbered_in_order(frames, numbers)
    assert_numbered_by_the_recordings_time(taken)  # 50 a second of recording
    assert numbers[-1] >= 40 and screen.closed and frames.error is None


def test_a_still_screen_repeats_its_first_image():
    _frames, taken, _screen = run(at_least(3))

    assert {image.pixelColor(5, 5).name() for _i, image in taken} == {"#00c800"}


def test_frames_show_the_newest_screen():
    def three_changes_seen(taken):
        return bool(taken) and taken[-1][1].pixelColor(5, 5).red() >= 3

    _frames, taken, _screen = run(three_changes_seen, screen=FakeScreen(every=0.1))

    reds = [image.pixelColor(5, 5).red() for _i, image in taken]
    assert reds == sorted(reds) and reds[-1] >= 3  # the screen changed, and frames followed


def test_the_pointer_is_drawn_into_every_frame():
    overlay = NoPointer()
    frames, taken, _screen = run(at_least(5), overlay=overlay)

    assert overlay.drawn == len(taken) + frames.queue_dropped and len(taken) >= 5


def test_a_pointer_that_cant_be_drawn_is_left_out_and_frames_go_on():
    class Broken(NoPointer):
        def draw(self, frame, now):
            super().draw(frame, now)
            raise ValueError("no pointer shape")

    overlay = Broken()
    frames, taken, _screen = run(at_least(6), overlay=overlay)

    assert len(taken) > 5 and frames.error is None
    assert str(frames.overlay_error) == "no pointer shape" and overlay.drawn == 1  # tried once


def test_a_pause_leaves_no_gap_in_the_numbers():
    state = {}

    def pause_for_a_while(clock, now):
        media = clock.media_time(now)
        if "paused" not in state and media and media > 0.3:
            clock.pause(now)
            state["paused"] = now
        elif "paused" in state and "resumed" not in state and now - state["paused"] > 0.5:
            clock.resume(now)
            state["resumed"] = now

    def well_after_the_pause(taken):
        return "resumed" in state and bool(taken) and taken[-1][0] >= 0.3 * FPS + 10

    clock = Clock()
    frames, taken, _screen = run(well_after_the_pause, clock=clock, overlay=Timing(clock), during=pause_for_a_while)

    numbers = [index for index, _image in taken]
    assert "resumed" in state, "the pause never ended"
    assert_numbered_in_order(frames, numbers)
    # Numbered by the recording's time, which stood still for the 0.5 s pause: had the pause made
    # frames, those after it would be 25 numbers ahead of their time.
    assert_numbered_by_the_recordings_time(taken)
    assert numbers[-1] >= 0.3 * FPS + 10


def test_falling_behind_skips_ahead_and_counts_it():
    class SlowOnce(NoPointer):
        def draw(self, frame, now):
            super().draw(frame, now)
            if self.drawn == 5:
                time.sleep(0.3)  # 15 frames' worth

    overlay = SlowOnce()
    frames, taken, _screen = run(at_least(8), overlay=overlay)

    numbers = [index for index, _image in taken]
    assert frames.late_skipped >= 10
    assert numbers == sorted(numbers) and len(set(numbers)) == len(numbers)
    assert numbers[5] - numbers[4] > 3  # the jump after the stall


def test_a_full_queue_drops_new_frames_instead_of_waiting():
    clock = Clock()
    clock.begin(time.perf_counter())
    frames = ScreenFrames(AREA, FPS, clock, NoPointer(), open_screen=lambda area: FakeScreen(),
                          first_image=first_image, queue_size=3)
    frames.start()
    deadline = time.perf_counter() + LIMIT
    while frames.queue_dropped + frames.late_skipped <= 10 and time.perf_counter() < deadline:
        time.sleep(0.01)  # nobody takes them
    frames.stop()

    assert len(frames.take()) == 3 and frames.made == 3
    assert frames.queue_dropped + frames.late_skipped > 10  # it kept going rather than waiting


def test_a_screen_that_cant_be_opened_is_reported_at_once():
    def refuse(area):
        raise OSError("no duplication here")

    frames = ScreenFrames(AREA, FPS, Clock(), NoPointer(), open_screen=refuse, first_image=first_image)
    started = time.perf_counter()

    with pytest.raises(OSError, match="no duplication"):
        frames.start()
    assert time.perf_counter() - started < screen_frames.START_TIMEOUT / 2  # said at once, not timed out


def test_stopping_ends_the_thread_and_closes_the_screen():
    screen = FakeScreen()
    frames, _taken, _ = run(at_least(2), screen=screen)

    assert screen.closed
    assert not any(t.name == "screen-frames" and t.is_alive() for t in threading.enumerate())


def test_a_screen_that_answers_slowly_doesnt_hold_up_the_frames():
    # A software graphics driver (GitHub's runners) or a busy GPU: each wait for the screen took ~1 s,
    # and a frame thread that also waited on the screen made 3 frames in 3 s.
    class Slow(FakeScreen):
        def grab(self, timeout_ms):
            time.sleep(1.0)  # far past its timeout
            return None

    started = time.perf_counter()
    frames, taken, _screen = run(at_least(25), limit=6.0, screen=Slow())

    numbers = [index for index, _image in taken]
    assert numbers[0] == 0 and numbers == sorted(set(numbers))
    # Made on time anyway, from the screen's last image: 25 in about half a second, more on a busy
    # machine. Waiting on the screen, they'd come about one a second: 6 by the limit.
    assert len(numbers) >= 25, (len(numbers), time.perf_counter() - started)
