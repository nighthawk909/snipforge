"""WASAPI shared-mode capture of a microphone or of system audio (loopback), via ctypes.

Both kinds come out the same way: 48 kHz stereo float32 packets (Windows converts from the
device's own format, AUTOCONVERTPCM), each stamped with the QueryPerformanceCounter time of its
first frame. time.perf_counter() reads the same counter on Windows, so audio and video share
one clock and can be lined up without guessing.

Loopback gotcha: Windows sends no packets at all while nothing is playing. The mixer fills those
gaps with silence from the timestamps, so system audio never drifts out of place.
"""
from __future__ import annotations

import ctypes
import threading
import time
from collections.abc import Callable
from ctypes import Structure, byref, c_int64, c_long, c_uint32, c_uint64, c_ushort, c_void_p

from com import GUID, check, release, vcall

SAMPLE_RATE = 48000
CHANNELS = 2
BYTES_PER_FRAME = 4 * CHANNELS

CLSID_MMDeviceEnumerator = GUID("bcde0395-e52f-467c-8e3d-c4579291692e")
IID_IMMDeviceEnumerator = GUID("a95664d2-9614-4f35-a746-de8db63617e6")
IID_IAudioClient = GUID("1cb9ad4c-dbfa-4c32-b178-c2f568a703b2")
IID_IAudioCaptureClient = GUID("c8adbd64-e71e-48a0-a4de-185c395cd317")

E_RENDER, E_CAPTURE = 0, 1
E_CONSOLE = 0
CLSCTX_ALL = 0x17
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY = 0x1
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_IEEE_FLOAT = 3
BUFFER_100NS = 2_000_000  # 200 ms of buffer on the Windows side
POLL_SECONDS = 0.005


class WAVEFORMATEX(Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", c_ushort), ("nChannels", c_ushort), ("nSamplesPerSec", c_uint32),
                ("nAvgBytesPerSec", c_uint32), ("nBlockAlign", c_ushort), ("wBitsPerSample", c_ushort),
                ("cbSize", c_ushort)]


# packet callback: (float32 interleaved bytes, frame count, QPC seconds of the first frame, discontinuity)
PacketCallback = Callable[[bytes, int, float, bool], None]


class WasapiCapture:
    """Captures one endpoint on its own thread. `loopback=True` records what the default speakers
    play; otherwise the default microphone."""

    def __init__(self, loopback: bool, on_packet: PacketCallback) -> None:
        self.loopback = loopback
        self._on_packet = on_packet
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.error: Exception | None = None
        self.device_name = "speakers (loopback)" if loopback else "microphone"
        self.packets = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"wasapi-{self.device_name}", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        if self.error:
            raise self.error

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)

    def _run(self) -> None:
        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx(None, 0)  # multithreaded apartment for this thread
        enumerator = device = client = capture = None
        try:
            enumerator = c_void_p()
            check(ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                         byref(IID_IMMDeviceEnumerator), byref(enumerator)), "CoCreateInstance")
            device = c_void_p()
            flow = E_RENDER if self.loopback else E_CAPTURE
            check(vcall(enumerator, 4, c_long, flow, E_CONSOLE, byref(device),
                        argtypes=(ctypes.c_int, ctypes.c_int, c_void_p)), "GetDefaultAudioEndpoint")
            client = c_void_p()
            check(vcall(device, 3, c_long, byref(IID_IAudioClient), CLSCTX_ALL, None, byref(client),
                        argtypes=(c_void_p, ctypes.c_uint, c_void_p, c_void_p)), "Activate")
            fmt = WAVEFORMATEX(WAVE_FORMAT_IEEE_FLOAT, CHANNELS, SAMPLE_RATE, SAMPLE_RATE * BYTES_PER_FRAME,
                               BYTES_PER_FRAME, 32, 0)
            flags = AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
            if self.loopback:
                flags |= AUDCLNT_STREAMFLAGS_LOOPBACK
            check(vcall(client, 3, c_long, AUDCLNT_SHAREMODE_SHARED, flags, BUFFER_100NS, 0, byref(fmt), None,
                        argtypes=(ctypes.c_int, ctypes.c_uint, c_int64, c_int64, c_void_p, c_void_p)), "Initialize")
            capture = c_void_p()
            check(vcall(client, 14, c_long, byref(IID_IAudioCaptureClient), byref(capture),
                        argtypes=(c_void_p, c_void_p)), "GetService")
            check(vcall(client, 10, c_long), "Start")
        except Exception as exc:  # report to start(), which raises it on the caller's thread
            self.error = exc
            self._ready.set()
            for obj in (capture, client, device, enumerator):
                release(obj)
            ole32.CoUninitialize()
            return
        self._ready.set()
        try:
            self._pump(capture)
        finally:
            vcall(client, 11, c_long)  # Stop
            for obj in (capture, client, device, enumerator):
                release(obj)
            ole32.CoUninitialize()

    def _pump(self, capture: c_void_p) -> None:
        size = c_uint32()
        data = c_void_p()
        frames = c_uint32()
        flags = c_uint32()
        devpos = c_uint64()
        qpc = c_uint64()
        while not self._stop.is_set():
            time.sleep(POLL_SECONDS)
            while True:
                check(vcall(capture, 5, c_long, byref(size), argtypes=(c_void_p,)), "GetNextPacketSize")
                if size.value == 0:
                    break
                check(vcall(capture, 3, c_long, byref(data), byref(frames), byref(flags), byref(devpos), byref(qpc),
                            argtypes=(c_void_p,) * 5), "GetBuffer")
                n = frames.value
                silent = bool(flags.value & AUDCLNT_BUFFERFLAGS_SILENT)
                payload = bytes(n * BYTES_PER_FRAME) if silent else ctypes.string_at(data, n * BYTES_PER_FRAME)
                vcall(capture, 4, c_long, n, argtypes=(ctypes.c_uint,))  # ReleaseBuffer
                self.packets += 1
                self._on_packet(payload, n, qpc.value * 1e-7,
                                bool(flags.value & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY))
