"""Just enough COM-through-ctypes for the prototype: GUIDs, vtable calls and HRESULT checks.

pywin32 can't call IAudioClient or the DXGI interfaces (they aren't IDispatch), so these call the
vtable directly. No new dependency; the final feature can keep this or move to comtypes (MIT).
"""
from __future__ import annotations

import ctypes
import uuid
from ctypes import POINTER, Structure, c_ubyte, c_uint, c_ulong, c_ushort, c_void_p


class GUID(Structure):
    _fields_ = [("d1", c_ulong), ("d2", c_ushort), ("d3", c_ushort), ("d4", c_ubyte * 8)]

    def __init__(self, text: str) -> None:
        b = uuid.UUID(text).bytes
        super().__init__(int.from_bytes(b[0:4], "big"), int.from_bytes(b[4:6], "big"),
                         int.from_bytes(b[6:8], "big"), (c_ubyte * 8)(*b[8:]))


def vcall(obj: c_void_p, index: int, restype, *args, argtypes=()):
    """Call method `index` of a COM interface pointer. `restype` c_long returns the HRESULT
    unchecked (ctypes' HRESULT type would raise, which hides the timeout codes we need)."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


def check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} failed (HRESULT 0x{hr & 0xFFFFFFFF:08X})")


def release(obj: c_void_p | None) -> None:
    if obj:
        vcall(obj, 2, c_uint)


def query(obj: c_void_p, iid: GUID, what: str) -> c_void_p:
    out = c_void_p()
    check(vcall(obj, 0, ctypes.c_long, ctypes.byref(iid), ctypes.byref(out), argtypes=(c_void_p, c_void_p)), what)
    return out
