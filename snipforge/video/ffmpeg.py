"""The FFmpeg libraries that Qt Multimedia ships, called directly (ctypes) for what Qt doesn't offer:
reading exactly what a video file contains, and later trimming and copying it without re-encoding.

They are Qt's own LGPL build, loaded from the PySide6 folder, so nothing extra is bundled
(THIRD_PARTY_NOTICES.md). Only the few struct fields used are declared below, copied from FFmpeg
9.0.1's headers; they only change between major versions, so any other version is refused
(FFmpegUnavailable) until it has been checked, rather than misread.

Every file is opened with FFmpeg's protocol whitelist set to "file" (the libraries can speak http,
rtsp and more, and SnipForge never lets them go online) and only the container formats SnipForge
records are read (MP4, Matroska, AVI), so a strange file never reaches FFmpeg's other readers.
"""
from __future__ import annotations

import ctypes
import errno
import os
import sys
from ctypes import POINTER, Structure, byref, c_char_p, c_int, c_int64, c_uint, c_uint32, c_void_p
from dataclasses import dataclass
from functools import cache
from pathlib import Path

TESTED = {"avformat": 63, "avcodec": 63, "avutil": 61, "swscale": 10}  # FFmpeg 9.0, in Qt 6.12
WINDOWS_NAMES = {"avutil": "avutil-61.dll", "avcodec": "avcodec-63.dll", "avformat": "avformat-63.dll",
                 "swscale": "swscale-10.dll"}
AV_NOPTS_VALUE = -0x8000000000000000
AV_TIME_BASE = 1_000_000
AVMEDIA_TYPE_VIDEO, AVMEDIA_TYPE_AUDIO = 0, 1
AVERROR_EOF = -0x20464F45  # FFERRTAG('E', 'O', 'F', ' ')
AVERROR_INVALIDDATA = -0x41444E49  # FFERRTAG('I', 'N', 'D', 'A')
AVERROR_EAGAIN = -errno.EAGAIN  # AVERROR(EAGAIN): 11 on Windows, 35 on macOS
AVSEEK_FLAG_BACKWARD = 1
AV_DISPOSITION_ATTACHED_PIC = 0x400  # a cover picture, not video
FORMATS = b"mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi"  # the containers SnipForge reads


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
                ("duration", c_int64), ("nb_frames", c_int64), ("disposition", c_int)]


class AVPacket(Structure):  # allocated by av_packet_alloc, never by us
    _fields_ = [("buf", c_void_p), ("pts", c_int64), ("dts", c_int64), ("data", c_void_p), ("size", c_int),
                ("stream_index", c_int), ("flags", c_int), ("side_data", c_void_p), ("side_data_elems", c_int),
                ("duration", c_int64), ("pos", c_int64)]


class AVFrame(Structure):  # allocated by av_frame_alloc, never by us
    _fields_ = [("data", c_void_p * 8), ("linesize", c_int * 8), ("extended_data", c_void_p), ("width", c_int),
                ("height", c_int), ("nb_samples", c_int), ("format", c_int), ("pict_type", c_int),
                ("sample_aspect_ratio", AVRational), ("pts", c_int64), ("pkt_dts", c_int64),
                ("time_base", AVRational), ("quality", c_int), ("opaque", c_void_p), ("repeat_pict", c_int),
                ("sample_rate", c_int), ("buf", c_void_p * 8), ("extended_buf", c_void_p), ("nb_extended_buf", c_int),
                ("side_data", c_void_p), ("nb_side_data", c_int), ("flags", c_int), ("color_range", c_int),
                ("color_primaries", c_int), ("color_trc", c_int), ("colorspace", c_int), ("chroma_location", c_int),
                ("best_effort_timestamp", c_int64)]


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


def _library_files() -> dict[str, Path]:
    """Where Qt keeps each library: next to Qt's DLLs on Windows, in Qt/lib (as libavformat.63.dylib
    and so on) on macOS. Checked by version once loaded, whatever the name."""
    folder = _folder()
    if sys.platform == "win32":
        return {name: folder / filename for name, filename in WINDOWS_NAMES.items()}
    found = {}
    lib = folder / "Qt" / "lib"
    for name, major in TESTED.items():
        patterns = (f"lib{name}.{major}*.dylib", f"lib{name}.so.{major}*")
        matches = [m for p in patterns for m in sorted(lib.glob(p))]
        matches = matches or [m for p in patterns for m in sorted(folder.rglob(p))]
        if not matches:
            raise FFmpegUnavailable(f"Qt's lib{name} wasn't found under {folder}")
        found[name] = matches[0]
    return found


@cache
def _libs() -> dict[str, ctypes.CDLL]:
    files = _library_files()
    if sys.platform == "win32":
        os.add_dll_directory(str(_folder()))
    libs = {}
    for name in [n for n in ("avutil", "swscale", "avcodec", "avformat") if n in files]:  # dependencies first
        try:
            libs[name] = ctypes.CDLL(str(files[name]))
        except OSError as exc:
            raise FFmpegUnavailable(f"{files[name].name} is missing or broken: {exc}") from exc
    for name, lib in libs.items():
        getattr(lib, f"{name}_version").restype = c_uint
    check_versions({name: getattr(lib, f"{name}_version")() >> 16 for name, lib in libs.items()})
    _declare(libs)
    return libs


def _declare(libs: dict[str, ctypes.CDLL]) -> None:
    fmt, codec, util, sws = libs["avformat"], libs["avcodec"], libs["avutil"], libs["swscale"]
    ctx_p, pkt_p, frame_p = POINTER(AVFormatContext), POINTER(AVPacket), POINTER(AVFrame)
    for lib, name, restype, argtypes in [
        (fmt, "avformat_open_input", c_int, [POINTER(ctx_p), c_char_p, c_void_p, c_void_p]),
        (fmt, "avformat_find_stream_info", c_int, [ctx_p, c_void_p]),
        (fmt, "avformat_close_input", None, [POINTER(ctx_p)]),
        (fmt, "avformat_alloc_output_context2", c_int, [POINTER(ctx_p), c_void_p, c_char_p, c_char_p]),
        (fmt, "avformat_free_context", None, [ctx_p]),
        (fmt, "avformat_new_stream", POINTER(AVStream), [ctx_p, c_void_p]),
        (fmt, "avformat_write_header", c_int, [ctx_p, c_void_p]),
        (fmt, "av_write_trailer", c_int, [ctx_p]),
        (fmt, "av_read_frame", c_int, [ctx_p, pkt_p]),
        (fmt, "av_interleaved_write_frame", c_int, [ctx_p, pkt_p]),
        (fmt, "av_seek_frame", c_int, [ctx_p, c_int, c_int64, c_int]),
        (fmt, "avio_open2", c_int, [POINTER(c_void_p), c_char_p, c_int, c_void_p, POINTER(c_void_p)]),
        (fmt, "avio_closep", c_int, [POINTER(c_void_p)]),
        (codec, "avcodec_get_name", c_char_p, [c_int]),
        (codec, "avcodec_license", c_char_p, []),
        (codec, "avcodec_parameters_copy", c_int, [POINTER(AVCodecParameters), POINTER(AVCodecParameters)]),
        (codec, "avcodec_find_decoder", c_void_p, [c_int]),
        (codec, "avcodec_alloc_context3", c_void_p, [c_void_p]),
        (codec, "avcodec_parameters_to_context", c_int, [c_void_p, POINTER(AVCodecParameters)]),
        (codec, "avcodec_open2", c_int, [c_void_p, c_void_p, c_void_p]),
        (codec, "avcodec_send_packet", c_int, [c_void_p, pkt_p]),
        (codec, "avcodec_receive_frame", c_int, [c_void_p, frame_p]),
        (codec, "avcodec_free_context", None, [POINTER(c_void_p)]),
        (codec, "av_packet_alloc", pkt_p, []),
        (codec, "av_packet_free", None, [POINTER(pkt_p)]),
        (codec, "av_packet_unref", None, [pkt_p]),
        (codec, "av_packet_rescale_ts", None, [pkt_p, AVRational, AVRational]),
        (util, "av_dict_set", c_int, [POINTER(c_void_p), c_char_p, c_char_p, c_int]),
        (util, "av_dict_free", None, [POINTER(c_void_p)]),
        (util, "av_strerror", c_int, [c_int, c_char_p, ctypes.c_size_t]),
        (util, "av_frame_alloc", frame_p, []),
        (util, "av_frame_free", None, [POINTER(frame_p)]),
        (util, "av_frame_unref", None, [frame_p]),
        (util, "av_get_pix_fmt", c_int, [c_char_p]),
        (sws, "sws_getContext", c_void_p, [c_int, c_int, c_int, c_int, c_int, c_int, c_int, c_void_p, c_void_p,
                                           c_void_p]),
        (sws, "sws_scale", c_int, [c_void_p, c_void_p, c_void_p, c_int, c_int, c_void_p, c_void_p]),
        (sws, "sws_freeContext", None, [c_void_p]),
    ]:
        fn = getattr(lib, name)
        fn.restype, fn.argtypes = restype, argtypes


def available() -> bool:
    return unavailable_reason() is None


def unavailable_reason() -> str | None:
    """Why the libraries can't be used here, or None when they can."""
    try:
        _libs()
    except FFmpegUnavailable as exc:
        return str(exc)
    return None


def versions() -> dict[str, int]:
    """Major versions of the libraries found (raises FFmpegUnavailable if they can't be used)."""
    return {name: getattr(lib, f"{name}_version")() >> 16 for name, lib in _libs().items()}


def license_text() -> str:
    return _libs()["avcodec"].avcodec_license().decode()


def libs() -> dict[str, ctypes.CDLL]:
    """The loaded libraries, for the other video modules (raises FFmpegUnavailable)."""
    return _libs()


def message(code: int) -> str:
    """FFmpeg's description of an error code."""
    buf = ctypes.create_string_buffer(256)
    _libs()["avutil"].av_strerror(code, buf, len(buf))
    return buf.value.decode(errors="replace") or f"error {code}"


def seconds(value: int, base: AVRational) -> float | None:
    """A timestamp in seconds, or None when the file doesn't say."""
    if value == AV_NOPTS_VALUE or not base.den:
        return None
    return value * base.num / base.den


def local_options(formats: bytes | None = None) -> c_void_p:
    """An AVDictionary that limits FFmpeg to local files (and to `formats`, when reading); the
    caller frees it with av_dict_free."""
    util = _libs()["avutil"]
    options = c_void_p()
    settings = [(b"protocol_whitelist", b"file")] + ([(b"format_whitelist", formats)] if formats else [])
    for key, value in settings:
        if util.av_dict_set(byref(options), key, value, 0) < 0:
            util.av_dict_free(byref(options))
            raise FFmpegError("Couldn't limit FFmpeg to local files")  # never open anything without it
    return options


def open_input(path: str | os.PathLike) -> ctypes._Pointer:
    """Open a local file for reading; the caller closes it with close_input()."""
    libs = _libs()
    options = local_options(FORMATS)
    ctx = POINTER(AVFormatContext)()
    name = os.fspath(path)
    try:
        code = libs["avformat"].avformat_open_input(byref(ctx), name.encode("utf-8"), None, byref(options))
    finally:
        libs["avutil"].av_dict_free(byref(options))
    if code < 0:
        raise FFmpegError(f"Couldn't open {Path(name).name}: {message(code)}")
    code = libs["avformat"].avformat_find_stream_info(ctx, None)
    if code < 0:
        close_input(ctx)
        raise FFmpegError(f"Couldn't read {Path(name).name}: {message(code)}")
    return ctx


def close_input(ctx) -> None:
    _libs()["avformat"].avformat_close_input(byref(ctx))


def streams(ctx) -> list[AVStream]:
    fc = ctx.contents
    return [fc.streams[i].contents for i in range(fc.nb_streams)]


def kind(stream: AVStream) -> int:
    return stream.codecpar.contents.codec_type


def video_stream(ctx) -> AVStream | None:
    """The file's video (not a cover picture), if it has any."""
    return next((st for st in streams(ctx) if kind(st) == AVMEDIA_TYPE_VIDEO
                 and not st.disposition & AV_DISPOSITION_ATTACHED_PIC), None)


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
                start=seconds(st.start_time, st.time_base), duration=seconds(st.duration, st.time_base),
                frames=max(0, st.nb_frames)))
        fmt = fc.iformat.contents.name.decode() if fc.iformat else ""
        return MediaInfo(fmt, seconds(fc.duration, AVRational(1, AV_TIME_BASE)), tuple(streams))
    finally:
        close_input(ctx)
