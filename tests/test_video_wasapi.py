"""snipforge/video/wasapi.py without sound devices: messages, the structures passed to Windows, and
the capture loop driven through a fake COM object (so the method slots it calls are checked too).
The desktop suite (tests/desktop/test_video_audio.py) records from real devices."""
import ctypes
import sys
import threading
import time
from ctypes import POINTER, c_long, c_uint32, c_uint64, c_void_p

import pytest

from snipforge.video import wasapi

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="COM is Windows only")


def test_a_blocked_microphone_says_where_to_turn_it_on():
    error = wasapi.explain(0x80070005, wasapi.MICROPHONE)

    assert isinstance(error, wasapi.MicrophoneBlocked)
    assert "Let desktop apps access your microphone" in str(error)


def test_other_failures_are_explained_in_words():
    assert "unplugged" in str(wasapi.explain(0x88890004, wasapi.SYSTEM))
    assert "unplugged" in str(wasapi.explain(0x80070490, wasapi.MICROPHONE))
    assert "Another app" in str(wasapi.explain(0x8889000A, wasapi.MICROPHONE))
    assert "0x80004005" in str(wasapi.explain(0x80004005, wasapi.SYSTEM))
    assert not isinstance(wasapi.explain(0x80070005, wasapi.SYSTEM), wasapi.MicrophoneBlocked)


def test_the_structures_windows_reads_have_its_sizes():
    assert ctypes.sizeof(wasapi.WAVEFORMATEX) == 18
    assert ctypes.sizeof(wasapi.PROPERTYKEY) == 20
    assert ctypes.sizeof(wasapi.PROPVARIANT) == 24


# --- a fake IAudioCaptureClient --------------------------------------------------------------------

class FakeCom:
    """A COM object made in Python: a table of callbacks at the slots given, 0 (S_OK) elsewhere."""

    def __init__(self, methods: dict, slots: int = 8) -> None:
        self.callbacks = []
        self.table = (c_void_p * slots)()
        for i in range(slots):
            argtypes, fn = methods.get(i, ((), lambda this, *a: 0))
            callback = ctypes.WINFUNCTYPE(c_long, c_void_p, *argtypes)(fn)
            self.callbacks.append(callback)
            self.table[i] = ctypes.cast(callback, c_void_p).value
        self.vtable_pointer = c_void_p(ctypes.addressof(self.table))
        self.ptr = c_void_p(ctypes.addressof(self.vtable_pointer))


class FakeCaptureClient(FakeCom):
    """Hands out queued packets through IAudioCaptureClient's slots: GetBuffer (3), ReleaseBuffer (4),
    GetNextPacketSize (5)."""

    def __init__(self, packets) -> None:
        self.packets = list(packets)  # (frames, flags, qpc in 100 ns, value of every sample)
        self.buffers = []
        self.released = []

        def next_size(_this, size):
            ctypes.cast(size, POINTER(c_uint32)).contents.value = self.packets[0][0] if self.packets else 0
            return 0

        def get_buffer(_this, data, frames, flags, _position, qpc):
            n, flag, stamp, value = self.packets[0]
            buffer = (ctypes.c_float * (n * 2))(*([value] * (n * 2)))
            self.buffers.append(buffer)
            ctypes.cast(data, POINTER(c_void_p)).contents.value = ctypes.addressof(buffer)
            ctypes.cast(frames, POINTER(c_uint32)).contents.value = n
            ctypes.cast(flags, POINTER(c_uint32)).contents.value = flag
            ctypes.cast(qpc, POINTER(c_uint64)).contents.value = stamp
            return 0

        def release_buffer(_this, n):
            self.released.append(n)
            self.packets.pop(0)
            return 0

        super().__init__({3: ((c_void_p,) * 5, get_buffer), 4: ((ctypes.c_uint,), release_buffer),
                          5: ((c_void_p,), next_size)})


@windows_only
def test_each_packet_is_handed_on_with_its_time_unless_windows_says_it_is_wrong():
    got = []
    capture = wasapi.WasapiCapture(wasapi.MICROPHONE, lambda *packet: got.append(packet))
    fake = FakeCaptureClient([
        (480, 0, 1_234_567_890, 0.25),  # 123.456789 s
        (480, wasapi.AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR, 1_234_667_890, 0.25),
        (480, 0, 0, 0.25),  # no timestamp at all
        (240, wasapi.AUDCLNT_BUFFERFLAGS_SILENT | wasapi.AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY, 1_234_867_890, 0.9),
    ])
    capture._stop.set()  # one pass, then stop

    capture._pump(fake.ptr)

    assert [(n, t, glitch) for _payload, n, t, glitch in got] == [
        (480, pytest.approx(123.456789), False), (480, None, False), (480, None, False),
        (240, pytest.approx(123.486789), True)]
    assert got[0][0] == bytes((ctypes.c_float * 960)(*([0.25] * 960)))
    assert got[3][0] == bytes(240 * 8)  # Windows said silent: silence, whatever the buffer held
    assert fake.released == [480, 480, 480, 240]
    assert capture.packets == 4 and capture.frames == 1680


class _FakeEnumerator:
    """Stands in for the device enumerator: its default device is a fake that only counts releases."""

    def __init__(self) -> None:
        self.device = FakeCom({})

    def default(self, _kind):
        return self.device.ptr

    def by_id(self, _device_id):
        return self.device.ptr

    def close(self) -> None:
        pass


@windows_only
def test_a_failing_packet_handler_is_reported_not_swallowed(monkeypatch):
    errors = []
    fake = FakeCaptureClient([(480, 0, 1_000_000_000 + i, 0.1) for i in range(5)])
    monkeypatch.setattr(wasapi, "_Enumerator", _FakeEnumerator)
    monkeypatch.setattr(wasapi.WasapiCapture, "_open", lambda self, device: (None, fake.ptr))

    def handler(*_packet):
        raise KeyError("mic")

    capture = wasapi.WasapiCapture(wasapi.MICROPHONE, handler, on_error=errors.append)
    capture.start()
    capture._thread.join(5)

    assert not capture._thread.is_alive()
    assert len(errors) == 1 and "stopped" in str(errors[0])


@windows_only
def test_any_failure_while_opening_is_reported_at_once(monkeypatch):
    monkeypatch.setattr(wasapi, "_Enumerator", _FakeEnumerator)

    def broken(self, device):
        raise ValueError("bad argument")

    monkeypatch.setattr(wasapi.WasapiCapture, "_open", broken)
    capture = wasapi.WasapiCapture(wasapi.SYSTEM, lambda *packet: None)
    started = time.perf_counter()

    with pytest.raises(wasapi.AudioUnavailable, match="computer's sound couldn't be opened"):
        capture.start()
    assert time.perf_counter() - started < 1.0  # not the 5 s start timeout


@windows_only
def test_stopping_hands_on_what_was_still_waiting(monkeypatch):
    got = []
    capture = wasapi.WasapiCapture(wasapi.MICROPHONE, lambda *packet: got.append(packet))
    fake = FakeCaptureClient([])

    def last_packet_then_stop():
        fake.packets.append((480, 0, 1_000_000_000, 0.1))
        capture._stop.set()

    threading.Timer(0.05, last_packet_then_stop).start()

    capture._pump(fake.ptr)

    assert len(got) == 1
