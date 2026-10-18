"""Checking a finished recording with FFmpeg: is the file what was recorded?

The container's length is the longer of its streams, so each stream is checked on its own: a file
whose picture stopped early but whose sound ran on would otherwise pass.
"""
from __future__ import annotations

from pathlib import Path

from . import ffmpeg

LENGTH_TOLERANCE = 0.5  # seconds, plus two frames: how far a stream may be from the time recorded
FEW_FRAMES = 0.5  # fewer than this share of the frames due means the computer was too busy


def check_file(path: Path, seconds: float, *, sound: bool, size: tuple[int, int],
               fps: int) -> tuple[str, list[str]]:
    """(why the file isn't the recording, or ''), and warnings about a file that is."""
    try:
        info = ffmpeg.probe(path)
    except (ffmpeg.FFmpegError, OSError) as exc:
        return f"The recording was saved but can't be read back: {exc}", []
    video, audio = info.first("video"), info.first("audio")
    if video is None or video.codec != "h264":
        return "The recording was saved without H.264 video", []
    if (video.width, video.height) != tuple(size):
        return f"The recording is {video.width}x{video.height}, not {size[0]}x{size[1]}", []
    if sound and (audio is None or audio.codec != "aac"):
        return "The recording was saved without its sound", []
    tolerance = LENGTH_TOLERANCE + 2 / fps
    for name, stream in (("picture", video), ("sound", audio if sound else None)):
        if stream is not None and (stream.duration is None or abs(stream.duration - seconds) > tolerance):
            return f"The recording's {name} is {stream.duration} s long, not the {seconds:.1f} s recorded", []
    warnings = []
    due = seconds * fps
    if seconds >= 1 and video.frames and video.frames < FEW_FRAMES * due:
        warnings.append(f"The computer was too busy: {video.frames} of {round(due)} frames were recorded, "
                        "so the video may look jerky")
    return "", warnings
