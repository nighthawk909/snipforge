"""Just enough COM through ctypes to call Windows' audio (WASAPI) and screen (DXGI) interfaces.

pywin32 can only call COM interfaces that support IDispatch, which these don't, so their methods are
called through the object's table of function pointers, by position. No new dependency.
"""
from __future__ import annotations

import ctypes
import uuid
from ctypes import POINTER, Structure, c_long, c_ubyte, c_uint, c_ulong, c_ushort, c_void_p
from functools import cache

_FUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)  # COM is __stdcall (the same as cdecl on x64)


class ComError(OSError):
    """A COM call failed; `hresult` is its error code as an unsigned 32-bit value."""

    def __init__(self, what: str, hresult: int) -> None:
        self.hresult = hresult & 0xFFFFFFFF
        super().__init__(f"{what} failed (HRESULT 0x{self.hresult:08X})")


class GUID(Structure):
    _fields_ = [("d1", c_ulong), ("d2", c_ushort), ("d3", c_ushort), ("d4", c_ubyte * 8)]

    def __init__(self, text: str) -> None:
        b = uuid.UUID(text).bytes
        super().__init__(int.from_bytes(b[0:4], "big"), int.from_bytes(b[4:6], "big"),
                         int.from_bytes(b[6:8], "big"), (c_ubyte * 8)(*b[8:]))

    def __str__(self) -> str:
        tail = bytes(self.d4)
        return str(uuid.UUID(bytes=self.d1.to_bytes(4, "big") + self.d2.to_bytes(2, "big")
                             + self.d3.to_bytes(2, "big") + tail))


@cache
def _prototype(restype, argtypes: tuple):
    return _FUNCTYPE(restype, c_void_p, *argtypes)


def vcall(obj: c_void_p, index: int, restype, *args, argtypes: tuple = ()):
    """Call method `index` of a COM interface pointer. With restype c_long the HRESULT comes back
    as a number, unchecked (ctypes' own HRESULT type raises, hiding codes such as timeouts)."""
    table = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    return _prototype(restype, tuple(argtypes))(table[index])(obj, *args)


def check(hr: int, what: str) -> int:
    """Raise ComError for a failed HRESULT; return it otherwise (S_OK, S_FALSE and so on)."""
    if hr < 0:
        raise ComError(what, hr)
    return hr


def release(obj: c_void_p | None) -> None:
    """IUnknown::Release, for a pointer that may be empty."""
    if obj:
        vcall(obj, 2, c_uint)


def query(obj: c_void_p, iid: GUID, what: str) -> c_void_p:
    """IUnknown::QueryInterface; the caller releases the result."""
    out = c_void_p()
    check(vcall(obj, 0, c_long, ctypes.byref(iid), ctypes.byref(out), argtypes=(c_void_p, c_void_p)), what)
    return out
