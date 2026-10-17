"""Screen frames for recording: DXGI Desktop Duplication, copying only the recorded area.

Qt's QScreenCapture reads the whole monitor back every frame (20-29 fps on a 3440x1440 screen);
copying just the area on the graphics card first keeps up with the screen (docs/VIDEO-PLAN.md).
Windows 10 1703 or later, no dependency, no admin, no yellow capture border, and windows hidden
with SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) are left out by Windows itself.

The parts that bite in practice:
- a monitor can hang off either graphics card of a hybrid laptop, so the right one is looked up;
- Qt makes the process per-monitor DPI aware (v2), where only IDXGIOutput5::DuplicateOutput1 works;
- Windows takes the duplication away on a desktop switch (UAC prompt, lock screen, mode change),
  so it's reopened, and refused reopens are retried until Windows allows it again;
- a rotated monitor's image comes in the monitor's own orientation, so the area is found there
  and turned upright;
- DRM-protected video shows black, and each frame says so (protected_content).

Everything is in physical desktop pixels on one monitor. The pointer isn't in the frames: the
recorder draws it (video/cursor.py).
"""
from __future__ import annotations

import ctypes
import logging
import time
from collections.abc import Callable
from ctypes import POINTER, Structure, byref, c_long, c_longlong, c_uint, c_void_p, c_wchar
from dataclasses import dataclass
from functools import cache
from itertools import count
from typing import Protocol

from .com import GUID, ComError, check, query, release, vcall

log = logging.getLogger(__name__)

Area = tuple[int, int, int, int]  # x, y, width, height in physical desktop pixels

IID_IDXGIFactory1 = GUID("770aae78-f26f-4dba-a829-253c83d1b387")
IID_IDXGIOutput5 = GUID("80a07424-ab52-42eb-833c-0c42fd282d98")
IID_ID3D11Texture2D = GUID("6f15aaf2-d208-4e89-9ab4-489535d34f9c")

DXGI_FORMAT_B8G8R8A8_UNORM = 87
D3D_DRIVER_TYPE_UNKNOWN = 0  # required when the graphics card is given
D3D11_SDK_VERSION = 7
D3D11_USAGE_STAGING = 3
D3D11_CPU_ACCESS_READ = 0x20000
D3D11_MAP_READ = 1

E_ACCESSDENIED = 0x80070005
E_NOINTERFACE = 0x80004002
DXGI_ERROR_NOT_FOUND = 0x887A0002
DXGI_ERROR_UNSUPPORTED = 0x887A0004
DXGI_ERROR_DEVICE_REMOVED = 0x887A0005
DXGI_ERROR_DEVICE_RESET = 0x887A0007
DXGI_ERROR_NOT_CURRENTLY_AVAILABLE = 0x887A0022
DXGI_ERROR_ACCESS_LOST = 0x887A0026
DXGI_ERROR_WAIT_TIMEOUT = 0x887A0027
DXGI_ERROR_SESSION_DISCONNECTED = 0x887A0028

# Windows took the duplication (or the graphics card) away: open it again.
_LOST = frozenset({DXGI_ERROR_ACCESS_LOST, DXGI_ERROR_DEVICE_REMOVED, DXGI_ERROR_DEVICE_RESET})
# Why Windows won't duplicate the screen, in words for the person recording.
_UNAVAILABLE = {
    E_ACCESSDENIED: "Windows isn't letting apps see the screen right now (a UAC prompt or the lock screen)",
    E_NOINTERFACE: "recording the screen needs Windows 10 version 1703 or later",
    DXGI_ERROR_UNSUPPORTED: "this display or graphics driver can't be recorded (Remote Desktop or a basic driver)",
    DXGI_ERROR_NOT_CURRENTLY_AVAILABLE: "too many apps are recording this screen at once",
    DXGI_ERROR_SESSION_DISCONNECTED: "this Windows session is disconnected",
}

# Positions in the COM interfaces' tables of methods.
_FACTORY1_ENUM_ADAPTERS1 = 12
_ADAPTER_ENUM_OUTPUTS = 7
_OUTPUT_GET_DESC = 7
_OUTPUT5_DUPLICATE_OUTPUT1 = 26
_DUPLICATION_GET_DESC = 7
_DUPLICATION_ACQUIRE_NEXT_FRAME = 8
_DUPLICATION_RELEASE_FRAME = 14
_DEVICE_CREATE_TEXTURE2D = 5
_CONTEXT_MAP = 14
_CONTEXT_UNMAP = 15
_CONTEXT_COPY_SUBRESOURCE_REGION = 46
_ADD_REF = 1

_MASK = 0xFFFFFFFF
_DEGREES = {0: 0, 1: 0, 2: 90, 3: 180, 4: 270}  # DXGI_MODE_ROTATION: unspecified, identity, 90, 180, 270


class AreaError(ValueError):
    """The area isn't on exactly one monitor (or is empty)."""


class DuplicationUnavailable(OSError):
    """Windows won't duplicate the screen here, and says why."""


class AccessLost(Exception):
    """Windows took the duplication away (UAC prompt, lock screen, mode change); open it again."""


class _RECT(Structure):
    _fields_ = [("left", c_long), ("top", c_long), ("right", c_long), ("bottom", c_long)]


class _OUTPUT_DESC(Structure):  # noqa: N801 - DXGI_OUTPUT_DESC
    _fields_ = [("DeviceName", c_wchar * 32), ("DesktopCoordinates", _RECT), ("AttachedToDesktop", c_long),
                ("Rotation", c_uint), ("Monitor", c_void_p)]


class _OUTDUPL_DESC(Structure):  # noqa: N801 - DXGI_OUTDUPL_DESC (its DXGI_MODE_DESC flattened)
    _fields_ = [("Width", c_uint), ("Height", c_uint), ("RefreshNumerator", c_uint), ("RefreshDenominator", c_uint),
                ("Format", c_uint), ("ScanlineOrdering", c_uint), ("Scaling", c_uint), ("Rotation", c_uint),
                ("DesktopImageInSystemMemory", c_long)]


class _FRAME_INFO(Structure):  # noqa: N801 - DXGI_OUTDUPL_FRAME_INFO
    _fields_ = [("LastPresentTime", c_longlong), ("LastMouseUpdateTime", c_longlong), ("AccumulatedFrames", c_uint),
                ("RectsCoalesced", c_long), ("ProtectedContentMaskedOut", c_long), ("PointerX", c_long),
                ("PointerY", c_long), ("PointerVisible", c_long), ("TotalMetadataBufferSize", c_uint),
                ("PointerShapeBufferSize", c_uint)]


class _TEXTURE2D_DESC(Structure):  # noqa: N801 - D3D11_TEXTURE2D_DESC
    _fields_ = [(name, c_uint) for name in ("Width", "Height", "MipLevels", "ArraySize", "Format", "SampleCount",
                                            "SampleQuality", "Usage", "BindFlags", "CPUAccessFlags", "MiscFlags")]


class _BOX(Structure):  # D3D11_BOX
    _fields_ = [(name, c_uint) for name in ("left", "top", "front", "right", "bottom", "back")]


class _MAPPED(Structure):  # D3D11_MAPPED_SUBRESOURCE
    _fields_ = [("pData", c_void_p), ("RowPitch", c_uint), ("DepthPitch", c_uint)]


@dataclass(frozen=True)
class Monitor:
    """A monitor's place on the desktop (physical pixels) and how far it's turned, clockwise."""
    left: int
    top: int
    right: int
    bottom: int
    rotation: int = 0
    name: str = ""

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


@dataclass(frozen=True)
class Acquired:
    """One frame from Windows: whether the area was copied, when the image was shown
    (time.perf_counter() seconds, None if only the pointer moved) and whether DRM blacked part out."""
    copied: bool
    present_time: float | None
    protected: bool


def degrees(rotation: int) -> int:
    """DXGI_MODE_ROTATION as degrees clockwise."""
    try:
        return _DEGREES[rotation]
    except KeyError:
        raise ValueError(f"unknown monitor rotation {rotation}") from None


def monitor_for(area: Area, monitors: list[Monitor]) -> int:
    """Which of the monitors holds the whole area; AreaError says why none does."""
    x, y, width, height = area
    if width <= 0 or height <= 0:
        raise AreaError(f"The area to record is empty ({width}x{height})")
    home = next((i for i, monitor in enumerate(monitors) if monitor.contains(x, y)), None)
    if home is None:
        raise AreaError(f"The area to record at {x},{y} isn't on any monitor")
    monitor = monitors[home]
    if x + width <= monitor.right and y + height <= monitor.bottom:
        return home
    if any(other.left < x + width and x < other.right and other.top < y + height and y < other.bottom
           for i, other in enumerate(monitors) if i != home):
        raise AreaError("The area to record spans two monitors; a recording can only show one")
    raise AreaError(f"The area to record goes past the edge of its monitor ({monitor.width}x{monitor.height})")


def texture_box(area: Area, monitor: Monitor) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the area in the duplicated image, which is in the monitor's own,
    unrotated orientation (the mapping in Microsoft's Desktop Duplication sample)."""
    x, y, width, height = area
    left, top = x - monitor.left, y - monitor.top
    right, bottom = left + width, top + height
    w, h = monitor.width, monitor.height
    return {0: (left, top, right, bottom), 90: (top, w - right, bottom, w - left),
            180: (w - right, h - bottom, w - left, h - top), 270: (h - bottom, left, h - top, right)}[monitor.rotation]


def upright(data: bytes, stride: int, width: int, height: int, rotation: int) -> tuple[bytes, int]:
    """The copied area (width x height, in the monitor's own orientation) turned the way it's seen."""
    if rotation == 0:
        return data, stride
    from PySide6.QtGui import QImage, QTransform
    image = QImage(data, width, height, stride, QImage.Format_RGB32)
    turned = image.transformed(QTransform().rotate(rotation))
    return bytes(turned.constBits()), turned.bytesPerLine()


class Session(Protocol):
    """One opened duplication of the area (the real one is _Duplication; tests pass fakes)."""
    rotation: int
    copy_size: tuple[int, int]

    def acquire(self, timeout_ms: int, *, copy_always: bool) -> Acquired | None: ...
    def read(self) -> tuple[bytes, int]: ...
    def close(self) -> None: ...


class AreaDuplicator:
    """Copies the area (x, y, width, height), in physical desktop pixels on one monitor.

    grab() hands over BGRA pixels whenever the screen changed; otherwise the caller repeats its last
    frame. Use it from one thread, and close() it (or use `with`) to release the graphics objects.
    """

    def __init__(self, x: int, y: int, width: int, height: int, *, opener: Callable[[Area], Session] | None = None):
        self.area: Area = (x, y, width, height)
        self.width, self.height = width, height
        self.reopens = 0  # how often Windows took the duplication away and it was opened again
        self.protected_content = False  # the last frame had DRM-protected video blacked out
        self.frame_time: float | None = None  # when the last copied image was shown, perf_counter() seconds
        self.last_error: Exception | None = None  # why the duplication can't be reopened right now
        self._opener = opener or _Duplication
        self._closed = False
        self._fresh = True  # nothing copied since (re)opening: copy the next frame, changed or not
        self._session: Session | None = self._opener(self.area)

    @property
    def lost(self) -> bool:
        """Windows has taken the duplication away and hasn't let it be reopened yet."""
        return self._session is None and not self._closed

    def grab(self, timeout_ms: int) -> tuple[bytes, int] | None:
        """(BGRA pixels, bytes per row) of the area if the screen changed within the timeout, else
        None. Rows are `width` pixels of 4 bytes, then padding up to the row length."""
        if self._closed:
            raise ValueError("the duplicator is closed")
        if self._session is None and not self._reopen():
            time.sleep(timeout_ms / 1000)  # wait as AcquireNextFrame would, rather than spin
            return None
        session = self._session
        try:
            got = session.acquire(timeout_ms, copy_always=self._fresh)
            if got is None:
                return None
            self.protected_content = got.protected
            if not got.copied:
                return None  # only the pointer moved
            data, stride = session.read()
        except AccessLost as exc:
            log.info("Windows took the screen away (%s); opening it again", exc)
            self._drop()
            self._reopen()
            return None
        self._fresh = False
        if got.present_time is not None:
            self.frame_time = got.present_time
        if session.rotation:
            data, stride = upright(data, stride, *session.copy_size, session.rotation)
        return data, stride

    def _reopen(self) -> bool:
        try:
            self._session = self._opener(self.area)
        except (OSError, ValueError) as exc:  # COM errors, DuplicationUnavailable, AreaError
            if str(exc) != str(self.last_error):
                log.warning("Can't record the screen right now: %s", exc)
            self.last_error = exc
            return False
        self.reopens += 1
        self.last_error = None
        self._fresh = True
        return True

    def _drop(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except OSError as exc:
                log.warning("Couldn't release the screen duplication: %s", exc)

    def close(self) -> None:
        self._drop()
        self._closed = True

    def __enter__(self) -> AreaDuplicator:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def monitors() -> list[Monitor]:
    """The monitors attached to the desktop, on every graphics card."""
    found = _outputs()
    for adapter, output, _monitor in found:
        release(output)
        release(adapter)
    return [monitor for _adapter, _output, monitor in found]


class _Duplication:
    """The graphics objects for one opened duplication of the area: a Direct3D device on the
    monitor's own graphics card, the duplication, and a small texture the area is copied into."""

    def __init__(self, area: Area) -> None:
        self._device = self._context = self._dup = self._staging = None
        try:
            self._open(area)
        except BaseException:
            self.close()
            raise

    def _open(self, area: Area) -> None:
        adapter, output, monitor = _find_output(area)
        try:
            self.rotation = monitor.rotation
            left, top, right, bottom = texture_box(area, monitor)
            self._box = _BOX(left, top, 0, right, bottom, 1)
            self.copy_size = (right - left, bottom - top)
            self._device, self._context = c_void_p(), c_void_p()
            level = c_uint()
            _unavailable_if(_d3d11().D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, None, 0, None, 0,
                                                       D3D11_SDK_VERSION, byref(self._device), byref(level),
                                                       byref(self._context)), "D3D11CreateDevice")
            try:
                output5 = query(output, IID_IDXGIOutput5, "IDXGIOutput5")
            except ComError as exc:
                if exc.hresult in _UNAVAILABLE:
                    raise DuplicationUnavailable(f"{_UNAVAILABLE[exc.hresult]} ({exc})") from exc
                raise
            try:
                formats = (c_uint * 1)(DXGI_FORMAT_B8G8R8A8_UNORM)  # 8-bit colour, also from an HDR screen
                self._dup = c_void_p()
                _unavailable_if(vcall(output5, _OUTPUT5_DUPLICATE_OUTPUT1, c_long, self._device, 0, 1, formats,
                                      byref(self._dup), argtypes=(c_void_p, c_uint, c_uint, c_void_p, c_void_p)),
                                "DuplicateOutput1")
            finally:
                release(output5)
            self._check_image_size(monitor)
            desc = _TEXTURE2D_DESC(*self.copy_size, 1, 1, DXGI_FORMAT_B8G8R8A8_UNORM, 1, 0, D3D11_USAGE_STAGING, 0,
                                   D3D11_CPU_ACCESS_READ, 0)
            self._staging = c_void_p()
            check(vcall(self._device, _DEVICE_CREATE_TEXTURE2D, c_long, byref(desc), None, byref(self._staging),
                        argtypes=(c_void_p, c_void_p, c_void_p)), "CreateTexture2D")
        finally:
            release(output)
            release(adapter)

    def _check_image_size(self, monitor: Monitor) -> None:
        """The duplicated image must be the monitor's size (turned to its own orientation), or the
        copy would silently miss: Direct3D drops copies from outside a texture."""
        desc = _OUTDUPL_DESC()
        vcall(self._dup, _DUPLICATION_GET_DESC, None, byref(desc), argtypes=(c_void_p,))
        expected = (monitor.width, monitor.height) if monitor.rotation in (0, 180) else (monitor.height,
                                                                                         monitor.width)
        if (desc.Width, desc.Height) != expected:
            raise DuplicationUnavailable(f"the screen's image is {desc.Width}x{desc.Height}, not the monitor's "
                                         f"{expected[0]}x{expected[1]} (is its resolution changing?)")

    def acquire(self, timeout_ms: int, *, copy_always: bool) -> Acquired | None:
        info, resource = _FRAME_INFO(), c_void_p()
        hr = vcall(self._dup, _DUPLICATION_ACQUIRE_NEXT_FRAME, c_long, timeout_ms, byref(info), byref(resource),
                   argtypes=(c_uint, c_void_p, c_void_p))
        if hr & _MASK == DXGI_ERROR_WAIT_TIMEOUT:
            return None
        _raise_if_lost(hr, "AcquireNextFrame")
        changed = info.LastPresentTime != 0  # 0: only the pointer moved
        copied = changed or copy_always
        try:
            if copied:
                texture = query(resource, IID_ID3D11Texture2D, "ID3D11Texture2D")
                try:
                    vcall(self._context, _CONTEXT_COPY_SUBRESOURCE_REGION, None, self._staging, 0, 0, 0, 0, texture,
                          0, byref(self._box),
                          argtypes=(c_void_p, c_uint, c_uint, c_uint, c_uint, c_void_p, c_uint, c_void_p))
                finally:
                    release(texture)
        finally:
            release(resource)
            released = vcall(self._dup, _DUPLICATION_RELEASE_FRAME, c_long)
        _raise_if_lost(released, "ReleaseFrame")
        present = info.LastPresentTime / _qpc_frequency() if changed else None
        return Acquired(copied, present, bool(info.ProtectedContentMaskedOut))

    def read(self) -> tuple[bytes, int]:
        mapped = _MAPPED()
        _raise_if_lost(vcall(self._context, _CONTEXT_MAP, c_long, self._staging, 0, D3D11_MAP_READ, 0, byref(mapped),
                             argtypes=(c_void_p, c_uint, c_uint, c_uint, c_void_p)), "Map")
        try:
            return ctypes.string_at(mapped.pData, mapped.RowPitch * self.copy_size[1]), mapped.RowPitch
        finally:
            vcall(self._context, _CONTEXT_UNMAP, None, self._staging, 0, argtypes=(c_void_p, c_uint))

    def close(self) -> None:
        for name in ("_staging", "_dup", "_context", "_device"):
            release(getattr(self, name))
            setattr(self, name, None)


def _raise_if_lost(hr: int, what: str) -> None:
    if hr & _MASK in _LOST:
        raise AccessLost(f"{what}: HRESULT 0x{hr & _MASK:08X}")
    check(hr, what)


def _unavailable_if(hr: int, what: str) -> None:
    """check(), with the failures that mean "Windows won't duplicate the screen here" explained."""
    reason = _UNAVAILABLE.get(hr & _MASK)
    if reason is not None:
        raise DuplicationUnavailable(f"{reason} ({what}: HRESULT 0x{hr & _MASK:08X})")
    check(hr, what)


def _find_output(area: Area) -> tuple[c_void_p, c_void_p, Monitor]:
    """(graphics card, monitor, its place) for the monitor holding the area; the caller releases both."""
    found = _outputs()
    chosen = None
    try:
        if not found:
            raise DuplicationUnavailable("no monitor is attached to the desktop")
        chosen = monitor_for(area, [monitor for _adapter, _output, monitor in found])
    finally:
        for i, (adapter, output, _monitor) in enumerate(found):
            if i != chosen:
                release(output)
                release(adapter)
    return found[chosen]


def _outputs() -> list[tuple[c_void_p, c_void_p, Monitor]]:
    """(graphics card, monitor, its place) for every monitor attached to the desktop. Each entry holds
    its own reference to both, which the caller releases."""
    found = []
    factory = c_void_p()
    check(_dxgi().CreateDXGIFactory1(byref(IID_IDXGIFactory1), byref(factory)), "CreateDXGIFactory1")
    try:
        for a in count():
            adapter = c_void_p()
            hr = vcall(factory, _FACTORY1_ENUM_ADAPTERS1, c_long, a, byref(adapter), argtypes=(c_uint, c_void_p))
            if hr & _MASK == DXGI_ERROR_NOT_FOUND:
                break
            check(hr, "EnumAdapters1")
            try:
                found.extend(_adapter_outputs(adapter))
            finally:
                release(adapter)
    except BaseException:
        for adapter, output, _monitor in found:
            release(output)
            release(adapter)
        raise
    finally:
        release(factory)
    return found


def _adapter_outputs(adapter: c_void_p) -> list[tuple[c_void_p, c_void_p, Monitor]]:
    found = []
    try:
        for o in count():
            output = c_void_p()
            hr = vcall(adapter, _ADAPTER_ENUM_OUTPUTS, c_long, o, byref(output), argtypes=(c_uint, c_void_p))
            if hr & _MASK == DXGI_ERROR_NOT_FOUND:
                break  # a graphics card with no monitors (the fast one, on many laptops) stops at once
            check(hr, "EnumOutputs")
            desc = _OUTPUT_DESC()
            hr = vcall(output, _OUTPUT_GET_DESC, c_long, byref(desc), argtypes=(c_void_p,))
            if hr < 0 or not desc.AttachedToDesktop:
                release(output)
                continue
            r = desc.DesktopCoordinates
            vcall(adapter, _ADD_REF, c_uint)  # the entry's own reference to its graphics card
            found.append((adapter, output, Monitor(r.left, r.top, r.right, r.bottom, degrees(desc.Rotation),
                                                   desc.DeviceName)))
    except BaseException:
        for adapter_ref, output, _monitor in found:
            release(output)
            release(adapter_ref)
        raise
    return found


@cache
def _dxgi():
    dll = ctypes.WinDLL("dxgi")
    dll.CreateDXGIFactory1.argtypes = [c_void_p, POINTER(c_void_p)]
    dll.CreateDXGIFactory1.restype = c_long
    return dll


@cache
def _d3d11():
    dll = ctypes.WinDLL("d3d11")
    dll.D3D11CreateDevice.argtypes = [c_void_p, c_uint, c_void_p, c_uint, c_void_p, c_uint, c_uint,
                                      POINTER(c_void_p), POINTER(c_uint), POINTER(c_void_p)]
    dll.D3D11CreateDevice.restype = c_long
    return dll


@cache
def _qpc_frequency() -> int:
    """QueryPerformanceCounter ticks per second: DXGI's frame times are in them, as is perf_counter()."""
    frequency = c_longlong()
    if not ctypes.WinDLL("kernel32").QueryPerformanceFrequency(byref(frequency)):
        raise ctypes.WinError()
    return frequency.value
