"""Screen frames through DXGI Desktop Duplication, copying only the recorded area off the GPU.

Qt's QScreenCapture reads back the whole monitor every frame (20-29 fps on a 3440x1440 screen in
our tests); copying just the area on the GPU first reached the screen's own update rate. Windows 8+
API, no dependency, works without admin, and never shows a yellow capture border.

Handles the parts that bite in practice:
- the monitor can hang off either GPU of a hybrid laptop, so the right adapter is looked up;
- Qt makes the process per-monitor-DPI aware (v2), where only IDXGIOutput5::DuplicateOutput1 works;
- Windows drops the duplication on a desktop switch (UAC prompt, lock screen, mode change), so it
  is reopened (ACCESS_LOST).
Windows excluded with SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE), such as the recording
controls, are left out of the frames by Windows itself.
"""
from __future__ import annotations

import ctypes
from ctypes import Structure, byref, c_long, c_uint, c_void_p, c_wchar
from ctypes import wintypes as w

from com import GUID, check, query, release, vcall

IID_IDXGIFactory1 = GUID("770aae78-f26f-4dba-a829-253c83d1b387")
IID_IDXGIOutput5 = GUID("80a07424-ab52-42eb-833c-0c42fd282d98")
IID_ID3D11Texture2D = GUID("6f15aaf2-d208-4e89-9ab4-489535d34f9c")

DXGI_FORMAT_B8G8R8A8_UNORM = 87
D3D11_USAGE_STAGING = 3
D3D11_CPU_ACCESS_READ = 0x20000
D3D11_MAP_READ = 1
D3D11_SDK_VERSION = 7
DXGI_ERROR_WAIT_TIMEOUT = 0x887A0027
DXGI_ERROR_ACCESS_LOST = 0x887A0026
DXGI_ERROR_NOT_FOUND = 0x887A0002
DXGI_MODE_ROTATION_IDENTITY = 1


class OUTPUT_DESC(Structure):  # noqa: N801 - Windows name
    _fields_ = [("DeviceName", c_wchar * 32), ("DesktopCoordinates", w.RECT), ("AttachedToDesktop", w.BOOL),
                ("Rotation", c_uint), ("Monitor", c_void_p)]


class TEX2D_DESC(Structure):  # noqa: N801
    _fields_ = [("Width", c_uint), ("Height", c_uint), ("MipLevels", c_uint), ("ArraySize", c_uint),
                ("Format", c_uint), ("SampleCount", c_uint), ("SampleQuality", c_uint), ("Usage", c_uint),
                ("BindFlags", c_uint), ("CPUAccessFlags", c_uint), ("MiscFlags", c_uint)]


class BOX(Structure):
    _fields_ = [(n, c_uint) for n in ("left", "top", "front", "right", "bottom", "back")]


class MAPPED(Structure):
    _fields_ = [("pData", c_void_p), ("RowPitch", c_uint), ("DepthPitch", c_uint)]


class FRAME_INFO(Structure):  # noqa: N801
    _fields_ = [("LastPresentTime", ctypes.c_longlong), ("LastMouseUpdateTime", ctypes.c_longlong),
                ("AccumulatedFrames", c_uint), ("RectsCoalesced", w.BOOL), ("ProtectedContentMaskedOut", w.BOOL),
                ("PointerX", c_long), ("PointerY", c_long), ("PointerVisible", w.BOOL),
                ("TotalMetadataBufferSize", c_uint), ("PointerShapeBufferSize", c_uint)]


def _find_output(x: int, y: int):
    """The (adapter, output, desc) whose desktop area contains the point; caller releases both."""
    factory = c_void_p()
    check(ctypes.windll.dxgi.CreateDXGIFactory1(byref(IID_IDXGIFactory1), byref(factory)), "CreateDXGIFactory1")
    try:
        a = 0
        while True:
            adapter = c_void_p()
            hr = vcall(factory, 12, c_long, a, byref(adapter), argtypes=(c_uint, c_void_p))  # EnumAdapters1
            if hr & 0xFFFFFFFF == DXGI_ERROR_NOT_FOUND:
                break
            check(hr, "EnumAdapters1")
            o = 0
            while True:
                output = c_void_p()
                hr = vcall(adapter, 7, c_long, o, byref(output), argtypes=(c_uint, c_void_p))  # EnumOutputs
                if hr & 0xFFFFFFFF == DXGI_ERROR_NOT_FOUND:
                    break
                check(hr, "EnumOutputs")
                desc = OUTPUT_DESC()
                check(vcall(output, 7, c_long, byref(desc), argtypes=(c_void_p,)), "GetDesc")
                r = desc.DesktopCoordinates
                if desc.AttachedToDesktop and r.left <= x < r.right and r.top <= y < r.bottom:
                    return adapter, output, desc
                release(output)
                o += 1
            release(adapter)
            a += 1
    finally:
        release(factory)
    raise OSError(f"No monitor contains the point {x},{y}")


class AreaDuplicator:
    """Copies the area (x, y, width, height), in physical desktop pixels on one monitor."""

    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self.x, self.y, self.width, self.height = x, y, width, height
        self.dev = self.ctx = self.dup = self.staging = None
        self.reopened = 0
        self._open()

    def _open(self) -> None:
        adapter, output, desc = _find_output(self.x, self.y)
        try:
            r = desc.DesktopCoordinates
            if desc.Rotation not in (0, DXGI_MODE_ROTATION_IDENTITY):
                raise OSError("Rotated monitors aren't handled by the prototype")
            if self.x + self.width > r.right or self.y + self.height > r.bottom:
                raise OSError("The area must lie on one monitor")
            self.dev, self.ctx = c_void_p(), c_void_p()
            level = c_uint()
            check(ctypes.windll.d3d11.D3D11CreateDevice(adapter, 0, None, 0, None, 0, D3D11_SDK_VERSION,
                                                        byref(self.dev), byref(level), byref(self.ctx)),
                  "D3D11CreateDevice")
            output5 = query(output, IID_IDXGIOutput5, "IDXGIOutput5")
            try:
                formats = (c_uint * 1)(DXGI_FORMAT_B8G8R8A8_UNORM)
                self.dup = c_void_p()
                check(vcall(output5, 26, c_long, self.dev, 0, 1, formats, byref(self.dup),
                            argtypes=(c_void_p, c_uint, c_uint, c_void_p, c_void_p)), "DuplicateOutput1")
            finally:
                release(output5)
            desc2d = TEX2D_DESC(self.width, self.height, 1, 1, DXGI_FORMAT_B8G8R8A8_UNORM, 1, 0,
                                D3D11_USAGE_STAGING, 0, D3D11_CPU_ACCESS_READ, 0)
            self.staging = c_void_p()
            check(vcall(self.dev, 5, c_long, byref(desc2d), None, byref(self.staging),
                        argtypes=(c_void_p, c_void_p, c_void_p)), "CreateTexture2D")
            left, top = self.x - r.left, self.y - r.top
            self.box = BOX(left, top, 0, left + self.width, top + self.height, 1)
        finally:
            release(output)
            release(adapter)

    def grab(self, timeout_ms: int) -> tuple[bytes, int] | None:
        """(BGRA pixels, bytes per row) when the screen changed within the timeout, else None."""
        info = FRAME_INFO()
        resource = c_void_p()
        hr = vcall(self.dup, 8, c_long, timeout_ms, byref(info), byref(resource),
                   argtypes=(c_uint, c_void_p, c_void_p))  # AcquireNextFrame
        code = hr & 0xFFFFFFFF
        if code == DXGI_ERROR_WAIT_TIMEOUT:
            return None
        if code == DXGI_ERROR_ACCESS_LOST:
            self.close()
            self._open()
            self.reopened += 1
            return None
        check(hr, "AcquireNextFrame")
        try:
            texture = query(resource, IID_ID3D11Texture2D, "ID3D11Texture2D")
            vcall(self.ctx, 46, None, self.staging, 0, 0, 0, 0, texture, 0, byref(self.box),
                  argtypes=(c_void_p, c_uint, c_uint, c_uint, c_uint, c_void_p, c_uint, c_void_p))  # CopySubresource
            release(texture)
        finally:
            release(resource)
            vcall(self.dup, 14, c_long)  # ReleaseFrame
        if info.LastPresentTime == 0 and info.AccumulatedFrames == 0:
            return None  # only the pointer moved; the image didn't change
        mapped = MAPPED()
        check(vcall(self.ctx, 14, c_long, self.staging, 0, D3D11_MAP_READ, 0, byref(mapped),
                    argtypes=(c_void_p, c_uint, c_uint, c_uint, c_void_p)), "Map")
        try:
            return ctypes.string_at(mapped.pData, mapped.RowPitch * self.height), mapped.RowPitch
        finally:
            vcall(self.ctx, 15, None, self.staging, 0, argtypes=(c_void_p, c_uint))  # Unmap

    def close(self) -> None:
        for name in ("staging", "dup", "ctx", "dev"):
            release(getattr(self, name))
            setattr(self, name, None)
