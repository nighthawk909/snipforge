"""Spike: trim an MP4 without re-encoding, using the FFmpeg libraries Qt Multimedia already ships.

    python prototypes/video/trim_spike.py in.mp4 out.mp4 START END   # seconds

Stream copy from the keyframe before START; packets before START get negative timestamps, which the
MP4 muxer turns into an edit list, so players start exactly at START (to the frame) without any
re-encoding. Takes milliseconds and loses no quality.

Talks to avformat/avcodec through ctypes. Only a few struct fields are read (offsets below), and
they are checked against known values after opening the file; a PySide6 update that changes FFmpeg's
major version must re-run tests/test_video_trim.py (in the real feature) before release.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import POINTER, Structure, byref, c_char_p, c_int, c_int64, c_uint, c_void_p

import PySide6

QT = os.path.dirname(PySide6.__file__)
os.add_dll_directory(QT)
avutil = ctypes.CDLL(os.path.join(QT, "avutil-61.dll"))
avcodec = ctypes.CDLL(os.path.join(QT, "avcodec-63.dll"))
avformat = ctypes.CDLL(os.path.join(QT, "avformat-63.dll"))

AV_TIME_BASE = 1_000_000
AVSEEK_FLAG_BACKWARD = 1
AVIO_FLAG_WRITE = 2
AVERROR_EOF = -0x20464F45  # FFERRTAG('E','O','F',' ')

# Offsets (bytes, 64-bit) of the few public fields used; stable within a major version.
FMT_PB, FMT_NB_STREAMS, FMT_STREAMS, FMT_URL = 32, 44, 48, 88
AV_NOPTS_VALUE = -0x8000000000000000
ST_INDEX, ST_CODECPAR, ST_TIME_BASE, ST_START_TIME = 8, 16, 32, 40
PAR_CODEC_TYPE, PAR_CODEC_TAG = 0, 8
AVMEDIA_TYPE_VIDEO, AVMEDIA_TYPE_AUDIO = 0, 1
AUDIO_PREROLL = 0.05  # seconds of audio kept before the cut, for the decoder to settle


class AVRational(Structure):
    _fields_ = [("num", c_int), ("den", c_int)]


class AVPacket(Structure):  # the head of the struct; allocated by av_packet_alloc, never by us
    _fields_ = [("buf", c_void_p), ("pts", c_int64), ("dts", c_int64), ("data", c_void_p), ("size", c_int),
                ("stream_index", c_int), ("flags", c_int), ("side_data", c_void_p), ("side_data_elems", c_int),
                ("duration", c_int64), ("pos", c_int64)]


for name, res, args in [
    ("avformat_open_input", c_int, [POINTER(c_void_p), c_char_p, c_void_p, c_void_p]),
    ("avformat_find_stream_info", c_int, [c_void_p, c_void_p]),
    ("avformat_alloc_output_context2", c_int, [POINTER(c_void_p), c_void_p, c_char_p, c_char_p]),
    ("avformat_new_stream", c_void_p, [c_void_p, c_void_p]),
    ("avio_open", c_int, [c_void_p, c_char_p, c_int]),
    ("avio_closep", c_int, [c_void_p]),
    ("avformat_write_header", c_int, [c_void_p, c_void_p]),
    ("av_read_frame", c_int, [c_void_p, c_void_p]),
    ("av_interleaved_write_frame", c_int, [c_void_p, c_void_p]),
    ("av_write_trailer", c_int, [c_void_p]),
    ("av_seek_frame", c_int, [c_void_p, c_int, c_int64, c_int]),
    ("avformat_close_input", None, [POINTER(c_void_p)]),
    ("avformat_free_context", None, [c_void_p]),
]:
    fn = getattr(avformat, name)
    fn.restype, fn.argtypes = res, args
for name, res, args in [
    ("avcodec_parameters_copy", c_int, [c_void_p, c_void_p]),
    ("av_packet_alloc", POINTER(AVPacket), []),
    ("av_packet_free", None, [c_void_p]),
    ("av_packet_unref", None, [c_void_p]),
    ("av_packet_rescale_ts", None, [c_void_p, AVRational, AVRational]),
]:
    fn = getattr(avcodec, name)
    fn.restype, fn.argtypes = res, args
avutil.av_strerror.argtypes = [c_int, c_char_p, ctypes.c_size_t]


def _check(code: int, what: str) -> int:
    if code < 0:
        msg = ctypes.create_string_buffer(256)
        avutil.av_strerror(code, msg, 256)
        raise OSError(f"{what}: {msg.value.decode(errors='replace')}")
    return code


def _field(base: int, offset: int, ctype):
    return ctype.from_address(base + offset)


def trim(src: str, dst: str, start: float, end: float) -> dict:
    ictx = c_void_p()
    _check(avformat.avformat_open_input(byref(ictx), src.encode(), None, None), "open")
    octx = c_void_p()
    pkt = avcodec.av_packet_alloc()
    try:
        _check(avformat.avformat_find_stream_info(ictx, None), "stream info")
        base = ictx.value
        url = ctypes.cast(_field(base, FMT_URL, c_void_p).value, c_char_p).value
        assert url == src.encode(), "AVFormatContext layout differs from this FFmpeg build"
        count = _field(base, FMT_NB_STREAMS, c_uint).value
        streams = ctypes.cast(_field(base, FMT_STREAMS, c_void_p).value, POINTER(c_void_p))
        kinds = [_field(_field(streams[i], ST_CODECPAR, c_void_p).value, PAR_CODEC_TYPE, c_int).value
                 for i in range(count)]
        # Time zero is the first video frame. Qt 6.12's Matroska output starts the streams at ~1 s, with
        # one stray AAC priming packet at 0 and every audio packet labelled 1.024 s long (it doesn't
        # rescale packet durations to Matroska's millisecond clock); both are corrected here.
        origin = 0.0
        for i, kind in enumerate(kinds):
            first = _field(streams[i], ST_START_TIME, c_int64).value
            if kind == AVMEDIA_TYPE_VIDEO and first != AV_NOPTS_VALUE:
                tb = AVRational.from_address(streams[i] + ST_TIME_BASE)
                origin = first * tb.num / tb.den
                break
        start, end = start + origin, end + origin
        _check(avformat.avformat_alloc_output_context2(byref(octx), None, b"mp4", dst.encode()), "output")
        in_tb, out_streams = [], []
        for i in range(count):
            ist = streams[i]
            assert _field(ist, ST_INDEX, c_int).value == i, "AVStream layout differs from this FFmpeg build"
            ost = avformat.avformat_new_stream(octx, None)
            in_par, out_par = _field(ist, ST_CODECPAR, c_void_p).value, _field(ost, ST_CODECPAR, c_void_p).value
            _check(avcodec.avcodec_parameters_copy(out_par, in_par), "copy parameters")
            _field(out_par, PAR_CODEC_TAG, c_uint).value = 0
            tb = AVRational.from_address(ist + ST_TIME_BASE)
            out = AVRational.from_address(ost + ST_TIME_BASE)
            out.num, out.den = tb.num, tb.den
            in_tb.append(AVRational(tb.num, tb.den))
            out_streams.append(ost)
        _check(avformat.avio_open(octx.value + FMT_PB, dst.encode(), AVIO_FLAG_WRITE), "create file")
        _check(avformat.avformat_write_header(octx, None), "header")
        out_tb = [AVRational(r.num, r.den) for r in (AVRational.from_address(s + ST_TIME_BASE) for s in out_streams)]
        if start > origin + 0.001:  # copying from the start needs no seek (and Qt's MKVs may have no index)
            _check(avformat.av_seek_frame(ictx, -1, int(start * AV_TIME_BASE), AVSEEK_FLAG_BACKWARD), "seek")
        written = [0] * count
        done = [False] * count
        while not all(done):
            code = avformat.av_read_frame(ictx, pkt)
            if code == AVERROR_EOF:
                break
            _check(code, "read")
            p = pkt.contents
            i = p.stream_index
            tb = in_tb[i]
            t = p.pts * tb.num / tb.den
            if t >= end:
                done[i] = True
                avcodec.av_packet_unref(pkt)
                continue
            if kinds[i] == AVMEDIA_TYPE_AUDIO:
                if t < start - AUDIO_PREROLL:  # audio needs no keyframe run-up; drops the stray packet too
                    avcodec.av_packet_unref(pkt)
                    continue
                p.duration = 0  # let the muxer work it out from the next packet
            shift = round(start * tb.den / tb.num)
            p.pts -= shift
            p.dts -= shift  # negative before START: the muxer writes an edit list
            p.pos = -1
            avcodec.av_packet_rescale_ts(pkt, tb, out_tb[i])
            _check(avformat.av_interleaved_write_frame(octx, pkt), "write")
            written[i] += 1
        _check(avformat.av_write_trailer(octx), "trailer")
        return {"packets": written}
    finally:
        if octx:
            avformat.avio_closep(octx.value + FMT_PB)
            avformat.avformat_free_context(octx)
        avcodec.av_packet_free(byref(ctypes.cast(pkt, c_void_p)))
        avformat.avformat_close_input(byref(ictx))


if __name__ == "__main__":
    import time
    t0 = time.perf_counter()
    info = trim(sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]))
    print(f"Trimmed in {1000 * (time.perf_counter() - t0):.0f} ms: {info}")
