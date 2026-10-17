"""Trimming a video, or copying it into MP4, without re-encoding: no quality lost, done in moments.

The copy starts at the keyframe before `start`; those extra frames get times before zero, which the
MP4 writer turns into an edit list, so players start exactly at `start`, to the frame. (Qt's encoder
puts keyframes up to 3 s apart, so up to 3 s of hidden video can come along; players that ignore edit
lists would show it.) Sound is cut to the nearest audio packet (about 20 ms). Only the audio and video
streams are copied. Uses the FFmpeg libraries Qt ships, local files only.
"""
from __future__ import annotations

import logging
import os
import tempfile
from ctypes import POINTER, byref, c_char_p, c_void_p
from dataclasses import dataclass, field
from pathlib import Path

from . import ffmpeg

log = logging.getLogger(__name__)

AVIO_FLAG_WRITE = 2
AUDIO_PREROLL = 0.05  # seconds of sound kept before the cut, so the decoder starts cleanly
MAX_AUDIO_PACKET = 0.25  # seconds; an AAC packet is about 0.021
EPSILON = 1e-6


def copy(src: str | os.PathLike, dst: str | os.PathLike, start: float = 0.0, end: float | None = None) -> Path:
    """Copy `src` from `start` up to `end` (seconds; `end` itself not included; None for the rest)
    into the MP4 file `dst`, which is written whole or not at all. Raises ValueError for a range or
    a name that can't work, FFmpegError if FFmpeg fails, and OSError if `dst` can't be replaced
    (open in another program, for example)."""
    src, dst = _check_names(src, dst)
    if start < 0 or (end is not None and end <= start):
        raise ValueError(f"Nothing to keep between {start} and {end} s")
    handle, partial_name = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.stem}.", suffix=".partial.mp4")
    os.close(handle)
    partial = Path(partial_name)
    try:
        _Copy(src, partial, start, end).run()
        os.replace(partial, dst)
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError as exc:  # an antivirus or sync client holding it; never hide the real error
            log.warning("Couldn't remove %s: %s", partial, exc)
    return dst


def _check_names(src, dst) -> tuple[Path, Path]:
    if any("://" in os.fspath(p) for p in (src, dst)):
        raise ValueError("Only local files can be trimmed")
    src, dst = Path(src), Path(dst)
    if ":" in dst.name:  # an NTFS alternate data stream, or a protocol FFmpeg would try
        raise ValueError(f"{dst.name} isn't a plain file name")
    if dst.exists() and src.exists() and os.path.samefile(src, dst):
        raise ValueError("A trim can't overwrite the video it comes from")
    return src, dst


@dataclass
class _Stream:
    source: ffmpeg.AVStream
    out_index: int
    out_base: ffmpeg.AVRational | None = None
    audio: bool = False
    finished: bool = False


@dataclass
class _Copy:
    src: Path
    dst: Path
    start: float
    end: float | None
    streams: dict[int, _Stream] = field(default_factory=dict)
    visible_video: int = 0

    def run(self) -> None:
        libs = ffmpeg.libs()
        fmt, codec_lib = libs["avformat"], libs["avcodec"]
        ictx = ffmpeg.open_input(self.src)
        octx = None
        packet = codec_lib.av_packet_alloc()
        try:
            first, last = self._range(ictx)
            octx = self._open_output(ictx)
            if first > EPSILON:
                code = fmt.av_seek_frame(ictx, -1, round(first * ffmpeg.AV_TIME_BASE), ffmpeg.AVSEEK_FLAG_BACKWARD)
                if code < 0:
                    raise ffmpeg.FFmpegError(f"Couldn't go to {self.start:.2f} s in {self.src.name}: "
                                             f"{ffmpeg.message(code)}")
            self._copy_packets(ictx, octx, packet, first, last)
            if not self.visible_video and any(not s.audio for s in self.streams.values()):
                raise ValueError(f"{self.src.name} has no video after {self.start:.2f} s")
            code = fmt.av_write_trailer(octx)
            if code < 0:
                raise ffmpeg.FFmpegError(f"Couldn't finish {self.dst.name}: {ffmpeg.message(code)}")
        finally:
            if octx:
                self._discard(octx)
            codec_lib.av_packet_free(byref(packet))
            ffmpeg.close_input(ictx)

    def _range(self, ictx) -> tuple[float, float | None]:
        """The cut, in the file's own time: zero is its first video frame (Qt's MKVs start at ~1 s)."""
        video = ffmpeg.video_stream(ictx)
        origin = max(0.0, ffmpeg.seconds(video.start_time, video.time_base) or 0.0) if video else 0.0
        length = ffmpeg.seconds(ictx.contents.duration, ffmpeg.AVRational(1, ffmpeg.AV_TIME_BASE))
        if length is not None and self.start >= length - origin - EPSILON:
            raise ValueError(f"{self.src.name} is only {length - origin:.2f} s long")
        return self.start + origin, None if self.end is None else self.end + origin

    def _open_output(self, ictx):
        libs = ffmpeg.libs()
        fmt, codec_lib = libs["avformat"], libs["avcodec"]
        octx = POINTER(ffmpeg.AVFormatContext)()
        name = os.fspath(self.dst).encode("utf-8")
        code = fmt.avformat_alloc_output_context2(byref(octx), None, b"mp4", name)
        if code < 0:
            raise ffmpeg.FFmpegError(f"Couldn't create {self.dst.name}: {ffmpeg.message(code)}")
        for stream in ffmpeg.streams(ictx):
            if ffmpeg.kind(stream) not in (ffmpeg.AVMEDIA_TYPE_VIDEO, ffmpeg.AVMEDIA_TYPE_AUDIO) or (
                    stream.disposition & ffmpeg.AV_DISPOSITION_ATTACHED_PIC):
                continue  # subtitles, data and cover pictures stay behind
            out = fmt.avformat_new_stream(octx, None)
            if not out or codec_lib.avcodec_parameters_copy(out.contents.codecpar, stream.codecpar) < 0:
                fmt.avformat_free_context(octx)
                raise ffmpeg.FFmpegError(f"Couldn't copy the streams of {self.src.name}")
            out.contents.codecpar.contents.codec_tag = 0  # let the MP4 writer pick its own tag
            out.contents.time_base = stream.time_base
            self.streams[stream.index] = _Stream(stream, len(self.streams),
                                                 audio=ffmpeg.kind(stream) == ffmpeg.AVMEDIA_TYPE_AUDIO)
        options = ffmpeg.local_options()
        pb = c_void_p()
        try:
            code = fmt.avio_open2(byref(pb), c_char_p(name), AVIO_FLAG_WRITE, None, byref(options))
        finally:
            libs["avutil"].av_dict_free(byref(options))
        if code >= 0:
            octx.contents.pb = pb
            code = fmt.avformat_write_header(octx, None)
        if code < 0:
            self._discard(octx)
            raise ffmpeg.FFmpegError(f"Couldn't write {self.dst.name}: {ffmpeg.message(code)}")
        for s in self.streams.values():
            s.out_base = octx.contents.streams[s.out_index].contents.time_base
        return octx

    @staticmethod
    def _discard(octx) -> None:
        fmt = ffmpeg.libs()["avformat"]
        if octx.contents.pb:
            pb = c_void_p(octx.contents.pb)
            fmt.avio_closep(byref(pb))
            octx.contents.pb = None
        fmt.avformat_free_context(octx)

    def _copy_packets(self, ictx, octx, packet, first: float, last: float | None) -> None:
        libs = ffmpeg.libs()
        fmt, codec_lib = libs["avformat"], libs["avcodec"]
        while not all(s.finished for s in self.streams.values()):
            code = fmt.av_read_frame(ictx, packet)
            if code == ffmpeg.AVERROR_EOF:
                return
            if code < 0:
                raise ffmpeg.FFmpegError(f"Couldn't read {self.src.name}: {ffmpeg.message(code)}")
            try:
                stream = self.streams.get(packet.contents.stream_index)
                if stream and not stream.finished:
                    self._copy_packet(octx, packet, stream, first, last)
            finally:
                codec_lib.av_packet_unref(packet)

    def _copy_packet(self, octx, packet, stream: _Stream, first: float, last: float | None) -> None:
        libs = ffmpeg.libs()
        p = packet.contents
        base = stream.source.time_base
        stamp = p.pts if p.pts != ffmpeg.AV_NOPTS_VALUE else p.dts
        t = stamp * base.num / base.den
        if last is not None and t >= last - EPSILON:
            stream.finished = True  # and nothing more from it: a later B-frame may need a frame left out
            return
        if stream.audio and t < first - AUDIO_PREROLL:
            return
        if not stream.audio and t >= first - EPSILON:
            self.visible_video += 1
        if stream.audio and p.duration * base.num / base.den > MAX_AUDIO_PACKET:
            p.duration = 0  # Qt's Matroska says 1.024 s per AAC packet; the MP4 writer works it out instead
        shift = round(first * base.den / base.num)
        if p.pts != ffmpeg.AV_NOPTS_VALUE:
            p.pts -= shift
        if p.dts != ffmpeg.AV_NOPTS_VALUE:
            p.dts -= shift  # before zero ahead of the cut: the MP4 writer adds an edit list
        p.pos = -1
        p.stream_index = stream.out_index
        libs["avcodec"].av_packet_rescale_ts(packet, base, stream.out_base)
        code = libs["avformat"].av_interleaved_write_frame(octx, packet)
        if code < 0:
            raise ffmpeg.FFmpegError(f"Couldn't write {self.dst.name}: {ffmpeg.message(code)}")
