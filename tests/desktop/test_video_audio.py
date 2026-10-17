"""Desktop tests: recording from real sound devices with WASAPI (snipforge/video/wasapi.py).

Skipped where a device is missing (CI machines usually have no sound card). Run with:

    QT_QPA_PLATFORM=windows python -m pytest -m desktop
"""
import array
import math
import threading
import time

import pytest
from PySide6.QtCore import QTimer

from snipforge.video import wasapi

pytestmark = pytest.mark.desktop


def need(kind):
    found = wasapi.devices(kind)
    if not found:
        pytest.skip(f"no {kind} device on this machine")
    return found


class Collector:
    def __init__(self):
        self.lock = threading.Lock()
        self.packets = []  # (wall time received, stamp, frames, peak)

    def __call__(self, payload, frames, stamp, _glitch):
        samples = array.array("f")
        samples.frombytes(payload)
        peak = max((abs(v) for v in samples), default=0.0)
        with self.lock:
            self.packets.append((time.perf_counter(), stamp, frames, peak))


def run_for(seconds, qapp):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        qapp.processEvents()
        time.sleep(0.01)


def test_devices_are_listed_with_one_default(qapp):
    for kind in (wasapi.MICROPHONE, wasapi.SYSTEM):
        found = wasapi.devices(kind)
        assert sum(d.is_default for d in found) <= 1
        assert all(d.id and d.name for d in found)


def test_the_default_speakers_report_their_mute_and_volume(qapp):
    need(wasapi.SYSTEM)

    state = wasapi.endpoint_state(wasapi.SYSTEM)

    assert isinstance(state.muted, bool) and 0.0 <= state.volume <= 1.0


def test_system_audio_hears_what_plays(qapp):
    need(wasapi.SYSTEM)
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    tone = array.array("h", (int(6000 * math.sin(2 * math.pi * 1000 * i / 48000)) for i in range(48000))).tobytes()
    sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt)
    out = sink.start()
    feed = QTimer()
    left = [tone]
    feed.timeout.connect(lambda: left.__setitem__(0, left[0][out.write(left[0][:sink.bytesFree()]):]))
    collector = Collector()
    capture = wasapi.WasapiCapture(wasapi.SYSTEM, collector)
    capture.start()
    try:
        feed.start(5)
        run_for(1.5, qapp)
    finally:
        feed.stop()
        sink.stop()
        capture.stop()

    loud = [p for p in collector.packets if p[3] > 0.05]
    assert loud, "the tone never reached the system-audio recording"
    assert max(p[3] for p in loud) < 0.5  # it's the tone (0.18 full scale), not noise or a clipped mess


def test_the_microphone_records_in_real_time_on_the_shared_clock(qapp):
    need(wasapi.MICROPHONE)
    collector = Collector()
    capture = wasapi.WasapiCapture(wasapi.MICROPHONE, collector)
    try:
        capture.start()
    except wasapi.MicrophoneBlocked as exc:
        pytest.skip(str(exc))
    try:
        run_for(1.0, qapp)
    finally:
        capture.stop()

    packets = collector.packets[5:]  # the device settles first
    seconds = sum(p[2] for p in packets) / 48000
    span = packets[-1][1] - packets[0][1]
    assert seconds == pytest.approx(span, abs=0.05)  # 48 000 frames for every second of timestamps
    lags = [received - stamp for received, stamp, _frames, _peak in packets]
    assert all(0 <= lag < 0.5 for lag in lags)  # stamped on the clock time.perf_counter() reads


def test_a_device_that_isnt_there_is_explained(qapp):
    capture = wasapi.WasapiCapture(wasapi.MICROPHONE, lambda *a: None, device_id="{0.0.1.00000000}.{nonexistent}")

    with pytest.raises(wasapi.AudioUnavailable):
        capture.start()
