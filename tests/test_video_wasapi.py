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
    """A COM object made in Python: a table of callbacks at the slots given, 0 (S_OK) elsewhere. It
    counts its references (AddRef, Release; hand_out() is Windows handing one out) and Stop calls
    (IAudioClient's slot 11), so leaks and double releases show."""

    def __init__(self, methods: dict, slots: int = 16) -> None:
        self.refs = self.stops = 0

        def add_ref(_this):
            self.refs += 1
            return self.refs

        def release(_this):
            self.refs -= 1
            return self.refs

        def stop(_this):
            self.stops += 1
            return 0

        methods = {1: ((), add_ref), 2: ((), release), 11: ((), stop), **methods}
        self.callbacks = []
        self.table = (c_void_p * slots)()
        for i in range(slots):
            argtypes, fn = methods.get(i, ((), lambda this, *a: 0))
            callback = ctypes.WINFUNCTYPE(c_long, c_void_p, *argtypes)(fn)
            self.callbacks.append(callback)
            self.table[i] = ctypes.cast(callback, c_void_p).value
        self.vtable_pointer = c_void_p(ctypes.addressof(self.table))
        self.ptr = c_void_p(ctypes.addressof(self.vtable_pointer))

    def hand_out(self) -> c_void_p:
        self.refs += 1
        return self.ptr


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


# --- following Windows' default device ------------------------------------------------------------

class _Devices:
    """Windows' sound devices as the capture sees them: fakes by name (the device, its audio client
    and its capture client, each counting references), one of them the default (or none), each with
    the packets its capture client hands out. Opening one in `refuse` fails, as a device still
    settling does."""

    def __init__(self, packets: dict) -> None:
        self.coms = {name: FakeCom({}) for name in packets}
        self.clients = {name: FakeCom({}) for name in packets}
        self.captures = {name: FakeCaptureClient(queued) for name, queued in packets.items()}
        self.default_name: str | None = next(iter(packets))
        self.refuse: set[str] = set()
        self.opened: list[str] = []

    def name_of(self, device) -> str:
        address = getattr(device, "value", device)
        return next(n for n, com in self.coms.items() if com.ptr.value == address)

    def leaks(self) -> dict:
        everything = [*self.coms.items(), *self.clients.items(), *self.captures.items()]
        return {name: com.refs for name, com in everything if com.refs}

    def install(self, monkeypatch) -> None:
        devices = self

        class Enumerator:
            def default(self, _kind):
                return devices.coms[devices.default_name].hand_out() if devices.default_name else None

            def by_id(self, device_id):
                return devices.coms[device_id].hand_out()

            def close(self):
                pass

        def open_(_capture, device):
            name = devices.name_of(device)
            devices.opened.append(name)
            if name in devices.refuse:
                raise wasapi.ComError("Initialize", wasapi.AUDCLNT_E_DEVICE_INVALIDATED)
            return devices.clients[name].hand_out(), devices.captures[name].hand_out()

        monkeypatch.setattr(wasapi, "_Enumerator", Enumerator)
        monkeypatch.setattr(wasapi.WasapiCapture, "_open", open_)
        monkeypatch.setattr(wasapi, "_device_id", devices.name_of)
        monkeypatch.setattr(wasapi, "FOLLOW_SECONDS", 0.01)
        monkeypatch.setattr(wasapi, "RETRY_SECONDS", 0.01)
        monkeypatch.setattr(wasapi, "REOPEN_GRACE", 0.1)


def unplugged() -> FakeCom:
    """A capture client whose device was unplugged: GetNextPacketSize fails as Windows' does."""
    return FakeCom({5: ((c_void_p,), lambda this, size: wasapi.AUDCLNT_E_DEVICE_INVALIDATED - 2**32)})


def wait_until(condition, seconds=3.0) -> bool:
    deadline = time.perf_counter() + seconds
    while not condition() and time.perf_counter() < deadline:
        time.sleep(0.005)
    return condition()


def pumping(devices, then):
    """WasapiCapture._pump, calling then(name) as each device starts being pumped."""
    pump = wasapi.WasapiCapture._pump

    def wrapped(self, capture, moved=None):
        then(devices.opened[-1])
        return pump(self, capture, moved)
    return wrapped


@windows_only
def test_the_computers_sound_follows_windows_to_a_new_default_device(monkeypatch):
    devices = _Devices({"speakers": [(480, 0, 1_000_000_000, 0.1)],
                        "headset": [(480, 0, 1_000_100_000, 0.2), (480, 0, 1_000_200_000, 0.2)]})
    devices.install(monkeypatch)
    got = []
    capture = wasapi.WasapiCapture(wasapi.SYSTEM, lambda *packet: got.append(packet))
    capture.start()

    assert wait_until(lambda: len(got) == 1)
    devices.default_name = "headset"  # Windows moves the sound there
    assert wait_until(lambda: len(got) == 3)
    capture.stop()

    assert devices.opened == ["speakers", "headset"] and capture.switches == 1
    assert [packet[3] for packet in got] == [False, True, False]  # a gap for the mixer, once
    assert got[1][0] == bytes((ctypes.c_float * 960)(*([0.2] * 960)))  # from the headset
    assert devices.clients["speakers"].stops == 1 and devices.leaks() == {}


@windows_only
def test_a_new_default_that_wont_open_yet_keeps_the_old_one_recording(monkeypatch):
    devices = _Devices({"speakers": [], "headset": [(480, 0, 1_000_500_000, 0.2)]})
    devices.install(monkeypatch)
    devices.refuse.add("headset")  # a Bluetooth headset still switching profile
    got = []
    capture = wasapi.WasapiCapture(wasapi.SYSTEM, lambda *packet: got.append(packet))
    capture.start()
    devices.default_name = "headset"

    assert wait_until(lambda: devices.opened.count("headset") >= 2)
    devices.captures["speakers"].packets.append((480, 0, 1_000_000_000, 0.1))
    assert wait_until(lambda: len(got) == 1)  # still recording the speakers meanwhile
    devices.refuse.clear()
    assert wait_until(lambda: len(got) == 2)
    capture.stop()

    assert capture.switches == 1 and got[1][3] is True and devices.leaks() == {}


@windows_only
def test_a_default_device_that_goes_away_is_followed_to_the_next(monkeypatch):
    devices = _Devices({"usb": [], "speakers": [(480, 0, 1_000_000_000, 0.3)]})
    devices.install(monkeypatch)
    devices.captures["usb"] = unplugged()

    def windows_settles(name):
        if name == "usb":  # unplugged; for a moment Windows still names it the default, then moves on
            devices.refuse.add("usb")
            threading.Timer(0.05, lambda: setattr(devices, "default_name", "speakers")).start()

    monkeypatch.setattr(wasapi.WasapiCapture, "_pump", pumping(devices, windows_settles))
    errors, got = [], []
    capture = wasapi.WasapiCapture(wasapi.SYSTEM, lambda *packet: got.append(packet), on_error=errors.append)
    capture.start()

    assert wait_until(lambda: got)
    capture.stop()

    assert devices.opened[0] == "usb" and devices.opened[-1] == "speakers" and errors == []
    assert devices.leaks() == {}


@windows_only
def test_with_no_device_left_it_says_so_once_and_picks_up_one_that_comes_back(monkeypatch):
    devices = _Devices({"headset": [], "headset again": [(480, 0, 1_000_000_000, 0.4)]})
    devices.install(monkeypatch)
    devices.captures["headset"] = unplugged()
    monkeypatch.setattr(wasapi.WasapiCapture, "_pump",
                        pumping(devices, lambda name: setattr(devices, "default_name", None)))
    errors, got = [], []
    capture = wasapi.WasapiCapture(wasapi.SYSTEM, lambda *packet: got.append(packet), on_error=errors.append)
    capture.start()

    assert wait_until(lambda: errors)
    time.sleep(0.05)  # still looking, without saying it again
    devices.default_name = "headset again"  # it reconnects
    assert wait_until(lambda: got)
    capture.stop()

    assert len(errors) == 1 and "no others" in str(errors[0]) and devices.leaks() == {}


@windows_only
def test_stopping_while_waiting_for_a_device_ends_at_once(monkeypatch):
    devices = _Devices({"usb": []})
    devices.install(monkeypatch)
    devices.captures["usb"] = unplugged()
    monkeypatch.setattr(wasapi.WasapiCapture, "_pump",
                        pumping(devices, lambda name: setattr(devices, "default_name", None)))
    capture = wasapi.WasapiCapture(wasapi.SYSTEM, lambda *packet: None)
    capture.start()
    assert wait_until(lambda: devices.opened)
    time.sleep(0.03)

    started = time.perf_counter()
    capture.stop()

    assert not capture._thread.is_alive() and time.perf_counter() - started < 1.0
    assert devices.leaks() == {}


@windows_only
def test_no_default_for_a_moment_keeps_the_device_being_recorded(monkeypatch):
    devices = _Devices({"speakers": [(480, 0, 1_000_000_000, 0.1)]})
    devices.install(monkeypatch)
    got = []
    capture = wasapi.WasapiCapture(wasapi.SYSTEM, lambda *packet: got.append(packet))
    capture.start()
    assert wait_until(lambda: got)

    devices.default_name = None  # Windows between devices
    time.sleep(0.1)  # ten checks
    capture.stop()

    assert devices.opened == ["speakers"] and capture.switches == 0 and devices.leaks() == {}


@windows_only
def test_a_chosen_device_is_kept_whatever_windows_makes_the_default(monkeypatch):
    devices = _Devices({"usb mic": [(480, 0, 1_000_000_000, 0.1)], "laptop mic": []})
    devices.install(monkeypatch)
    devices.default_name = "laptop mic"
    got = []
    capture = wasapi.WasapiCapture(wasapi.MICROPHONE, lambda *packet: got.append(packet), device_id="usb mic")
    capture.start()

    assert wait_until(lambda: got)
    time.sleep(0.1)  # many checks' worth, had it been following
    capture.stop()

    assert devices.opened == ["usb mic"] and capture.switches == 0 and devices.leaks() == {}


@windows_only
def test_a_device_id_is_read_and_its_memory_given_back():
    text = "{0.0.0.00000000}.{6c2f9a3e-0000-4000-8000-000000000001}"
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoTaskMemAlloc.restype = c_void_p
    ole32.CoTaskMemAlloc.argtypes = [ctypes.c_size_t]

    def get_id(_this, out):
        size = (len(text) + 1) * 2
        memory = ole32.CoTaskMemAlloc(size)
        ctypes.memmove(memory, ctypes.create_unicode_buffer(text), size)
        ctypes.cast(out, POINTER(c_void_p)).contents.value = memory
        return 0

    device = FakeCom({5: ((c_void_p,), get_id)})

    assert wasapi._device_id(device.ptr) == text  # read, and freed with CoTaskMemFree (no crash)
    assert wasapi._id_or_none(FakeCom({5: ((c_void_p,), lambda this, out: -2147024809)}).ptr) is None
