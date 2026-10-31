"""Saving an edited video (snipforge/video/render.py): cut out the middle, a title card, a caption,
for real, through Qt's encoder and FFmpeg, on a numbered test clip (each frame shows its number)."""
import array
import sys
import time

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from snipforge.video import clip, ffmpeg, frame_times, frames, render
from snipforge.video.edit import Title, VideoEdit
from snipforge.video.encoder import CHANNELS, SAMPLE_RATE
from snipforge.video.timeline import TrimRange

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not ffmpeg.available(),
                                reason="Qt's FFmpeg isn't usable here (ffmpeg.unavailable_reason() says why)")
FPS = 30


@pytest.fixture(scope="module")
def recording(tmp_path_factory, qapp):
    return clip.make_test_clip(tmp_path_factory.mktemp("rec") / "Recording 1.mp4", seconds=2.0, size=(320, 240),
                               fps=FPS)


@pytest.fixture
def edit(recording):
    return VideoEdit.whole(frame_times.read(recording))


def save(recording, edit, dst, cancel_after: int | None = None):
    """Run a Render to its end; (what it said, the progress it reported)."""
    job = render.Render(recording, dst, edit)
    said, progress = [], []
    loop = QEventLoop()
    job.finished.connect(lambda path: (said.append(("finished", path)), loop.quit()))
    job.failed.connect(lambda message: (said.append(("failed", message)), loop.quit()))
    job.cancelled.connect(lambda: (said.append(("cancelled", None)), loop.quit()))
    job.progress.connect(progress.append)
    if cancel_after is not None:
        job.progress.connect(lambda _f: len(progress) >= cancel_after and job.cancel())
    QTimer.singleShot(60_000, loop.quit)
    job.start()
    if not said:
        loop.exec()
    job.wait(5)
    return said, progress


def numbers(path) -> list[int]:
    return [clip.frame_number(image) for _t, image in frames.read(path)]


def test_cutting_out_the_middle_saves_the_rest_joined_up(recording, edit, tmp_path):
    cut = edit.with_cut(20, 40)
    dst = tmp_path / "Recording 1 (edited).mp4"
    said, progress = save(recording, cut, dst)
    assert said == [("finished", dst)] and progress[-1] == 1.0 and progress == sorted(progress)
    assert numbers(dst) == list(range(20)) + list(range(40, 60))
    info = ffmpeg.probe(dst)
    assert info.first("video").codec == "h264" and info.first("audio").codec == "aac"
    assert info.first("audio").duration == pytest.approx(40 / FPS, abs=0.1)  # the sound is cut the same way
    assert not list(tmp_path.glob("*.partial.*"))


def test_a_title_card_and_a_caption_are_part_of_the_saved_video(recording, edit, tmp_path):
    edited = edit.with_range(TrimRange(edit.frames, 30, 60)).with_title(Title("Quarterly report", 1.0)) \
        .with_caption("Click Save to keep it")
    dst = tmp_path / "titled.mp4"
    said, _ = save(recording, edited, dst)
    assert said == [("finished", dst)]
    shown = list(frames.read(dst))
    cards = [image for t, image in shown if t < 1.0 - 1e-6]
    assert len(cards) == pytest.approx(FPS, abs=1)  # a second of title card
    assert cards[0].pixelColor(4, 4).lightness() < 40  # near-black
    video = [image for t, image in shown if t >= 1.0 - 1e-6]
    assert [clip.frame_number(image) for image in video] == list(range(30, 60))
    bottom = video[0].pixelColor(160, 236)
    assert bottom.lightness() < 60  # the caption's dark band along the bottom
    assert ffmpeg.probe(dst).first("audio").duration == pytest.approx(2.0, abs=0.1)  # silence under the card


def test_cancelling_a_save_leaves_nothing(recording, edit, tmp_path):
    dst = tmp_path / "cancelled.mp4"
    said, _ = save(recording, edit.with_cut(10, 20), dst, cancel_after=3)
    assert said == [("cancelled", None)]
    deadline = time.monotonic() + 5
    while list(tmp_path.glob("*.partial.*")) and time.monotonic() < deadline:  # Windows lets go a moment later
        QEventLoop().processEvents()
        time.sleep(0.05)
    assert not dst.exists() and not list(tmp_path.glob("*.partial.*"))


def test_a_gif_of_an_edit_has_the_cut_closed_up(recording, edit, tmp_path):
    from PIL import Image

    dst = tmp_path / "edited.gif"
    render.export_edited_gif(recording, dst, edit.with_cut(15, 45), fps=10, width=160)
    with Image.open(dst) as gif:
        count, size = gif.n_frames, gif.size
        total = 0
        for n in range(count):
            gif.seek(n)
            total += gif.info.get("duration", 0)
    assert size == (160, 120)
    assert total == pytest.approx(1000, abs=110)  # 30 of 60 frames kept: a second


class FakeEncoder:
    def __init__(self):
        self.sound: list[tuple[int, list[float]]] = []

    def add_audio(self, first, samples):
        self.sound.append((first, list(samples)))


def test_the_kept_sound_goes_in_one_unbroken_stream(edit, qapp, tmp_path):
    """Silence under the title card, the kept parts back to back, nothing doubled."""
    edited = edit.with_title(Title("Hi", 1.0)).with_cut(15, 45)  # keeps 0-0.5 s and 1.5-2 s
    fake = FakeEncoder()
    job = render.Render.__new__(render.Render)  # just its sound placing, with no file or encoder
    job._sound_plan, job._samples_out, job._encoder = render.sound_plan(edited), 0, fake
    second = array.array("f", [0.5] * SAMPLE_RATE * CHANNELS)  # a second of sound, from 0 s
    job.add_sound(0.0, second)
    job.add_sound(1.0, array.array("f", [0.25] * SAMPLE_RATE * CHANNELS))
    firsts = [first for first, _samples in fake.sound]
    lengths = [len(samples) // CHANNELS for _first, samples in fake.sound]
    assert firsts[0] == 0 and all(a + n == b for a, n, b in zip(firsts, lengths, firsts[1:], strict=False))
    stream = [s for _first, samples in fake.sound for s in samples[::CHANNELS]]
    assert set(stream[:SAMPLE_RATE]) == {0.0}  # the title card
    assert set(stream[SAMPLE_RATE:SAMPLE_RATE * 3 // 2]) == {0.5}  # 0 to 0.5 s
    assert set(stream[SAMPLE_RATE * 3 // 2:]) == {0.25} and len(stream) == SAMPLE_RATE * 2  # 1.5 to 2 s


def test_the_frame_rate_comes_from_the_usual_gap(qapp):
    from snipforge.video.timeline import FrameTimes

    starts = [n / 30 for n in range(60) if n % 7]  # a busy recording: every seventh frame lost
    assert render.frame_rate(FrameTimes(tuple(starts), 2.0)) == 30
