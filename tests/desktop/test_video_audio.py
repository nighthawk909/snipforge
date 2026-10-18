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


def tone_level(left: list[float], hz: float = 1000.0, rate: int = 48000) -> tuple[float, float]:
    """(amplitude at `hz`, overall RMS) of a block: a pure tone of amplitude A gives (A, A/sqrt 2)."""
    if not left:
        return 0.0, 0.0
    i = sum(v * math.cos(2 * math.pi * hz * n / rate) for n, v in enumerate(left))
    q = sum(v * math.sin(2 * math.pi * hz * n / rate) for n, v in enumerate(left))
    return 2 * math.hypot(i, q) / len(left), math.sqrt(sum(v * v for v in left) / len(left))


class Collector:
    def __init__(self):
        self.lock = threading.Lock()
        self.packets = []  # (wall time received, stamp, frames, peak, left channel)

    def __call__(self, payload, frames, stamp, _glitch):
        samples = array.array("f")
        samples.frombytes(payload)
        peak = max((abs(v) for v in samples), default=0.0)
        with self.lock:
            self.packets.append((time.perf_counter(), stamp, frames, peak, list(samples[0::2])))


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
    middle = [s for p in loud[len(loud) // 4: 3 * len(loud) // 4] for s in p[4]]  # away from its start and end
    amplitude, rms = tone_level(middle)
    # the 1 kHz tone played (0.18 of full scale; Windows spreads a mono sound over two speakers 3 dB down)
    assert 0.09 < amplitude < 0.25
    assert amplitude / (rms * math.sqrt(2)) > 0.8  # and little else: it's that tone, not other sound


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
    assert all(p[1] is not None for p in packets)
    span = packets[-1][1] - packets[0][1]
    assert seconds == pytest.approx(span, abs=0.05)  # 48 000 frames for every second of timestamps
    lags = [received - stamp for received, stamp, *_rest in packets]
    assert all(0 <= lag < 0.1 for lag in lags)  # stamped on the clock time.perf_counter() reads


def test_a_device_that_isnt_there_is_explained(qapp):
    capture = wasapi.WasapiCapture(wasapi.MICROPHONE, lambda *a: None, device_id="{0.0.1.00000000}.{nonexistent}")

    started = time.perf_counter()
    with pytest.raises(wasapi.AudioUnavailable, match="unplugged"):
        capture.start()
    assert time.perf_counter() - started < 2.0


def test_the_default_device_followed_while_recording_is_windows_default(qapp):
    """Recording Windows' default device, the capture checks its id now and then to follow it to a
    new one: it must be the id Windows lists as the default."""
    def default_ids():
        enumerator = wasapi._Enumerator()
        try:
            return {kind: wasapi._default_id(enumerator, kind) for kind in (wasapi.SYSTEM, wasapi.MICROPHONE)}
        finally:
            enumerator.close()

    found = wasapi._in_com(default_ids)
    for kind, default in found.items():
        listed = {device.id: device for device in wasapi.devices(kind)}
        if not listed:
            continue  # none of this kind on this PC
        assert default in listed and listed[default].is_default, (kind, default, listed)
    if not any(found.values()):
        pytest.skip("no sound devices on this PC")
