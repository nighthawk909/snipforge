"""The FFmpeg libraries that Qt Multimedia ships, called directly (ctypes) for what Qt doesn't offer:
reading exactly what a video file contains, and later trimming and copying it without re-encoding.

They are Qt's own LGPL build, loaded from the PySide6 folder, so nothing extra is bundled
(THIRD_PARTY_NOTICES.md). Only the few struct fields used are declared below, copied from FFmpeg
9.0.1's headers; they only change between major versions, so any other version is refused
(FFmpegUnavailable) until it has been checked, rather than misread.

Every file is opened with FFmpeg's protocol whitelist set to "file": the libraries can speak http,
rtsp and more, and SnipForge never lets them go online.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import POINTER, Structure, byref, c_char_p, c_int, c_int64, c_uint, c_uint32, c_void_p
from dataclasses import dataclass
from functools import cache
from pathlib import Path

TESTED = {"avformat": 63, "avcodec": 63, "avutil": 61}  # FFmpeg 9.0, in Qt 6.12
WINDOWS_NAMES = {"avutil": "avutil-61.dll", "avcodec": "avcodec-63.dll", "avformat": "avformat-63.dll"}
AV_NOPTS_VALUE = -0x8000000000000000
AV_TIME_BASE = 1_000_000
AVMEDIA_TYPE_VIDEO, AVMEDIA_TYPE_AUDIO = 0, 1


class FFmpegError(RuntimeError):
    """FFmpeg couldn't do what was asked (a missing or unreadable file, for example)."""


class FFmpegUnavailable(FFmpegError):
    """The libraries aren't there, or are a version these declarations weren't checked against."""


# The leading fields of FFmpeg's structs, as declared in FFmpeg 9.0.1's headers. FFmpeg only adds
# fields at the end within a major version, so these prefixes stay valid; nothing here allocates one.
class AVRational(Structure):
    _fields_ = [("num", c_int), ("den", c_int)]


class AVChannelLayout(Structure):
    _fields_ = [("order", c_int), ("nb_channels", c_int), ("u", ctypes.c_uint64), ("opaque", c_void_p)]


class AVCodecParameters(Structure):
    _fields_ = [("codec_type", c_int), ("codec_id", c_int), ("codec_tag", c_uint32), ("extradata", c_void_p),
                ("extradata_size", c_int), ("coded_side_data", c_void_p), ("nb_coded_side_data", c_int),
                ("format", c_int), ("bit_rate", c_int64), ("bits_per_coded_sample", c_int),
                ("bits_per_raw_sample", c_int), ("profile", c_int), ("level", c_int), ("width", c_int),
                ("height", c_int), ("sample_aspect_ratio", AVRational), ("framerate", AVRational),
                ("field_order", c_int), ("color_range", c_int), ("color_primaries", c_int), ("color_trc", c_int),
                ("color_space", c_int), ("chroma_location", c_int), ("video_delay", c_int),
                ("ch_layout", AVChannelLayout), ("sample_rate", c_int)]


class AVStream(Structure):
    _fields_ = [("av_class", c_void_p), ("index", c_int), ("id", c_int), ("codecpar", POINTER(AVCodecParameters)),
                ("priv_data", c_void_p), ("time_base", AVRational), ("start_time", c_int64),
                ("duration", c_int64), ("nb_frames", c_int64)]


class AVInputFormat(Structure):
    _fields_ = [("name", c_char_p)]


class AVFormatContext(Structure):
    _fields_ = [("av_class", c_void_p), ("iformat", POINTER(AVInputFormat)), ("oformat", c_void_p),
                ("priv_data", c_void_p), ("pb", c_void_p), ("ctx_flags", c_int), ("nb_streams", c_uint),
                ("streams", POINTER(POINTER(AVStream))), ("nb_stream_groups", c_uint), ("stream_groups", c_void_p),
                ("nb_chapters", c_uint), ("chapters", c_void_p), ("url", c_char_p), ("start_time", c_int64),
                ("duration", c_int64)]


@dataclass(frozen=True)
class StreamInfo:
    index: int
    kind: str  # "video", "audio" or "other"
    codec: str  # FFmpeg's codec name: "h264", "aac", ...
    width: int  # video only
    height: int
    sample_rate: int  # audio only
    channels: int
    start: float | None  # seconds
    duration: float | None
    frames: int  # 0 when the file doesn't say


@dataclass(frozen=True)
class MediaInfo:
    format: str  # FFmpeg's container names, e.g. "mov,mp4,m4a,3gp,3g2,mj2"
    duration: float | None  # seconds
    streams: tuple[StreamInfo, ...]

    def first(self, kind: str) -> StreamInfo | None:
        return next((s for s in self.streams if s.kind == kind), None)


def _folder() -> Path:
    import PySide6  # the libraries sit next to Qt's own, in development and in the packaged app

    return Path(PySide6.__file__).parent


def check_versions(found: dict[str, int]) -> None:
    """Refuse any FFmpeg the declarations above weren't checked against."""
    wrong = [f"{name} {found.get(name)}" for name, major in TESTED.items() if found.get(name) != major]
    if wrong:
        raise FFmpegUnavailable("Video files need FFmpeg 9.0 (avformat 63) as Qt 6.12 ships it; "
                                f"found {', '.join(wrong)}")


@cache
def _libs() -> dict[str, ctypes.CDLL]:
    if sys.platform != "win32":
        raise FFmpegUnavailable("Reading video files directly isn't available on this platform yet")
    folder = _folder()
    os.add_dll_directory(str(folder))
    libs = {}
    for name, filename in WINDOWS_NAMES.items():
        try:
            libs[name] = ctypes.CDLL(str(folder / filename))
        except OSError as exc:
            raise FFmpegUnavailable(f"{filename} is missing or broken: {exc}") from exc
    for name, lib in libs.items():
        getattr(lib, f"{name}_version").restype = c_uint
    check_versions({name: getattr(lib, f"{name}_version")() >> 16 for name, lib in libs.items()})
    _declare(libs)
    return libs


def _declare(libs: dict[str, ctypes.CDLL]) -> None:
    fmt, codec, util = libs["avformat"], libs["avcodec"], libs["avutil"]
    for lib, name, restype, argtypes in [
        (fmt, "avformat_open_input", c_int, [POINTER(POINTER(AVFormatContext)), c_char_p, c_void_p, c_void_p]),
        (fmt, "avformat_find_stream_info", c_int, [POINTER(AVFormatContext), c_void_p]),
        (fmt, "avformat_close_input", None, [POINTER(POINTER(AVFormatContext))]),
        (codec, "avcodec_get_name", c_char_p, [c_int]),
        (codec, "avcodec_license", c_char_p, []),
        (util, "av_dict_set", c_int, [POINTER(c_void_p), c_char_p, c_char_p, c_int]),
        (util, "av_dict_free", None, [POINTER(c_void_p)]),
        (util, "av_strerror", c_int, [c_int, c_char_p, ctypes.c_size_t]),
    ]:
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = restype, argtypes


def available() -> bool:
    try:
        _libs()
    except FFmpegUnavailable:
        return False
    return True


def versions() -> dict[str, int]:
    """Major versions of the libraries found (raises FFmpegUnavailable if they can't be used)."""
    return {name: getattr(lib, f"{name}_version")() >> 16 for name, lib in _libs().items()}


def license_text() -> str:
    return _libs()["avcodec"].avcodec_license().decode()


def _message(code: int) -> str:
    buf = ctypes.create_string_buffer(256)
    _libs()["avutil"].av_strerror(code, buf, len(buf))
    return buf.value.decode(errors="replace") or f"error {code}"


def _seconds(value: int, base: AVRational) -> float | None:
    if value == AV_NOPTS_VALUE or not base.den:
        return None
    return value * base.num / base.den


def open_input(path: str | os.PathLike) -> ctypes._Pointer:
    """Open a local file for reading; the caller closes it with close_input()."""
    libs = _libs()
    options = c_void_p()
    libs["avutil"].av_dict_set(byref(options), b"protocol_whitelist", b"file", 0)
    ctx = POINTER(AVFormatContext)()
    name = os.fspath(path)
    try:
        code = libs["avformat"].avformat_open_input(byref(ctx), name.encode("utf-8"), None, byref(options))
    finally:
        libs["avutil"].av_dict_free(byref(options))
    if code < 0:
        raise FFmpegError(f"Couldn't open {Path(name).name}: {_message(code)}")
    code = libs["avformat"].avformat_find_stream_info(ctx, None)
    if code < 0:
        close_input(ctx)
        raise FFmpegError(f"Couldn't read {Path(name).name}: {_message(code)}")
    return ctx


def close_input(ctx) -> None:
    _libs()["avformat"].avformat_close_input(byref(ctx))


def probe(path: str | os.PathLike) -> MediaInfo:
    """What a video file really contains: its container, streams, codecs, sizes and lengths."""
    libs = _libs()
    ctx = open_input(path)
    try:
        fc = ctx.contents
        streams = []
        for i in range(fc.nb_streams):
            st = fc.streams[i].contents
            par = st.codecpar.contents
            kind = {AVMEDIA_TYPE_VIDEO: "video", AVMEDIA_TYPE_AUDIO: "audio"}.get(par.codec_type, "other")
            streams.append(StreamInfo(
                index=st.index, kind=kind, codec=(libs["avcodec"].avcodec_get_name(par.codec_id) or b"").decode(),
                width=par.width if kind == "video" else 0, height=par.height if kind == "video" else 0,
                sample_rate=par.sample_rate if kind == "audio" else 0,
                channels=par.ch_layout.nb_channels if kind == "audio" else 0,
                start=_seconds(st.start_time, st.time_base), duration=_seconds(st.duration, st.time_base),
                frames=max(0, st.nb_frames)))
        fmt = fc.iformat.contents.name.decode() if fc.iformat else ""
        return MediaInfo(fmt, _seconds(fc.duration, AVRational(1, AV_TIME_BASE)), tuple(streams))
    finally:
        close_input(ctx)
