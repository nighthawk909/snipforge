"""snipforge/video/com.py: the COM-through-ctypes helpers recording's audio and screen code use."""
import ctypes
import sys

import pytest

from snipforge.video import com


def test_a_guid_keeps_its_value():
    guid = com.GUID("6f15aaf2-d208-4e89-9ab4-489535d34f9c")

    assert (guid.d1, guid.d2, guid.d3) == (0x6F15AAF2, 0xD208, 0x4E89)
    assert bytes(guid.d4) == bytes.fromhex("9ab4489535d34f9c")
    assert str(guid) == "6f15aaf2-d208-4e89-9ab4-489535d34f9c"
    assert ctypes.sizeof(guid) == 16


def test_failed_calls_raise_with_their_code():
    assert com.check(0, "Fine") == 0
    assert com.check(1, "S_FALSE is a success") == 1
    with pytest.raises(com.ComError, match="Initialize failed \\(HRESULT 0x80070057\\)") as raised:
        com.check(-0x7FF8FFA9, "Initialize")  # E_INVALIDARG as a signed number
    assert raised.value.hresult == 0x80070057


def test_releasing_nothing_does_nothing():
    com.release(None)
    com.release(ctypes.c_void_p())


@pytest.mark.skipif(sys.platform != "win32", reason="COM is Windows only")
def test_calls_reach_a_real_com_object():
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 0)
    enumerator = ctypes.c_void_p()
    com.check(ole32.CoCreateInstance(ctypes.byref(com.GUID("bcde0395-e52f-467c-8e3d-c4579291692e")), None, 0x17,
                                     ctypes.byref(com.GUID("a95664d2-9614-4f35-a746-de8db63617e6")),
                                     ctypes.byref(enumerator)), "CoCreateInstance")
    try:
        unknown = com.query(enumerator, com.GUID("00000000-0000-0000-c000-000000000046"), "QueryInterface")
        assert unknown
        com.release(unknown)
        with pytest.raises(com.ComError) as raised:  # an interface it doesn't have: E_NOINTERFACE
            com.query(enumerator, com.GUID("6f15aaf2-d208-4e89-9ab4-489535d34f9c"), "QueryInterface")
        assert raised.value.hresult == 0x80004002
    finally:
        com.release(enumerator)
