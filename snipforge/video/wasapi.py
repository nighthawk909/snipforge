"""Recording sound with WASAPI, Windows' own audio API: a microphone, or what the speakers play
("loopback", for system audio). No driver, no admin rights, no extra package.

Both arrive the same way: 48 kHz stereo float32 packets (Windows converts from the device's own
format), each stamped with the QueryPerformanceCounter time of its first frame, which is the clock
time.perf_counter() reads. So sound lines up with the video's frames (video/mixer.py places it).

Loopback sends nothing at all while nothing plays; the mixer turns those gaps into silence.

Recording Windows' default device (no device chosen), the capture follows it, checking every
FOLLOW_SECONDS: when Windows makes another device the default (headphones plugged in, a Bluetooth
headset connecting), it opens that one and only then lets go of the old, keeping the old if the new
won't open yet (tried again at the next check). When the device being recorded goes away, it waits
up to REOPEN_GRACE for Windows to settle on another; if there's none, it says so once and carries on
looking, so a headset that reconnects is picked up again. Otherwise the computer's sound would
quietly go missing from the rest of the recording once it plays elsewhere.
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections.abc import Callable
from ctypes import Structure, byref, c_float, c_int, c_int64, c_long, c_uint, c_uint32, c_uint64, c_ushort, c_void_p
from dataclasses import dataclass

from .com import GUID, ComError, check, release, vcall

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000
CHANNELS = 2
BYTES_PER_FRAME = 4 * CHANNELS
MICROPHONE, SYSTEM = "microphone", "system"

CLSID_MMDeviceEnumerator = GUID("bcde0395-e52f-467c-8e3d-c4579291692e")
IID_IMMDeviceEnumerator = GUID("a95664d2-9614-4f35-a746-de8db63617e6")
IID_IAudioClient = GUID("1cb9ad4c-dbfa-4c32-b178-c2f568a703b2")
IID_IAudioCaptureClient = GUID("c8adbd64-e71e-48a0-a4de-185c395cd317")
IID_IAudioEndpointVolume = GUID("5cdf2c82-841e-4546-9722-0cf74078229a")
PKEY_FRIENDLY_NAME = (GUID("a45c254e-df1c-4efd-8020-67d146a850e0"), 14)

E_RENDER, E_CAPTURE, E_CONSOLE = 0, 1, 0
CLSCTX_ALL = 0x17
DEVICE_STATE_ACTIVE = 1
STGM_READ = 0
VT_LPWSTR = 31
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY = 0x1
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR = 0x4
WAVE_FORMAT_IEEE_FLOAT = 3
BUFFER_100NS = 10_000_000  # one second of room on Windows' side, so a busy moment loses nothing
POLL_SECONDS = 0.005
START_TIMEOUT = 5.0
FOLLOW_SECONDS = 1.0  # following Windows' default device: how often it's checked
REOPEN_GRACE = 5.0  # after the device being recorded went: how long to wait for another before saying so
RETRY_SECONDS = 0.25  # meanwhile, how often to try

E_ACCESSDENIED = 0x80070005
AUDCLNT_E_DEVICE_INVALIDATED = 0x88890004
AUDCLNT_E_DEVICE_IN_USE = 0x8889000A
E_NOTFOUND = 0x80070490


class AudioUnavailable(RuntimeError):
    """A sound device can't be recorded; the message says why, in words for the person recording."""


class MicrophoneBlocked(AudioUnavailable):
    """Windows' privacy settings don't let desktop apps use the microphone."""


class WAVEFORMATEX(Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", c_ushort), ("nChannels", c_ushort), ("nSamplesPerSec", c_uint32),
                ("nAvgBytesPerSec", c_uint32), ("nBlockAlign", c_ushort), ("wBitsPerSample", c_ushort),
                ("cbSize", c_ushort)]


class PROPERTYKEY(Structure):
    _fields_ = [("fmtid", GUID), ("pid", c_uint32)]


class PROPVARIANT(Structure):
    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort), ("value", c_void_p),
                ("extra", c_void_p)]


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    is_default: bool


@dataclass(frozen=True)
class EndpointState:
    muted: bool
    volume: float  # 0-1, Windows' volume slider for the device


# packet callback: (interleaved float32 bytes, frames, seconds on the perf_counter clock or None when
# Windows says the timestamp is unreliable, discontinuity)
PacketCallback = Callable[[bytes, int, float | None, bool], None]


def explain(hr: int, kind: str) -> AudioUnavailable:
    """What a failed WASAPI call means for the person recording."""
    hr &= 0xFFFFFFFF
    what = _what(kind)
    if hr == E_ACCESSDENIED and kind == MICROPHONE:
        return MicrophoneBlocked("Windows isn't letting apps use the microphone. Turn on Settings > Privacy & "
                                 "security > Microphone > Let desktop apps access your microphone.")
    if hr in (AUDCLNT_E_DEVICE_INVALIDATED, E_NOTFOUND):
        return AudioUnavailable(f"The {what} device was unplugged or switched off.")
    if hr == AUDCLNT_E_DEVICE_IN_USE:
        return AudioUnavailable(f"Another app is using the {what} device on its own.")
    return AudioUnavailable(f"The {what} can't be recorded (error 0x{hr:08X}).")


def _what(kind: str) -> str:
    return "microphone" if kind == MICROPHONE else "computer's sound"


def _flow(kind: str) -> int:
    return E_CAPTURE if kind == MICROPHONE else E_RENDER


class _Enumerator:
    """IMMDeviceEnumerator for the current thread (which must have COM initialised)."""

    def __init__(self) -> None:
        self.ptr = c_void_p()
        check(ctypes.windll.ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                                   byref(IID_IMMDeviceEnumerator), byref(self.ptr)),
              "Opening Windows' audio devices")

    def default(self, kind: str) -> c_void_p | None:
        device = c_void_p()
        hr = vcall(self.ptr, 4, c_long, _flow(kind), E_CONSOLE, byref(device), argtypes=(c_int, c_int, c_void_p))
        return device if hr >= 0 else None  # E_NOTFOUND: no such device at all

    def by_id(self, device_id: str) -> c_void_p:
        device = c_void_p()
        check(vcall(self.ptr, 5, c_long, device_id, byref(device), argtypes=(ctypes.c_wchar_p, c_void_p)),
              "Finding the sound device")
        return device

    def active(self, kind: str) -> list[c_void_p]:
        collection = c_void_p()
        check(vcall(self.ptr, 3, c_long, _flow(kind), DEVICE_STATE_ACTIVE, byref(collection),
                    argtypes=(c_int, c_uint, c_void_p)), "Listing sound devices")
        found = []
        try:
            count = c_uint()
            check(vcall(collection, 3, c_long, byref(count), argtypes=(c_void_p,)), "Counting sound devices")
            for i in range(count.value):
                device = c_void_p()
                check(vcall(collection, 4, c_long, i, byref(device), argtypes=(c_uint, c_void_p)), "Sound device")
                found.append(device)
            return found
        except Exception:
            for device in found:
                release(device)
            raise
        finally:
            release(collection)

    def close(self) -> None:
        release(self.ptr)


def _device_id(device: c_void_p) -> str:
    text = ctypes.c_wchar_p()
    check(vcall(device, 5, c_long, byref(text), argtypes=(c_void_p,)), "Reading a device's id")
    try:
        return text.value or ""
    finally:
        ctypes.windll.ole32.CoTaskMemFree(text)


def _id_or_none(device: c_void_p) -> str | None:
    """The device's id, or None (logged) if Windows won't say."""
    try:
        return _device_id(device) or None
    except ComError as exc:
        log.debug("Couldn't read a sound device's id: %s", exc)
        return None


def _default_id(enumerator, kind: str) -> str | None:
    """Windows' default device's id now, or None if there's none (or it can't be read)."""
    device = enumerator.default(kind)
    if not device:
        return None
    try:
        return _id_or_none(device)
    finally:
        release(device)


def _friendly_name(device: c_void_p) -> str:
    store = c_void_p()
    if vcall(device, 4, c_long, STGM_READ, byref(store), argtypes=(c_uint, c_void_p)) < 0:
        return ""
    value = PROPVARIANT()
    try:
        key = PROPERTYKEY(*PKEY_FRIENDLY_NAME)
        if vcall(store, 5, c_long, byref(key), byref(value), argtypes=(c_void_p, c_void_p)) < 0:
            return ""
        return ctypes.wstring_at(value.value) if value.vt == VT_LPWSTR and value.value else ""
    finally:
        ctypes.windll.ole32.PropVariantClear(byref(value))
        release(store)


def _in_com(work):
    """Run `work()` with COM set up on this thread (and torn down if this call set it up)."""
    ole32 = ctypes.windll.ole32
    hr = ole32.CoInitializeEx(None, 0)
    try:
        return work()
    finally:
        if hr >= 0:
            ole32.CoUninitialize()


def devices(kind: str) -> list[Device]:
    """The active microphones (MICROPHONE) or speakers (SYSTEM), the default one marked."""
    def work():
        enumerator = _Enumerator()
        try:
            default = enumerator.default(kind)
            default_id = _device_id(default) if default else ""
            release(default)
            found = []
            active = enumerator.active(kind)
            try:
                for device in active:
                    device_id = _device_id(device)
                    found.append(Device(device_id, _friendly_name(device) or "Sound device", device_id == default_id))
            finally:
                for device in active:
                    release(device)
            return found
        finally:
            enumerator.close()
    return _in_com(work)


def endpoint_state(kind: str, device_id: str | None = None) -> EndpointState | None:
    """Whether a device is muted, and its volume; None if there's no such device."""
    def work():
        enumerator = _Enumerator()
        device = volume = None
        try:
            try:
                device = enumerator.by_id(device_id) if device_id else enumerator.default(kind)
            except ComError:
                return None  # unplugged since it was listed
            if not device:
                return None
            volume = c_void_p()
            check(vcall(device, 3, c_long, byref(IID_IAudioEndpointVolume), CLSCTX_ALL, None, byref(volume),
                        argtypes=(c_void_p, c_uint, c_void_p, c_void_p)), "Reading the device's volume")
            muted, level = c_int(), c_float()
            check(vcall(volume, 15, c_long, byref(muted), argtypes=(c_void_p,)), "Reading mute")
            check(vcall(volume, 9, c_long, byref(level), argtypes=(c_void_p,)), "Reading volume")
            return EndpointState(bool(muted.value), round(level.value, 3))
        finally:
            release(volume)
            release(device)
            enumerator.close()
    return _in_com(work)


class WasapiCapture:
    """Records one device on its own thread, calling `on_packet` there for every packet. `on_error`
    (also on that thread) hears about a device that disappears while recording."""

    def __init__(self, kind: str, on_packet: PacketCallback, device_id: str | None = None,
                 on_error: Callable[[AudioUnavailable], None] | None = None) -> None:
        self.kind, self.device_id = kind, device_id
        self._on_packet, self._on_error = on_packet, on_error
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._failure: AudioUnavailable | None = None
        self.packets = 0
        self.frames = 0
        self.switches = 0  # moves to a new default device
        self._glitch_next = False  # the first packet after a move: the mixer mustn't run on from the last

    def start(self) -> None:
        """Open the device; raises AudioUnavailable (or MicrophoneBlocked) if it can't be recorded."""
        self._thread = threading.Thread(target=self._run, name=f"wasapi-{self.kind}", daemon=True)
        self._thread.start()
        if not self._ready.wait(START_TIMEOUT):
            self.stop()
            raise AudioUnavailable(f"The {_what(self.kind)} took too long to start.")
        if self._failure:
            raise self._failure

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(2)

    def _run(self) -> None:
        ole32, avrt = ctypes.windll.ole32, ctypes.windll.avrt
        initialised = ole32.CoInitializeEx(None, 0) >= 0
        avrt.AvSetMmThreadCharacteristicsW.restype = c_void_p
        avrt.AvRevertMmThreadCharacteristics.argtypes = [c_void_p]
        mmcss = enumerator = None
        held: list = []  # what's open on the device being recorded: device, client, capture
        try:
            try:
                mmcss = avrt.AvSetMmThreadCharacteristicsW("Pro Audio", byref(c_uint32()))  # audio-thread priority
                enumerator = _Enumerator()
                held[:] = self._open_device(enumerator)
            except ComError as exc:
                self._failure = explain(exc.hresult, self.kind)
            except AudioUnavailable as exc:
                self._failure = exc
            except Exception as exc:  # whatever it is, start() must hear now, not after its timeout
                log.exception("Opening the %s failed", _what(self.kind))
                self._failure = AudioUnavailable(f"The {_what(self.kind)} couldn't be opened ({exc}).")
            finally:
                self._ready.set()
            if not self._failure:
                self._record(enumerator, held)
        except ComError as exc:
            self._report(explain(exc.hresult, self.kind))
        except AudioUnavailable as exc:  # the default went and there's no other device to go to
            self._report(exc)
        except Exception as exc:  # including one raised by on_packet: never die without saying so
            log.exception("Recording the %s stopped", _what(self.kind))
            self._report(AudioUnavailable(f"Recording the {_what(self.kind)} stopped ({exc})."))
        finally:
            self._close_device(held)
            if enumerator:
                enumerator.close()
            if mmcss:
                avrt.AvRevertMmThreadCharacteristics(mmcss)
            if initialised:
                ole32.CoUninitialize()

    def _open_device(self, enumerator) -> list:
        """The device to record (the chosen one, or Windows' default), opened: [device, client, capture]."""
        device = enumerator.by_id(self.device_id) if self.device_id else enumerator.default(self.kind)
        if not device:
            raise AudioUnavailable("No microphone is connected." if self.kind == MICROPHONE
                                   else "No speakers or headphones are connected.")
        try:
            client, capture = self._open(device)
        except Exception:
            release(device)
            raise
        return [device, client, capture]

    @staticmethod
    def _close_device(held: list) -> None:
        device, client, capture = (held + [None, None, None])[:3]
        held.clear()
        if client:
            vcall(client, 11, c_long)  # Stop
        for obj in (capture, client, device):
            release(obj)

    def _record(self, enumerator, held: list) -> None:
        """Hand on packets until stop(). Following Windows' default device, move to the new one when
        it changes or the one being recorded goes away (see the module's notes)."""
        recording = {"id": None if self.device_id else _id_or_none(held[0])}
        if recording["id"] is None:  # a device chosen (that one, or nothing), or one that can't be told apart
            self._pump(held[2])
            return

        def moved() -> bool:
            return _default_id(enumerator, self.kind) not in (None, recording["id"])

        while True:
            try:
                stopped, lost = self._pump(held[2], moved), False
            except ComError as exc:
                if exc.hresult not in (AUDCLNT_E_DEVICE_INVALIDATED, E_NOTFOUND):
                    raise
                stopped, lost = False, True
            if stopped or self._stop.is_set():
                return
            if lost:
                self._close_device(held)
                new = self._reopen_after_loss(enumerator)
                if new is None:  # stopped while waiting
                    return
            else:
                new = self._try_open(enumerator)  # the new default, before letting go of this one
                if new is None:
                    continue  # it won't open yet: on with this one, and tried again at the next check
                self._close_device(held)
            held[:] = new
            recording["id"] = _id_or_none(new[0])
            self.switches += 1
            self._glitch_next = True
            log.info("Recording the %s from Windows' new default device", _what(self.kind))

    def _try_open(self, enumerator) -> list | None:
        """Windows' default device, opened; None if there's none or it won't open (yet)."""
        try:
            return self._open_device(enumerator)
        except (ComError, AudioUnavailable) as exc:
            log.info("The %s's new default device didn't open: %s", _what(self.kind), exc)
            return None

    def _reopen_after_loss(self, enumerator) -> list | None:
        """After the device being recorded went: the next default, tried every RETRY_SECONDS while
        Windows settles on one; past REOPEN_GRACE, said once, and looked for every FOLLOW_SECONDS
        until one comes back. None if stop() came first."""
        deadline, said = time.perf_counter() + REOPEN_GRACE, False
        while not self._stop.is_set():
            new = self._try_open(enumerator)
            if new is not None:
                return new
            if not said and time.perf_counter() >= deadline:
                self._report(AudioUnavailable(
                    "The microphone was disconnected, and there's no other: recording without it until one is "
                    "connected." if self.kind == MICROPHONE else "The speakers or headphones were disconnected, "
                    "and there are no others: recording without the computer's sound until some are connected."))
                said = True
            self._stop.wait(FOLLOW_SECONDS if said else RETRY_SECONDS)
        return None

    def _report(self, error: AudioUnavailable) -> None:
        if self._on_error:
            try:
                self._on_error(error)
            except Exception:
                log.exception("on_error failed")

    def _open(self, device: c_void_p) -> tuple[c_void_p, c_void_p]:
        client, capture = c_void_p(), c_void_p()
        check(vcall(device, 3, c_long, byref(IID_IAudioClient), CLSCTX_ALL, None, byref(client),
                    argtypes=(c_void_p, c_uint, c_void_p, c_void_p)), "Activating the audio client")
        try:
            fmt = WAVEFORMATEX(WAVE_FORMAT_IEEE_FLOAT, CHANNELS, SAMPLE_RATE, SAMPLE_RATE * BYTES_PER_FRAME,
                               BYTES_PER_FRAME, 32, 0)
            flags = AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
            if self.kind == SYSTEM:
                flags |= AUDCLNT_STREAMFLAGS_LOOPBACK
            check(vcall(client, 3, c_long, AUDCLNT_SHAREMODE_SHARED, flags, BUFFER_100NS, 0, byref(fmt), None,
                        argtypes=(c_int, c_uint, c_int64, c_int64, c_void_p, c_void_p)), "Initialize")
            check(vcall(client, 14, c_long, byref(IID_IAudioCaptureClient), byref(capture),
                        argtypes=(c_void_p, c_void_p)), "GetService")
            check(vcall(client, 10, c_long), "Start")
            return client, capture
        except Exception:
            release(capture)
            release(client)
            raise

    def _pump(self, capture: c_void_p, moved: Callable[[], bool] | None = None) -> bool:
        """Hand on every packet until stop(), then the last few still waiting: True. With `moved`,
        asked every FOLLOW_SECONDS, also until it says the device has changed: False."""
        check_at = time.perf_counter() + FOLLOW_SECONDS
        while True:
            stopping = self._stop.is_set()
            self._drain(capture)
            if stopping:
                return True
            if moved is not None and time.perf_counter() >= check_at:
                check_at = time.perf_counter() + FOLLOW_SECONDS
                if moved():
                    return False
            time.sleep(POLL_SECONDS)

    def _drain(self, capture: c_void_p) -> None:
        size, data, frames, flags = c_uint32(), c_void_p(), c_uint32(), c_uint32()
        position, qpc = c_uint64(), c_uint64()
        while True:
            check(vcall(capture, 5, c_long, byref(size), argtypes=(c_void_p,)), "GetNextPacketSize")
            if size.value == 0:
                return
            check(vcall(capture, 3, c_long, byref(data), byref(frames), byref(flags), byref(position),
                        byref(qpc), argtypes=(c_void_p,) * 5), "GetBuffer")
            n = frames.value
            silent = bool(flags.value & AUDCLNT_BUFFERFLAGS_SILENT)
            payload = bytes(n * BYTES_PER_FRAME) if silent else ctypes.string_at(data, n * BYTES_PER_FRAME)
            check(vcall(capture, 4, c_long, n, argtypes=(c_uint,)), "ReleaseBuffer")
            self.packets += 1
            self.frames += n
            # qpc is in 100 ns units of QueryPerformanceCounter time, the clock perf_counter reads;
            # None when Windows flags it as wrong (or gives none), so the mixer won't trust it.
            unreliable = flags.value & AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR or qpc.value == 0
            glitch = bool(flags.value & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY) or self._glitch_next
            self._glitch_next = False
            self._on_packet(payload, n, None if unreliable else qpc.value * 1e-7, glitch)
