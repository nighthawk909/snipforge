"""Desktop tests: a whole recording with the real screen (DXGI), the real speakers' sound (WASAPI
loopback) and Qt's encoder. A test window flashes a box while a beep plays; the MP4 must have the
flashes and the beeps at the same times, and a window hidden from capture must not be in it.

Run with: QT_QPA_PLATFORM=windows python -m pytest -m desktop
"""
import array
import ctypes
import math

import pytest
from PySide6.QtCore import QEventLoop, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from snipforge.video import frames, wasapi
from snipforge.video.recorder import Recorder, RecordingError, Settings

pytestmark = pytest.mark.desktop

FLASHES = (1.0, 2.5)  # seconds into the recording
FLASH_SECONDS = 0.3
SECONDS = 3.5
WDA_EXCLUDEFROMCAPTURE = 0x11


@pytest.fixture(scope="module", autouse=True)
def real_platform(qapp):
    if QGuiApplication.platformName() == "offscreen":
        pytest.skip("desktop tests need a real display: set QT_QPA_PLATFORM=windows")


class Pattern(QWidget):
    """A dark window with a box in the middle that turns white while `flash` is on."""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.flash = False
        self.setGeometry(120, 120, 640, 360)

    def box(self) -> QRect:
        return QRect(self.width() // 2 - 80, self.height() // 2 - 60, 160, 120)

    def paintEvent(self, _event):  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 34, 48))
        painter.fillRect(self.box(), QColor("white") if self.flash else QColor("black"))
        painter.end()


class Beeper:
    """One output stream kept open (opening one per beep takes ~0.6 s), a 1 kHz beep swapped in."""

    def __init__(self):
        from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

        fmt = QAudioFormat()
        fmt.setSampleRate(48000)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self.sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt)
        self.sink.setBufferSize(48000 * 2 * 40 // 1000)
        self.tone = array.array("h", (int(9000 * math.sin(2 * math.pi * 1000 * i / 48000))
                                      for i in range(int(48000 * FLASH_SECONDS)))).tobytes()
        self.pending = b""
        self.out = self.sink.start()
        self.timer = QTimer()
        self.timer.timeout.connect(self.feed)
        self.timer.start(5)

    def beep(self):
        self.pending = self.tone
        self.feed()

    def feed(self):
        free = self.sink.bytesFree() // 2 * 2
        chunk, self.pending = self.pending[:free], self.pending[free:]
        if free:
            self.out.write(chunk + bytes(free - len(chunk)))

    def stop(self):
        self.timer.stop()
        self.sink.stop()


def physical(widget) -> tuple[int, int, int, int]:
    """The widget's area in physical desktop pixels (Qt keeps each screen's top-left physical)."""
    screen = widget.screen()
    ratio, origin = screen.devicePixelRatio(), screen.geometry().topLeft()
    g = widget.geometry()
    return (origin.x() + round((g.x() - origin.x()) * ratio), origin.y() + round((g.y() - origin.y()) * ratio),
            round(g.width() * ratio) // 2 * 2, round(g.height() * ratio) // 2 * 2)


def run_recording(qapp, tmp_path, *, sound: bool):
    pattern = Pattern()
    pattern.show()
    hidden = QWidget(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    hidden.setStyleSheet("background: #ff00ff")
    hidden.setGeometry(140, 140, 160, 60)  # inside the recorded area
    hidden.show()
    qapp.processEvents()
    ctypes.windll.user32.SetWindowDisplayAffinity(int(hidden.winId()), WDA_EXCLUDEFROMCAPTURE)
    beeper = Beeper() if sound else None
    recorder = Recorder(Settings(physical(pattern), tmp_path / "recording.mp4", microphone=False, system_audio=sound))
    loop, outcome = QEventLoop(), {}
    recorder.finished.connect(lambda result: (outcome.update(result=result), loop.quit()))
    recorder.failed.connect(lambda message: (outcome.update(failure=message), loop.quit()))

    def flash(on: bool):
        if on and beeper:
            beeper.beep()
        pattern.flash = on
        pattern.repaint()

    def began(state: str):  # timed from when recording begins, after the encoder's warm-up
        if state == "recording":
            for at in FLASHES:
                QTimer.singleShot(int(at * 1000), lambda: flash(True))
                QTimer.singleShot(int((at + FLASH_SECONDS) * 1000), lambda: flash(False))
            QTimer.singleShot(int(SECONDS * 1000), recorder.stop)

    recorder.state_changed.connect(began)
    try:
        recorder.start()
    except RecordingError as exc:
        pattern.close()
        hidden.close()
        pytest.skip(f"this machine can't record its screen: {exc}")
    QTimer.singleShot(int(SECONDS * 1000) + 45_000, loop.quit)
    loop.exec()
    pattern.close()
    hidden.close()
    if beeper:
        beeper.stop()
    assert "result" in outcome, outcome.get("failure", "the recording never finished")
    return outcome["result"], pattern


def flash_times(path, pattern_box: QRect, scale: float) -> tuple[list[float], int]:
    centre = pattern_box.center()
    on, onsets, magenta = False, [], 0
    for t, image in frames.read(path):
        point = (round(centre.x() * scale), round(centre.y() * scale))
        lit = image.pixelColor(*point).lightness() > 200
        if lit and not on:
            onsets.append(t)
        on = lit
        corner = image.pixelColor(round(60 * scale), round(40 * scale))
        magenta += corner.red() > 200 and corner.green() < 60 and corner.blue() > 200
    return onsets, magenta


def loudness(path) -> list[tuple[float, float]]:
    """(time, RMS) of every 5 ms of the file's sound, decoded with Qt."""
    from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

    decoder = QAudioDecoder()
    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    decoder.setAudioFormat(fmt)
    loop, windows = QEventLoop(), []

    def ready():
        buffer = decoder.read()
        samples = array.array("f")
        samples.frombytes(bytes(buffer.constData())[: buffer.byteCount()])
        start = buffer.startTime() / 1e6
        for i in range(0, len(samples) - 240, 240):
            windows.append((start + i / 48000, math.sqrt(sum(v * v for v in samples[i:i + 240]) / 240)))

    decoder.bufferReady.connect(ready)
    decoder.finished.connect(loop.quit)
    QTimer.singleShot(20_000, loop.quit)
    decoder.setSource(QUrl.fromLocalFile(str(path)))
    decoder.start()
    loop.exec()
    return windows


def beep_times(path) -> list[float]:
    """When the beeps start in the file's sound. Loud is relative to the loudest moment: what the
    speakers' loopback hears depends on the output device and its volume (a Bluetooth headset at
    13% volume made the beeps a tenth as loud as on the laptop's speakers)."""
    windows = loudness(path)
    peak = max((rms for _t, rms in windows), default=0.0)
    if peak < 0.001:
        pytest.skip("nothing played on the default speakers reached the recording (muted, or asleep?)")
    onsets, on = [], False
    for t, rms in windows:
        loud = rms > 0.3 * peak
        if loud and not on and (not onsets or t - onsets[-1] > 0.2):
            onsets.append(t)
        on = loud
    return onsets


def test_a_recording_shows_the_screen_and_leaves_out_hidden_windows(qapp, tmp_path):
    result, pattern = run_recording(qapp, tmp_path, sound=False)

    scale = pattern.screen().devicePixelRatio()
    flashes, magenta = flash_times(result.path, pattern.box(), scale)
    assert [round(t, 1) for t in flashes] == pytest.approx([1.0, 2.5], abs=0.2)
    assert magenta == 0  # the window hidden from capture never shows
    stats = result.stats
    lost = stats["frames_skipped_late"] + stats["frames_dropped_queue"] + stats["frames_dropped_encoder"]
    assert lost <= max(2, 0.03 * SECONDS * 30), stats  # the plan's limit: at most 3% dropped


def test_sound_and_picture_stay_together(qapp, tmp_path):
    if not wasapi.devices(wasapi.SYSTEM):
        pytest.skip("no speakers on this machine")
    result, pattern = run_recording(qapp, tmp_path, sound=True)

    flashes, _magenta = flash_times(result.path, pattern.box(), pattern.screen().devicePixelRatio())
    beeps = beep_times(result.path)
    assert len(flashes) == len(beeps) == len(FLASHES), (flashes, beeps)
    for flash, beep in zip(flashes, beeps, strict=True):
        # The beep reaches the file ~0.1 s after the flash: Windows' own audio-output latency.
        assert -0.05 < beep - flash < 0.25, (flashes, beeps)
